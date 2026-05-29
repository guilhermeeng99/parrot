// Bridges the Rust supervisor's sidecar lifecycle to the UI. The supervisor
// (src-tauri/src/supervisor.rs) owns spawn/health/restart and signals the UI:
//   - `sidecar_ready` / `sidecar_failed` commands → latched terminal state
//   - `sidecar-ready` / `sidecar-failed` events    → fast notification
// We query the latched commands AND listen for events, so a missed event (page
// mounted after the fact, or before listeners attached) can never strand the UI.
// Outside Tauri (`bun run dev` in a browser) there is no supervisor, so we
// resolve immediately and let the page probe the dev sidecar directly.

import { inTauri } from "./client";

/** Last resort: if the sidecar is up but never reports healthy, stop waiting
 *  after this long and let the page probe (so its error + Retry stays reachable). */
const READY_TIMEOUT_MS = 90_000;

/** Raised when the supervisor reports the sidecar permanently failed to start. */
export class SidecarFailedError extends Error {
  constructor() {
    super("The voice engine failed to start. Check the logs and restart Parrot.");
    this.name = "SidecarFailedError";
  }
}

/**
 * Resolve once the supervisor reports the sidecar healthy (or the wait times
 * out). Rejects with `SidecarFailedError` if the supervisor gave up. No-op
 * (resolves immediately) outside Tauri.
 */
export async function whenSidecarReady(): Promise<void> {
  if (!inTauri()) return;

  const { invoke } = await import("@tauri-apps/api/core");

  // Terminal states are latched in Rust — a single query is race-proof.
  if (await invoke<boolean>("sidecar_failed")) throw new SidecarFailedError();
  if (await invoke<boolean>("sidecar_ready")) return;

  const { listen } = await import("@tauri-apps/api/event");
  await new Promise<void>((resolve, reject) => {
    let settled = false;
    const unlisten: Array<() => void> = [];
    const stop = () => {
      settled = true;
      clearInterval(poll);
      clearTimeout(timer);
      for (const u of unlisten) u();
    };
    const succeed = () => {
      if (settled) return;
      stop();
      resolve();
    };
    const fail = () => {
      if (settled) return;
      stop();
      reject(new SidecarFailedError());
    };

    const track = (u: () => void) => (settled ? u() : unlisten.push(u));
    void listen("sidecar-ready", succeed).then(track);
    void listen("sidecar-failed", fail).then(track);

    // Safety net: poll both latched states in case an event fired before we
    // subscribed.
    const poll = setInterval(async () => {
      if (settled) return;
      if (await invoke<boolean>("sidecar_failed")) return fail();
      if (await invoke<boolean>("sidecar_ready")) return succeed();
    }, 400);

    // Never hang the UI: fall through to a probe if readiness never arrives.
    const timer = setTimeout(succeed, READY_TIMEOUT_MS);
  });
}
