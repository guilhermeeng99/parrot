// Tauri (Rust) command glue — native operations the browser sandbox can't do.
// Every call guards `inTauri()` so the dev browser degrades gracefully instead
// of throwing. Errors are wrapped in `ApiError` (status unset) so the toast
// layer is uniform with HTTP errors. See docs/specs/ipc-contract.md §11.

import { ApiError, inTauri } from "./client";
import type { AppPaths, UpdateStatus } from "./types";

async function invoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  if (!inTauri()) throw new ApiError(`invoke:${cmd}`, 0, "Native features need the desktop app.");
  const { invoke: tauriInvoke } = await import("@tauri-apps/api/core");
  try {
    return await tauriInvoke<T>(cmd, args);
  } catch (e) {
    throw new ApiError(`invoke:${cmd}`, 0, e instanceof Error ? e.message : String(e));
  }
}

/** Native "Save As" → returns the chosen path, or null if cancelled. */
export const saveAudioDialog = (defaultName: string, wavBytes?: Uint8Array) =>
  invoke<string | null>("save_audio_dialog", {
    defaultName,
    wavBytes: wavBytes ? Array.from(wavBytes) : undefined,
  });

export const revealInFolder = (path: string) => invoke<void>("reveal_in_folder", { path });

export const playAudio = (path: string) => invoke<void>("play_audio", { path });
export const stopAudio = () => invoke<void>("stop_audio");

export const getAppPaths = () => invoke<AppPaths>("get_app_paths");

export const readLogTail = (source: "backend" | "tauri", tail = 300) =>
  invoke<{ lines: string[]; path: string; exists: boolean; total_lines: number }>("read_log_tail", {
    source,
    tail,
  });

export const checkForUpdate = () => invoke<UpdateStatus>("check_for_update");
export const installUpdate = () => invoke<void>("install_update");
export const quitApp = () => invoke<void>("quit_app");
