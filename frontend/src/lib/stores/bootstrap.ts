import { writable } from "svelte/store";
import { SidecarFailedError, errMsg, whenSidecarReady } from "$lib/api";

// Supervisor boot gate (architecture §3 / first-run-setup §5). The Rust shell
// owns the venv/process lifecycle; here we only mirror "is the engine up yet".
// Outside Tauri (`bun run dev`) whenSidecarReady resolves immediately.
type BootState = "checking" | "ready" | "failed";

export const bootstrap = writable<{ state: BootState; message?: string }>({ state: "checking" });

export async function initBootstrap(): Promise<void> {
  bootstrap.set({ state: "checking" });
  try {
    await whenSidecarReady();
    bootstrap.set({ state: "ready" });
  } catch (e) {
    const message =
      e instanceof SidecarFailedError ? e.message : `Couldn't start the engine. ${errMsg(e)}`;
    bootstrap.set({ state: "failed", message });
  }
}
