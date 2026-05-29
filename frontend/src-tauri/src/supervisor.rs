//! Sidecar supervisor — the Rust shell's most important job.
//!
//! Spawns the Python FastAPI sidecar, health-checks it on `/healthz`, restarts
//! it if it crashes (with backoff + a give-up cap), and kills it when the app
//! exits. It also: attaches to an already-healthy sidecar instead of spawning a
//! duplicate; takes ownership of the port from a non-Parrot squatter; pipes the
//! sidecar's stdout/stderr to rotating-ish log files; ensures the venv exists;
//! and surfaces boot stages to the UI (`bootstrap-stage` / `bootstrap-log`).
//! This is the only place that owns the sidecar process.
//! See docs/specs/architecture.md §3 and docs/specs/first-run-setup.md.

use std::fs::File;
use std::net::TcpStream;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Mutex, MutexGuard};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use tauri::{AppHandle, Emitter, Manager};

/// How often the supervisor polls the child + health endpoint.
const POLL_INTERVAL: Duration = Duration::from_millis(800);
/// Give up (stop respawning, emit `sidecar-failed`) after this many consecutive
/// starts that never became healthy.
const MAX_RAPID_FAILURES: u32 = 5;
/// Ceiling on the restart backoff.
const BACKOFF_CAP_SECS: u64 = 30;
/// A spawned child that binds but never serves `/healthz` within this window is
/// killed and counted as a failure — otherwise a hung-but-alive sidecar would
/// keep `try_wait` returning `None` forever and never trip `MAX_RAPID_FAILURES`.
const STARTUP_DEADLINE_SECS: u64 = 300;
/// Windows process-creation flag that suppresses a flashing console window when
/// spawning a console subprocess (uv/netstat/taskkill/python) from a GUI app.
#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

/// Apply `CREATE_NO_WINDOW` on Windows so helper subprocesses (uv, netstat,
/// taskkill, tasklist, the sidecar python) never flash a console. No-op on other
/// platforms. Centralized so every `Command` in this module stays consistent.
fn no_window(cmd: &mut Command) -> &mut Command {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    cmd
}

/// Shared state for the supervised sidecar. Managed by Tauri so commands and
/// the run-loop can reach it.
pub struct SidecarState {
    /// The running child, if any. Held in a Mutex so the monitor thread can
    /// poll it and the exit handler can kill it.
    child: Mutex<Option<Child>>,
    /// Set on app exit so the monitor stops restarting the sidecar.
    shutting_down: AtomicBool,
    /// True once `/healthz` has answered at least once.
    pub ready: AtomicBool,
    /// True once the supervisor has permanently given up (crash-looped). Latched
    /// so the UI can query the terminal state even if it missed the event.
    pub failed: AtomicBool,
    /// Current boot stage (for the splash) + a backfillable log tail.
    stage: Mutex<String>,
    logs: Mutex<Vec<String>>,
    /// Recovery signals set by the Retry / Clean & Retry commands.
    retry_requested: AtomicBool,
    clean_requested: AtomicBool,
    /// The supervisor's monitor thread, so `shutdown()` can join it (bounded) and
    /// guarantee no spawn races teardown after exit returns.
    monitor: Mutex<Option<JoinHandle<()>>>,
    /// The loopback port the sidecar is bound to.
    pub port: u16,
}

impl SidecarState {
    pub fn new(port: u16) -> Self {
        Self {
            child: Mutex::new(None),
            shutting_down: AtomicBool::new(false),
            ready: AtomicBool::new(false),
            failed: AtomicBool::new(false),
            stage: Mutex::new("checking".into()),
            logs: Mutex::new(Vec::new()),
            retry_requested: AtomicBool::new(false),
            clean_requested: AtomicBool::new(false),
            monitor: Mutex::new(None),
            port,
        }
    }

    fn set_stage(&self, app: &AppHandle, stage: &str) {
        *lock(&self.stage) = stage.to_string();
        let _ = app.emit("bootstrap-stage", stage);
        self.log(app, &format!("[stage] {stage}"));
    }

    fn log(&self, app: &AppHandle, line: &str) {
        {
            let mut guard = lock(&self.logs);
            guard.push(line.to_string());
            if guard.len() > 500 {
                let drop = guard.len() - 500;
                guard.drain(0..drop);
            }
        }
        let _ = app.emit("bootstrap-log", line);
    }
}

/// Lock a Mutex, recovering the guard if a previous holder panicked. The
/// supervisor is the most critical path in the app — a poisoned lock must not
/// be allowed to panic the monitor thread and silently end supervision.
fn lock<T>(m: &Mutex<T>) -> MutexGuard<'_, T> {
    m.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
}

/// Restart backoff (seconds) for the Nth consecutive failure:
/// 0→0, 1→1, 2→2, 3→4, 4→8, 5→16, then capped at `BACKOFF_CAP_SECS`.
fn backoff_secs(consecutive_failures: u32) -> u64 {
    if consecutive_failures == 0 {
        return 0;
    }
    let shift = consecutive_failures.saturating_sub(1).min(5);
    (1u64 << shift).min(BACKOFF_CAP_SECS)
}

/// New consecutive-failure count after a child exits. A child that ever answered
/// `/healthz` is treated as a real start (counter resets); one that died without
/// ever becoming healthy is a failure — regardless of how long it limped along,
/// so a process that binds then hangs without serving health still trips the cap.
fn failures_after_exit(prev: u32, was_healthy: bool) -> u32 {
    if was_healthy {
        0
    } else {
        prev + 1
    }
}

/// Port the sidecar binds to. `PARROT_PORT` overrides the 3900 default; the
/// supervisor passes the same value to the child so they never diverge. A value
/// outside the valid u16 port range falls back to 3900 (matches the Python
/// `config.port()` validation so the two processes never bind different ports).
pub fn resolve_port() -> u16 {
    std::env::var("PARROT_PORT")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(3900)
}

/// Directory containing the sidecar's `main.py`. `PARROT_SIDECAR_DIR` overrides
/// the dev default (`<repo>/sidecar`, relative to this crate). In a packaged
/// build this points at the bundled sidecar resource dir.
fn sidecar_dir() -> PathBuf {
    if let Ok(dir) = std::env::var("PARROT_SIDECAR_DIR") {
        return PathBuf::from(dir);
    }
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../sidecar")
}

/// The durable data dir the sidecar owns (`parrot_data/`). Honors a
/// `PARROT_DATA_DIR` override (dev/test); else `%APPDATA%\\Parrot\\parrot_data`.
/// The supervisor passes this to the sidecar as `PARROT_DATA_DIR` so the two
/// processes agree on the location (e.g. for `get_app_paths`).
pub fn data_dir(app: &AppHandle) -> PathBuf {
    if let Ok(dir) = std::env::var("PARROT_DATA_DIR") {
        return PathBuf::from(dir);
    }
    app.path()
        .app_data_dir()
        .map(|p| p.join("parrot_data"))
        .unwrap_or_else(|_| PathBuf::from("parrot_data"))
}

/// The bootstrapped venv directory. It lives UNDER the writable data dir
/// (`parrot_data/.venv`), never inside the read-only installed bundle — see
/// packaging.md Rule 4 + the "disk full / read-only location" edge case. The
/// installed sidecar source dir may be in Program Files and is not writable.
fn venv_dir(app: &AppHandle) -> PathBuf {
    data_dir(app).join(".venv")
}

/// The venv's Python interpreter. The supervisor spawns THIS directly (not
/// `uv run ...`) so the immediate child is `python.exe` and `child.kill()`
/// actually reaps the engine instead of just the `uv` launcher (architecture
/// §3.3 / orphaned-process fix). Windows layout: `<venv>\Scripts\python.exe`.
fn venv_python(app: &AppHandle) -> PathBuf {
    venv_dir(app).join("Scripts").join("python.exe")
}

/// Marker written ONLY after a fully successful `uv sync`. Its presence (next to
/// an existing venv python) is what lets a later launch skip the bootstrap. An
/// interrupted cu124 download leaves python.exe behind WITHOUT this sentinel, so
/// the fast-path re-syncs instead of booting a torch-less venv (B3). Lives inside
/// the venv so wiping the venv (Clean & Retry) also clears it.
fn sync_sentinel(app: &AppHandle) -> PathBuf {
    venv_dir(app).join(".parrot-sync-complete")
}

/// Is the bootstrap complete enough to skip? Requires BOTH the venv interpreter
/// AND the success sentinel — a python.exe without the sentinel is a partial
/// (interrupted) install that must be re-synced rather than booted broken (B3).
fn venv_is_ready(app: &AppHandle) -> bool {
    venv_python(app).exists() && sync_sentinel(app).exists()
}

/// Where the sidecar's stdout/stderr logs land (and the Tauri log).
pub fn log_dir(app: &AppHandle) -> PathBuf {
    app.path()
        .app_log_dir()
        .unwrap_or_else(|_| data_dir(app).join("logs"))
}

/// Open the uv bootstrap log for the given stdio handle (append, so successive
/// launches + backoff retries accumulate rather than clobbering the prior run). In a
/// release build (`windows_subsystem = "windows"`, no console) uv's inherited
/// stdout/stderr would vanish, leaving a failed `uv sync --extra cu124` invisible.
/// Routing both streams to `<log_dir>/uv_bootstrap.log` makes the failure readable
/// from Settings → Logs, the same way `spawn_sidecar` files the python child's
/// output. Falls back to `null` if the log file can't be opened (best-effort).
fn uv_log_stdio(app: &AppHandle) -> Stdio {
    let logs = log_dir(app);
    let _ = std::fs::create_dir_all(&logs);
    File::options()
        .create(true)
        .append(true)
        .open(logs.join("uv_bootstrap.log"))
        .map(Stdio::from)
        .unwrap_or_else(|_| Stdio::null())
}

/// Does a `/healthz` body identify a real Parrot sidecar? A foreign server
/// squatting on the port might 200 with junk — or merely 200 with a body that
/// happens to CONTAIN the `"status":"ok"` substring (e.g. inside some other
/// field). Parse as JSON and assert the TOP-LEVEL `status` field equals `ok`, so
/// only a real Parrot health envelope counts. Pure (no I/O) so it is unit-testable
/// without a server.
fn is_ok_health_body(body: &str) -> bool {
    serde_json::from_str::<serde_json::Value>(body)
        .ok()
        .and_then(|v| v.get("status").and_then(|s| s.as_str()).map(str::to_string))
        .is_some_and(|status| status == "ok")
}

fn health_ok(port: u16) -> bool {
    let url = format!("http://127.0.0.1:{port}/healthz");
    match ureq::get(&url).timeout(Duration::from_secs(2)).call() {
        Ok(resp) => {
            if resp.status() != 200 {
                return false;
            }
            resp.into_string().map(|b| is_ok_health_body(&b)).unwrap_or(false)
        }
        Err(_) => false,
    }
}

/// Is *something* accepting TCP on the loopback port (healthy or not)?
fn port_in_use(port: u16) -> bool {
    let addr = format!("127.0.0.1:{port}");
    addr.parse()
        .ok()
        .and_then(|a| TcpStream::connect_timeout(&a, Duration::from_millis(300)).ok())
        .is_some()
}

/// How long to wait for a reclaimed port to actually free up before spawning.
const PORT_FREE_TIMEOUT: Duration = Duration::from_secs(3);

/// Poll until the port is free (or the timeout elapses), instead of sleeping a
/// fixed interval and racing a socket that's slow to release after taskkill.
/// Returns `false` if shutdown was requested while waiting (caller should bail).
fn wait_for_port_free(shutting_down: &AtomicBool, port: u16) -> bool {
    let deadline = Instant::now() + PORT_FREE_TIMEOUT;
    while port_in_use(port) {
        if shutting_down.load(Ordering::SeqCst) {
            return false;
        }
        if Instant::now() >= deadline {
            break; // give up waiting; spawning anyway is best-effort
        }
        std::thread::sleep(Duration::from_millis(150));
    }
    !shutting_down.load(Ordering::SeqCst)
}

/// Kill a process squatting on the port (Windows: netstat → tasklist → taskkill).
/// Best-effort: failures are logged and we proceed to spawn regardless. We only
/// force-kill PIDs whose image is `python.exe`/`uv.exe` — a stale Parrot sidecar
/// or its launcher. An unknown image (some unrelated app that grabbed 3900) is
/// left alone and logged, so we never `taskkill /F` a process that isn't ours.
fn kill_orphan_on_port(app: &AppHandle, state: &SidecarState, port: u16) {
    #[cfg(windows)]
    {
        let out = no_window(Command::new("netstat").args(["-ano", "-p", "TCP"])).output();
        let Ok(out) = out else { return };
        let text = String::from_utf8_lossy(&out.stdout);
        let needle = format!(":{port}");
        for line in text.lines() {
            if !(line.contains(&needle) && line.to_uppercase().contains("LISTENING")) {
                continue;
            }
            let Some(pid) = line.split_whitespace().last() else { continue };
            match pid_image(pid) {
                Some(image) if is_parrot_owned_image(&image) => {
                    state.log(app, &format!("[supervisor] killing port squatter pid={pid} ({image})"));
                    let _ = no_window(Command::new("taskkill").args(["/F", "/PID", pid])).output();
                }
                other => {
                    let label = other.unwrap_or_else(|| "<unknown>".into());
                    state.log(app, &format!("[supervisor] skipping non-Parrot port owner pid={pid} ({label})"));
                }
            }
        }
    }
    #[cfg(not(windows))]
    {
        let _ = (app, state, port); // POSIX takeover is out of scope (Windows-only app)
    }
}

/// Resolve a PID to its process image name via `tasklist` (Windows). Returns the
/// image (e.g. `python.exe`) or `None` if the PID isn't found / can't be parsed.
#[cfg(windows)]
fn pid_image(pid: &str) -> Option<String> {
    let out = no_window(
        Command::new("tasklist").args(["/FI", &format!("PID eq {pid}"), "/FO", "CSV", "/NH"]),
    )
    .output()
    .ok()?;
    let text = String::from_utf8_lossy(&out.stdout);
    // CSV "/NH" rows look like: "python.exe","1234","Console","1","42,000 K"
    let first = text.lines().find(|l| !l.trim().is_empty())?;
    let name = first.trim_start_matches('"').split('"').next()?;
    if name.is_empty() || name.contains("INFO:") {
        return None; // tasklist prints "INFO: No tasks..." when the PID is gone
    }
    Some(name.to_string())
}

/// Is this process image one Parrot is allowed to force-kill on the port — the
/// bootstrapped sidecar (`python.exe`) or its `uv` launcher?
#[cfg(windows)]
fn is_parrot_owned_image(image: &str) -> bool {
    let lower = image.to_ascii_lowercase();
    lower == "python.exe" || lower == "uv.exe" || lower == "pythonw.exe"
}

/// Outcome of waiting on a spawned `uv` child.
enum UvOutcome {
    /// Shutdown was requested mid-run; the child was killed + reaped. The caller
    /// must bail out of the supervise loop.
    ShutdownRequested,
    /// The child finished on its own (exit code carried) or polling errored
    /// (`None` exit). The caller stays on the spawn/health retry path.
    Exited(Option<std::process::ExitStatus>),
}

impl UvOutcome {
    /// Did the child exit cleanly (zero status)? A spawn that never produced a
    /// status, a non-zero exit, or a shutdown is NOT success — so a partial /
    /// interrupted `uv sync` is never mistaken for a complete bootstrap (B3).
    fn succeeded(&self) -> bool {
        matches!(self, UvOutcome::Exited(Some(status)) if status.success())
    }

    /// Was the wait cut short by an app-quit (so the caller must bail)?
    fn is_shutdown(&self) -> bool {
        matches!(self, UvOutcome::ShutdownRequested)
    }
}

/// Poll a spawned `uv` child until it exits, killing + reaping it if shutdown is
/// requested mid-run. Returns `ShutdownRequested` iff shutdown was requested (so
/// the caller bails out of the supervise loop); otherwise `Exited(..)` carrying
/// the child's exit status (or `None` on a poll error) — the spawn/health path
/// then retries, and the sentinel logic can tell a clean sync from a failed one.
fn wait_uv_or_shutdown(child: &mut Child, state: &SidecarState) -> UvOutcome {
    loop {
        if state.shutting_down.load(Ordering::SeqCst) {
            let _ = child.kill();
            let _ = child.wait();
            return UvOutcome::ShutdownRequested;
        }
        match child.try_wait() {
            Ok(Some(status)) => return UvOutcome::Exited(Some(status)),
            Err(_) => return UvOutcome::Exited(None),
            Ok(None) => std::thread::sleep(Duration::from_millis(200)),
        }
    }
}

/// Ensure the Python venv + deps exist (first-run bootstrap). The venv lives
/// under the writable data dir (`parrot_data/.venv`), NOT inside the installed
/// sidecar bundle (packaging Rule 4). The project (`pyproject.toml`/`uv.lock`)
/// is resolved from `sidecar_dir()`; `UV_PROJECT_ENVIRONMENT` redirects uv's
/// environment to the data-dir venv. A *complete* bootstrap (interpreter +
/// success sentinel) short-circuits this on every later launch.
///
/// Returns `false` ONLY when shutdown was requested (the caller then bails out of
/// the supervise loop). A `uv` failure that is NOT a shutdown returns `true` so
/// the caller still attempts the spawn; the spawn/health path then accounts it as
/// a normal failure (backoff + retry), preserving the pre-existing recovery flow.
/// Both `uv venv` and `uv sync` (and the `nvidia-smi` probe) are spawned + polled
/// (never a blocking `.status()`) and killed on quit, so a first-run install can't
/// hang app exit or orphan a child; uv's output is filed to `uv_bootstrap.log`.
fn ensure_venv(app: &AppHandle, state: &SidecarState) -> bool {
    if venv_is_ready(app) {
        return true; // fully bootstrapped (python + sentinel) — reused later (B3)
    }
    if state.shutting_down.load(Ordering::SeqCst) {
        return false;
    }
    // A partial venv (python.exe present, sentinel missing) is an interrupted
    // prior install — re-run `uv sync` over it rather than booting a torch-less
    // env. `uv` is idempotent, so re-running `uv venv` over an existing one is safe.
    let project = sidecar_dir();
    let venv = venv_dir(app);

    if run_uv_venv(app, state, &project, &venv) == UvStep::ShutdownRequested {
        return false;
    }
    match run_uv_sync(app, state, &project, &venv) {
        UvStep::ShutdownRequested => false,
        UvStep::Done { synced } => {
            if synced {
                // Mark the bootstrap complete so the next launch can fast-path it.
                let _ = std::fs::write(sync_sentinel(app), b"ok");
            }
            // A failed/partial sync left no sentinel; the spawn/health path then
            // accounts a missing python as a normal failure (backoff + retry).
            true
        }
    }
}

/// Result of one bootstrap `uv` step. `Done { synced }` reports whether the step
/// finished with a clean (zero) exit — used to gate the success sentinel (B3).
#[derive(PartialEq, Eq)]
enum UvStep {
    ShutdownRequested,
    Done { synced: bool },
}

/// Run `uv venv` (spawned + polled, output to `uv_bootstrap.log`). `uv venv` can
/// block on a one-time managed-CPython download (no system Python), so it is
/// polled too — a quit must not hang on it.
fn run_uv_venv(app: &AppHandle, state: &SidecarState, project: &PathBuf, venv: &PathBuf) -> UvStep {
    state.set_stage(app, "creating_venv");
    let spawned = no_window(
        Command::new("uv")
            .arg("venv")
            .current_dir(project)
            .env("UV_PROJECT_ENVIRONMENT", venv),
    )
    .stdout(uv_log_stdio(app))
    .stderr(uv_log_stdio(app))
    .spawn();
    let Ok(mut child) = spawned else {
        state.log(app, "[supervisor] failed to launch `uv venv`");
        return UvStep::Done { synced: false };
    };
    let outcome = wait_uv_or_shutdown(&mut child, state);
    if outcome.is_shutdown() {
        return UvStep::ShutdownRequested;
    }
    UvStep::Done { synced: outcome.succeeded() }
}

/// Run `uv sync` (spawned + polled, output to `uv_bootstrap.log`). `--no-dev`
/// keeps pytest/httpx out of the runtime venv; `--extra engine` pulls the model
/// lib + ML stack; `--extra {cpu|cu124}` picks the torch wheel variant from the
/// NVIDIA probe so GPU boxes get CUDA and everyone else the CPU wheel
/// (device-detection.md / packaging Rule 4 + 6). This is the multi-minute step.
fn run_uv_sync(app: &AppHandle, state: &SidecarState, project: &PathBuf, venv: &PathBuf) -> UvStep {
    state.set_stage(app, "installing_deps");
    let torch_extra = if has_nvidia_gpu(state) { "cu124" } else { "cpu" };
    state.log(app, &format!("[supervisor] engine deps: torch variant = {torch_extra}"));
    let spawned = no_window(
        Command::new("uv")
            .args(["sync", "--no-dev", "--extra", "engine", "--extra", torch_extra])
            .current_dir(project)
            .env("UV_PROJECT_ENVIRONMENT", venv),
    )
    .stdout(uv_log_stdio(app))
    .stderr(uv_log_stdio(app))
    .spawn();
    let Ok(mut child) = spawned else {
        state.log(app, "[supervisor] failed to launch `uv sync`");
        return UvStep::Done { synced: false };
    };
    let outcome = wait_uv_or_shutdown(&mut child, state);
    if matches!(&outcome, UvOutcome::Exited(Some(s)) if !s.success()) {
        state.log(app, "[supervisor] `uv sync` exited non-zero; see uv_bootstrap.log");
    }
    if outcome.is_shutdown() {
        return UvStep::ShutdownRequested;
    }
    UvStep::Done { synced: outcome.succeeded() }
}

/// Bound on how long the `nvidia-smi` GPU probe may run before we treat it as
/// "no GPU". A hung/half-installed driver can make `nvidia-smi` block
/// uninterruptibly, and `ensure_venv` must never block app-quit — so we poll a
/// spawned child against this deadline (+ the shutdown flag) instead of a
/// blocking `.status()`.
const GPU_PROBE_TIMEOUT: Duration = Duration::from_secs(5);

/// Whether an NVIDIA GPU + driver is present, deciding which torch wheel the
/// first-run venv installs (CUDA vs CPU). `nvidia-smi` ships with the NVIDIA
/// driver, so a clean `-L` (list GPUs) exit means CUDA wheels are worth pulling.
/// Any failure (binary absent, no driver, no device), a spawn error, OR a probe
/// that overruns `GPU_PROBE_TIMEOUT` / is interrupted by shutdown → CPU-only.
/// This only steers the wheel SET — `core/device.py` still does the authoritative
/// `torch.cuda` probe at runtime, so a false negative degrades to CPU rather than
/// breaking.
///
/// Spawned + polled (never a blocking `.status()`) so a hung driver can't block
/// app-quit: it honors `state.shutting_down` and a short bounded deadline, the
/// same pattern as `wait_uv_or_shutdown`.
fn has_nvidia_gpu(state: &SidecarState) -> bool {
    let spawned = no_window(Command::new("nvidia-smi").arg("-L"))
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn();
    let Ok(mut child) = spawned else {
        return false; // binary absent / spawn failed → no GPU (CPU wheel)
    };
    let deadline = Instant::now() + GPU_PROBE_TIMEOUT;
    loop {
        if state.shutting_down.load(Ordering::SeqCst) || Instant::now() >= deadline {
            let _ = child.kill();
            let _ = child.wait();
            return false; // quit or hung driver → degrade to CPU
        }
        match child.try_wait() {
            Ok(Some(status)) => return status.success(),
            Err(_) => return false, // poll error → no GPU
            Ok(None) => std::thread::sleep(Duration::from_millis(100)),
        }
    }
}

fn spawn_sidecar(app: &AppHandle, port: u16) -> std::io::Result<Child> {
    let logs = log_dir(app);
    let _ = std::fs::create_dir_all(&logs);
    let stdout = File::create(logs.join("backend.log")).map(Stdio::from).unwrap_or_else(|_| Stdio::null());
    let stderr = File::create(logs.join("backend_err.log")).map(Stdio::from).unwrap_or_else(|_| Stdio::null());

    // Spawn the venv's python DIRECTLY (not `uv run …`) so the immediate child is
    // python.exe — `child.kill()` then reaps the engine, not just a uv launcher
    // that would leave python orphaned on the port (architecture §3.3).
    no_window(
        Command::new(venv_python(app))
            .arg("main.py")
            .current_dir(sidecar_dir())
            .env("PARROT_PORT", port.to_string())
            .env("PARROT_DATA_DIR", data_dir(app)),
    )
    .stdout(stdout)
    .stderr(stderr)
    .spawn()
}

/// Sleep for `dur`, but wake early (returning `true`) if shutdown is requested.
/// Returns `true` iff shutdown was requested by the time it returns.
fn sleep_unless_shutdown(shutting_down: &AtomicBool, dur: Duration) -> bool {
    let step = Duration::from_millis(100);
    let mut slept = Duration::ZERO;
    while slept < dur {
        if shutting_down.load(Ordering::SeqCst) {
            return true;
        }
        let chunk = step.min(dur - slept);
        std::thread::sleep(chunk);
        slept += chunk;
    }
    shutting_down.load(Ordering::SeqCst)
}

/// The supervise loop's mutable bookkeeping, bundled so the loop body can be
/// split into focused helpers without a long argument list. (Pure refactor of
/// what were free `let mut` locals in `start`; semantics unchanged.)
struct MonitorVars {
    healthy: bool,
    had_child: bool,
    attached: bool,
    failures: u32,
    /// When the current child was spawned (None once it's healthy/attached).
    /// Drives the startup deadline: a child that binds but never serves health is
    /// killed + counted so a hung-but-alive sidecar still trips the cap.
    started_at: Option<Instant>,
}

/// Control-flow signal from a monitor-loop helper back to the loop body — the
/// helper-friendly stand-in for a bare `break`/`continue`/fall-through.
enum Flow {
    /// Leave the supervise loop (shutdown, or a freshly spawned child reaped after
    /// a quit raced the spawn).
    Break,
    /// Restart the loop body from the top.
    Continue,
    /// Fall through to the rest of the loop body (health re-check + poll sleep).
    Next,
}

/// Start the supervisor on a background thread: attach-or-spawn → health-check →
/// keep alive (restart on crash, with backoff) until the app shuts down. On
/// repeated never-healthy starts it parks in `failed` until a Retry command.
pub fn start(app: AppHandle, port: u16) {
    // Clone for post-spawn state access; the original `app` moves into the thread.
    let app_for_handle = app.clone();
    let handle = std::thread::spawn(move || run_monitor(app, port));

    // Keep the monitor handle so shutdown() can join it (bounded) — this is what
    // guarantees no spawn can race teardown after exit returns.
    if let Some(state) = app_for_handle.try_state::<SidecarState>() {
        *lock(&state.monitor) = Some(handle);
    }
}

/// The supervise loop itself (runs on the monitor thread). Each iteration:
/// attach-or-detect-exit → (respawn on exit) → mark-ready-on-health → poll sleep.
/// The per-iteration work lives in focused helpers; this body is just the
/// sequencing + the `Flow` dispatch.
fn run_monitor(app: AppHandle, port: u16) {
    let state = app.state::<SidecarState>();
    let mut vars = MonitorVars {
        healthy: false,
        had_child: false,
        attached: false,
        failures: 0,
        started_at: None,
    };

    state.set_stage(&app, "checking");

    while !state.shutting_down.load(Ordering::SeqCst) {
        try_attach(&app, &state, port, &mut vars);

        if detect_exit(&app, &state, &mut vars) {
            match handle_exit_and_respawn(&app, &state, port, &mut vars) {
                Flow::Break => break,
                Flow::Continue => continue,
                Flow::Next => {}
            }
        }

        mark_ready_if_healthy(&app, &state, port, &mut vars);

        if sleep_unless_shutdown(&state.shutting_down, POLL_INTERVAL) {
            break;
        }
    }

    reap_child_on_shutdown(&state);
}

/// Attach to an already-healthy Parrot sidecar instead of spawning a duplicate
/// (dev `bun run dev` against a manual backend; a surviving sidecar after a
/// relaunch), and drop the attachment if that external sidecar later dies.
fn try_attach(app: &AppHandle, state: &SidecarState, port: u16, vars: &mut MonitorVars) {
    if !vars.attached && !vars.had_child && health_ok(port) {
        vars.attached = true;
        vars.healthy = true;
        state.ready.store(true, Ordering::SeqCst);
        state.set_stage(app, "ready");
        let _ = app.emit("sidecar-ready", port);
    }
    // An attached external sidecar that died → reclaim and spawn our own.
    if vars.attached && !health_ok(port) {
        vars.attached = false;
        vars.healthy = false;
        state.ready.store(false, Ordering::SeqCst);
    }
}

/// Decide whether the current (non-attached) child has exited. A spawned-but-not-
/// yet-healthy child that overran the startup deadline is killed here so it counts
/// as a never-healthy failure (otherwise `try_wait` would keep returning `None`
/// forever for a hung process).
fn detect_exit(app: &AppHandle, state: &SidecarState, vars: &mut MonitorVars) -> bool {
    let deadline_blown = !vars.attached
        && !vars.healthy
        && vars
            .started_at
            .is_some_and(|t| t.elapsed() >= Duration::from_secs(STARTUP_DEADLINE_SECS));
    if deadline_blown {
        state.log(app, "[supervisor] sidecar never became healthy within startup deadline; killing");
        if let Some(mut child) = lock(&state.child).take() {
            let _ = child.kill();
            let _ = child.wait(); // release the port before respawning
        }
    }

    if vars.attached {
        return false;
    }
    if deadline_blown {
        return true;
    }
    let mut guard = lock(&state.child);
    match guard.as_mut() {
        Some(child) => matches!(child.try_wait(), Ok(Some(_))),
        None => true,
    }
}

/// Handle a detected child exit: account the failure, give up (park) at the cap,
/// otherwise back off, reclaim the port, ensure the venv, and respawn. Returns a
/// `Flow` telling the loop body to break, restart, or fall through.
fn handle_exit_and_respawn(
    app: &AppHandle,
    state: &SidecarState,
    port: u16,
    vars: &mut MonitorVars,
) -> Flow {
    vars.started_at = None;
    if vars.had_child {
        vars.failures = failures_after_exit(vars.failures, vars.healthy);
    }

    if vars.failures >= MAX_RAPID_FAILURES {
        return give_up_until_retry(app, state, vars);
    }

    let wait = backoff_secs(vars.failures);
    if wait > 0 {
        eprintln!("[supervisor] restarting sidecar in {wait}s (failure #{})", vars.failures);
        if sleep_unless_shutdown(&state.shutting_down, Duration::from_secs(wait)) {
            return Flow::Break;
        }
    }

    // Reclaim the port from a stale Parrot process before spawning, then wait
    // (bounded) for the port to actually free up rather than sleeping a fixed
    // interval and racing a slow-to-release socket.
    if port_in_use(port) && !health_ok(port) {
        state.log(app, "[supervisor] port in use; reclaiming");
        kill_orphan_on_port(app, state, port);
        if !wait_for_port_free(&state.shutting_down, port) {
            return Flow::Break; // shutdown requested while waiting
        }
    }

    // The bootstrap (uv sync) can take minutes; abort cleanly if the user quit
    // during it (also covers a quit during the port-wait above).
    if !ensure_venv(app, state) || state.shutting_down.load(Ordering::SeqCst) {
        return Flow::Break;
    }

    state.set_stage(app, "starting_backend");
    state.ready.store(false, Ordering::SeqCst);
    vars.healthy = false;
    respawn_sidecar(app, state, port, vars)
}

/// Latch the terminal `failed` state, emit `sidecar-failed`, and park until a
/// Retry/Clean command (or shutdown). On retry, reset the counters and restart
/// the loop; on shutdown, break out.
fn give_up_until_retry(app: &AppHandle, state: &SidecarState, vars: &mut MonitorVars) -> Flow {
    eprintln!("[supervisor] sidecar failed {}× to start; giving up", vars.failures);
    state.failed.store(true, Ordering::SeqCst);
    state.set_stage(app, "failed");
    let _ = app.emit("sidecar-failed", vars.failures);

    if !park_until_retry(app, state) {
        return Flow::Break;
    }
    vars.failures = 0;
    vars.had_child = false;
    Flow::Continue
}

/// Spawn the sidecar and store it under the child lock, re-checking `shutting_down`
/// atomically to close the spawn-after-shutdown race. A spawn error is accounted as
/// a failure and the loop restarts; a child reaped because a quit raced the spawn
/// breaks the loop.
fn respawn_sidecar(app: &AppHandle, state: &SidecarState, port: u16, vars: &mut MonitorVars) -> Flow {
    let child = match spawn_sidecar(app, port) {
        Ok(child) => child,
        Err(e) => {
            eprintln!("[supervisor] failed to spawn sidecar: {e}");
            state.log(app, &format!("[supervisor] spawn failed: {e}"));
            vars.had_child = false;
            vars.failures += 1;
            return Flow::Continue;
        }
    };

    // Store the child and re-check `shutting_down` ATOMICALLY under the child lock:
    // `shutdown()` sets the flag before taking the child, so checking it while
    // holding the lock closes the spawn-after-shutdown race. The block hands back
    // the child only when it was NOT stored, so we can reap it (never leave a
    // freshly spawned child orphaned).
    let unstored = {
        let mut guard = lock(&state.child);
        if state.shutting_down.load(Ordering::SeqCst) {
            Some(child)
        } else {
            *guard = Some(child);
            None
        }
    };
    if let Some(mut orphan) = unstored {
        let _ = orphan.kill();
        let _ = orphan.wait();
        return Flow::Break;
    }
    vars.had_child = true;
    vars.started_at = Some(Instant::now());
    eprintln!("[supervisor] spawned sidecar on :{port}");
    Flow::Next
}

/// Promote a freshly-spawned child to healthy once `/healthz` answers, clearing
/// the startup-deadline clock and emitting `sidecar-ready`.
fn mark_ready_if_healthy(app: &AppHandle, state: &SidecarState, port: u16, vars: &mut MonitorVars) {
    if vars.healthy || !health_ok(port) {
        return;
    }
    vars.healthy = true;
    vars.started_at = None; // cleared the startup-deadline clock
    state.ready.store(true, Ordering::SeqCst);
    state.set_stage(app, "ready");
    let _ = app.emit("sidecar-ready", port);
    eprintln!("[supervisor] sidecar healthy on :{port}");
}

/// Shutting down — kill the child and BLOCK on its exit so the loopback port is
/// released before the monitor thread (and the app) exits. Take it out first so
/// the guard drops before `state`. An attached external sidecar is left running on
/// purpose (we didn't spawn it).
fn reap_child_on_shutdown(state: &SidecarState) {
    let last = lock(&state.child).take();
    if let Some(mut child) = last {
        let _ = child.kill();
        let _ = child.wait();
        eprintln!("[supervisor] sidecar terminated");
    }
}

/// Park in the `failed` state until a Retry/Clean command or shutdown. Returns
/// `true` to resume (retry requested), `false` to stop (shutting down).
fn park_until_retry(app: &AppHandle, state: &SidecarState) -> bool {
    loop {
        if state.shutting_down.load(Ordering::SeqCst) {
            return false;
        }
        if state.retry_requested.swap(false, Ordering::SeqCst) {
            if state.clean_requested.swap(false, Ordering::SeqCst) {
                // Clean & Retry: wipe the bootstrapped venv (under the data dir,
                // NOT the read-only sidecar bundle) + kill any stale sidecar.
                let _ = std::fs::remove_dir_all(venv_dir(app));
                if port_in_use(state.port) && !health_ok(state.port) {
                    kill_orphan_on_port(app, state, state.port);
                }
            }
            state.failed.store(false, Ordering::SeqCst);
            state.set_stage(app, "checking");
            return true;
        }
        std::thread::sleep(Duration::from_millis(300));
    }
}

/// Bound on how long `shutdown` waits for the monitor thread to wind down.
const SHUTDOWN_JOIN_TIMEOUT: Duration = Duration::from_secs(10);

/// Stop supervising and kill the sidecar. Called on app exit. Kills+waits the
/// child here (so the port is released before exit returns) and joins the
/// monitor thread (bounded) so no respawn can race teardown.
pub fn shutdown(app: &AppHandle) {
    let Some(state) = app.try_state::<SidecarState>() else { return };
    state.shutting_down.store(true, Ordering::SeqCst);

    // Reap the child ourselves and block on its exit — releases the loopback
    // port before the process tree tears down (architecture §3.3).
    if let Some(mut child) = lock(&state.child).take() {
        let _ = child.kill();
        let _ = child.wait();
    }

    // Join the monitor (bounded): it wakes on `shutting_down` within a poll tick.
    // Take the handle out first so the lock isn't held across the join.
    let handle = lock(&state.monitor).take();
    if let Some(handle) = handle {
        let deadline = Instant::now() + SHUTDOWN_JOIN_TIMEOUT;
        while !handle.is_finished() && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(50));
        }
        if handle.is_finished() {
            let _ = handle.join(); // returns immediately; only joins once finished
        }
        // If it somehow didn't finish in time we drop the handle (detach) rather
        // than block app exit indefinitely — the child is already reaped above.
    }
}

// --- command backings (wrapped by #[tauri::command] fns in lib.rs) -----------

pub fn bootstrap_stage(state: &SidecarState) -> String {
    lock(&state.stage).clone()
}

pub fn bootstrap_logs(state: &SidecarState) -> Vec<String> {
    lock(&state.logs).clone()
}

pub fn request_retry(state: &SidecarState, clean: bool) {
    if clean {
        state.clean_requested.store(true, Ordering::SeqCst);
    }
    state.retry_requested.store(true, Ordering::SeqCst);
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;

    #[test]
    fn backoff_is_exponential_then_capped() {
        assert_eq!(backoff_secs(0), 0);
        assert_eq!(backoff_secs(1), 1);
        assert_eq!(backoff_secs(2), 2);
        assert_eq!(backoff_secs(3), 4);
        assert_eq!(backoff_secs(4), 8);
        assert_eq!(backoff_secs(5), 16);
        assert_eq!(backoff_secs(6), BACKOFF_CAP_SECS); // 32 would exceed the cap
        assert_eq!(backoff_secs(100), BACKOFF_CAP_SECS);
    }

    #[test]
    fn give_up_threshold_is_reachable_via_backoff() {
        for n in 1..MAX_RAPID_FAILURES {
            assert!(backoff_secs(n) > 0, "failure #{n} must back off");
        }
    }

    #[test]
    fn failure_accounting_resets_only_on_a_healthy_run() {
        assert_eq!(failures_after_exit(4, true), 0);
        assert_eq!(failures_after_exit(0, false), 1);
        assert_eq!(failures_after_exit(3, false), 4);
    }

    #[test]
    fn never_healthy_deaths_reach_the_give_up_cap() {
        let mut failures = 0;
        for _ in 0..MAX_RAPID_FAILURES {
            failures = failures_after_exit(failures, false);
        }
        assert!(failures >= MAX_RAPID_FAILURES);
    }

    /// Build an `ExitStatus` with the given raw code, cross-platform, so the
    /// sentinel-gating logic can be unit-tested without spawning a real process.
    fn exit_status(code: i32) -> std::process::ExitStatus {
        #[cfg(windows)]
        {
            use std::os::windows::process::ExitStatusExt;
            std::process::ExitStatus::from_raw(code as u32)
        }
        #[cfg(unix)]
        {
            use std::os::unix::process::ExitStatusExt;
            std::process::ExitStatus::from_raw(code << 8) // wait-status: code in high byte
        }
    }

    #[test]
    fn uv_outcome_succeeds_only_on_a_clean_exit() {
        // Only a zero exit gates the success sentinel (B3): a non-zero exit, a
        // poll error (no status), and a shutdown are all NOT success — so a
        // partial/interrupted `uv sync` never writes the "bootstrap complete"
        // marker that the next launch fast-paths on.
        assert!(UvOutcome::Exited(Some(exit_status(0))).succeeded());
        assert!(!UvOutcome::Exited(Some(exit_status(1))).succeeded());
        assert!(!UvOutcome::Exited(None).succeeded());
        assert!(!UvOutcome::ShutdownRequested.succeeded());
    }

    #[test]
    fn uv_outcome_is_shutdown_only_when_quit_requested() {
        assert!(UvOutcome::ShutdownRequested.is_shutdown());
        assert!(!UvOutcome::Exited(Some(exit_status(0))).is_shutdown());
        assert!(!UvOutcome::Exited(None).is_shutdown());
    }

    #[test]
    fn resolve_port_default_and_overrides() {
        let prev = std::env::var("PARROT_PORT").ok();

        std::env::remove_var("PARROT_PORT");
        assert_eq!(resolve_port(), 3900);

        std::env::set_var("PARROT_PORT", "4123");
        assert_eq!(resolve_port(), 4123);

        std::env::set_var("PARROT_PORT", "not-a-port");
        assert_eq!(resolve_port(), 3900, "unparseable port falls back to default");

        std::env::set_var("PARROT_PORT", "70000");
        assert_eq!(resolve_port(), 3900, "out-of-range port falls back to default");

        match prev {
            Some(v) => std::env::set_var("PARROT_PORT", v),
            None => std::env::remove_var("PARROT_PORT"),
        }
    }

    #[test]
    fn sidecar_dir_honors_override() {
        let prev = std::env::var("PARROT_SIDECAR_DIR").ok();

        std::env::set_var("PARROT_SIDECAR_DIR", "/tmp/parrot-custom-sidecar");
        assert_eq!(sidecar_dir(), PathBuf::from("/tmp/parrot-custom-sidecar"));

        match prev {
            Some(v) => std::env::set_var("PARROT_SIDECAR_DIR", v),
            None => std::env::remove_var("PARROT_SIDECAR_DIR"),
        }
    }

    #[test]
    fn sleep_unless_shutdown_returns_immediately_when_flag_set() {
        let flag = AtomicBool::new(true);
        assert!(sleep_unless_shutdown(&flag, Duration::from_secs(60)));
    }

    #[test]
    fn lock_recovers_from_a_poisoned_mutex() {
        let m = Arc::new(Mutex::new(7));
        let m2 = Arc::clone(&m);
        let _ = std::thread::spawn(move || {
            let _g = m2.lock().unwrap();
            panic!("intentional poison");
        })
        .join();
        assert_eq!(*lock(&m), 7);
    }

    #[test]
    fn retry_request_sets_flags() {
        let state = SidecarState::new(3900);
        request_retry(&state, true);
        assert!(state.retry_requested.load(Ordering::SeqCst));
        assert!(state.clean_requested.load(Ordering::SeqCst));
    }

    #[test]
    fn ok_health_body_accepts_both_spacings() {
        // The sidecar serializes compact JSON, but JSON parsing is
        // whitespace-insensitive so a pretty-printer's space after the colon (a
        // proxy/middleware reformat) still reads as our own healthy envelope.
        assert!(is_ok_health_body(r#"{"status":"ok"}"#));
        assert!(is_ok_health_body(r#"{"status": "ok"}"#));
        // Realistic body with extra fields around the marker.
        assert!(is_ok_health_body(r#"{"status":"ok","version":"0.1.0","port":3900}"#));
    }

    #[test]
    fn ok_health_body_rejects_foreign_and_malformed() {
        // A different app that 200s on the port (the squatter case).
        assert!(!is_ok_health_body(r#"{"status":"error"}"#));
        assert!(!is_ok_health_body("<html><body>Some other server</body></html>"));
        // Empty / junk payloads must not pass.
        assert!(!is_ok_health_body(""));
        assert!(!is_ok_health_body("ok"));
        assert!(!is_ok_health_body("not json at all"));
        // Looks similar but isn't the ok marker.
        assert!(!is_ok_health_body(r#"{"status":"okay"}"#));
    }

    #[test]
    fn ok_health_body_rejects_substring_match_not_top_level() {
        // A foreign 200 whose body merely CONTAINS the marker (inside another
        // field, or as a nested value) must NOT be mis-attached as the sidecar —
        // only a TOP-LEVEL `status == "ok"` counts. This is the regression the
        // JSON parse fixes vs. the prior raw substring match.
        assert!(!is_ok_health_body(r#"{"message":"the status:ok page is here"}"#));
        assert!(!is_ok_health_body(r#"{"status":"degraded","detail":{"status":"ok"}}"#));
        assert!(!is_ok_health_body(r#"{"health":{"status":"ok"}}"#));
        // Top-level `status` of the wrong JSON type is not the ok string.
        assert!(!is_ok_health_body(r#"{"status":true}"#));
        assert!(!is_ok_health_body(r#"{"status":1}"#));
    }
}
