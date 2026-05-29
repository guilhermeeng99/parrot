import { get, writable } from "svelte/store";
import {
  type GenerateParams,
  type GenerationResult,
  errMsg,
  generateSpeech,
  subscribeGenerationProgress,
} from "$lib/api";
import { loadHistory } from "./history";

// Synthesis request lifecycle (synthesis.md). While a request is in flight the
// store sits in `submitting`; the page stays interactive because inference runs
// off the event loop server-side. The first-ever generation can sit here a while
// (model load + GPU inference).
type SynthState = "idle" | "submitting" | "done" | "error";

export const synthesis = writable<{
  state: SynthState;
  result?: GenerationResult;
  error?: string;
  /** true when the last error was an OOM (UI offers Flush & retry). */
  oom?: boolean;
  /** 0–1 synthesis progress while `submitting` (per-step, from the SSE stream).
   *  Stays 0 during the cold model load, then climbs with the diffusion steps. */
  progress?: number;
}>({ state: "idle" });

// One in-flight request at a time. We keep its controller out of the store
// (it's not UI state) so resetSynthesis() can cancel a navigation-away.
let inFlight: AbortController | null = null;

/** Revoke the previous result's object URL so each new blob doesn't leak. */
function revokePrevious(): void {
  const prev = get(synthesis).result;
  if (prev) URL.revokeObjectURL(prev.url);
}

export async function speak(params: GenerateParams): Promise<void> {
  inFlight?.abort();
  const controller = new AbortController();
  inFlight = controller;

  revokePrevious();
  synthesis.set({ state: "submitting", progress: 0 });

  // Live %-complete from the engine's per-step SSE. Best-effort: if the stream
  // can't open, generation still runs — the bar just stays at "Preparing…".
  // The stream replays a small buffer on connect, which can include the PREVIOUS
  // generation's tail — ignore everything until THIS generation's `start` so the
  // bar doesn't flash to the old 100% before resetting.
  let stopProgress: (() => void) | null = null;
  let sawStart = false;
  try {
    stopProgress = await subscribeGenerationProgress((e) => {
      if (e.phase === "start") sawStart = true;
      if (!sawStart) return; // skip replayed events from a prior generation
      const pct = e.phase === "done" ? 1 : e.pct;
      synthesis.update((s) => (s.state === "submitting" ? { ...s, progress: pct } : s));
    });
  } catch {
    /* no progress stream — fall through with an indeterminate bar */
  }

  try {
    const result = await generateSpeech(params, controller.signal);
    if (controller.signal.aborted) {
      URL.revokeObjectURL(result.url);
      return;
    }
    synthesis.set({ state: "done", result });
    await loadHistory();
  } catch (e) {
    // User-cancel (navigated away / new request) is not an error — stay quiet.
    if (controller.signal.aborted || (e instanceof DOMException && e.name === "AbortError")) return;
    const message = errMsg(e);
    synthesis.set({ state: "error", error: message, oom: /out of memory/i.test(message) });
  } finally {
    stopProgress?.();
    if (inFlight === controller) inFlight = null;
  }
}

export function resetSynthesis(): void {
  inFlight?.abort();
  inFlight = null;
  revokePrevious();
  synthesis.set({ state: "idle" });
}
