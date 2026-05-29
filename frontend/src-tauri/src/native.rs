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
use tauri::{AppHandle, Emitter, State};
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

/// Download progress emitted as the `update-progress` Tauri event so the Svelte
/// updater store can render a progress bar (the updater is `dialog: false` —
/// the UI draws its own prompts; ipc-contract §11 / packaging.md updater store).
#[derive(Serialize, Clone)]
pub struct UpdateProgress {
    downloaded: usize,
    total: Option<u64>,
    done: bool,
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
            // Probe the default output device ON the audio thread (rodio's
            // OutputStream is !Send so it can't cross back) and report success
            // through `ready_tx` BEFORE we decide whether to keep the sender. If
            // the probe fails we store `None`, so audio_sender() returns None and
            // play_audio surfaces the typed "No audio output device available."
            // error instead of Ok-but-silent (the receiver thread had exited).
            let (ready_tx, ready_rx) = mpsc::channel::<bool>();
            std::thread::spawn(move || audio_thread(rx, ready_tx));
            match ready_rx.recv() {
                Ok(true) => Some(Mutex::new(tx)),
                _ => None, // probe failed (or thread died before reporting)
            }
        })
        .as_ref()
        .and_then(|m| m.lock().ok().map(|g| g.clone()))
}

/// The audio playback thread. Opens the default output stream, reports whether
/// that succeeded over `ready_tx`, then serves Play/Stop commands until the
/// sender drops. Kept off the IPC thread because `rodio::OutputStream` is not
/// `Send` and must own its device for the stream's lifetime.
fn audio_thread(rx: mpsc::Receiver<AudioCmd>, ready_tx: Sender<bool>) {
    let Ok((_stream, handle)) = rodio::OutputStream::try_default() else {
        let _ = ready_tx.send(false); // no device — caller stores None
        return;
    };
    // `_stream` must stay alive for the whole loop or playback goes silent, so it
    // stays bound at function scope (dropped only when the thread ends).
    let _ = ready_tx.send(true);
    let mut sink: Option<rodio::Sink> = None;
    for cmd in rx {
        match cmd {
            AudioCmd::Play(path) => play_into_sink(&handle, &mut sink, &path),
            AudioCmd::Stop => stop_sink(&mut sink),
        }
    }
}

/// Replace any current sink with a fresh one playing `path`. Decode/open failures
/// are swallowed (best-effort playback); a corrupt file just doesn't play.
fn play_into_sink(handle: &rodio::OutputStreamHandle, sink: &mut Option<rodio::Sink>, path: &PathBuf) {
    stop_sink(sink);
    let Ok(file) = File::open(path) else { return };
    let Ok(src) = rodio::Decoder::new(BufReader::new(file)) else { return };
    let Ok(new_sink) = rodio::Sink::try_new(handle) else { return };
    new_sink.append(src);
    *sink = Some(new_sink);
}

/// Stop + drop the current sink, if any.
fn stop_sink(sink: &mut Option<rodio::Sink>) {
    if let Some(s) = sink.take() {
        s.stop();
    }
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

/// Last `n` lines of `all`, oldest-first. Clamps `n` to a sane range (10..2000)
/// so a caller can't ask for 0 or an unbounded tail. Pure (no I/O) so it is
/// unit-testable without touching the filesystem.
fn tail_lines(all: &[&str], n: usize) -> Vec<String> {
    let n = n.clamp(10, 2000);
    all.iter()
        .skip(all.len().saturating_sub(n))
        .map(|s| s.to_string())
        .collect()
}

#[tauri::command]
pub fn read_log_tail(app: AppHandle, source: String, tail: Option<usize>) -> LogTail {
    let n = tail.unwrap_or(300);
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
    LogTail { lines: tail_lines(&all, n), path: path_str, exists: true, total_lines: total }
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
    // A re-check is required, not redundant: the prior `check_for_update` returns
    // only a serializable summary to JS — the `Update` handle (needed to download)
    // can't cross the IPC boundary, so `install_update` must obtain its own. The
    // network cost is one conditional GET of `latest.json`, not the artifact.
    let update = updater
        .check()
        .await
        .map_err(|e| e.to_string())?
        .ok_or_else(|| "No update available.".to_string())?;

    // Surface download progress to the UI via events (download_and_install's
    // callbacks are otherwise fire-and-forget). `on_chunk` carries the running
    // byte count + the content-length (if the server sent one); `on_done` flips
    // the terminal flag the updater store waits on before prompting restart.
    let app_chunk = app.clone();
    let app_done = app.clone();
    let mut downloaded = 0usize;
    update
        .download_and_install(
            move |chunk_len, total| {
                downloaded += chunk_len;
                let _ = app_chunk.emit(
                    "update-progress",
                    UpdateProgress { downloaded, total, done: false },
                );
            },
            move || {
                let _ = app_done.emit(
                    "update-progress",
                    UpdateProgress { downloaded: 0, total: None, done: true },
                );
            },
        )
        .await
        .map_err(|e| e.to_string())?;
    app.restart();
}

#[tauri::command]
pub fn quit_app(app: AppHandle, quitting: State<Quitting>) {
    quitting.0.store(true, std::sync::atomic::Ordering::SeqCst);
    app.exit(0);
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A small log longer than the clamp floor so the requested `n` is what's
    /// actually exercised (n inside the clamp range, not pinned by it).
    fn sample(len: usize) -> Vec<String> {
        (0..len).map(|i| format!("line{i}")).collect()
    }

    #[test]
    fn tail_returns_last_n_in_oldest_first_order() {
        let owned = sample(50);
        let all: Vec<&str> = owned.iter().map(String::as_str).collect();
        let out = tail_lines(&all, 20);
        assert_eq!(out.len(), 20);
        // Last 20 of 0..50 → 30..50, still ascending (oldest-first, not reversed).
        assert_eq!(out.first().unwrap(), "line30");
        assert_eq!(out.last().unwrap(), "line49");
    }

    #[test]
    fn tail_n_greater_than_len_returns_everything() {
        // n is clamped to <=2000; with only 100 lines the skip is 0 → all lines.
        let owned = sample(100);
        let all: Vec<&str> = owned.iter().map(String::as_str).collect();
        let out = tail_lines(&all, 999);
        assert_eq!(out.len(), 100);
        assert_eq!(out.first().unwrap(), "line0");
        assert_eq!(out.last().unwrap(), "line99");
    }

    #[test]
    fn tail_clamps_low_request_up_to_the_floor() {
        // Asking for 0 (or anything <10) is clamped to 10, never an empty tail.
        let owned = sample(40);
        let all: Vec<&str> = owned.iter().map(String::as_str).collect();
        let out = tail_lines(&all, 0);
        assert_eq!(out.len(), 10, "n below the floor is raised to 10");
        assert_eq!(out.first().unwrap(), "line30"); // last 10 of 0..40
        assert_eq!(out.last().unwrap(), "line39");
    }

    #[test]
    fn tail_clamps_huge_request_down_to_the_ceiling() {
        // n is clamped to 2000; with more lines than that, only the last 2000
        // survive (the ceiling actually bites).
        let owned = sample(2500);
        let all: Vec<&str> = owned.iter().map(String::as_str).collect();
        let out = tail_lines(&all, usize::MAX);
        assert_eq!(out.len(), 2000);
        assert_eq!(out.first().unwrap(), "line500"); // last 2000 of 0..2500
        assert_eq!(out.last().unwrap(), "line2499");
    }

    #[test]
    fn tail_handles_empty_input() {
        let out = tail_lines(&[], 300);
        assert!(out.is_empty(), "no lines in → no lines out, regardless of n");
    }
}
