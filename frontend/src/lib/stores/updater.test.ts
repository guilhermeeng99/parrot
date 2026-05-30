import { get } from "svelte/store";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { UpdateProgress, UpdateStatus } from "$lib/api";

// Updater store: outside Tauri there is no updater, so checkUpdate must short
// out to up_to_date WITHOUT calling check_for_update. The IPC surface is mocked;
// errMsg stays real so the error branches produce a readable message.
const { inTauri, checkForUpdate, onUpdateProgress, installUpdate, getAppVersion } = vi.hoisted(
  () => ({
    inTauri: vi.fn(),
    checkForUpdate: vi.fn(),
    onUpdateProgress: vi.fn(),
    installUpdate: vi.fn(),
    getAppVersion: vi.fn(),
  }),
);

vi.mock("$lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("$lib/api")>();
  return { ...actual, inTauri, checkForUpdate, onUpdateProgress, installUpdate, getAppVersion };
});

async function loadStore() {
  vi.resetModules();
  return import("./updater");
}

function update(over: Partial<UpdateStatus> = {}): UpdateStatus {
  return { available: false, ...over };
}

afterEach(() => {
  inTauri.mockReset();
  checkForUpdate.mockReset();
  onUpdateProgress.mockReset();
  installUpdate.mockReset();
  getAppVersion.mockReset();
});

describe("checkUpdate", () => {
  it("resolves to up_to_date outside Tauri without probing for an update", async () => {
    const { updater, checkUpdate } = await loadStore();
    inTauri.mockReturnValue(false);
    await checkUpdate();
    expect(get(updater).state).toBe("up_to_date");
    expect(checkForUpdate).not.toHaveBeenCalled();
  });

  it("surfaces an available update with its version and notes", async () => {
    const { updater, checkUpdate } = await loadStore();
    inTauri.mockReturnValue(true);
    checkForUpdate.mockResolvedValue(update({ available: true, version: "1.2.0", notes: "Fixes" }));
    await checkUpdate();
    const s = get(updater);
    expect(s.state).toBe("available");
    expect(s.version).toBe("1.2.0");
    expect(s.notes).toBe("Fixes");
  });

  it("reports up_to_date when no update is available", async () => {
    const { updater, checkUpdate } = await loadStore();
    inTauri.mockReturnValue(true);
    checkForUpdate.mockResolvedValue(update({ available: false }));
    await checkUpdate();
    expect(get(updater).state).toBe("up_to_date");
  });

  it("goes error (non-fatal) when the check throws", async () => {
    const { updater, checkUpdate } = await loadStore();
    inTauri.mockReturnValue(true);
    checkForUpdate.mockRejectedValue(new Error("no network"));
    await checkUpdate();
    const s = get(updater);
    expect(s.state).toBe("error");
    expect(s.error).toContain("no network");
  });
});

describe("applyUpdate", () => {
  it("streams download progress, then unlistens after install", async () => {
    const { updater, applyUpdate } = await loadStore();
    let emitProgress!: (p: UpdateProgress) => void;
    const unlisten = vi.fn();
    onUpdateProgress.mockImplementation(async (cb: (p: UpdateProgress) => void) => {
      emitProgress = cb;
      return unlisten;
    });
    let finishInstall!: () => void;
    installUpdate.mockImplementation(() => new Promise<void>((r) => (finishInstall = r)));

    const p = applyUpdate();
    await vi.waitFor(() => expect(emitProgress).toBeTypeOf("function"));
    expect(get(updater).state).toBe("downloading");

    emitProgress({ downloaded: 50, total: 100, done: false });
    expect(get(updater).progress).toEqual({ downloaded: 50, total: 100 });

    finishInstall();
    await p;
    expect(unlisten).toHaveBeenCalled(); // listener always cleaned up
  });

  it("goes error and still unlistens when the install fails", async () => {
    const { updater, applyUpdate } = await loadStore();
    const unlisten = vi.fn();
    onUpdateProgress.mockResolvedValue(unlisten);
    installUpdate.mockRejectedValue(new Error("signature invalid"));

    await applyUpdate();
    const s = get(updater);
    expect(s.state).toBe("error");
    expect(s.error).toContain("signature invalid");
    expect(unlisten).toHaveBeenCalled();
  });
});

describe("loadAppVersion", () => {
  it("fetches the version once per session", async () => {
    const { appVersion, loadAppVersion } = await loadStore();
    getAppVersion.mockResolvedValue("0.0.9");
    await loadAppVersion();
    await loadAppVersion(); // second call must NOT re-fetch
    expect(get(appVersion)).toBe("0.0.9");
    expect(getAppVersion).toHaveBeenCalledOnce();
  });
});
