import { writable } from "svelte/store";

// App-shell navigation. Parrot has three modes (Clone / Speak / Settings) plus
// a profile-detail sheet. No router — mode is shared state (a desktop window).
export type Mode = "clone" | "speak" | "settings";

export const mode = writable<Mode>("clone");
/** Profile preselected when arriving at Speak via a VoiceCard's "Speak with this". */
export const preselectedProfile = writable<string>("");
/** The profile whose detail sheet is open, or null. */
export const openProfileId = writable<string | null>(null);

export function speakWith(id: string): void {
  preselectedProfile.set(id);
  mode.set("speak");
}

export function openProfile(id: string): void {
  openProfileId.set(id);
}

export function closeProfile(): void {
  openProfileId.set(null);
}
