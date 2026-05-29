//! Native glue exposed to the UI as Tauri commands (ipc-contract.md §11).
//!
//! The browser sandbox can't open save dialogs, reveal folders, play system
//! audio, tail logs, or drive the updater — these commands do. Fallible ops
//! return `Result<_, String>`; the `Err(String)` surfaces to JS as a rejected
//! promise that `native.ts` wraps in an `ApiError`.

use std::fs::File;
use std::io::{BufReader, Read};
use std::path::PathBuf;
use std::sync::mpsc::{self, Sender};
use std::sync::{Mutex, OnceLock};

use serde::Serialize;
use tauri::{AppHandle, State};
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_opener::OpenerExt;

use crate::supervisor;

/// Latched on tray-Quit so the window-close handler knows to actually exit
/// rather than hide-to-tray (architecture §3.3).
#[derive(Default)]
pub struct Quitting(pub std::sync::atomic::AtomicBool);

#[derive(Serialize)]
pub struct AppPaths {
    #[serde(rename = "dataDir")]
    data_dir: String,
    #[serde(rename = "outputsDir")]
    outputs_dir: String,
    #[serde(rename = "voicesDir")]
    voices_dir: String,
    #[serde(rename = "dbPath")]
    db_path: String,
    #[serde(rename = "logPath")]
    log_path: String,
}

#[derive(Serialize)]
pub struct LogTail {
    lines: Vec<String>,
    path: String,
    exists: bool,
    total_lines: usize,
}

#[derive(Serialize)]
pub struct UpdateStatus {
    available: bool,
    version: Option<String>,
    notes: Option<String>,
}

// --- audio playback (dedicated thread; rodio's OutputStream is not Send) -----
enum AudioCmd {
    Play(PathBuf),
    Stop,
}

fn audio_sender() -> Option<Sender<AudioCmd>> {
    static SENDER: OnceLock<Option<Mutex<Sender<AudioCmd>>>> = OnceLock::new();
    SENDER
        .get_or_init(|| {
            let (tx, rx) = mpsc::channel::<AudioCmd>();
            std::thread::spawn(move || {
                let (_stream, handle) = match rodio::OutputStream::try_default() {
                    Ok(v) => v,
                    Err(_) => return, // no audio device — playback commands no-op
                };
                let mut sink: Option<rodio::Sink> = None;
                for cmd in rx {
                    match cmd {
                        AudioCmd::Play(path) => {
                            if let Some(s) = sink.take() {
                                s.stop();
                            }
                            if let Ok(file) = File::open(&path) {
                                if let Ok(src) = rodio::Decoder::new(BufReader::new(file)) {
                                    if let Ok(s) = rodio::Sink::try_new(&handle) {
                                        s.append(src);
                                        sink = Some(s);
                                    }
                                }
                            }
                        }
                        AudioCmd::Stop => {
                            if let Some(s) = sink.take() {
                                s.stop();
                            }
                        }
                    }
                }
            });
            Some(Mutex::new(tx))
        })
        .as_ref()
        .and_then(|m| m.lock().ok().map(|g| g.clone()))
}

#[tauri::command]
pub fn save_audio_dialog(
    app: AppHandle,
    default_name: String,
    wav_bytes: Option<Vec<u8>>,
) -> Result<Option<String>, String> {
    let chosen = app
        .dialog()
        .file()
        .add_filter("WAV audio", &["wav"])
        .set_file_name(&default_name)
        .blocking_save_file();

    let Some(file_path) = chosen else {
        return Ok(None); // user cancelled
    };
    let path = file_path.into_path().map_err(|e| e.to_string())?;
    if let Some(bytes) = wav_bytes {
        std::fs::write(&path, bytes).map_err(|e| e.to_string())?;
    }
    Ok(Some(path.to_string_lossy().into_owned()))
}

#[tauri::command]
pub fn reveal_in_folder(app: AppHandle, path: String) -> Result<(), String> {
    app.opener()
        .reveal_item_in_dir(PathBuf::from(path))
        .map_err(|e| e.to_string())
}

#[tauri::command]
pub fn play_audio(path: String) -> Result<(), String> {
    audio_sender()
        .ok_or_else(|| "No audio output device available.".to_string())?
        .send(AudioCmd::Play(PathBuf::from(path)))
        .map_err(|e| e.to_string())
}

#[tauri::command]
pub fn stop_audio() {
    if let Some(tx) = audio_sender() {
        let _ = tx.send(AudioCmd::Stop);
    }
}

#[tauri::command]
pub fn get_app_paths(app: AppHandle) -> AppPaths {
    let data = supervisor::data_dir(&app);
    let log = supervisor::log_dir(&app).join("backend.log");
    AppPaths {
        outputs_dir: data.join("outputs").to_string_lossy().into_owned(),
        voices_dir: data.join("voices").to_string_lossy().into_owned(),
        db_path: data.join("parrot.db").to_string_lossy().into_owned(),
        data_dir: data.to_string_lossy().into_owned(),
        log_path: log.to_string_lossy().into_owned(),
    }
}

#[tauri::command]
pub fn read_log_tail(app: AppHandle, source: String, tail: Option<usize>) -> LogTail {
    let n = tail.unwrap_or(300).clamp(10, 2000);
    let path = match source.as_str() {
        "tauri" => supervisor::log_dir(&app).join("parrot.log"),
        _ => supervisor::log_dir(&app).join("backend.log"),
    };
    let path_str = path.to_string_lossy().into_owned();
    let Ok(mut file) = File::open(&path) else {
        return LogTail { lines: vec![], path: path_str, exists: false, total_lines: 0 };
    };
    let mut contents = String::new();
    let _ = file.read_to_string(&mut contents);
    let all: Vec<&str> = contents.lines().collect();
    let total = all.len();
    let lines = all
        .iter()
        .skip(total.saturating_sub(n))
        .map(|s| s.to_string())
        .collect();
    LogTail { lines, path: path_str, exists: true, total_lines: total }
}

#[tauri::command]
pub async fn check_for_update(app: AppHandle) -> UpdateStatus {
    use tauri_plugin_updater::UpdaterExt;
    let none = UpdateStatus { available: false, version: None, notes: None };
    let Ok(updater) = app.updater() else {
        return none; // updater not configured (e.g. dev) — non-fatal
    };
    match updater.check().await {
        Ok(Some(update)) => UpdateStatus {
            available: true,
            version: Some(update.version.clone()),
            notes: update.body.clone(),
        },
        _ => none,
    }
}

#[tauri::command]
pub async fn install_update(app: AppHandle) -> Result<(), String> {
    use tauri_plugin_updater::UpdaterExt;
    let updater = app.updater().map_err(|e| e.to_string())?;
    let update = updater
        .check()
        .await
        .map_err(|e| e.to_string())?
        .ok_or_else(|| "No update available.".to_string())?;
    update
        .download_and_install(|_, _| {}, || {})
        .await
        .map_err(|e| e.to_string())?;
    app.restart();
}

#[tauri::command]
pub fn quit_app(app: AppHandle, quitting: State<Quitting>) {
    quitting.0.store(true, std::sync::atomic::Ordering::SeqCst);
    app.exit(0);
}
