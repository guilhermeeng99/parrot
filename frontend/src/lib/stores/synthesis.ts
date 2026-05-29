import { writable } from "svelte/store";
import { type GenerateParams, type GenerationResult, errMsg, generateSpeech } from "$lib/api";
import { loadHistory } from "./history";

// Synthesis request lifecycle (synthesis.md). The first-ever generation may sit
// in `submitting` while the model loads (inference blocks on the GPU pool); the
// page stays interactive because inference runs off the event loop server-side.
type SynthState = "idle" | "submitting" | "generating" | "done" | "error";

export const synthesis = writable<{
  state: SynthState;
  result?: GenerationResult;
  error?: string;
  /** true when the last error was an OOM (UI offers Flush & retry). */
  oom?: boolean;
}>({ state: "idle" });

export async function speak(params: GenerateParams): Promise<void> {
  synthesis.set({ state: "submitting" });
  try {
    const result = await generateSpeech(params);
    synthesis.set({ state: "done", result });
    await loadHistory();
  } catch (e) {
    const message = errMsg(e);
    synthesis.set({ state: "error", error: message, oom: /out of memory/i.test(message) });
  }
}

export function resetSynthesis(): void {
  synthesis.set({ state: "idle" });
}
