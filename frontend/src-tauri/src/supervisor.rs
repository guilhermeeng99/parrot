//! Sidecar supervisor — the Rust shell's most important job.
//!
//! Spawns the Python FastAPI sidecar, health-checks it on `/healthz`, restarts
//! it if it crashes (with backoff + a give-up cap), and kills it when the app
//! exits. It also: attaches to an already-healthy sidecar instead of spawning a
//! duplicate; takes ownership of the port from a non-Parrot squatter; pipes the
//! sidecar's stdout/stderr to rotating-ish log files; and surfaces boot stages to
//! the UI (`bootstrap-stage` / `bootstrap-log`). This is the only place that owns
//! the sidecar process. The first-run venv/uv bootstrap concern lives in its
//! sibling `bootstrap.rs`. See docs/specs/architecture.md §3 and first-run-setup.md.

use std::net::TcpStream;
use std::process::{Child, Command};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Mutex, MutexGuard};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

#[cfg(windows)]
use std::path::PathBuf;

use tauri::{AppHandle, Emitter, Manager};

// The venv/uv/path bootstrap concern. `data_dir`/`log_dir` are re-exported so
// `native.rs` (and existing call sites) keep using `supervisor::data_dir`.
pub use crate::bootstrap::{data_dir, log_dir};
use crate::bootstrap::{
    ensure_venv, log_file_stdio, no_window, sidecar_dir, venv_dir, venv_python,
};

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

/// Shared state for the supervised sidecar. Managed by Tauri so commands and
/// the run-loop can reach it.
pub struct SidecarState {
    /// The running child, if any. Held in a Mutex so the monitor thread can
    /// poll it and the exit handler can kill it.
    child: Mutex<Option<Child>>,
    /// Set on app exit so the monitor stops restarting the sidecar. `pub(crate)`
    /// so the bootstrap helpers can abort a multi-minute `uv` step on quit.
    pub(crate) shutting_down: AtomicBool,
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

    /// Set the boot stage + emit it to the splash. `pub(crate)` so the bootstrap
    /// helpers (creating_venv / installing_deps) can drive it.
    pub(crate) fn set_stage(&self, app: &AppHandle, stage: &str) {
        *lock(&self.stage) = stage.to_string();
        let _ = app.emit("bootstrap-stage", stage);
        self.log(app, &format!("[stage] {stage}"));
    }

    /// Append a boot-log line (bounded) + emit it. `pub(crate)` so the bootstrap
    /// helpers can surface uv launch failures.
    pub(crate) fn log(&self, app: &AppHandle, line: &str) {
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
        .and_then(|s| s.parse::<u16>().ok())
        .filter(|port| *port != 0)
        .unwrap_or(3900)
}

/// Does a `/healthz` body identify a real Parrot sidecar? A foreign server
/// squatting on the port might 200 with junk — or merely 200 with a body that
/// happens to CONTAIN the `"status":"ok"` substring (e.g. inside some other
/// field). Parse as JSON and assert the TOP-LEVEL `status` field equals `ok`, so
/// only a real Parrot health envelope counts. Pure (no I/O) so it is unit-testable
/// without a server.
fn is_ok_health_body(body: &str) -> bool {
    match serde_json::from_str::<serde_json::Value>(body).ok() {
        Some(serde_json::Value::Object(map)) if map.len() == 1 => {
            matches!(map.get("status"), Some(serde_json::Value::String(status)) if status == "ok")
        }
        _ => false,
    }
}

fn health_ok(port: u16) -> bool {
    let url = format!("http://127.0.0.1:{port}/healthz");
    match ureq::get(&url).timeout(Duration::from_secs(2)).call() {
        Ok(resp) => {
            if resp.status() != 200 {
                return false;
            }
            resp.into_string()
                .map(|b| is_ok_health_body(&b))
                .unwrap_or(false)
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
        let markers = parrot_process_markers(app);
        for line in text.lines() {
            if !(line.contains(&needle) && line.to_uppercase().contains("LISTENING")) {
                continue;
            }
            let Some(pid) = line.split_whitespace().last() else {
                continue;
            };
            match pid_process_info(pid) {
                Some(info) if is_parrot_owned_process(&info, &markers) => {
                    state.log(
                        app,
                        &format!(
                            "[supervisor] killing port squatter pid={pid} ({})",
                            info.image
                        ),
                    );
                    let _ = no_window(Command::new("taskkill").args(["/F", "/PID", pid])).output();
                }
                other => {
                    let label = other
                        .map(|info| info.image)
                        .unwrap_or_else(|| "<unknown or unverified>".into());
                    state.log(
                        app,
                        &format!("[supervisor] skipping non-Parrot port owner pid={pid} ({label})"),
                    );
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
    let out = no_window(Command::new("tasklist").args([
        "/FI",
        &format!("PID eq {pid}"),
        "/FO",
        "CSV",
        "/NH",
    ]))
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

#[cfg(windows)]
#[derive(Debug, Clone)]
struct ProcessInfo {
    image: String,
    metadata: String,
}

/// Resolve a PID to both image and command/executable metadata. The image gate is
/// not enough: users can have unrelated Python services on the same dev port.
#[cfg(windows)]
fn pid_process_info(pid: &str) -> Option<ProcessInfo> {
    let image = pid_image(pid)?;
    let metadata = pid_process_metadata(pid).unwrap_or_default();
    Some(ProcessInfo { image, metadata })
}

#[cfg(windows)]
fn pid_process_metadata(pid: &str) -> Option<String> {
    if !pid.chars().all(|c| c.is_ascii_digit()) {
        return None;
    }
    let script = format!(
        "$p = Get-CimInstance Win32_Process -Filter 'ProcessId = {pid}'; if ($p) {{ $p.ExecutablePath; $p.CommandLine }}"
    );
    let out = no_window(Command::new("powershell").args([
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        &script,
    ]))
    .output()
    .ok()?;
    if !out.status.success() {
        return None;
    }
    let text = String::from_utf8_lossy(&out.stdout).trim().to_string();
    if text.is_empty() {
        None
    } else {
        Some(text)
    }
}

#[cfg(windows)]
fn parrot_process_markers(app: &AppHandle) -> Vec<String> {
    [sidecar_dir(app), venv_dir(app), data_dir(app)]
        .into_iter()
        .map(normalize_path_marker)
        .collect()
}

#[cfg(windows)]
fn normalize_path_marker(path: PathBuf) -> String {
    normalize_process_text(&path.to_string_lossy())
}

#[cfg(windows)]
fn normalize_process_text(text: &str) -> String {
    text.replace('/', "\\").to_ascii_lowercase()
}

/// Is this process one Parrot is allowed to force-kill on the port? It must be a
/// plausible sidecar image AND its command/executable metadata must include one
/// of Parrot's own paths.
#[cfg(windows)]
fn is_parrot_owned_process(info: &ProcessInfo, markers: &[String]) -> bool {
    if !is_parrot_owned_image(&info.image) {
        return false;
    }
    let metadata = normalize_process_text(&info.metadata);
    !metadata.is_empty() && markers.iter().any(|marker| metadata.contains(marker))
}

/// Is this process image one Parrot is allowed to force-kill on the port — the
/// bootstrapped sidecar (`python.exe`) or its `uv` launcher?
#[cfg(windows)]
fn is_parrot_owned_image(image: &str) -> bool {
    let lower = image.to_ascii_lowercase();
    lower == "python.exe" || lower == "uv.exe" || lower == "pythonw.exe"
}

fn spawn_sidecar(app: &AppHandle, port: u16) -> std::io::Result<Child> {
    let logs = log_dir(app);
    let _ = std::fs::create_dir_all(&logs);
    let stdout = log_file_stdio(logs.join("backend.log"), false);
    let stderr = log_file_stdio(logs.join("backend_err.log"), false);

    // Spawn the venv's python DIRECTLY (not `uv run …`) so the immediate child is
    // python.exe — `child.kill()` then reaps the engine, not just a uv launcher
    // that would leave python orphaned on the port (architecture §3.3).
    no_window(
        Command::new(venv_python(app))
            .arg("main.py")
            .current_dir(sidecar_dir(app))
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
        state.log(
            app,
            "[supervisor] sidecar never became healthy within startup deadline; killing",
        );
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
        eprintln!(
            "[supervisor] restarting sidecar in {wait}s (failure #{})",
            vars.failures
        );
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
    eprintln!(
        "[supervisor] sidecar failed {}× to start; giving up",
        vars.failures
    );
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
fn respawn_sidecar(
    app: &AppHandle,
    state: &SidecarState,
    port: u16,
    vars: &mut MonitorVars,
) -> Flow {
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
    let Some(state) = app.try_state::<SidecarState>() else {
        return;
    };
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

    #[test]
    fn resolve_port_default_and_overrides() {
        let prev = std::env::var("PARROT_PORT").ok();

        std::env::remove_var("PARROT_PORT");
        assert_eq!(resolve_port(), 3900);

        std::env::set_var("PARROT_PORT", "4123");
        assert_eq!(resolve_port(), 4123);

        std::env::set_var("PARROT_PORT", "not-a-port");
        assert_eq!(
            resolve_port(),
            3900,
            "unparseable port falls back to default"
        );

        std::env::set_var("PARROT_PORT", "70000");
        assert_eq!(
            resolve_port(),
            3900,
            "out-of-range port falls back to default"
        );

        std::env::set_var("PARROT_PORT", "0");
        assert_eq!(resolve_port(), 3900, "zero is not a bindable app port");

        match prev {
            Some(v) => std::env::set_var("PARROT_PORT", v),
            None => std::env::remove_var("PARROT_PORT"),
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

    #[cfg(windows)]
    #[test]
    fn only_python_and_uv_images_are_force_killable() {
        // The orphan-on-port killer force-kills ONLY Parrot's own sidecar/launcher
        // images — never some unrelated app that merely grabbed the port. This is
        // the safety gate behind `taskkill /F`, so it gets its own regression test.
        assert!(is_parrot_owned_image("python.exe"));
        assert!(is_parrot_owned_image("Python.exe")); // case-insensitive
        assert!(is_parrot_owned_image("PYTHONW.EXE"));
        assert!(is_parrot_owned_image("uv.exe"));
        assert!(!is_parrot_owned_image("chrome.exe"));
        assert!(!is_parrot_owned_image("node.exe"));
        assert!(!is_parrot_owned_image("")); // unknown/blank is never ours
    }

    #[cfg(windows)]
    #[test]
    fn force_kill_requires_parrot_process_metadata() {
        let markers = vec![normalize_process_text(
            r"C:\Users\me\AppData\Roaming\com.parrot.app\parrot_data",
        )];
        let parrot = ProcessInfo {
            image: "python.exe".into(),
            metadata: r#""C:\Users\me\AppData\Roaming\com.parrot.app\parrot_data\venv\Scripts\python.exe" main.py"#.into(),
        };
        let unrelated_python = ProcessInfo {
            image: "python.exe".into(),
            metadata: r#""C:\work\other\venv\Scripts\python.exe" -m uvicorn app:app --port 3900"#
                .into(),
        };
        let parrot_node = ProcessInfo {
            image: "node.exe".into(),
            metadata: parrot.metadata.clone(),
        };

        assert!(is_parrot_owned_process(&parrot, &markers));
        assert!(!is_parrot_owned_process(&unrelated_python, &markers));
        assert!(!is_parrot_owned_process(&parrot_node, &markers));
    }

    #[test]
    fn ok_health_body_accepts_both_spacings() {
        // The sidecar serializes compact JSON, but JSON parsing is
        // whitespace-insensitive so a pretty-printer's space after the colon (a
        // proxy/middleware reformat) still reads as our own healthy envelope.
        assert!(is_ok_health_body(r#"{"status":"ok"}"#));
        assert!(is_ok_health_body(r#"{"status": "ok"}"#));
        assert!(!is_ok_health_body(
            r#"{"status":"ok","version":"0.1.0","port":3900}"#
        ));
    }

    #[test]
    fn ok_health_body_rejects_foreign_and_malformed() {
        // A different app that 200s on the port (the squatter case).
        assert!(!is_ok_health_body(r#"{"status":"error"}"#));
        assert!(!is_ok_health_body(
            "<html><body>Some other server</body></html>"
        ));
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
        assert!(!is_ok_health_body(
            r#"{"message":"the status:ok page is here"}"#
        ));
        assert!(!is_ok_health_body(
            r#"{"status":"degraded","detail":{"status":"ok"}}"#
        ));
        assert!(!is_ok_health_body(r#"{"health":{"status":"ok"}}"#));
        // Top-level `status` of the wrong JSON type is not the ok string.
        assert!(!is_ok_health_body(r#"{"status":true}"#));
        assert!(!is_ok_health_body(r#"{"status":1}"#));
    }
}
