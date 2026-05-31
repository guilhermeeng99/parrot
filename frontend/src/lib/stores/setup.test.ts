import { get } from "svelte/store";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { DownloadEvent, SetupStatus } from "$lib/api";

// The setup store drives the first-run gate off /setup/status (plus the SSE
// stream). We mock the IPC layer but keep errMsg/ApiError real so the store's
// gated-vs-failed branch (which sniffs the error message) runs as in prod.
const { getSetupStatus, startDownloadApi, subscribeDownload } = vi.hoisted(() => ({
  getSetupStatus: vi.fn(),
  startDownloadApi: vi.fn(),
  subscribeDownload: vi.fn(),
}));

vi.mock("$lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("$lib/api")>();
  return {
    ...actual,
    getSetupStatus,
    startDownload: startDownloadApi,
    subscribeDownload,
  };
});

// Fresh module per test so the store + its internal `unsubscribe` start clean.
async function loadStore() {
  vi.resetModules();
  return import("./setup");
}

function status(over: Partial<SetupStatus> = {}): SetupStatus {
  return {
    models_ready: false,
    missing: [{ repo_id: "k2-fsa/OmniVoice", label: "OmniVoice" }],
    hf_cache_dir: "C:/cache",
    disk_free_gb: 100,
    min_free_gb: 10,
    enough_disk: true,
    ...over,
  };
}

// subscribeDownload returns an unsubscribe fn and hands the store an onEvent
// callback. We capture that callback so a test can push synthetic SSE events.
let emit: (e: DownloadEvent) => void;
const unsub = vi.fn();

function progress(over: Partial<DownloadEvent> = {}): DownloadEvent {
  return {
    repo_id: "k2-fsa/OmniVoice",
    filename: "model.safetensors",
    downloaded: 0,
    total: 0,
    pct: 0,
    phase: "progress",
    ...over,
  };
}

beforeEach(() => {
  subscribeDownload.mockImplementation(async (onEvent: (e: DownloadEvent) => void) => {
    emit = onEvent;
    return unsub;
  });
});

afterEach(() => {
  getSetupStatus.mockReset();
  startDownloadApi.mockReset();
  subscribeDownload.mockReset();
  unsub.mockReset();
});

describe("checkSetup", () => {
  it("goes ready when the status reports models_ready", async () => {
    const { setup, checkSetup } = await loadStore();
    getSetupStatus.mockResolvedValue(status({ models_ready: true }));
    await checkSetup();
    expect(get(setup).state).toBe("ready");
  });

  it("goes needs_download when models are missing", async () => {
    const { setup, checkSetup } = await loadStore();
    getSetupStatus.mockResolvedValue(status({ models_ready: false }));
    await checkSetup();
    expect(get(setup).state).toBe("needs_download");
  });

  it("goes download_failed when the status call throws", async () => {
    const { setup, checkSetup } = await loadStore();
    getSetupStatus.mockRejectedValue(new Error("connection refused"));
    await checkSetup();
    const s = get(setup);
    expect(s.state).toBe("download_failed");
    expect(s.message).toContain("connection refused");
  });
});

describe("startDownload", () => {
  it("maps a gated (403) failure to needs_token", async () => {
    const { setup, checkSetup, startDownload } = await loadStore();
    getSetupStatus.mockResolvedValue(status());
    await checkSetup(); // seed the repo from missing[0]
    const { ApiError } = await import("$lib/api");
    startDownloadApi.mockRejectedValue(new ApiError("/setup/download", 403, "repo is gated"));
    await startDownload();
    expect(get(setup).state).toBe("needs_token");
    expect(unsub).toHaveBeenCalled();
  });

  it("maps a non-gated failure to download_failed", async () => {
    const { setup, checkSetup, startDownload } = await loadStore();
    getSetupStatus.mockResolvedValue(status());
    await checkSetup();
    const { ApiError } = await import("$lib/api");
    startDownloadApi.mockRejectedValue(new ApiError("/setup/download", 500, "disk exploded"));
    await startDownload();
    expect(get(setup).state).toBe("download_failed");
    expect(unsub).toHaveBeenCalled();
  });

  it("maps an SSE subscription failure to download_failed", async () => {
    const { setup, checkSetup, startDownload } = await loadStore();
    getSetupStatus.mockResolvedValue(status());
    await checkSetup();
    subscribeDownload.mockRejectedValue(new Error("stream refused"));
    await startDownload();
    const s = get(setup);
    expect(s.state).toBe("download_failed");
    expect(s.message).toContain("stream refused");
    expect(startDownloadApi).not.toHaveBeenCalled();
  });
});

describe("SSE-driven verification", () => {
  it("install_done → verifying → ready only when status confirms models_ready", async () => {
    const { setup, checkSetup, startDownload } = await loadStore();
    getSetupStatus.mockResolvedValueOnce(status()); // checkSetup → needs_download
    await checkSetup();
    startDownloadApi.mockResolvedValue({ status: "started", repo_id: "k2-fsa/OmniVoice" });
    await startDownload();

    // A fresh status snapshot confirms the cache landed.
    getSetupStatus.mockResolvedValueOnce(status({ models_ready: true }));
    emit({ ...progress(), phase: "install_done" });
    await vi.waitFor(() => expect(get(setup).state).toBe("ready"));
    expect(unsub).toHaveBeenCalled();
  });

  it("install_done stays download_failed when the verify status is not ready", async () => {
    const { setup, checkSetup, startDownload } = await loadStore();
    getSetupStatus.mockResolvedValueOnce(status());
    await checkSetup();
    startDownloadApi.mockResolvedValue({ status: "started", repo_id: "k2-fsa/OmniVoice" });
    await startDownload();

    getSetupStatus.mockResolvedValueOnce(status({ models_ready: false }));
    emit({ ...progress(), phase: "install_done" });
    await vi.waitFor(() => expect(get(setup).state).toBe("download_failed"));
  });

  it("install_error maps to download_failed with the error message", async () => {
    const { setup, checkSetup, startDownload } = await loadStore();
    getSetupStatus.mockResolvedValueOnce(status());
    await checkSetup();
    startDownloadApi.mockResolvedValue({ status: "started", repo_id: "k2-fsa/OmniVoice" });
    await startDownload();

    emit({ ...progress(), phase: "install_error", error: "network reset" });
    const s = get(setup);
    expect(s.state).toBe("download_failed");
    expect(s.message).toBe("network reset");
    expect(unsub).toHaveBeenCalled();
  });
});
