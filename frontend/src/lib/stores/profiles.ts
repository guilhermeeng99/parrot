import { writable } from "svelte/store";
import {
  type CreateProfileInput,
  type ProfilePatch,
  type VoiceProfile,
  createProfile,
  deleteProfile,
  errMsg,
  listProfiles,
  lockProfile,
  unlockProfile,
  updateProfile,
} from "$lib/api";
import { toasts } from "./toasts";

// Library store (voice-profiles §4.1): idle → loading → loaded ↘ error.
// On error the last good list stays visible (stale-but-usable); refresh() is
// silent (no loading flash). Cross-tab freshness = plain re-fetch.
type LibState = "idle" | "loading" | "loaded" | "error";

interface State {
  state: LibState;
  profiles: VoiceProfile[];
  error?: string;
}

const store = writable<State>({ state: "idle", profiles: [] });
export const profiles = { subscribe: store.subscribe };

export async function loadProfiles(): Promise<void> {
  store.update((s) => ({ ...s, state: s.profiles.length ? s.state : "loading" }));
  try {
    const list = await listProfiles();
    store.set({ state: "loaded", profiles: list });
  } catch (e) {
    store.update((s) => ({ ...s, state: "error", error: errMsg(e) }));
  }
}

/** Background re-fetch — keeps the list visible, surfaces failures as a toast. */
export async function refreshProfiles(): Promise<void> {
  try {
    const list = await listProfiles();
    store.update((s) => ({ ...s, state: "loaded", profiles: list, error: undefined }));
  } catch (e) {
    toasts.error(`Couldn't refresh voices: ${errMsg(e)}`);
  }
}

export async function createVoice(input: CreateProfileInput): Promise<boolean> {
  try {
    const { name } = await createProfile(input);
    toasts.success(`Saved '${name}'`);
    await refreshProfiles();
    return true;
  } catch (e) {
    toasts.error(errMsg(e));
    return false;
  }
}

export async function editProfile(id: string, patch: ProfilePatch): Promise<boolean> {
  try {
    await updateProfile(id, patch);
    await refreshProfiles();
    return true;
  } catch (e) {
    toasts.error(errMsg(e));
    return false;
  }
}

export async function removeProfile(id: string): Promise<boolean> {
  // Optimistic removal; re-fetch reconciles. On failure the refresh restores it.
  store.update((s) => ({ ...s, profiles: s.profiles.filter((p) => p.id !== id) }));
  try {
    await deleteProfile(id);
    toasts.success("Voice deleted");
    return true;
  } catch (e) {
    toasts.error(errMsg(e));
    await refreshProfiles();
    return false;
  }
}

export async function lock(id: string, historyId: string, seed?: number | null): Promise<boolean> {
  try {
    await lockProfile(id, historyId, seed);
    toasts.success("Locked this take as the voice's reference");
    await refreshProfiles();
    return true;
  } catch (e) {
    toasts.error(errMsg(e));
    return false;
  }
}

export async function unlock(id: string): Promise<boolean> {
  try {
    await unlockProfile(id);
    toasts.success("Reverted to the original clone");
    await refreshProfiles();
    return true;
  } catch (e) {
    toasts.error(errMsg(e));
    return false;
  }
}
