# IPC Contract

The single source of truth for the **frontend ↔ sidecar** boundary in Parrot. The Svelte UI never imports Python or torch; it only knows the shapes on this page. The Tauri (Rust) shell owns the Python process lifecycle and exposes native glue (dialogs, fs/reveal, updater) as Tauri commands. Everything the UI does crosses one of two boundaries documented here:

1. **HTTP / WebSocket** to the Python FastAPI sidecar at `http://127.0.0.1:3900` (REST + SSE) and `ws://127.0.0.1:3900/ws/tts` (streaming synthesis). The frontend dev server runs on `http://localhost:3901` (dev only; absent in packaged builds); in a packaged build the webview talks to the sidecar directly on `127.0.0.1:3900`.
2. **Tauri `invoke()`** to Rust commands for native operations the browser sandbox can't perform.

> **Binding rule (testable):** any change to an endpoint, header, or shape on this page MUST update **both** this document **and** the typed TS client under `frontend/src/lib/api/` in the *same commit*. A PR that touches a router signature without touching the matching client file (or vice-versa) fails review. See [../../CLAUDE.md](../../CLAUDE.md).

Related specs: [voice-profiles.md](./voice-profiles.md) · [synthesis.md](./synthesis.md) · [voice-cloning.md](./voice-cloning.md) · [first-run-setup.md](./first-run-setup.md) · [architecture.md](./architecture.md) · [device-detection.md](./device-detection.md) · [settings.md](./settings.md)

---

## 1. Transport & conventions

```
Base URL    http://127.0.0.1:3900            (loopback only — never bound to 0.0.0.0 by default)
WS URL      ws://127.0.0.1:3900/ws/tts       (chunked-PCM synthesis only)
Dev origin  http://localhost:3901            (CORS-allowed; tauri://localhost, http://tauri.localhost, https://tauri.localhost also allowed)
Audio out   WAV at the model's native sample rate, mono
```

| Convention | Value |
|------------|-------|
| Request bodies | `multipart/form-data` for anything carrying a file (generate, create profile, lock); `application/json` for pure-data updates (PUT profile, set token); query/path params otherwise. |
| Response bodies | `application/json` for data; `audio/wav` stream for `/generate`; `audio/wav` file for profile audio; `text/event-stream` for setup progress. |
| Out-of-band metadata | Returned as `X-*` response headers on streaming responses (see `/generate`). |
| IDs | 8-char hex strings (`uuid4()[:8]`) for profiles and history rows. |
| Time | Unix epoch seconds as `REAL` (float) in all `*_at` fields. |
| Auth | None. The sidecar is loopback-only and ships no credentials; this is by design (local-first). |
| Route prefix | All sidecar routes are **unprefixed** (no `/api/` prefix). |

---

## 2. Error envelope

Every error response from the sidecar is a FastAPI `HTTPException`, serialized as a single JSON object:

```jsonc
// HTTP 4xx / 5xx
{ "detail": "Human-readable message, safe to surface in a toast." }
```

- `detail` is always a **string** for the endpoints in this contract (no nested validation arrays are exposed; form validation that FastAPI would emit as a 422 list is caught and re-raised as a 400 with a string `detail`).
- Unhandled exceptions return `500` with `{ "detail": "<exc>" }` and are written to the crash log; the message is safe to show.
- A client that disconnects mid-stream is **logged server-side as a disconnect and gets no status response** (the client is already gone). The UI treats its own aborted fetch as a user cancel, not an error — there is no error envelope for this case.

### Status codes used

| Code | Meaning in Parrot |
|------|-------------------|
| `200` | Success (JSON, WAV stream, or SSE). |
| `400` | Bad input — empty profile name, no editable fields in a PUT, invalid `effect_preset`, validation error from the engine. |
| `404` | Profile / history row not found. |
| `429` | Model re-download attempted within the 60 s cooldown after a failed download. |
| `500` | Synthesis or I/O failure; `detail` points the user to logs. |

> A mid-stream client disconnect is **not** assigned an HTTP status — the connection is already closed, so the server only logs it. The UI's `AbortController` is the cancel path on the client side.

### Typed-client mirror

The client mirrors this envelope with one class and a set of helpers (`frontend/src/lib/api/client.ts`):

```ts
export class ApiError extends Error {
  status: number;
  detail?: unknown;            // the parsed `detail` string from the envelope
}

// readError() parses { detail } | { error } | raw text, in that order.
// apiFetch()    → Response, throws ApiError on !res.ok
// apiJson<T>()  → parsed JSON (GET)
// apiPost<T>()  → FormData passes through untouched; objects are JSON-encoded
// apiPostRaw()  → raw Response (callers read X-* headers / stream the body — used by /generate)
// apiPut<T>() / apiDelete<T>() → parsed JSON
// errMsg(e)     → user-facing message, preferring the sidecar `detail`
```

Rules for the typed client:

1. One module per endpoint group: `generate.ts`, `profiles.ts`, `history.ts`, `setup.ts`, `engine.ts`, `health.ts`, `settings.ts`, `ttsStream.ts`. Each re-exports through `frontend/src/lib/api/index.ts`.
2. Request/response shapes are declared as TS interfaces in `frontend/src/lib/api/types.ts` and must match the tables below field-for-field.
3. Never throw a bare `Error` from a client function — wrap sidecar failures in `ApiError` so the toast layer can read `.status` and `.detail`.
4. `multipart` endpoints take a `FormData` and return the raw `Response` (callers read `X-*` headers and stream the body).

---

## 3. Synthesis

### `POST /generate`

Synthesize speech and stream WAV back. Writes one `generation_history` row. Content type: `multipart/form-data`.

**Form params**

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `text` | string | — | **Required.** Truncated to 200 chars when stored in history. |
| `language` | string? | `null` | Model language hint; `"Auto"` is normalized to `null` when a profile resolves it. |
| `ref_audio` | file? | — | Inline reference clip (ad-hoc clone). Mutually exclusive with `profile_id` in practice — `profile_id` wins. |
| `ref_text` | string? | — | Transcript of the reference clip. |
| `instruct` | string? | — | Optional style hint (de-emphasized in Parrot's UI; field retained). |
| `duration` | float? | `null` | Target seconds; `null` lets the model decide. |
| `num_step` | int | `16` | Denoiser steps. |
| `guidance_scale` | float | `2.0` | CFG scale. |
| `speed` | float | `1.0` | Playback rate multiplier. |
| `denoise` | bool | `true` | Reference-audio denoise pre-pass. |
| `postprocess_output` | bool | `true` | Model-side output cleanup. |
| `profile_id` | string? | — | Resolve a stored voice profile (see resolution rules). |
| `seed` | int? | `null` | Deterministic seed; falls back to the profile's stored seed if unset. |
| `effect_preset` | string | `"broadcast"` | DSP master chain. `"raw"` skips all DSP. Unknown id → `400`. |
| `t_shift` | float? | `null` | *Advanced.* Forwarded to the model only when set. |
| `layer_penalty_factor` | float? | `null` | *Advanced.* Forwarded only when set. |
| `position_temperature` | float? | `null` | *Advanced.* Forwarded only when set. |
| `class_temperature` | float? | `null` | *Advanced.* Forwarded only when set. |

**Profile resolution (testable order)**

1. If `profile_id` is given and the row has `is_locked = 1` **and** a `locked_audio_path` → use the locked audio; fill missing `ref_text`/`instruct`/`seed` from the row.
2. Else if `profile_id` is given → use the row's `ref_audio_path`; fill missing `ref_text`/`instruct`/`seed` from the row.
3. Else if `ref_audio` file is present → write it to a temp file, use it, delete it after the response.
4. `language == "Auto"` is converted to `null` before inference when a profile is resolved.

**Response** — `200`, `Content-Type: audio/wav`, body is the WAV stream (16 KiB chunks). Metadata rides on headers:

| Header | Meaning |
|--------|---------|
| `X-Audio-Id` | History row id (8-char hex); also the output filename stem. |
| `X-Gen-Time` | Wall-clock generation seconds (float string). |
| `X-Audio-Path` | Output filename, e.g. `a1b2c3d4.wav` (relative to the outputs dir). |
| `X-Seed` | Seed actually used, or empty string if none. |
| `X-Audio-Duration` | Output duration in seconds (float string). |
| `Content-Length` | Exact byte length of the WAV. |

**Errors** — `400` invalid input / unknown `effect_preset`; `500` inference/OOM failure (message points to logs). If the client aborts mid-stream the server logs the disconnect and returns no status (the client is already gone).

> Client (`generate.ts`) returns the raw `Response` so the caller can read the `X-*` headers and pipe the body to an `<audio>` element or save dialog. Full pipeline detail lives in [synthesis.md](./synthesis.md).

### `GET /generate/progress-stream`

Live per-step progress for the in-flight synthesis, so the Speak UI shows a real %-complete bar instead of an indeterminate spinner. Method **`GET`**; response `Content-Type: text/event-stream` (SSE, one-way server push); one JSON `GenerationProgressEvent` per `data:` line, `: keepalive` comment line every ~30 s on idle. Loopback-only (`127.0.0.1:3900`) and gated like the rest of the engine surface — it carries **no audio**, only progress. Parrot is single-user (one generation at a time), so events are broadcast to every subscriber (the page's one progress bar).

```jsonc
// GenerationProgressEvent
{ "phase": "start" | "step" | "done" | "error",
  "step": 0,        // diffusion steps completed
  "total": 16,      // = num_step
  "pct": 0.0 }      // 0.0–1.0; held < 1.0 (ceiling 0.97) until the terminal `done` (tail decode/DSP isn't step-granular)
```

- **`start`** — published once when a generation begins (clears any stale replayed events); `step = 0`, `total = num_step`, `pct = 0`.
- **`step`** — one diffusion step completed; `pct = step / total`, clamped below `1.0`.
- **`done`** — terminal success; `pct = 1.0`. The stream stays open for the next generation, but the UI closes its subscription on this event.
- **`error`** — terminal failure (the `POST /generate` also 500s); `pct = 0.0`.

The engine exposes no native progress hook, so the sidecar counts the model's per-step forward passes in the `generation_progress` service (a worker thread publishes into the event-loop broadcaster, mirroring the `setup_manager` download broadcaster). The Speak UI opens this stream **just before** `POST /generate` so it catches the `start` phase, and closes it on a terminal event / when the request settles; failure to open is non-fatal (the bar falls back to indeterminate). On connect the broadcaster replays a tiny buffer, which may include a prior generation's tail — the store ignores everything until this generation's `start` (see [synthesis.md §Progress](./synthesis.md#progress)).

> Consumed via `EventSource` (like `/setup/download-stream`): `generate.ts` exposes `subscribeGenerationProgress(onEvent)` wrapping `new EventSource(apiUrl('/generate/progress-stream'))` and returns an unsubscribe fn that calls `EventSource.close()`. There is no `onError` arg — `new EventSource` never throws, so a connect failure is non-fatal (the bar stays indeterminate) and surfaces only as `es.onerror`.

---

## 4. Profiles

A voice profile is a reusable clone (reference clip + transcript + settings). [voice-profiles.md](./voice-profiles.md) **owns** the `VoiceProfile` entity and the full `/profiles` CRUD + lock/unlock + usage contract; the table below mirrors those shapes for the typed client. The capture flow (record/upload, normalization, `ref_text` guidance, create→profile state machine) lives in [voice-cloning.md](./voice-cloning.md).

| Method | Path | Body | Returns | Errors |
|--------|------|------|---------|--------|
| `GET` | `/profiles` | — | `VoiceProfile[]` (newest first) | — |
| `POST` | `/profiles` | form: `name`*, `ref_audio`* (file), `ref_text`, `instruct`, `language`, `seed` | `{ id, name }` | rolls back the saved audio file if the DB insert fails |
| `GET` | `/profiles/{id}` | — | `VoiceProfile` | `404` not found |
| `PUT` | `/profiles/{id}` | json: `name?`, `ref_text?`, `instruct?`, `language?` | full updated `VoiceProfile` | `400` empty name / no editable fields; `404` not found |
| `GET` | `/profiles/{id}/audio` | — | `audio/wav` file (locked audio if present, else reference) | `404` profile / no audio / file missing on disk |
| `GET` | `/profiles/{id}/usage` | — | `{ synth_recent: UsageRow[], synth_total: int }` | — |
| `POST` | `/profiles/{id}/lock` | form: `history_id`*, `seed?` | `{ locked: true, profile_id, locked_audio_path }` | `404` profile / history / source audio missing |
| `POST` | `/profiles/{id}/unlock` | — | `{ unlocked: true, profile_id }` | `404` profile not found |
| `DELETE` | `/profiles/{id}` | — | `{ deleted: "<id>" }` | — (idempotent) |

**Shapes**

```ts
// frontend/src/lib/api/types.ts
interface VoiceProfile {
  id: string;
  name: string;
  ref_audio_path: string;        // filename relative to the voices dir
  ref_text: string;              // default ""
  language: string;              // default "Auto"
  instruct: string;              // default "" (de-emphasized)
  locked_audio_path: string;     // default "" — set when is_locked
  seed: number | null;
  is_locked: 0 | 1;
  created_at: number;            // epoch seconds
}

// A profile's usage list returns a trimmed row, not the full HistoryRow — just
// the fields the usage UI needs (voice-profiles.md owns this shape; D6).
interface UsageRow {
  id: string;                    // == X-Audio-Id of the generation
  text: string;
  audio_path: string;            // filename relative to the outputs dir
  created_at: number;            // epoch seconds
  generation_time: number;
}

interface ProfileUsage {
  synth_recent: UsageRow[];      // ≤20 most-recent generations referencing this profile (newest first)
  synth_total: number;
}
```

**Lock semantics (testable):** `lock` copies the chosen history row's audio to `{id}_locked.wav` in the voices dir, stores the first 100 chars of that history's text as `ref_text`, persists the `seed`, and sets `is_locked = 1`. `unlock` deletes the locked file, clears `locked_audio_path`, nulls `seed`, and sets `is_locked = 0`. `delete` removes both audio files, nulls `profile_id` on dependent `generation_history` rows (preserving the FK), then deletes the row. The authoritative business rules for these operations are in [voice-profiles.md](./voice-profiles.md).

> **Parrot trim:** OmniVoice's `personality` field and `GET /personalities` preset endpoint are **dropped** — Parrot ships no personality presets. The optional `instruct` style param survives but is not surfaced as a picker.

---

## 5. History

The synthesis log.

| Method | Path | Returns | Notes |
|--------|------|---------|-------|
| `GET` | `/history` | `HistoryRow[]` | Newest first, capped at 50. |
| `GET` | `/history/{id}/audio` | `audio/wav` file | Serves a past generation's WAV so the History list can replay it (in-app playback). `404` if the row or file is missing. |
| `GET` | `/history/{id}/audio.mp3` | `audio/mpeg` bytes | The same clip re-encoded to MP3 for the user's **download/export** (smaller, shareable). Playback stays WAV; only the exported file is MP3. `404` if the row or file is missing. |
| `DELETE` | `/history` | `{ cleared: true }` | Deletes every row and its on-disk audio. |
| `DELETE` | `/history/{id}` | `{ deleted: true }` | Deletes one row + its audio file. |

```ts
interface HistoryRow {
  id: string;                    // == X-Audio-Id of the generation
  text: string;                  // first 200 chars
  language: string;              // "Auto" if unspecified
  profile_id: string | null;    // FK → voice_profiles.id; null after profile delete
  audio_path: string;            // filename relative to the outputs dir
  duration_seconds: number;
  generation_time: number;
  seed: number | null;
  created_at: number;
}
```

Deletes are best-effort on the file (a missing file does not fail the request) and always remove the DB row. The `generation_history` row shape has no `mode` column.

### `POST /audio/mp3` — stateless export transcode

Body: raw WAV bytes (`Content-Type: audio/wav`). Returns the same audio re-encoded as MP3 (`audio/mpeg`). No DB, no model — just `soundfile`. The Speak screen uses this to export a **fresh result** straight from the WAV bytes it holds in memory, so an export never depends on a history row (which the user may have already cleared). History-list exports instead use `GET /history/{id}/audio.mp3` (the row + file still exist there). `400` on empty or undecodable input.

---

## 6. First-run setup

The model-gate + download flow the boot screen depends on. See [first-run-setup.md](./first-run-setup.md).

| Method | Path | Returns | Notes |
|--------|------|---------|-------|
| `GET` | `/setup/status` | `SetupStatus` | Drives the "models ready?" boot gate. |
| `POST` | `/setup/download` | `{ status: "download_started", repo_id }` | JSON body `{ repo_id }` (validated against the catalog → `400` if unknown). Starts the model download. `429` within 60 s of a prior failure. Progress arrives on the SSE stream. |
| `GET` | `/setup/download-stream` | `text/event-stream` | SSE of download progress; one `DownloadEvent` per `data:` line; `: keepalive` every 30 s. |

```ts
interface SetupStatus {
  models_ready: boolean;                      // false → show the download wizard
  missing: { repo_id: string; label: string }[]; // required models not yet cached ([] when ready)
  hf_cache_dir: string;
  disk_free_gb: number;
  min_free_gb: number;                        // 10
  enough_disk: boolean;
}

// One SSE event (JSON in the `data:` field). Phase names match first-run-setup.md.
interface DownloadEvent {
  repo_id: string;
  filename: string;
  downloaded: number;                         // bytes
  total: number;                              // bytes (0 while resolving)
  pct: number;                                // 0.0–1.0
  phase: 'install_start' | 'resolving' | 'progress'
       | 'install_retry' | 'install_done' | 'install_error';
  error?: string;                             // present on install_error / install_retry
  attempt?: number;                           // present on install_retry
  rate?: number;                              // optional bytes/s readout
}
```

> **Parrot trim:** OmniVoice's `POST /setup/warmup`, `POST /models/install`, `DELETE /models/{id}`, and the `GET /setup/preflight` system-health panel (OS/RAM/GPU/ffmpeg/yt-dlp checks) are **out of Parrot's documented surface**. The setup surface is exactly these three endpoints — `GET /setup/status`, `POST /setup/download`, `GET /setup/download-stream` — with no model-management surface beyond them. Parrot needs no ffmpeg/yt-dlp (no dubbing, no YouTube clipping). Device detection is covered separately in [device-detection.md](./device-detection.md).

> The SSE stream is consumed via `EventSource`, not `apiFetch`. `setup.ts` exposes a `subscribeDownload(onEvent, onError)` that wraps `new EventSource(apiUrl('/setup/download-stream'))` and `JSON.parse`s each `data:` payload into a `DownloadEvent`.

---

## 6A. Reference transcription (ASR)

The clone-time speech-to-text that auto-fills `ref_text`. This is Parrot's **only** ASR surface and exists solely to serve cloning — see [transcription.md](./transcription.md) for the full contract, the scope carve-out, and the engine rationale (openai-whisper on the existing torch/CUDA stack, av-based decode, no system ffmpeg). The typed client is `frontend/src/lib/api/transcribe.ts`.

| Method | Path | Body | Returns | Errors |
|--------|------|------|---------|--------|
| `GET` | `/transcribe/status` | — | `TranscribeStatus` | — |
| `POST` | `/transcribe/download` | json: `{ model }` | `{ status: "download_started", model }` | `400` unknown model; `429` within 60 s of a failed download |
| `GET` | `/transcribe/download-stream` | — | `text/event-stream` of `TranscribeDownloadEvent` | — |
| `POST` | `/transcribe` | form: `ref_audio`* (file), `model`, `language` | `TranscribeResult` | `400` unknown model; `409` model not downloaded; `415` unsupported ext; `500` decode/engine failure |

```ts
interface TranscribeModel {
  id: string;          // "large-v3"
  label: string;       // "Large v3 (max fidelity)"
  size_mb: number;     // ~3100
  downloaded: boolean; // .pt present under parrot_data/whisper_models/
}
interface TranscribeStatus {
  models: TranscribeModel[];
  default_model: string;      // "large-v3"
  device: "cuda" | "cpu";     // resolved compute device (device-detection.md)
  device_label?: string;      // e.g. "GPU (CUDA) — RTX 4090"
  gpu: boolean;               // device === "cuda" (drives the "GPU acceleration on" badge)
}
// Mirrors DownloadEvent but keyed by `model`, not `repo_id`.
interface TranscribeDownloadEvent {
  model: string;
  filename: string;
  downloaded: number;  // bytes
  total: number;       // bytes (0 while resolving)
  pct: number;         // 0.0–1.0
  phase: 'install_start' | 'resolving' | 'progress'
       | 'install_retry' | 'install_done' | 'install_error';
  error?: string;      // present on install_error / install_retry
  attempt?: number;    // present on install_retry
}
interface TranscribeResult {
  text: string;        // the transcript ("" when no speech was heard — not an error)
  language: string;    // detected/echoed language code, e.g. "pt"
  model: string;       // model id used
}
```

- `POST /transcribe` is **blocking** (no per-step progress stream — the UI shows an indeterminate "Transcribing…" spinner). It runs in a threadpool so the Whisper call never stalls the loop, and the engine serializes calls (Parrot is single-user). The %-bar is reserved for the multi-GB *download*, which mirrors `/setup/download-stream` (the client wraps it as `subscribeTranscribeDownload` exactly like `subscribeDownload`).
- `language` takes the clone Language-picker values (full English names + `"Auto"`); `"Auto"`/unknown → auto-detect.
- The download caches single-file `.pt` weights under `parrot_data/whisper_models/` — **not** the HF cache the OmniVoice gate (`/setup/*`) uses.

> **Scope note:** transcription is wired **only** into the Clone capture flow ([voice-cloning.md](./voice-cloning.md)). There is no Speak/History/standalone use, and `ref_text` is the only thing it writes (no new entity — see [transcription.md §7](./transcription.md)).

---

## 7. Settings — Hugging Face token

The HF token is **optional** and needed only for gated model download. Resolution order (identical everywhere): (1) the in-app encrypted setting in the `settings` table (per-install Fernet key) — the documented default path; (2) the `HF_TOKEN` environment variable — a documented power-user override. These endpoints manage the in-app stored token. See [settings.md](./settings.md).

| Method | Path | Body | Returns | Notes |
|--------|------|------|---------|-------|
| `GET` | `/settings/hf-token` | — | `TokenState` | The masked cascade (`app` + `env` sources). Never the raw token. |
| `POST` | `/settings/hf-token` | json: `{ token: string }` | `TokenState` | Encrypts + persists the token, then re-validates (`whoami`); `400` on an empty token. |
| `DELETE` | `/settings/hf-token` | — | `TokenState` | Clears the stored token (keeps the salt); the `HF_TOKEN` env var, if set, still applies as an override. |

```ts
// The masked token cascade — settings.md owns this model. `masked` is "hf_…<last 3>".
interface TokenSource {
  source: 'app' | 'env';
  set: boolean;
  masked: string | null;                       // "hf_…<last 3>", or null when unset
  whoami_user: string | null;
  whoami_ok: boolean;
}
interface TokenState {
  active: 'app' | 'env' | null;                // highest-priority source that validated
  sources: TokenSource[];
}
```

> Routes are **unprefixed** like every other sidecar route (no `/api/` prefix). The `settings` table stores secrets (value encrypted); it is **not** used for appearance. Appearance (theme, zoom) is frontend-local only — see [settings.md](./settings.md) and [design-system.md](./design-system.md).

---

## 8. Engine status

Parrot ships exactly one TTS engine (`omnivoice`, pure-Python via `transformers`); the multi-engine picker is dropped. This is the **only** engine/device endpoint — a read-only stub so the UI can show which engine is active and which device it is running on, without offering a switch.

| Method | Path | Returns |
|--------|------|---------|
| `GET` | `/engine/status` | `{ active: "omnivoice", device: string }` |

```ts
interface EngineStatus {
  active: 'omnivoice';                         // single fixed engine, not selectable
  device: 'cuda' | 'cpu';                      // the resolved compute device
  device_label?: string;                       // optional human label, e.g. "cuda (RTX 4090)"
}
```

- `device` is one of exactly `cuda`, `cpu`. How the device is resolved is owned by [device-detection.md](./device-detection.md).
- There is **no** `backends` array — Parrot has a single fixed engine, nothing to enumerate or select.

> **Parrot trim:** OmniVoice's `POST /engines/select`, the `asr`/`llm` families, translation-engine install/uninstall, effect-preset listing, and per-engine `health` spawn-ping are **all dropped**. There is no `/engine`, `/engine-status`, `/engines`, `/engines/tts`, `/system/info`, or `/system/notifications` surface. The client (`engine.ts`) exposes a single `getEngineStatus()` and no setter.

---

## 9. Health

Used by the Rust supervisor to gate "sidecar is up". See [architecture.md](./architecture.md).

| Method | Path | Returns |
|--------|------|---------|
| `GET` | `/healthz` | `{ status: "ok" }` |

```ts
interface Health {
  status: 'ok';
}
```

- `/healthz` is a fast liveness probe for the Rust supervisor: it imports no torch and reports **no device field** (device is reported only by `GET /engine/status`). The body is exactly `{ "status": "ok" }`.
- The supervisor polls `/healthz` after spawning the sidecar and only marks the window "ready" on a `200`. Boot timeout and restart policy live in [architecture.md](./architecture.md).
- This is the **only** endpoint the Rust shell calls over HTTP; everything else is the Svelte UI's job.

> The supervisor's `BACKEND_PORT` (3900) and this path stay in sync — they are asserted together in the supervisor's boot test.

---

## 10. WebSocket — streaming synthesis (optional)

`ws://127.0.0.1:3900/ws/tts` — low-latency chunked PCM for live preview. This channel is for **chunked-PCM synthesis only**; it is not an event bus / pub-sub channel. Optional: the standard path is `POST /generate`. The connection stays open for successive requests (conversational mode).

**Client → server** (one JSON message per request):

```ts
interface WsTtsRequest {
  text: string;                                // required
  voice?: string;                              // profile_id (resolved server-side)
  language?: string;
  speed?: number;                              // default 1.0
  instruct?: string;
  seed?: number;                               // deterministic seed; falls back to the profile's stored seed
  num_step?: number;                           // diffusion steps (advanced; server default 16)
  guidance_scale?: number;                     // CFG scale (advanced; server default 2.0)
}
```

**Server → client** (sequence per request):

```jsonc
// 1. start  (JSON)
{ "type": "start", "sample_rate": 24000, "channels": 1, "format": "pcm16", "engine": "omnivoice" }
// 2. N binary frames — raw PCM16, mono, ~200 ms per chunk
// 3. done   (JSON)
{ "type": "done", "duration_s": 4.2, "gen_time_s": 1.1, "samples": 100800, "sample_rate": 24000, "engine": "omnivoice" }
// on failure (JSON, instead of done)
{ "type": "error", "detail": "..." }
```

Rules:

1. A request missing `text` gets `{ "type": "error", "detail": "Missing 'text' field in request" }` and the socket stays open.
2. Binary frames are PCM16 LE samples; the client must apply `sample_rate`/`channels` from the `start` frame.
3. `voice` is resolved exactly like `/generate`'s `profile_id` (locked audio wins over reference).

> **Parrot trim:** voice-design emotion/engine-override fields from OmniVoice (`emo_vector`, `emo_text`, `emo_audio`, `emo_alpha`, `description`, `engine`) are **dropped** from Parrot's `WsTtsRequest`. The kept fields mirror the `/generate` form subset that makes sense for live preview — `text`, `voice` (== `profile_id`), `language`, `speed`, `instruct`, `seed` — minus the file upload, DSP-preset, and advanced model knobs (the WS path applies only broadcast mastering, never an `effect_preset`).

---

## 11. Tauri (Rust) commands — native glue

The browser sandbox can't open files or reveal folders. The Svelte UI calls these via `@tauri-apps/api/core`'s `invoke()`; clients live in `frontend/src/lib/api/native.ts`. Names below are the Parrot command set. The sidecar's *lifecycle* (spawn / health-check / teardown) is owned solely by the supervisor module and is **not** UI-callable; the UI may **observe** boot state and **request a retry** through the read-only/recovery commands in §11.1 (see [architecture.md](./architecture.md) §3, §5.1).

| `invoke()` name | Args | Returns | Purpose |
|-----------------|------|---------|---------|
| `save_audio_dialog` | `{ defaultName: string, audioBytes?: number[] }` | `string \| null` (chosen path, or null if cancelled) | Native "Save As" dialog to export audio. Writes `audioBytes` verbatim and derives the dialog file-type filter from `defaultName`'s extension, so it serves both exports: a **generated** clip (transcoded to MP3 server-side via `GET /history/{id}/audio.mp3`, `.mp3`) and a voice's **original reference** clip (downloaded as-is via `GET /profiles/{id}/audio`, in its source format). |
| `reveal_in_folder` | `{ path: string }` | `void` | Reveal a file in Windows Explorer. |
| `get_app_paths` | — | `{ dataDir, outputsDir, voicesDir, dbPath, logPath }` | Resolve `parrot_data/` locations for the UI (e.g. "open data folder"). |
| `read_log_tail` | `{ source: "backend" \| "tauri", tail?: number }` | `{ lines: string[], path, exists, total_lines }` | Tail a log (`tail` clamped 10–2000, default 300). Implemented + tested for **future use**: the current Settings UI surfaces the backend log by revealing `backend.log` in Explorer (via `reveal_in_folder`), not by calling this. `"backend"` reads `backend.log` (sidecar stdout). `"tauri"` targets `parrot.log`, which is **not yet written** — no Tauri logging plugin is registered, so that source returns `exists: false` until logging is wired (see [architecture.md](./architecture.md) §7). |
| `check_for_update` | — | `{ available: boolean, version?: string, notes?: string }` | Query the Tauri updater. |
| `install_update` | — | `Result<(), string>` | Download + apply the pending update, then relaunch. |
| `quit_app` | — | `void` | Set the quitting flag and exit (tears down the sidecar). |

Conventions:

- Rust commands return `Result<T, String>` for fallible operations; the `Err(String)` surfaces to JS as a thrown promise rejection. The `native.ts` client wraps these in `ApiError` (with `status` unset) so the toast layer is uniform with HTTP errors.
- Paths returned to the UI are absolute, native Windows strings. The UI treats them as opaque (passes them straight back to `reveal_in_folder`).
- `install_update` streams download progress via an **`update-progress`** Tauri event (subscribe with `@tauri-apps/api/event`'s `listen`; `native.ts` exposes `onUpdateProgress(handler)`). Payload `{ downloaded: number, total: number | null, done: boolean }` — `downloaded`/`total` are bytes (`total` is `null` when the server sent no content-length), and `done` is `true` on the terminal frame. The updater store renders a download readout from it.
- **Spawn / health-check / teardown of the Python sidecar is intentionally NOT UI-callable** — only the supervisor module owns the sidecar lifecycle, and the UI can never start, stop, or restart the *process*. What the UI *can* do is **observe** boot state and **request a recovery retry**, through the read-only/recovery command group in §11.1. (The UI also observes engine liveness via `/healthz` over HTTP, never by spawning.)

### 11.1 — Supervisor / bootstrap commands (observe + recover)

The boot splash drives the bootstrap store (architecture.md §5.1) off these. They are read-only or recovery-only: none of them spawn, kill, or restart the sidecar process directly — they read latched state or *signal* the supervisor's own state machine to retry. Two Tauri **events** (`bootstrap-stage`, `bootstrap-log`) push live updates; the commands below are the pull/backfill and recovery surface.

| `invoke()` name | Args | Returns | Purpose |
|-----------------|------|---------|---------|
| `backend_port` | — | `number` (u16) | The loopback port the sidecar is bound to (default `3900`, `PARROT_PORT` override). The UI builds its API base URL from this. |
| `sidecar_ready` | — | `boolean` | Whether the supervisor has seen `/healthz` answer at least once. |
| `sidecar_failed` | — | `boolean` | Whether the supervisor permanently gave up (crash-looped past `MAX_RAPID_FAILURES`). Latched, so the UI gets the terminal state even if it missed the `sidecar-failed` event. |
| `bootstrap_status` | — | `string` | Current boot stage (`checking` → `creating_venv` → `installing_deps` → `starting_backend` → `ready` \| `failed`). Pull counterpart to the `bootstrap-stage` event. |
| `get_bootstrap_logs` | — | `string[]` | The boot-log tail (backfill for a late-mounting splash that missed early `bootstrap-log` lines). |
| `retry_bootstrap` | — | `void` | Reset a `failed` boot and re-run the spawn sequence (Retry action). |
| `clean_and_retry_bootstrap` | — | `void` | Like Retry, but first wipe the bootstrapped venv + kill any stale sidecar on the port (Reset & retry action). |

**Tauri events** (supervisor → UI; subscribe via `@tauri-apps/api/event`'s `listen`):

| Event | Payload | When |
|-------|---------|------|
| `bootstrap-stage` | `string` (the new stage) | Every stage transition. |
| `bootstrap-log` | `string` (one log line) | Each supervisor log line (stage transitions also emit a `[stage] <name>` line). |
| `sidecar-ready` | `number` (the port) | The first time `/healthz` answers (attach or spawn). |
| `sidecar-failed` | `number` (failure count) | The supervisor gives up after `MAX_RAPID_FAILURES` never-healthy starts. |

> **Parrot trim of native commands:** OmniVoice's dictation-shortcut (`get/set_dictation_shortcut`), tray-recording (`set_tray_recording`), pill-autostart (`enable/disable/is_pill_autostart_enabled`), `simulate_paste`, launch-as-widget, and `hf_cache_scan` commands are dropped — they belong to dictation / pill / gallery features that Parrot doesn't ship. `read_log_tail`, `quit_app`, and the dialog/reveal/updater glue remain. (Parrot's earlier `play_audio`/`stop_audio` native commands were also dropped — playback is the WebView's HTML `<audio>`.)

---

## 12. Edge cases

- **`profile_id` and `ref_audio` both sent to `/generate`** → `profile_id` wins; the uploaded file is ignored (and never written).
- **`profile_id` references a deleted profile** → the row lookup returns nothing, `resolved_profile_id` stays `null`, and generation proceeds with whatever inline params were sent (no `404`); the history row records `profile_id = null`.
- **Unknown `effect_preset`** → `400` before any audio is produced (validated against the preset registry).
- **OOM / engine crash mid-generation** → `500` with a message telling the user to flush + retry; GPU cache is emptied server-side. The UI should offer a "reload model" affordance.
- **Client aborts the `/generate` fetch** (user navigates away) → the server logs a disconnect and returns no status (the client is gone); the `AbortController.signal` passed into `generateSpeech` is the cancel path. No history-row guarantee: a row may or may not have been written depending on timing.
- **`PUT /profiles/{id}` with an all-`null` body** → `400` ("no editable fields"), never a silent no-op.
- **`PUT` name set to whitespace** → `400` ("A voice profile needs a name.").
- **Lock against a `history_id` whose audio was already deleted** → `404` ("Audio file not found on disk"); the profile is left unlocked.
- **Delete a profile in use by history** → succeeds; dependent history rows have `profile_id` nulled (FK preserved), so history survives.
- **`POST /setup/download` retried after a failure within 60 s** → `429` with seconds-remaining; the wizard must show the cooldown, not spin.
- **SSE stream idle** → server emits `: keepalive` comment lines every 30 s; the client must ignore comment lines (no `data:`).
- **Sidecar not yet up** → all HTTP calls reject at the socket layer (connection refused); the UI gates the whole app behind a `/healthz` `200` and shows a boot/splash state until then.
- **WS request missing `text`** → inline `{ "type": "error" }` frame; socket stays open for the next request.
- **Setting an empty HF token** → `POST /settings/hf-token` with a blank `token` is a `400`; the stored token is unchanged.

---

## 13. Data touched

| Endpoint group | DB tables | On-disk |
|----------------|-----------|---------|
| `/generate` | inserts `generation_history` | writes `<id>.wav` to the outputs dir; temp ref file deleted after |
| `/history*` | reads / deletes `generation_history` | deletes matching output WAVs; `audio.mp3` re-encodes a WAV to MP3 in memory (no file written) |
| `/profiles*` | reads / writes `voice_profiles`; nulls `generation_history.profile_id` on delete | writes/reads/deletes `<id>.<ext>` and `<id>_locked.wav` in the voices dir |
| `/setup/*` | none | reads/writes the HuggingFace cache (`$HF_HUB_CACHE`) |
| `/transcribe/*` | none | writes/reads Whisper `.pt` weights under `parrot_data/whisper_models/`; decodes the posted clip in memory (no file written). Output lands in `voice_profiles.ref_text` only when the user saves the profile. |
| `/settings/hf-token*` | reads / writes `settings` (value encrypted) | none |
| `/engine/status`, `/healthz` | none | none |
| `/ws/tts` | reads `voice_profiles` (voice resolution) | none (streams in-memory PCM) |

All SQLite tables live in the single DB under `parrot_data/` (WAL mode, `foreign_keys` ON, created idempotently and alembic-migrated). The bootstrapped Python venv lives at `parrot_data/.venv`. User data must survive upgrades with no manual migration. See [voice-profiles.md](./voice-profiles.md) and [architecture.md](./architecture.md) for the full entity and storage contracts.

> Live cross-tab / cross-window consistency, where needed, is a plain re-fetch (re-GET after a mutation or a poll) — there is no event bus or pub-sub channel.
