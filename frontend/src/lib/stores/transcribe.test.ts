import { get } from "svelte/store";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { TranscribeDownloadEvent, TranscribeStatus } from "$lib/api";

// The transcribe store drives model selection, the download SSE machine, and the
// auto-fire transcription. We mock the IPC layer but keep errMsg/ApiError real.
const {
  getTranscribeStatus,
  startTranscribeDownload,
  subscribeTranscribeDownload,
  transcribeReference,
} = vi.hoisted(() => ({
  getTranscribeStatus: vi.fn(),
  startTranscribeDownload: vi.fn(),
  subscribeTranscribeDownload: vi.fn(),
  transcribeReference: vi.fn(),
}));

vi.mock("$lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("$lib/api")>();
  return {
    ...actual,
    getTranscribeStatus,
    startTranscribeDownload,
    subscribeTranscribeDownload,
    transcribeReference,
  };
});

async function loadStore() {
  vi.resetModules();
  return import("./transcribe");
}

function status(over: Partial<TranscribeStatus> = {}): TranscribeStatus {
  return {
    models: [
      { id: "small", label: "Small", size_mb: 470, downloaded: false },
      { id: "large-v3", label: "Large v3 (max fidelity)", size_mb: 3100, downloaded: false },
    ],
    default_model: "large-v3",
    device: "cpu",
    gpu: false,
    ...over,
  };
}

const readyStatus = () =>
  status({
    models: [
      { id: "small", label: "Small", size_mb: 470, downloaded: false },
      { id: "large-v3", label: "Large v3 (max fidelity)", size_mb: 3100, downloaded: true },
    ],
  });

let emit: (e: TranscribeDownloadEvent) => void;
const unsub = vi.fn();

function ev(over: Partial<TranscribeDownloadEvent> = {}): TranscribeDownloadEvent {
  return {
    model: "large-v3",
    filename: "large-v3.pt",
    downloaded: 0,
    total: 0,
    pct: 0,
    phase: "progress",
    ...over,
  };
}

beforeEach(() => {
  subscribeTranscribeDownload.mockImplementation(
    async (onEvent: (e: TranscribeDownloadEvent) => void) => {
      emit = onEvent;
      return unsub;
    },
  );
});

afterEach(() => {
  getTranscribeStatus.mockReset();
  startTranscribeDownload.mockReset();
  subscribeTranscribeDownload.mockReset();
  transcribeReference.mockReset();
  unsub.mockReset();
});

describe("loadTranscribeStatus", () => {
  it("seeds the selected model from the catalog default", async () => {
    const { transcribe, loadTranscribeStatus } = await loadStore();
    getTranscribeStatus.mockResolvedValue(status());
    await loadTranscribeStatus();
    expect(get(transcribe).selectedModel).toBe("large-v3");
  });
});

describe("downloadModel → verify", () => {
  it("install_done re-fetches status and marks the model downloaded", async () => {
    const { transcribe, loadTranscribeStatus, downloadModel } = await loadStore();
    getTranscribeStatus.mockResolvedValueOnce(status());
    await loadTranscribeStatus();
    startTranscribeDownload.mockResolvedValue({ status: "download_started", model: "large-v3" });
    await downloadModel();

    getTranscribeStatus.mockResolvedValueOnce(readyStatus());
    emit(ev({ phase: "install_done" }));

    await vi.waitFor(() => expect(get(transcribe).download.state).toBe("idle"));
    expect(get(transcribe).status?.models.find((m) => m.id === "large-v3")?.downloaded).toBe(true);
    expect(unsub).toHaveBeenCalled();
  });

  it("progress updates pct; install_error → failed with the message", async () => {
    const { transcribe, loadTranscribeStatus, downloadModel } = await loadStore();
    getTranscribeStatus.mockResolvedValueOnce(status());
    await loadTranscribeStatus();
    startTranscribeDownload.mockResolvedValue({ status: "download_started", model: "large-v3" });
    await downloadModel();

    emit(ev({ phase: "progress", pct: 0.5 }));
    expect(get(transcribe).download.pct).toBe(0.5);

    emit(ev({ phase: "install_error", error: "network reset" }));
    const d = get(transcribe).download;
    expect(d.state).toBe("failed");
    expect(d.message).toBe("network reset");
  });
});

describe("runTranscription", () => {
  it("no-ops (returns null) when the selected model isn't downloaded", async () => {
    const { transcribe, loadTranscribeStatus, runTranscription } = await loadStore();
    getTranscribeStatus.mockResolvedValue(status());
    await loadTranscribeStatus();
    const out = await runTranscription(new Blob(["x"]), "ref.webm", "Auto");
    expect(out).toBeNull();
    expect(transcribeReference).not.toHaveBeenCalled();
    expect(get(transcribe).transcription.state).toBe("idle");
  });

  it("fills the transcript when the model is ready", async () => {
    const { transcribe, loadTranscribeStatus, runTranscription } = await loadStore();
    getTranscribeStatus.mockResolvedValue(readyStatus());
    await loadTranscribeStatus();
    transcribeReference.mockResolvedValue({ text: "olá mundo", language: "pt", model: "large-v3" });
    const out = await runTranscription(new Blob(["x"]), "ref.webm", "Portuguese");
    expect(out).toBe("olá mundo");
    expect(get(transcribe).transcription).toMatchObject({ state: "done", text: "olá mundo" });
    expect(transcribeReference).toHaveBeenCalledWith(
      expect.objectContaining({ model: "large-v3", language: "Portuguese" }),
    );
  });

  it("surfaces an error and returns null when the transcribe call fails", async () => {
    const { transcribe, loadTranscribeStatus, runTranscription } = await loadStore();
    getTranscribeStatus.mockResolvedValue(readyStatus());
    await loadTranscribeStatus();
    const { ApiError } = await import("$lib/api");
    transcribeReference.mockRejectedValue(new ApiError("/transcribe", 500, "boom"));
    const out = await runTranscription(new Blob(["x"]), "ref.webm", "Auto");
    expect(out).toBeNull();
    expect(get(transcribe).transcription.state).toBe("error");
  });
});

describe("shouldSeedTranscript (SM-3 no-clobber)", () => {
  it("seeds an empty field", async () => {
    const { shouldSeedTranscript } = await loadStore();
    expect(shouldSeedTranscript("", null)).toBe(true);
  });

  it("re-seeds a field still equal to the previous auto-fill", async () => {
    const { shouldSeedTranscript } = await loadStore();
    expect(shouldSeedTranscript("old auto-fill", "old auto-fill")).toBe(true);
  });

  it("preserves a value the user hand-edited", async () => {
    const { shouldSeedTranscript } = await loadStore();
    expect(shouldSeedTranscript("my correction", "old auto-fill")).toBe(false);
  });
});
