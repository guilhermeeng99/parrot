// Tauri (Rust) command glue — native operations the browser sandbox can't do.
// Every call guards `inTauri()` so the dev browser degrades gracefully instead
// of throwing. Errors are wrapped in `ApiError` (status unset) so the toast
// layer is uniform with HTTP errors. See docs/specs/ipc-contract.md §11.

import { ApiError, inTauri } from "./client";
import type { AppPaths, UpdateProgress, UpdateStatus } from "./types";

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

/** Subscribe to install_update download progress (`update-progress` event).
 *  Returns an unlisten fn; a no-op unlisten outside Tauri (the dev browser has
 *  no updater). See docs/specs/ipc-contract.md §11. */
export async function onUpdateProgress(
  handler: (p: UpdateProgress) => void,
): Promise<() => void> {
  if (!inTauri()) return () => {};
  const { listen } = await import("@tauri-apps/api/event");
  return listen<UpdateProgress>("update-progress", (e) => handler(e.payload));
}

export const quitApp = () => invoke<void>("quit_app");

// Boot-splash glue (ipc-contract §11). The Rust supervisor owns the venv +
// process lifecycle; these expose its current stage + log tail and let the
// failed-boot screen retry (optionally wiping the bootstrapped venv first).

/** Current boot stage, e.g. "creating_venv" | "installing_deps" | "checking"
 *  | "starting_backend" | "ready" | "failed". */
export const bootstrapStatus = () => invoke<string>("bootstrap_status");

/** Backfill the boot-log tail (a late-mounting splash didn't miss early lines). */
export const getBootstrapLogs = () => invoke<string[]>("get_bootstrap_logs");

/** Reset a failed boot and re-run the spawn sequence. */
export const retryBootstrap = () => invoke<void>("retry_bootstrap");

/** Like retryBootstrap, but first wipe the venv + kill any stale sidecar. */
export const cleanAndRetryBootstrap = () => invoke<void>("clean_and_retry_bootstrap");
