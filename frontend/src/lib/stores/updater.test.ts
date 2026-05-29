import { get } from "svelte/store";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { UpdateStatus } from "$lib/api";

// Updater store: outside Tauri there is no updater, so checkUpdate must short
// out to up_to_date WITHOUT calling check_for_update. inTauri/checkForUpdate are
// mocked; errMsg stays real so the error branch produces a readable message.
const { inTauri, checkForUpdate } = vi.hoisted(() => ({
  inTauri: vi.fn(),
  checkForUpdate: vi.fn(),
}));

vi.mock("$lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("$lib/api")>();
  return { ...actual, inTauri, checkForUpdate };
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
