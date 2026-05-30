import { apiBase, apiDelete, apiFetch, apiJson, apiPost, apiPut } from "./client";
import type { ProfileUsage, VoiceProfile } from "./types";

export const listProfiles = () => apiJson<VoiceProfile[]>("/profiles");

export const getProfile = (id: string) => apiJson<VoiceProfile>(`/profiles/${id}`);

export interface CreateProfileInput {
  name: string;
  refAudio: Blob;
  refAudioFilename?: string;
  refText?: string;
  instruct?: string;
  language?: string;
  seed?: number | null;
}

export function createProfile(input: CreateProfileInput): Promise<{ id: string; name: string }> {
  const fd = new FormData();
  fd.set("name", input.name);
  fd.set("ref_audio", input.refAudio, input.refAudioFilename ?? "reference.wav");
  fd.set("ref_text", input.refText ?? "");
  fd.set("instruct", input.instruct ?? "");
  fd.set("language", input.language ?? "Auto");
  if (input.seed !== undefined && input.seed !== null) fd.set("seed", String(input.seed));
  return apiPost("/profiles", fd);
}

export interface ProfilePatch {
  name?: string;
  ref_text?: string;
  instruct?: string;
  language?: string;
}

export const updateProfile = (id: string, patch: ProfilePatch) =>
  apiPut<VoiceProfile>(`/profiles/${id}`, patch);

export const deleteProfile = (id: string) => apiDelete<{ deleted: string }>(`/profiles/${id}`);

export const getProfileUsage = (id: string) => apiJson<ProfileUsage>(`/profiles/${id}/usage`);

/** Full URL to a profile's representative audio (for an <audio> element). */
export const profileAudioUrl = (id: string) =>
  apiBase().then((base) => `${base}/profiles/${id}/audio`);

/** The profile's original reference clip as raw bytes (GET /profiles/{id}/audio),
 *  for downloading it to disk in its source format. Throws ApiError on failure. */
export async function profileAudioBytes(id: string): Promise<Uint8Array> {
  const res = await apiFetch(`/profiles/${id}/audio`);
  return new Uint8Array(await res.arrayBuffer());
}

export function lockProfile(id: string, historyId: string, seed?: number | null) {
  const fd = new FormData();
  fd.set("history_id", historyId);
  if (seed !== undefined && seed !== null) fd.set("seed", String(seed));
  return apiPost<{ locked: true; profile_id: string; locked_audio_path: string }>(
    `/profiles/${id}/lock`,
    fd,
  );
}

export const unlockProfile = (id: string) =>
  apiPost<{ unlocked: true; profile_id: string }>(`/profiles/${id}/unlock`);
