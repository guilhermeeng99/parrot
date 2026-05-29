import { writable } from "svelte/store";
import {
  SidecarFailedError,
  bootstrapStatus,
  cleanAndRetryBootstrap,
  errMsg,
  getBootstrapLogs,
  inTauri,
  retryBootstrap,
  whenSidecarReady,
} from "$lib/api";

// Supervisor boot gate (architecture §3 / first-run-setup §5). The Rust shell
// owns the venv/process lifecycle; here we mirror "is the engine up yet" AND the
// supervisor's current stage + log tail (ipc-contract §11) so the splash can
// show progress and the failed screen can retry. Outside Tauri (`bun run dev`)
// whenSidecarReady resolves immediately and the stage/log commands are no-ops.
type BootState = "checking" | "ready" | "failed";

/** Maps the supervisor's raw stage tokens to splash copy. */
const STAGE_LABELS: Record<string, string> = {
  creating_venv: "Setting up Parrot's engine (first run only)…",
  installing_deps: "Installing engine dependencies (first run only)…",
  starting_backend: "Starting the voice engine…",
  checking: "Starting Parrot's engine…",
  ready: "Ready.",
  failed: "The engine failed to start.",
};

export const bootstrap = writable<{
  state: BootState;
  message?: string;
  /** Human label for the current supervisor stage (splash subtitle). */
  stage?: string;
  /** Tail of the boot log, shown on the failed screen for diagnostics. */
  logs?: string[];
}>({ state: "checking" });

let stagePoll: ReturnType<typeof setInterval> | null = null;

/** Poll the supervisor's stage while we wait, so the splash isn't a blank
 *  spinner during the (slow) first-run venv create + dependency install. */
function startStagePolling(): void {
  if (!inTauri() || stagePoll) return;
  const tick = async () => {
    try {
      const stage = await bootstrapStatus();
      bootstrap.update((s) => (s.state === "checking" ? { ...s, stage: STAGE_LABELS[stage] } : s));
    } catch {
      // Command missing / shutting down — stop nagging.
      stopStagePolling();
    }
  };
  void tick();
  stagePoll = setInterval(tick, 600);
}

function stopStagePolling(): void {
  if (stagePoll) clearInterval(stagePoll);
  stagePoll = null;
}

export async function initBootstrap(): Promise<void> {
  bootstrap.set({ state: "checking", stage: STAGE_LABELS.checking });
  startStagePolling();
  try {
    await whenSidecarReady();
    stopStagePolling();
    bootstrap.set({ state: "ready" });
  } catch (e) {
    stopStagePolling();
    const message =
      e instanceof SidecarFailedError ? e.message : `Couldn't start the engine. ${errMsg(e)}`;
    // Best-effort log tail for the failed screen; absence must not mask the error.
    let logs: string[] | undefined;
    try {
      logs = await getBootstrapLogs();
    } catch {
      logs = undefined;
    }
    bootstrap.set({ state: "failed", message, logs });
  }
}

/** Ask the supervisor to re-run the spawn sequence, then re-gate on readiness.
 *  `clean` first wipes the bootstrapped venv (for a corrupt first-run install). */
export async function retry(clean = false): Promise<void> {
  if (inTauri()) {
    try {
      await (clean ? cleanAndRetryBootstrap() : retryBootstrap());
    } catch {
      // Fall through to initBootstrap, which surfaces a fresh failure if any.
    }
  }
  await initBootstrap();
}
