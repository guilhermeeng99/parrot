import { writable } from "svelte/store";
import { checkForUpdate, errMsg, inTauri, installUpdate } from "$lib/api";

// Updater store (packaging.md). Client-rendered (dialog:false). Outside Tauri
// there is no updater, so checks resolve to up_to_date rather than nagging.
type UpdState = "idle" | "checking" | "up_to_date" | "available" | "downloading" | "error";

export const updater = writable<{
  state: UpdState;
  version?: string;
  notes?: string;
  error?: string;
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
  updater.update((s) => ({ ...s, state: "downloading" }));
  try {
    await installUpdate(); // downloads, verifies the signature, relaunches
  } catch (e) {
    updater.set({ state: "error", error: errMsg(e) });
  }
}
