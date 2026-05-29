import { writable } from "svelte/store";
import { getEngineStatus } from "$lib/api";

// Device awareness (device-detection.md). Populated once per sidecar lifetime;
// cpu_only is a valid terminal state (slower, not an error).
type DeviceState = "unknown" | "resolving" | "accelerated" | "cpu_only" | "error";

export const device = writable<{
  state: DeviceState;
  device?: "cuda" | "cpu";
  label?: string;
}>({ state: "unknown" });

export async function loadDevice(): Promise<void> {
  device.set({ state: "resolving" });
  try {
    const s = await getEngineStatus();
    device.set({
      state: s.device === "cuda" ? "accelerated" : "cpu_only",
      device: s.device,
      label: s.device_label,
    });
  } catch {
    device.set({ state: "error" });
  }
}
