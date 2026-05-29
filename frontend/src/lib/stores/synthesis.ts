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

  // Live %-complete from the engine's per-step SSE. Best-effort and FIRE-AND-FORGET:
  // we must NOT await the subscribe, or generateSpeech() would start a microtask
  // later than the caller (and the abort signal) expects. If the stream can't
  // open, generation still runs — the bar just stays at "Preparing…".
  // The stream replays a small buffer on connect, which can include the PREVIOUS
  // generation's tail — ignore everything until THIS generation's `start` so the
  // bar doesn't flash to the old 100% before resetting.
  // Default to a no-op so the finally can always call it; the real unsubscribe
  // replaces it once the SSE stream connects (assigned in the .then below).
  let stopProgress: () => void = () => {};
  let settled = false; // set in finally — guards a subscribe that resolves too late
  let sawStart = false;
  subscribeGenerationProgress((e) => {
    if (e.phase === "start") sawStart = true;
    if (!sawStart) return; // skip replayed events from a prior generation
    if (e.phase === "done") {
      synthesis.update((s) => (s.state === "submitting" ? { ...s, progress: 1 } : s));
      return;
    }
    if (e.phase === "error") return; // let speak()'s catch drive the error UI, not the bar
    // Monotonic: never let a late/low pct snap the bar backwards.
    synthesis.update((s) =>
      s.state === "submitting" ? { ...s, progress: Math.max(s.progress ?? 0, e.pct) } : s,
    );
  })
    .then((stop) => {
      // The request may already have settled by the time the stream connects;
      // if so, close it immediately so we never leak the EventSource.
      if (settled) stop();
      else stopProgress = stop;
    })
    .catch(() => {
      /* no progress stream — indeterminate bar; generation continues regardless */
    });

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
    settled = true;
    stopProgress();
    if (inFlight === controller) inFlight = null;
  }
}

export function resetSynthesis(): void {
  inFlight?.abort();
  inFlight = null;
  revokePrevious();
  synthesis.set({ state: "idle" });
}
