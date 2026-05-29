//! Sidecar supervisor — the Rust shell's most important job.
//!
//! Spawns the Python FastAPI sidecar, health-checks it on `/healthz`, restarts
//! it if it crashes, and kills it when the app exits. This is the only place
//! that owns the sidecar process. See docs/specs/architecture.md §3.

use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::Duration;

use tauri::{AppHandle, Emitter, Manager};

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
    /// The loopback port the sidecar is bound to.
    pub port: u16,
}

impl SidecarState {
    pub fn new(port: u16) -> Self {
        Self {
            child: Mutex::new(None),
            shutting_down: AtomicBool::new(false),
            ready: AtomicBool::new(false),
            port,
        }
    }
}

/// Port the sidecar binds to. `PARROT_PORT` overrides the 3900 default; the
/// supervisor passes the same value to the child so they never diverge.
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

/// Start the supervisor on a background thread: spawn → health-check → keep
/// alive (restart on crash) until the app shuts down.
pub fn start(app: AppHandle, port: u16) {
    std::thread::spawn(move || {
        let state = app.state::<SidecarState>();
        let mut announced = false;

        while !state.shutting_down.load(Ordering::SeqCst) {
            // Spawn if there is no live child.
            let need_spawn = {
                let mut guard = state.child.lock().unwrap();
                match guard.as_mut() {
                    Some(child) => matches!(child.try_wait(), Ok(Some(_))),
                    None => true,
                }
            };

            if need_spawn {
                state.ready.store(false, Ordering::SeqCst);
                announced = false;
                match spawn_sidecar(port) {
                    Ok(child) => {
                        *state.child.lock().unwrap() = Some(child);
                        eprintln!("[supervisor] spawned sidecar on :{port}");
                    }
                    Err(e) => {
                        eprintln!("[supervisor] failed to spawn sidecar: {e}");
                        std::thread::sleep(Duration::from_secs(2));
                        continue;
                    }
                }
            }

            // Announce readiness once the sidecar answers /healthz.
            if !announced && health_ok(port) {
                state.ready.store(true, Ordering::SeqCst);
                announced = true;
                let _ = app.emit("sidecar-ready", port);
                eprintln!("[supervisor] sidecar healthy on :{port}");
            }

            std::thread::sleep(Duration::from_millis(800));
        }

        // Shutting down — kill the child. Take it out first so the MutexGuard
        // temporary drops before `state` (which borrows `app`) goes out of scope.
        let last = state.child.lock().unwrap().take();
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
        if let Some(mut child) = state.child.lock().unwrap().take() {
            let _ = child.kill();
        }
    }
}
