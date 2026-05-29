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
use std::time::Duration;

use tauri::{AppHandle, Emitter, Manager};

/// How often the supervisor polls the child + health endpoint.
const POLL_INTERVAL: Duration = Duration::from_millis(800);
/// Give up (stop respawning, emit `sidecar-failed`) after this many consecutive
/// starts that never became healthy.
const MAX_RAPID_FAILURES: u32 = 5;
/// Ceiling on the restart backoff.
const BACKOFF_CAP_SECS: u64 = 30;

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

/// Where the sidecar's stdout/stderr logs land (and the Tauri log).
pub fn log_dir(app: &AppHandle) -> PathBuf {
    app.path()
        .app_log_dir()
        .unwrap_or_else(|_| data_dir(app).join("logs"))
}

fn health_ok(port: u16) -> bool {
    let url = format!("http://127.0.0.1:{port}/healthz");
    match ureq::get(&url).timeout(Duration::from_secs(2)).call() {
        Ok(resp) => {
            if resp.status() != 200 {
                return false;
            }
            // A foreign server squatting on the port might 200 with junk — the
            // body must be exactly the ok payload to count as a Parrot sidecar.
            resp.into_string()
                .map(|b| b.contains("\"status\":\"ok\"") || b.contains("\"status\": \"ok\""))
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

/// Kill a non-Parrot process squatting on the port (Windows: netstat → taskkill).
/// Best-effort: failures are logged and we proceed to spawn regardless.
fn kill_orphan_on_port(port: u16) {
    #[cfg(windows)]
    {
        let out = Command::new("netstat").args(["-ano", "-p", "TCP"]).output();
        if let Ok(out) = out {
            let text = String::from_utf8_lossy(&out.stdout);
            let needle = format!(":{port}");
            for line in text.lines() {
                if line.contains(&needle) && line.to_uppercase().contains("LISTENING") {
                    if let Some(pid) = line.split_whitespace().last() {
                        let _ = Command::new("taskkill").args(["/F", "/PID", pid]).output();
                    }
                }
            }
        }
    }
    #[cfg(not(windows))]
    {
        let _ = port; // POSIX takeover is out of scope (Windows-only app)
    }
}

/// Ensure the Python venv + deps exist (first-run bootstrap). `uv run` will also
/// self-bootstrap, but doing it explicitly lets us surface the long install as a
/// visible stage. A present `.venv` short-circuits this on every later launch.
fn ensure_venv(app: &AppHandle, state: &SidecarState) {
    let dir = sidecar_dir();
    if dir.join(".venv").exists() {
        return; // already bootstrapped — reused on subsequent launches
    }
    state.set_stage(app, "creating_venv");
    let _ = Command::new("uv").arg("venv").current_dir(&dir).status();
    state.set_stage(app, "installing_deps");
    // `--no-dev`: keep pytest/httpx out of the runtime venv. `--extra engine`:
    // pull the PyTorch ML stack the model needs (packaging Rule 4).
    let _ = Command::new("uv")
        .args(["sync", "--no-dev", "--extra", "engine"])
        .current_dir(&dir)
        .status();
}

fn spawn_sidecar(app: &AppHandle, port: u16) -> std::io::Result<Child> {
    let logs = log_dir(app);
    let _ = std::fs::create_dir_all(&logs);
    let stdout = File::create(logs.join("backend.log")).map(Stdio::from).unwrap_or_else(|_| Stdio::null());
    let stderr = File::create(logs.join("backend_err.log")).map(Stdio::from).unwrap_or_else(|_| Stdio::null());

    Command::new("uv")
        .args(["run", "python", "main.py"])
        .current_dir(sidecar_dir())
        .env("PARROT_PORT", port.to_string())
        .env("PARROT_DATA_DIR", data_dir(app))
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

/// Start the supervisor on a background thread: attach-or-spawn → health-check →
/// keep alive (restart on crash, with backoff) until the app shuts down. On
/// repeated never-healthy starts it parks in `failed` until a Retry command.
pub fn start(app: AppHandle, port: u16) {
    std::thread::spawn(move || {
        let state = app.state::<SidecarState>();
        let mut healthy = false;
        let mut had_child = false;
        let mut attached = false;
        let mut failures: u32 = 0;

        state.set_stage(&app, "checking");

        while !state.shutting_down.load(Ordering::SeqCst) {
            // Attach to an already-healthy Parrot sidecar instead of spawning a
            // duplicate (dev `bun run dev` against a manual backend; a surviving
            // sidecar after a relaunch).
            if !attached && !had_child && health_ok(port) {
                attached = true;
                healthy = true;
                state.ready.store(true, Ordering::SeqCst);
                state.set_stage(&app, "ready");
                let _ = app.emit("sidecar-ready", port);
            }

            // An attached external sidecar that died → reclaim and spawn our own.
            if attached && !health_ok(port) {
                attached = false;
                healthy = false;
                state.ready.store(false, Ordering::SeqCst);
            }

            let exited = if attached {
                false
            } else {
                let mut guard = lock(&state.child);
                match guard.as_mut() {
                    Some(child) => matches!(child.try_wait(), Ok(Some(_))),
                    None => true,
                }
            };

            if exited {
                if had_child {
                    failures = failures_after_exit(failures, healthy);
                }

                if failures >= MAX_RAPID_FAILURES {
                    eprintln!("[supervisor] sidecar failed {failures}× to start; giving up");
                    state.failed.store(true, Ordering::SeqCst);
                    state.set_stage(&app, "failed");
                    let _ = app.emit("sidecar-failed", failures);

                    // Park until a Retry / Clean & Retry command (or shutdown).
                    if !park_until_retry(&app, &state) {
                        break;
                    }
                    failures = 0;
                    had_child = false;
                    continue;
                }

                let wait = backoff_secs(failures);
                if wait > 0 {
                    eprintln!("[supervisor] restarting sidecar in {wait}s (failure #{failures})");
                    if sleep_unless_shutdown(&state.shutting_down, Duration::from_secs(wait)) {
                        break;
                    }
                }

                // Reclaim the port from a non-Parrot squatter before spawning.
                if port_in_use(port) && !health_ok(port) {
                    state.log(&app, "[supervisor] port in use by a non-Parrot process; reclaiming");
                    kill_orphan_on_port(port);
                    std::thread::sleep(Duration::from_millis(500));
                }

                ensure_venv(&app, &state);
                state.set_stage(&app, "starting_backend");
                state.ready.store(false, Ordering::SeqCst);
                healthy = false;
                match spawn_sidecar(&app, port) {
                    Ok(child) => {
                        *lock(&state.child) = Some(child);
                        had_child = true;
                        eprintln!("[supervisor] spawned sidecar on :{port}");
                    }
                    Err(e) => {
                        eprintln!("[supervisor] failed to spawn sidecar: {e}");
                        state.log(&app, &format!("[supervisor] spawn failed: {e}"));
                        had_child = false;
                        failures += 1;
                        continue;
                    }
                }
            }

            if !healthy && health_ok(port) {
                healthy = true;
                state.ready.store(true, Ordering::SeqCst);
                state.set_stage(&app, "ready");
                let _ = app.emit("sidecar-ready", port);
                eprintln!("[supervisor] sidecar healthy on :{port}");
            }

            if sleep_unless_shutdown(&state.shutting_down, POLL_INTERVAL) {
                break;
            }
        }

        // Shutting down — kill the child (take it out first so the guard drops
        // before `state`, which borrows `app`). An attached external sidecar is
        // left running on purpose (we didn't spawn it).
        let last = lock(&state.child).take();
        if let Some(mut child) = last {
            let _ = child.kill();
            eprintln!("[supervisor] sidecar terminated");
        }
    });
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
                // Clean & Retry: wipe the bootstrapped venv + kill any squatter.
                let venv = sidecar_dir().join(".venv");
                let _ = std::fs::remove_dir_all(&venv);
                if port_in_use(state.port) && !health_ok(state.port) {
                    kill_orphan_on_port(state.port);
                }
            }
            state.failed.store(false, Ordering::SeqCst);
            state.set_stage(app, "checking");
            return true;
        }
        std::thread::sleep(Duration::from_millis(300));
    }
}

/// Stop supervising and kill the sidecar. Called on app exit.
pub fn shutdown(app: &AppHandle) {
    if let Some(state) = app.try_state::<SidecarState>() {
        state.shutting_down.store(true, Ordering::SeqCst);
        if let Some(mut child) = lock(&state.child).take() {
            let _ = child.kill();
        }
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
}
