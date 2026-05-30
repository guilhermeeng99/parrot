import { get } from "svelte/store";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { GenerationProgressEvent, GenerationResult } from "$lib/api";

// Synthesis store: generateSpeech + subscribeGenerationProgress are the IPC deps;
// loadHistory is the post-success refresh. All mocked so we exercise the state
// machine (incl. the SSE progress wiring) alone.
const { generateSpeech, subscribeGenerationProgress } = vi.hoisted(() => ({
  generateSpeech: vi.fn(),
  subscribeGenerationProgress: vi.fn(),
}));
const { loadHistory } = vi.hoisted(() => ({ loadHistory: vi.fn() }));

vi.mock("$lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("$lib/api")>();
  return { ...actual, generateSpeech, subscribeGenerationProgress };
});
vi.mock("./history", () => ({ loadHistory }));

async function loadStore() {
  vi.resetModules();
  return import("./synthesis");
}

function result(over: Partial<GenerationResult> = {}): GenerationResult {
  return {
    id: "gen_1",
    audioPath: "outputs/gen_1.wav",
    genTime: 1.2,
    durationSeconds: 3.4,
    seed: 42,
    url: "blob:fake",
    bytes: new Uint8Array(),
    ...over,
  };
}

// node's global URL has no object-URL helpers; add them without clobbering the
// constructor (the IPC barrel uses `new URL(...)` at import time).
const createObjectURL = vi.fn(() => "blob:fake");
const revokeObjectURL = vi.fn();

beforeEach(() => {
  (URL as unknown as { createObjectURL: unknown }).createObjectURL = createObjectURL;
  (URL as unknown as { revokeObjectURL: unknown }).revokeObjectURL = revokeObjectURL;
  loadHistory.mockResolvedValue(undefined);
  // Default: a no-op progress stream (resolves to a no-op stop) so the tests that
  // don't care about progress don't trip on `.then` of an unmocked return.
  subscribeGenerationProgress.mockImplementation(async () => () => {});
});

afterEach(() => {
  generateSpeech.mockReset();
  subscribeGenerationProgress.mockReset();
  loadHistory.mockReset();
  createObjectURL.mockReset();
  revokeObjectURL.mockReset();
});

describe("speak", () => {
  it("resolves to done with the result and refreshes history", async () => {
    const { synthesis, speak } = await loadStore();
    const r = result();
    generateSpeech.mockResolvedValue(r);
    await speak({ text: "hi" });
    const s = get(synthesis);
    expect(s.state).toBe("done");
    expect(s.result).toEqual(r);
    expect(loadHistory).toHaveBeenCalledOnce();
  });

  it("flags oom when the failure mentions out of memory", async () => {
    const { synthesis, speak } = await loadStore();
    generateSpeech.mockRejectedValue(new Error("CUDA out of memory"));
    await speak({ text: "hi" });
    const s = get(synthesis);
    expect(s.state).toBe("error");
    expect(s.oom).toBe(true);
    expect(loadHistory).not.toHaveBeenCalled();
  });

  it("does not flag oom for a generic failure", async () => {
    const { synthesis, speak } = await loadStore();
    generateSpeech.mockRejectedValue(new Error("something else broke"));
    await speak({ text: "hi" });
    const s = get(synthesis);
    expect(s.state).toBe("error");
    expect(s.oom).toBe(false);
  });
});

describe("progress (SSE)", () => {
  it("gates on start, advances monotonically, and stops the stream when settled", async () => {
    const { synthesis, speak } = await loadStore();
    let onEvent!: (e: GenerationProgressEvent) => void;
    const stop = vi.fn();
    subscribeGenerationProgress.mockImplementation(
      async (cb: (e: GenerationProgressEvent) => void) => {
        onEvent = cb;
        return stop;
      },
    );
    // Hold generateSpeech open so we can drive progress events mid-flight.
    let finish!: (r: GenerationResult) => void;
    generateSpeech.mockImplementation(() => new Promise<GenerationResult>((r) => (finish = r)));

    const p = speak({ text: "hi" });
    await vi.waitFor(() => expect(onEvent).toBeTypeOf("function"));

    // A replayed pre-start event from a prior generation is ignored (sawStart gate).
    onEvent({ phase: "step", step: 9, total: 10, pct: 0.9 });
    expect(get(synthesis).progress).toBe(0);

    onEvent({ phase: "start", step: 0, total: 10, pct: 0 });
    onEvent({ phase: "step", step: 5, total: 10, pct: 0.5 });
    expect(get(synthesis).progress).toBe(0.5);

    // Monotonic: a lower late pct must not snap the bar backwards.
    onEvent({ phase: "step", step: 3, total: 10, pct: 0.3 });
    expect(get(synthesis).progress).toBe(0.5);

    finish(result());
    await p;
    expect(get(synthesis).state).toBe("done");
    expect(stop).toHaveBeenCalled(); // EventSource closed in finally (no leak)
  });
});

describe("resetSynthesis", () => {
  it("aborts the in-flight controller and returns to idle", async () => {
    const { synthesis, speak, resetSynthesis } = await loadStore();
    // Capture the signal generateSpeech receives so we can assert it aborts.
    let captured: AbortSignal | undefined;
    generateSpeech.mockImplementation(
      (_params: unknown, signal?: AbortSignal) =>
        new Promise<GenerationResult>(() => {
          captured = signal; // never resolves — simulates an in-flight request
        }),
    );
    const pending = speak({ text: "hi" });
    // Let speak() reach the awaited generateSpeech call.
    await Promise.resolve();
    expect(captured?.aborted).toBe(false);

    resetSynthesis();
    expect(captured?.aborted).toBe(true);
    expect(get(synthesis).state).toBe("idle");
    void pending;
  });
});
