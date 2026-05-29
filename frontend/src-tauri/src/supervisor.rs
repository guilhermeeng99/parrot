//! Sidecar supervisor — the Rust shell's most important job.
//!
//! Spawns the Python FastAPI sidecar, health-checks it on `/healthz`, restarts
//! it if it crashes (with backoff + a give-up cap), and kills it when the app
//! exits. This is the only place that owns the sidecar process.
//! See docs/specs/architecture.md §3.

use std::path::PathBuf;
use std::process::{Child, Command};
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
            port,
        }
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
/// the dev default (`<repo>/sidecar`, relative to this crate).
fn sidecar_dir() -> PathBuf {
    if let Ok(dir) = std::env::var("PARROT_SIDECAR_DIR") {
        return PathBuf::from(dir);
    }
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../sidecar")
}

fn spawn_sidecar(port: u16) -> std::io::Result<Child> {
    Command::new("uv")
        .args(["run", "python", "main.py"])
        .current_dir(sidecar_dir())
        .env("PARROT_PORT", port.to_string())
        .spawn()
}

fn health_ok(port: u16) -> bool {
    let url = format!("http://127.0.0.1:{port}/healthz");
    match ureq::get(&url).timeout(Duration::from_secs(2)).call() {
        Ok(resp) => resp.status() == 200,
        Err(_) => false,
    }
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

/// Start the supervisor on a background thread: spawn → health-check → keep
/// alive (restart on crash, with backoff) until the app shuts down or the
/// sidecar fails to become healthy too many times in a row.
pub fn start(app: AppHandle, port: u16) {
    std::thread::spawn(move || {
        let state = app.state::<SidecarState>();
        // Whether the *current* child has answered /healthz this run.
        let mut healthy = false;
        // Whether a child was ever spawned (so the first iteration doesn't count
        // a non-existent child as a crash).
        let mut had_child = false;
        let mut failures: u32 = 0;

        while !state.shutting_down.load(Ordering::SeqCst) {
            // Has the current child exited (or is there none yet)?
            let exited = {
                let mut guard = lock(&state.child);
                match guard.as_mut() {
                    Some(child) => matches!(child.try_wait(), Ok(Some(_))),
                    None => true,
                }
            };

            if exited {
                if had_child {
                    // Account for the child that just died: healthy run resets,
                    // a never-healthy death counts toward the give-up cap.
                    failures = failures_after_exit(failures, healthy);
                }

                if failures >= MAX_RAPID_FAILURES {
                    eprintln!("[supervisor] sidecar failed {failures}× to start; giving up");
                    state.failed.store(true, Ordering::SeqCst);
                    let _ = app.emit("sidecar-failed", failures);
                    break;
                }

                let wait = backoff_secs(failures);
                if wait > 0 {
                    eprintln!(
                        "[supervisor] restarting sidecar in {wait}s (failure #{failures})"
                    );
                    if sleep_unless_shutdown(&state.shutting_down, Duration::from_secs(wait)) {
                        break;
                    }
                }

                state.ready.store(false, Ordering::SeqCst);
                healthy = false;
                match spawn_sidecar(port) {
                    Ok(child) => {
                        *lock(&state.child) = Some(child);
                        had_child = true;
                        eprintln!("[supervisor] spawned sidecar on :{port}");
                    }
                    Err(e) => {
                        // uv missing / not executable — count it and let the
                        // next iteration apply backoff or give up.
                        eprintln!("[supervisor] failed to spawn sidecar: {e}");
                        had_child = false;
                        failures += 1;
                        continue;
                    }
                }
            }

            // Announce readiness once the sidecar answers /healthz.
            if !healthy && health_ok(port) {
                healthy = true;
                state.ready.store(true, Ordering::SeqCst);
                let _ = app.emit("sidecar-ready", port);
                eprintln!("[supervisor] sidecar healthy on :{port}");
            }

            if sleep_unless_shutdown(&state.shutting_down, POLL_INTERVAL) {
                break;
            }
        }

        // Shutting down (or gave up) — kill the child. Take it out first so the
        // MutexGuard temporary drops before `state` (which borrows `app`) does.
        let last = lock(&state.child).take();
        if let Some(mut child) = last {
            let _ = child.kill();
            eprintln!("[supervisor] sidecar terminated");
        }
    });
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
        // Every failure short of the cap still backs off (>0s) so we never
        // busy-loop, and the cap is below the give-up count so we do give up.
        for n in 1..MAX_RAPID_FAILURES {
            assert!(backoff_secs(n) > 0, "failure #{n} must back off");
        }
    }

    #[test]
    fn failure_accounting_resets_only_on_a_healthy_run() {
        // A child that served /healthz resets the counter…
        assert_eq!(failures_after_exit(4, true), 0);
        // …a never-healthy death always counts, even if it limped a while.
        assert_eq!(failures_after_exit(0, false), 1);
        assert_eq!(failures_after_exit(3, false), 4);
    }

    #[test]
    fn never_healthy_deaths_reach_the_give_up_cap() {
        // Simulate the loop's accounting: repeated non-healthy exits must hit the
        // give-up threshold rather than loop forever.
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
        // Must not actually sleep 60s — the flag short-circuits it.
        assert!(sleep_unless_shutdown(&flag, Duration::from_secs(60)));
    }

    #[test]
    fn lock_recovers_from_a_poisoned_mutex() {
        let m = Arc::new(Mutex::new(7));
        let m2 = Arc::clone(&m);
        // Poison the mutex by panicking while holding the guard.
        let _ = std::thread::spawn(move || {
            let _g = m2.lock().unwrap();
            panic!("intentional poison");
        })
        .join();

        // The std guard would be `Err(Poisoned)`; our helper must still recover.
        assert_eq!(*lock(&m), 7);
    }
}
