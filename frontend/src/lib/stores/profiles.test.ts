import { get } from "svelte/store";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { VoiceProfile } from "$lib/api";

// Library store: mock every profile IPC call; keep errMsg real so toast copy is
// derived as in prod. toasts is mocked to a no-op (not under test here).
const api = vi.hoisted(() => ({
  listProfiles: vi.fn(),
  createProfile: vi.fn(),
  updateProfile: vi.fn(),
  deleteProfile: vi.fn(),
  lockProfile: vi.fn(),
  unlockProfile: vi.fn(),
}));

vi.mock("$lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("$lib/api")>();
  return { ...actual, ...api };
});
vi.mock("./toasts", () => ({
  toasts: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

async function loadStore() {
  vi.resetModules();
  return import("./profiles");
}

function profile(id: string, over: Partial<VoiceProfile> = {}): VoiceProfile {
  return {
    id,
    name: `Voice ${id}`,
    ref_audio_path: `${id}.wav`,
    ref_text: "",
    language: "Auto",
    instruct: "",
    locked_audio_path: "",
    seed: null,
    is_locked: 0,
    created_at: 0,
    ...over,
  };
}

afterEach(() => {
  for (const fn of Object.values(api)) fn.mockReset();
});

describe("removeProfile", () => {
  it("drops the id optimistically and keeps it gone when delete succeeds", async () => {
    const { profiles, loadProfiles, removeProfile } = await loadStore();
    api.listProfiles.mockResolvedValue([profile("a"), profile("b")]);
    await loadProfiles();
    api.deleteProfile.mockResolvedValue({ deleted: "a" });

    const ok = await removeProfile("a");
    expect(ok).toBe(true);
    expect(get(profiles).profiles.map((p) => p.id)).toEqual(["b"]);
    // success path must not re-fetch
    expect(api.listProfiles).toHaveBeenCalledTimes(1);
  });

  it("re-fetches to restore the profile when delete rejects, returning false", async () => {
    const { profiles, loadProfiles, removeProfile } = await loadStore();
    api.listProfiles.mockResolvedValue([profile("a"), profile("b")]);
    await loadProfiles();
    api.deleteProfile.mockRejectedValue(new Error("locked by another op"));
    // refreshProfiles() re-fetches the full (still-intact) server list.
    api.listProfiles.mockResolvedValue([profile("a"), profile("b")]);

    const ok = await removeProfile("a");
    expect(ok).toBe(false);
    expect(get(profiles).profiles.map((p) => p.id)).toEqual(["a", "b"]);
    expect(api.listProfiles).toHaveBeenCalledTimes(2); // initial load + restore
  });
});

describe("mutations refresh on success", () => {
  it("createVoice refreshes and returns true", async () => {
    const { createVoice } = await loadStore();
    api.createProfile.mockResolvedValue({ id: "a", name: "Voice a" });
    api.listProfiles.mockResolvedValue([profile("a")]);

    const ok = await createVoice({ name: "Voice a", refAudio: new Blob() });
    expect(ok).toBe(true);
    expect(api.listProfiles).toHaveBeenCalledOnce();
  });

  it("createVoice returns false without refreshing on failure", async () => {
    const { createVoice } = await loadStore();
    api.createProfile.mockRejectedValue(new Error("name taken"));

    const ok = await createVoice({ name: "dup", refAudio: new Blob() });
    expect(ok).toBe(false);
    expect(api.listProfiles).not.toHaveBeenCalled();
  });

  it("editProfile refreshes and returns true", async () => {
    const { editProfile } = await loadStore();
    api.updateProfile.mockResolvedValue(profile("a", { name: "Renamed" }));
    api.listProfiles.mockResolvedValue([profile("a", { name: "Renamed" })]);

    const ok = await editProfile("a", { name: "Renamed" });
    expect(ok).toBe(true);
    expect(api.listProfiles).toHaveBeenCalledOnce();
  });

  it("lock refreshes and returns true", async () => {
    const { lock } = await loadStore();
    api.lockProfile.mockResolvedValue({ locked: true, profile_id: "a", locked_audio_path: "x.wav" });
    api.listProfiles.mockResolvedValue([profile("a", { is_locked: 1 })]);

    const ok = await lock("a", "hist_1", 7);
    expect(ok).toBe(true);
    expect(api.lockProfile).toHaveBeenCalledWith("a", "hist_1", 7);
    expect(api.listProfiles).toHaveBeenCalledOnce();
  });

  it("unlock refreshes and returns true", async () => {
    const { unlock } = await loadStore();
    api.unlockProfile.mockResolvedValue({ unlocked: true, profile_id: "a" });
    api.listProfiles.mockResolvedValue([profile("a", { is_locked: 0 })]);

    const ok = await unlock("a");
    expect(ok).toBe(true);
    expect(api.listProfiles).toHaveBeenCalledOnce();
  });
});
