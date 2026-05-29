mod supervisor;

use std::sync::atomic::Ordering;

use supervisor::SidecarState;

/// The loopback port the sidecar is bound to. The UI uses this to build its
/// API base URL (it can also just default to 3900).
#[tauri::command]
fn backend_port(state: tauri::State<SidecarState>) -> u16 {
    state.port
}

/// Whether the supervisor has seen the sidecar answer `/healthz` at least once.
#[tauri::command]
fn sidecar_ready(state: tauri::State<SidecarState>) -> bool {
    state.ready.load(Ordering::SeqCst)
}

/// Whether the supervisor permanently gave up (the sidecar crash-looped). Latched,
/// so the UI gets the terminal state even if it missed the `sidecar-failed` event.
#[tauri::command]
fn sidecar_failed(state: tauri::State<SidecarState>) -> bool {
    state.failed.load(Ordering::SeqCst)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let port = supervisor::resolve_port();

    tauri::Builder::default()
        .manage(SidecarState::new(port))
        .setup(move |app| {
            // The Rust shell owns the sidecar lifecycle: spawn it, health-check
            // it, restart it on crash. See docs/specs/architecture.md §3.
            supervisor::start(app.handle().clone(), port);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            backend_port,
            sidecar_ready,
            sidecar_failed
        ])
        .build(tauri::generate_context!())
        .expect("error while building the Parrot application")
        .run(|app_handle, event| {
            if let tauri::RunEvent::Exit = event {
                // Tear the sidecar down with the app — never leave an orphan.
                supervisor::shutdown(app_handle);
            }
        });
}
