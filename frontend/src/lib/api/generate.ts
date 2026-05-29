import { apiPostRaw, apiUrl } from "./client";
import type { GenerateParams, GenerationProgressEvent, GenerationResult } from "./types";

/** The audio post-processing presets the engine accepts for `effect_preset`.
 *  Lives next to the IPC contract so the Speak UI and the server agree on the
 *  exact set (no hardcoded drift). See docs/specs/ipc-contract.md §4. */
export const EFFECT_PRESETS = [
  "broadcast",
  "cinematic",
  "podcast",
  "warm",
  "bright",
  "raw",
] as const;

export type EffectPreset = (typeof EFFECT_PRESETS)[number];

function toForm(params: GenerateParams): FormData {
  const fd = new FormData();
  fd.set("text", params.text);
  const put = (k: string, v: unknown) => {
    if (v !== undefined && v !== null && v !== "") fd.set(k, String(v));
  };
  put("language", params.language);
  put("profile_id", params.profile_id);
  put("speed", params.speed);
  put("seed", params.seed);
  put("num_step", params.num_step);
  put("guidance_scale", params.guidance_scale);
  put("effect_preset", params.effect_preset);
  put("duration", params.duration);
  put("t_shift", params.t_shift);
  put("layer_penalty_factor", params.layer_penalty_factor);
  put("position_temperature", params.position_temperature);
  put("class_temperature", params.class_temperature);
  put("ref_text", params.ref_text);
  put("instruct", params.instruct);
  if (params.denoise !== undefined) fd.set("denoise", String(params.denoise));
  if (params.postprocess_output !== undefined)
    fd.set("postprocess_output", String(params.postprocess_output));
  if (params.ref_audio && !params.profile_id) fd.set("ref_audio", params.ref_audio, "ref.wav");
  return fd;
}

/** POST /generate → reads the X-* metadata headers and the WAV body. The
 *  `signal` is the cancel path (user navigates away mid-generation). */
export async function generateSpeech(
  params: GenerateParams,
  signal?: AbortSignal,
): Promise<GenerationResult> {
  const res = await apiPostRaw("/generate", toForm(params), { signal });
  const buf = new Uint8Array(await res.arrayBuffer());
  const blob = new Blob([buf], { type: "audio/wav" });
  const seedHeader = res.headers.get("X-Seed") ?? "";
  return {
    id: res.headers.get("X-Audio-Id") ?? "",
    audioPath: res.headers.get("X-Audio-Path") ?? "",
    genTime: Number(res.headers.get("X-Gen-Time") ?? 0),
    durationSeconds: Number(res.headers.get("X-Audio-Duration") ?? 0),
    seed: seedHeader === "" ? null : Number(seedHeader),
    url: URL.createObjectURL(blob),
    bytes: buf,
  };
}

/** Subscribe to the in-flight generation's per-step progress (SSE). Returns an
 *  unsubscribe fn. Open it just before `generateSpeech` so the bar catches the
 *  `start` phase; close it when generation settles. `: keepalive` comment lines
 *  carry no `data:` and are ignored by EventSource. See ipc-contract.md §Generate. */
export async function subscribeGenerationProgress(
  onEvent: (e: GenerationProgressEvent) => void,
  onError?: (err: Event) => void,
): Promise<() => void> {
  const url = await apiUrl("/generate/progress-stream");
  const es = new EventSource(url);
  es.onmessage = (msg) => {
    try {
      onEvent(JSON.parse(msg.data) as GenerationProgressEvent);
    } catch {
      // ignore malformed payloads
    }
  };
  if (onError) es.onerror = onError;
  return () => es.close();
}
