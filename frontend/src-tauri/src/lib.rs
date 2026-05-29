mod native;
mod supervisor;

use std::sync::atomic::Ordering;

use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, TrayIconBuilder, TrayIconEvent};
use tauri::{Manager, WindowEvent};

use native::Quitting;
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

/// Current boot stage for the splash (checking → … → ready | failed).
#[tauri::command]
fn bootstrap_status(state: tauri::State<SidecarState>) -> String {
    supervisor::bootstrap_stage(&state)
}

/// Backfill the boot-log tail (a late-mounting splash didn't miss early lines).
#[tauri::command]
fn get_bootstrap_logs(state: tauri::State<SidecarState>) -> Vec<String> {
    supervisor::bootstrap_logs(&state)
}

/// Reset a failed boot and re-run the spawn sequence.
#[tauri::command]
fn retry_bootstrap(state: tauri::State<SidecarState>) {
    supervisor::request_retry(&state, false);
}

/// Like Retry, but first wipe the bootstrapped venv + kill any stale sidecar.
#[tauri::command]
fn clean_and_retry_bootstrap(state: tauri::State<SidecarState>) {
    supervisor::request_retry(&state, true);
}

fn show_main(app: &tauri::AppHandle) {
    if let Some(win) = app.get_webview_window("main") {
        let _ = win.show();
        let _ = win.set_focus();
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let port = supervisor::resolve_port();

    tauri::Builder::default()
        // single-instance MUST be the first plugin: a second launch focuses the
        // existing window instead of starting a competing sidecar (architecture §3.5).
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            show_main(app);
        }))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(SidecarState::new(port))
        .manage(Quitting::default())
        .setup(move |app| {
            // The Rust shell owns the sidecar lifecycle: spawn it, health-check
            // it, restart it on crash. See docs/specs/architecture.md §3.
            supervisor::start(app.handle().clone(), port);

            // Tray: closing the window hides to tray; only tray-Quit exits.
            let show = MenuItem::with_id(app, "show", "Show Parrot", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit Parrot", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show, &quit])?;
            // Build the tray with the window icon when present; if the icon is
            // somehow unavailable, build the tray without one rather than panic on
            // startup (a missing icon must never crash the app).
            let mut tray_builder = TrayIconBuilder::new();
            if let Some(icon) = app.default_window_icon() {
                tray_builder = tray_builder.icon(icon.clone());
            }
            let _tray = tray_builder
                .tooltip("Parrot")
                .menu(&menu)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => show_main(app),
                    "quit" => {
                        app.state::<Quitting>().0.store(true, Ordering::SeqCst);
                        app.exit(0);
                    }
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click { button: MouseButton::Left, .. } = event {
                        show_main(&tray.app_handle().clone());
                    }
                })
                .build(app)?;

            // Hide-to-tray on window close (unless we're really quitting).
            if let Some(win) = app.get_webview_window("main") {
                let handle = app.handle().clone();
                win.on_window_event(move |event| {
                    if let WindowEvent::CloseRequested { api, .. } = event {
                        if !handle.state::<Quitting>().0.load(Ordering::SeqCst) {
                            api.prevent_close();
                            if let Some(w) = handle.get_webview_window("main") {
                                let _ = w.hide();
                            }
                        }
                    }
                });
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            backend_port,
            sidecar_ready,
            sidecar_failed,
            bootstrap_status,
            get_bootstrap_logs,
            retry_bootstrap,
            clean_and_retry_bootstrap,
            native::save_audio_dialog,
            native::reveal_in_folder,
            native::get_app_paths,
            native::read_log_tail,
            native::check_for_update,
            native::install_update,
            native::quit_app,
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
