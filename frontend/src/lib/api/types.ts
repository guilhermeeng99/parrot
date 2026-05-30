// Request/response shapes for the frontend↔sidecar boundary. These mirror
// docs/specs/ipc-contract.md field-for-field — a change here MUST land with the
// matching router change in the same commit (the binding rule).

export interface Health {
  status: "ok";
}

export interface EngineStatus {
  /** Always "omnivoice" — Parrot ships one fixed engine. */
  active: "omnivoice";
  /** Auto-detected compute device. */
  device: "cuda" | "cpu";
  /** Optional human label, e.g. "GPU (CUDA) — RTX 4090". */
  device_label?: string;
}

export interface VoiceProfile {
  id: string;
  name: string;
  ref_audio_path: string;
  ref_text: string;
  language: string;
  instruct: string;
  locked_audio_path: string;
  seed: number | null;
  is_locked: 0 | 1;
  created_at: number;
}

export interface HistoryRow {
  id: string;
  text: string;
  language: string;
  profile_id: string | null;
  audio_path: string;
  duration_seconds: number;
  generation_time: number;
  seed: number | null;
  created_at: number;
}

/** A row in a profile's usage list (subset of HistoryRow). */
export interface UsageRow {
  id: string;
  text: string;
  audio_path: string;
  created_at: number;
  generation_time: number;
}

export interface ProfileUsage {
  synth_recent: UsageRow[];
  synth_total: number;
}

/** Metadata read off the /generate streaming response headers. */
export interface GenerationResult {
  id: string;
  audioPath: string;
  genTime: number;
  durationSeconds: number;
  seed: number | null;
  /** Object URL for the returned WAV blob (playable / downloadable). */
  url: string;
  /** The raw WAV bytes (for export to disk via the native save dialog). */
  bytes: Uint8Array;
}

export interface SetupStatus {
  models_ready: boolean;
  missing: { repo_id: string; label: string }[];
  hf_cache_dir: string;
  disk_free_gb: number;
  min_free_gb: number;
  enough_disk: boolean;
}

export type DownloadPhase =
  | "install_start"
  | "resolving"
  | "progress"
  | "install_retry"
  | "install_done"
  | "install_error";

export interface DownloadEvent {
  repo_id: string;
  filename: string;
  downloaded: number;
  total: number;
  pct: number; // 0.0–1.0
  phase: DownloadPhase;
  error?: string;
  attempt?: number;
  rate?: number;
}

// --- Reference transcription (ASR) — transcription.md / ipc-contract §6A -------

export interface TranscribeModel {
  id: string; // "large-v3"
  label: string; // "Large v3 (max fidelity)"
  size_mb: number; // ~3100
  downloaded: boolean; // .pt present under parrot_data/whisper_models/
}

export interface TranscribeStatus {
  models: TranscribeModel[];
  default_model: string; // "large-v3"
  device: "cuda" | "cpu"; // resolved compute device
  device_label?: string; // e.g. "GPU (CUDA) — RTX 4090"
  gpu: boolean; // device === "cuda" (drives the "GPU acceleration on" badge)
}

/** Mirrors DownloadEvent but keyed by `model`, not `repo_id`. */
export interface TranscribeDownloadEvent {
  model: string;
  filename: string;
  downloaded: number; // bytes
  total: number; // bytes (0 while resolving)
  pct: number; // 0.0–1.0
  phase: DownloadPhase;
  error?: string;
  attempt?: number;
}

export interface TranscribeResult {
  text: string; // transcript ("" when no speech was heard — not an error)
  language: string; // detected/echoed language code, e.g. "pt"
  model: string; // model id used
}

/** Phases of the synthesis-progress SSE stream (GET /generate/progress-stream). */
export type GenerationPhase = "start" | "step" | "done" | "error";

/** One synthesis-progress event. `pct` is 0.0–1.0 and stays below 1.0 until the
 *  terminal `done` event (the tail decode/DSP work is not step-granular). */
export interface GenerationProgressEvent {
  phase: GenerationPhase;
  step: number;
  total: number;
  pct: number; // 0.0–1.0
}

/** HF token cascade (settings.md read model). */
export interface TokenSource {
  source: "app" | "env";
  set: boolean;
  masked: string | null; // "hf_…<last 3>" — never the full token
  whoami_user: string | null;
  whoami_ok: boolean;
}

export interface TokenState {
  active: "app" | "env" | null;
  sources: TokenSource[];
}

/** Parameters for POST /generate (the subset the Speak UI exposes + advanced). */
export interface GenerateParams {
  text: string;
  language?: string | null;
  profile_id?: string | null;
  speed?: number;
  seed?: number | null;
  num_step?: number;
  guidance_scale?: number;
  effect_preset?: string;
  denoise?: boolean;
  postprocess_output?: boolean;
  duration?: number | null;
  t_shift?: number | null;
  layer_penalty_factor?: number | null;
  position_temperature?: number | null;
  class_temperature?: number | null;
  // ref_audio/ref_text are intentional plumbing for a DEFERRED ad-hoc-reference
  // UI (clone-from-scratch without first saving a profile). The /generate router
  // and generate.ts already wire them through; the Speak screen just doesn't
  // surface the control yet. Do NOT remove — keep the contract ready.
  /** Ad-hoc reference clip (clone-from-scratch); ignored when profile_id is set. */
  ref_audio?: Blob | null;
  ref_text?: string | null;
  instruct?: string | null;
}

/** App data locations resolved by the Rust shell (get_app_paths). */
export interface AppPaths {
  dataDir: string;
  outputsDir: string;
  voicesDir: string;
  dbPath: string;
  logPath: string;
}

export interface UpdateStatus {
  available: boolean;
  version?: string;
  notes?: string;
}

/** Download progress for install_update, pushed on the `update-progress` event. */
export interface UpdateProgress {
  downloaded: number; // bytes received so far
  total: number | null; // content-length, or null if the server sent none
  done: boolean; // true on the terminal frame (download finished)
}
