import { writable } from "svelte/store";
import {
  checkForUpdate,
  errMsg,
  getAppVersion,
  inTauri,
  installUpdate,
  onUpdateProgress,
} from "$lib/api";

// The running app's OWN version (e.g. "0.0.4"), shown in the header + Settings.
// Distinct from the updater store's `version`, which is the AVAILABLE update.
export const appVersion = writable<string>("");

let versionLoaded = false;
export async function loadAppVersion(): Promise<void> {
  if (versionLoaded) return; // fetched once per session; getAppVersion never throws
  versionLoaded = true;
  appVersion.set(await getAppVersion());
}

// Updater store (packaging.md). Client-rendered (dialog:false). Outside Tauri
// there is no updater, so checks resolve to up_to_date rather than nagging.
type UpdState = "idle" | "checking" | "up_to_date" | "available" | "downloading" | "error";

export const updater = writable<{
  state: UpdState;
  version?: string;
  notes?: string;
  error?: string;
  /** Bytes downloaded/total while state === "downloading" (from update-progress). */
  progress?: { downloaded: number; total: number | null };
}>({ state: "idle" });

export async function checkUpdate(): Promise<void> {
  if (!inTauri()) {
    updater.set({ state: "up_to_date" });
    return;
  }
  updater.set({ state: "checking" });
  try {
    const res = await checkForUpdate();
    updater.set(
      res.available
        ? { state: "available", version: res.version, notes: res.notes }
        : { state: "up_to_date" },
    );
  } catch (e) {
    // A failed update check is non-fatal — the app keeps running.
    updater.set({ state: "error", error: errMsg(e) });
  }
}

export async function applyUpdate(): Promise<void> {
  updater.update((s) => ({ ...s, state: "downloading", progress: undefined }));
  // Render download progress from the Rust `update-progress` event while the
  // artifact downloads (install_update is otherwise opaque until it relaunches).
  const unlisten = await onUpdateProgress((p) => {
    if (!p.done) {
      updater.update((s) => ({ ...s, progress: { downloaded: p.downloaded, total: p.total } }));
    }
  });
  try {
    await installUpdate(); // downloads, verifies the signature, relaunches
  } catch (e) {
    updater.set({ state: "error", error: errMsg(e) });
  } finally {
    unlisten();
  }
}
