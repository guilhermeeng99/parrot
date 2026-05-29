import { get } from "svelte/store";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { GenerationResult } from "$lib/api";

// Synthesis store: generateSpeech is the only IPC dep; loadHistory is the
// post-success refresh. Both are mocked so we exercise the state machine alone.
const { generateSpeech } = vi.hoisted(() => ({ generateSpeech: vi.fn() }));
const { loadHistory } = vi.hoisted(() => ({ loadHistory: vi.fn() }));

vi.mock("$lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("$lib/api")>();
  return { ...actual, generateSpeech };
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
});

afterEach(() => {
  generateSpeech.mockReset();
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
