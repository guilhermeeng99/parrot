//! First-run bootstrap — the venv/uv/path concern split out of `supervisor.rs`.
//!
//! Everything here serves getting a runnable Python sidecar onto an end user's
//! machine: resolving the sidecar source dir, the bundled `uv`, the writable
//! data-dir venv + its success sentinel; running `uv venv` / `uv sync` (spawned +
//! polled so a quit can't hang on a multi-minute install); and the `nvidia-smi`
//! probe that picks the torch wheel variant. The supervise loop + process
//! lifecycle stay in `supervisor.rs`; this module owns only "make the venv exist".
//!
//! It reaches back into `SidecarState` for exactly three things — `set_stage`,
//! `log`, and `shutting_down` — so the bootstrap can surface boot stages and abort
//! cleanly on app-quit. See docs/specs/first-run-setup.md and packaging.md Rule 4.

use std::fs::File;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::Ordering;
use std::time::{Duration, Instant};

use tauri::{AppHandle, Manager};

use crate::supervisor::SidecarState;

/// Windows process-creation flag that suppresses a flashing console window when
/// spawning a console subprocess (uv/nvidia-smi/python) from a GUI app.
#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

/// Apply `CREATE_NO_WINDOW` on Windows so helper subprocesses (uv, netstat,
/// taskkill, tasklist, the sidecar python) never flash a console. No-op on other
/// platforms. Centralized so every `Command` in the shell stays consistent.
pub(crate) fn no_window(cmd: &mut Command) -> &mut Command {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    cmd
}

/// Open `path` as a child-process stdio sink. `append` keeps prior content (the uv
/// bootstrap log accumulates across launches + backoff retries); otherwise the file
/// is truncated per launch (the sidecar's per-run stdout/stderr). Falls back to
/// `null` if the file can't be opened (best-effort logging — never fail a spawn on
/// a log file). Shared by the uv bootstrap log and the two sidecar log files.
pub(crate) fn log_file_stdio(path: PathBuf, append: bool) -> Stdio {
    let opened = if append {
        File::options().create(true).append(true).open(&path)
    } else {
        File::create(&path)
    };
    opened.map(Stdio::from).unwrap_or_else(|_| Stdio::null())
}

/// Sidecar source dir WITHOUT the bundled-resource lookup: `PARROT_SIDECAR_DIR`
/// override, else the in-repo dev path relative to this crate. This is the
/// fallback `sidecar_dir` uses when there is no bundled resource (dev builds).
fn sidecar_dir_fallback() -> PathBuf {
    if let Ok(dir) = std::env::var("PARROT_SIDECAR_DIR") {
        return PathBuf::from(dir);
    }
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../sidecar")
}

/// Directory containing the sidecar's `main.py` (+ `pyproject.toml`/`uv.lock`/
/// `app/`). A packaged build bundles the sidecar as Tauri `resources`, so at
/// runtime it lives under `<resource_dir>/sidecar`; we use that when its `main.py`
/// is present. Otherwise (`PARROT_SIDECAR_DIR` override, or a `tauri dev` build
/// with no staged resources) fall back to the repo path.
///
/// WHY this matters: the previous version returned the build-time
/// `CARGO_MANIFEST_DIR/../../sidecar` path unconditionally. That path does NOT
/// exist on an end user's machine, so `Command::current_dir()` on it made
/// `uv venv`/`uv sync`/the python spawn fail to launch — the "engine couldn't
/// start" first-run failure.
pub(crate) fn sidecar_dir(app: &AppHandle) -> PathBuf {
    if std::env::var("PARROT_SIDECAR_DIR").is_err() {
        if let Ok(res) = app.path().resource_dir() {
            let bundled = res.join("sidecar");
            if bundled.join("main.py").exists() {
                return bundled;
            }
        }
    }
    sidecar_dir_fallback()
}

/// Path to the `uv` executable. Tauri bundles the `externalBin` `uv` next to the
/// app binary (the target-triple suffix is stripped at install time → `uv.exe`
/// beside `parrot.exe`). A clean end-user machine has NO `uv` on `PATH`, so we
/// MUST invoke that adjacent copy — `Command::new("uv")` relying on `PATH` is the
/// other half of the "engine couldn't start" failure. Falls back to a bare `uv`
/// (resolved on PATH) only for dev (`tauri dev`/`cargo run`), where the
/// externalBin may not be staged beside the binary.
fn uv_bin() -> PathBuf {
    if let Ok(exe) = std::env::current_exe() {
        if let Some(adjacent) = exe.parent().map(|d| d.join("uv.exe")) {
            if adjacent.exists() {
                return adjacent;
            }
        }
    }
    PathBuf::from("uv")
}

/// The durable data dir the sidecar owns (`parrot_data/`). Honors a
/// `PARROT_DATA_DIR` override (dev/test); else `%APPDATA%\\Parrot\\parrot_data`.
/// The supervisor passes this to the sidecar as `PARROT_DATA_DIR` so the two
/// processes agree on the location (e.g. for `get_app_paths`). `pub` because
/// `native.rs` reads it (re-exported via `supervisor::data_dir`).
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
pub(crate) fn venv_dir(app: &AppHandle) -> PathBuf {
    data_dir(app).join(".venv")
}

/// The venv's Python interpreter. The supervisor spawns THIS directly (not
/// `uv run ...`) so the immediate child is `python.exe` and `child.kill()`
/// actually reaps the engine instead of just the `uv` launcher (architecture
/// §3.3 / orphaned-process fix). Windows layout: `<venv>\Scripts\python.exe`.
pub(crate) fn venv_python(app: &AppHandle) -> PathBuf {
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

/// Where the sidecar's stdout/stderr logs land (and the Tauri log). `pub` because
/// `native.rs` reads it (re-exported via `supervisor::log_dir`).
pub fn log_dir(app: &AppHandle) -> PathBuf {
    app.path()
        .app_log_dir()
        .unwrap_or_else(|_| data_dir(app).join("logs"))
}

/// Open the uv bootstrap log (append, so successive launches + backoff retries
/// accumulate rather than clobbering the prior run). In a release build
/// (`windows_subsystem = "windows"`, no console) uv's inherited stdout/stderr would
/// vanish, leaving a failed `uv sync --extra cu124` invisible. Routing both streams
/// to `<log_dir>/uv_bootstrap.log` makes the failure readable in the log dir
/// (revealed via Settings → Engine → View backend log).
fn uv_log_stdio(app: &AppHandle) -> Stdio {
    let logs = log_dir(app);
    let _ = std::fs::create_dir_all(&logs);
    log_file_stdio(logs.join("uv_bootstrap.log"), true)
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

/// Result of one bootstrap `uv` step. `Done { synced }` reports whether the step
/// finished with a clean (zero) exit — used to gate the success sentinel (B3).
#[derive(PartialEq, Eq)]
enum UvStep {
    ShutdownRequested,
    Done { synced: bool },
}

/// Spawn a `uv` subcommand (filing its output to `uv_bootstrap.log`) and wait for
/// it, killable on shutdown. The shared spawn-guard-wait-map body behind both
/// `uv venv` and `uv sync`; `what` labels it in the bootstrap log.
fn run_uv(
    app: &AppHandle,
    state: &SidecarState,
    project: &PathBuf,
    venv: &PathBuf,
    args: &[&str],
    what: &str,
) -> UvStep {
    let spawned = no_window(
        Command::new(uv_bin())
            .args(args)
            .current_dir(project)
            .env("UV_PROJECT_ENVIRONMENT", venv),
    )
    .stdout(uv_log_stdio(app))
    .stderr(uv_log_stdio(app))
    .spawn();
    let Ok(mut child) = spawned else {
        state.log(app, &format!("[supervisor] failed to launch `{what}`"));
        return UvStep::Done { synced: false };
    };
    let outcome = wait_uv_or_shutdown(&mut child, state);
    if matches!(&outcome, UvOutcome::Exited(Some(s)) if !s.success()) {
        state.log(app, &format!("[supervisor] `{what}` exited non-zero; see uv_bootstrap.log"));
    }
    if outcome.is_shutdown() {
        return UvStep::ShutdownRequested;
    }
    UvStep::Done { synced: outcome.succeeded() }
}

/// Run `uv venv` (spawned + polled). `uv venv` can block on a one-time managed-
/// CPython download (no system Python), so it is polled too — a quit must not hang.
fn run_uv_venv(app: &AppHandle, state: &SidecarState, project: &PathBuf, venv: &PathBuf) -> UvStep {
    state.set_stage(app, "creating_venv");
    run_uv(app, state, project, venv, &["venv"], "uv venv")
}

/// Run `uv sync`. `--no-dev` keeps pytest/httpx out of the runtime venv; `--extra
/// engine` pulls the model lib + ML stack; `--extra {cpu|cu124}` picks the torch
/// wheel variant from the NVIDIA probe so GPU boxes get CUDA and everyone else the
/// CPU wheel (device-detection.md / packaging Rule 4 + 6). `--frozen` installs
/// straight from the bundled `uv.lock` without re-resolving or rewriting it (the
/// project dir is the read-only install location, and the lock already pins every
/// engine + cpu/cu124 wheel). This is the multi-minute step.
fn run_uv_sync(app: &AppHandle, state: &SidecarState, project: &PathBuf, venv: &PathBuf) -> UvStep {
    state.set_stage(app, "installing_deps");
    let torch_extra = if has_nvidia_gpu(state) { "cu124" } else { "cpu" };
    state.log(app, &format!("[supervisor] engine deps: torch variant = {torch_extra}"));
    run_uv(
        app,
        state,
        project,
        venv,
        &["sync", "--no-dev", "--frozen", "--extra", "engine", "--extra", torch_extra],
        "uv sync",
    )
}

/// Ensure the Python venv + deps exist (first-run bootstrap). The venv lives under
/// the writable data dir (`parrot_data/.venv`), NOT inside the installed sidecar
/// bundle (packaging Rule 4). The project (`pyproject.toml`/`uv.lock`) is resolved
/// from `sidecar_dir()`; `UV_PROJECT_ENVIRONMENT` redirects uv's environment to the
/// data-dir venv. A *complete* bootstrap (interpreter + success sentinel)
/// short-circuits this on every later launch.
///
/// Returns `false` ONLY when shutdown was requested (the caller then bails out of
/// the supervise loop). A `uv` failure that is NOT a shutdown returns `true` so the
/// caller still attempts the spawn; the spawn/health path then accounts it as a
/// normal failure (backoff + retry), preserving the pre-existing recovery flow.
/// Both `uv venv` and `uv sync` (and the `nvidia-smi` probe) are spawned + polled
/// (never a blocking `.status()`) and killed on quit, so a first-run install can't
/// hang app exit or orphan a child; uv's output is filed to `uv_bootstrap.log`.
pub(crate) fn ensure_venv(app: &AppHandle, state: &SidecarState) -> bool {
    if venv_is_ready(app) {
        return true; // fully bootstrapped (python + sentinel) — reused later (B3)
    }
    if state.shutting_down.load(Ordering::SeqCst) {
        return false;
    }
    // A partial venv (python.exe present, sentinel missing) is an interrupted
    // prior install — re-run `uv sync` over it rather than booting a torch-less
    // env. `uv` is idempotent, so re-running `uv venv` over an existing one is safe.
    let project = sidecar_dir(app);
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sidecar_dir_honors_override() {
        let prev = std::env::var("PARROT_SIDECAR_DIR").ok();

        std::env::set_var("PARROT_SIDECAR_DIR", "/tmp/parrot-custom-sidecar");
        assert_eq!(sidecar_dir_fallback(), PathBuf::from("/tmp/parrot-custom-sidecar"));

        match prev {
            Some(v) => std::env::set_var("PARROT_SIDECAR_DIR", v),
            None => std::env::remove_var("PARROT_SIDECAR_DIR"),
        }
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
}
