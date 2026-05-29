# Synthesis (Speak)

Turn typed text into spoken audio in a chosen voice. This is one of Parrot's two functions (the other is [voice-cloning.md](./voice-cloning.md)). A request carries the text plus a voice source — a saved profile, an inline reference clip, or neither (default voice) — and returns a 24 kHz WAV stream. Every successful synthesis is recorded in `generation_history`.

Synthesis runs on the Python FastAPI sidecar at `http://127.0.0.1:3900`. The Svelte UI never touches the model; it speaks only the IPC contract below. See [architecture.md](./architecture.md) for the process architecture and [voice-profiles.md](./voice-profiles.md) for the profile entity it resolves against.

---

## Entity Contract

Synthesis does not own a persistent entity of its own; it reads `voice_profiles` and writes one `generation_history` row per success. The written row:

```
generation_history
  id                TEXT PK     -- 8-char uuid4 prefix; also returned as X-Audio-Id
  text              TEXT        -- request text, truncated to first 200 chars
  language          TEXT        -- resolved language, or "Auto" if none
  profile_id        TEXT FK->voice_profiles(id)   -- NULL if no profile used; set NULL on profile delete
  audio_path        TEXT        -- bare filename "<id>.wav" relative to parrot_data/outputs/
  duration_seconds  REAL        -- round(samples / sample_rate, 2)
  generation_time   REAL        -- wall-clock seconds spent in inference, round(_, 2)
  seed              INTEGER NULL -- the seed actually used (resolved), or NULL
  created_at        REAL        -- epoch seconds
```

Invariants:

- `audio_path` is always a bare basename (no directory component). The file lives under `parrot_data/outputs/`. Path-traversal is rejected at read time (see [Edge Cases](#edge-cases)).
- `duration_seconds` and `generation_time` are non-negative.
- `seed` in the row equals the seed reported in the `X-Seed` response header.
- The model's output sample rate is **24 kHz** (`model.sampling_rate`); the WAV is encoded at that rate.

> Note: `generation_history` has exactly the columns above — there is no `mode` column and no `instruct` column on this table. The `instruct` request parameter (see below) is resolved per-request and is stored on the **voice profile**, not on the history row. This is the canonical data model; existing `parrot_data/` upgrades with no manual migration, and the schema is the source of truth (see [../../CLAUDE.md](../../CLAUDE.md)).

---

## Business Rules

1. **Text is required.** A request with empty or whitespace-only `text` is rejected before inference (see [Edge Cases](#edge-cases)). The UI disables the Speak button when the textarea is empty.
2. **Inference never runs on the event loop.** The model call executes on the shared GPU thread-pool executor (`_gpu_pool`) via `loop.run_in_executor`, so the FastAPI event loop stays responsive (health checks, status polling, cancellation) during a long synthesis.
3. **The model loads lazily on first use.** `/generate` awaits `get_model()`, which loads weights on first call (guarded by an async lock so concurrent requests don't double-load). Until the model is ready the request blocks; the UI reflects this via the loading state machine below.
4. **Profile resolution is deterministic** and follows the order in [Profile Resolution](#profile-resolution). An explicit request field always wins over the value stored on the profile (e.g. a request `seed` overrides the profile's stored `seed`).
5. **`language: "Auto"` means "let the model decide."** When a profile is resolved and `language == "Auto"`, language is set to `null` before inference. The model auto-detects across its ~600 zero-shot languages.
6. **A seed makes synthesis reproducible.** When a seed is resolved (request → profile → none), it is applied with `torch.manual_seed` before generation and echoed back in `X-Seed` and the history row. No seed → non-deterministic output and an empty `X-Seed` header.
7. **Output is mastered, then peak-normalized.** Unless `effect_preset == "raw"`, model output passes through broadcast mastering, then the named effect chain for the preset, then peak normalization to **-2.0 dBFS**. `raw` returns the model output untouched.
8. **An unknown `effect_preset` is a client error (400),** not a server crash — it is validated against the known preset table and raises a `ValueError` that maps to HTTP 400.
9. **Every success writes exactly one history row.** After a successful synthesis the UI refreshes its history list by re-fetching `GET /history` (plain re-GET after the mutation); there is no push/event channel.
10. **OOM mid-generation produces a recoverable, user-facing message.** If inference aborts (the typical out-of-memory symptom), the engine runs GC, empties the accelerator cache, and returns a message instructing the user to Flush and retry (see [Edge Cases](#edge-cases)).
11. **Inline reference clips are temporary.** A `ref_audio` upload is written to a temp file used only for that request and deleted in a `finally` block. It is never added to `parrot_data/voices/`. Persisting a voice is the job of [voice-cloning.md](./voice-cloning.md).
12. **Single engine only.** Parrot ships exactly one backend, id `"omnivoice"`. There is no engine picker and no `engine` selection field on `/generate`. Device/engine state is read from `GET /engine/status` (see below).

---

## IPC Contract

### `POST /generate`

`multipart/form-data`. Returns a streaming `audio/wav` response.

**Parameters**

| Field | Type | Default | UI | Notes |
|---|---|---|---|---|
| `text` | string | — (required) | **Exposed** | The text to speak. |
| `language` | string? | `null` | **Exposed** | Language hint; `"Auto"` → auto-detect. |
| `profile_id` | string? | `null` | **Exposed** | Saved voice to speak in. Drives [resolution](#profile-resolution). |
| `speed` | float | `1.0` | **Exposed** | Playback rate multiplier. |
| `seed` | int? | `null` | **Exposed** (Advanced/optional) | Reproducibility seed; overrides profile seed. |
| `ref_audio` | file? | `null` | Internal | Inline reference clip; used only when no `profile_id`. Temp, deleted after request. |
| `ref_text` | string? | `null` | Internal | Transcript of the reference clip. Falls back to profile's stored `ref_text`. |
| `instruct` | string? | `null` | Hidden | Optional style hint. De-emphasized; falls back to profile's stored value. |
| `duration` | float? | `null` | Hidden | Target duration; `null` lets the model choose. |
| `num_step` | int | `16` | **Advanced** | Denoising/diffusion steps. |
| `guidance_scale` | float | `2.0` | **Advanced** | Classifier-free guidance strength. |
| `denoise` | bool | `true` | **Advanced** | Denoise pass on the reference. |
| `postprocess_output` | bool | `true` | **Advanced** | Model-side output post-processing. |
| `effect_preset` | string | `"broadcast"` | **Advanced** | DSP preset (see [presets](#dsp-effect-presets)). |
| `t_shift` | float? | `null` | **Advanced** | Sampler time-shift. |
| `layer_penalty_factor` | float? | `null` | **Advanced** | Sampler tuning. |
| `position_temperature` | float? | `null` | **Advanced** | Sampler tuning. |
| `class_temperature` | float? | `null` | **Advanced** | Sampler tuning. |

UI exposure summary (per Parrot scope):

- **Primary controls** (always visible): `text`, `language`, `profile_id`, `speed`, and an optional `seed`.
- **Advanced panel** (collapsed by default, "you probably don't need this"): `num_step`, `guidance_scale`, `effect_preset`, `t_shift`, `layer_penalty_factor`, `position_temperature`, `class_temperature`, `denoise`, `postprocess_output`, `duration`.
- **Hidden / not in UI**: `instruct` (de-emphasized style param, still accepted and resolved from a profile if set), `ref_audio` / `ref_text` (driven by the clone flow, not typed by the user here).

**Success — `200`**

Body: a `StreamingResponse` of `audio/wav` bytes (chunked at 16384 bytes server-side; the WAV is fully encoded before streaming, so `Content-Length` is exact).

Response headers:

| Header | Meaning |
|---|---|
| `X-Audio-Id` | 8-char id; primary key of the history row and the output filename stem. |
| `X-Gen-Time` | Inference wall-clock seconds (matches `generation_time`). |
| `X-Audio-Path` | Bare output filename `"<id>.wav"` under `parrot_data/outputs/`. |
| `X-Seed` | The resolved seed, or empty string if none. |
| `X-Audio-Duration` | Audio length in seconds (matches `duration_seconds`). |
| `Content-Length` | Exact byte length of the WAV. |

**Error cases**

| Status | Trigger | Detail |
|---|---|---|
| `400` | `text` missing | FastAPI form validation (`text` is required). |
| `400` | Unknown `effect_preset`, or any validation `ValueError` raised in inference | `"Unknown effect preset: '<id>'. Valid: [...]"` (or the underlying validation message). |
| `500` | OOM / inference aborted mid-generation | `"TTS engine stopped mid-generation. This usually means it ran out of memory. Try the Flush button to reload the model, then regenerate. Underlying error: <e>"` |
| `500` | Temp-file write failure for an inline `ref_audio` | Raw OS error string. |
| `500` | Any other inference failure — incl. a **silent or undecodable reference clip** (the reference is decoded lazily in the engine at first synthesis; `/profiles` create only checks the file extension, see [voice-profiles.md](./voice-profiles.md)) | `"Couldn't synthesize audio. See Settings → Engine → View backend log for the full trace. Underlying error: <e>"` |

### `GET /history`

Returns the most recent 50 `generation_history` rows, newest first (`ORDER BY created_at DESC LIMIT 50`), each as a JSON object with the [entity fields](#entity-contract). The UI re-GETs this endpoint to refresh the history list after any synthesis or delete.

### `DELETE /history`

Clears all history. Deletes each row's output file from `parrot_data/outputs/` (path-validated; missing files ignored), then deletes all rows. Returns `{"cleared": true}`. The UI re-fetches `GET /history` afterward to reflect the cleared list.

### `DELETE /history/{id}`

Deletes one history row and its output file (path-validated; missing file ignored). Returns `{"deleted": true}`. The UI re-fetches `GET /history` afterward to drop the removed row.

### `GET /engine/status`

The single engine/device endpoint. Returns `{"active":"omnivoice","device":"<id>"}` where `device` is one of `cuda` or `cpu` (an optional human label may be added as `device_label`). Parrot ships a single fixed engine — there is no engine-switch endpoint and no backends array. The synthesis loading state machine reads model/load status from this endpoint.

### `WS /ws/tts` (optional streaming)

Optional low-latency path for live preview; the primary Speak button uses `POST /generate`. This socket is for **chunked-PCM synthesis only** — it is not an event bus or pub/sub channel.

Protocol:

- Client → server, JSON: `{"text": "...", "voice": "<profile_id>", "language"?: "...", "speed"?: 1.0, "instruct"?: "...", "seed"?: ...}`.
- Server → client, JSON `start`: `{"type":"start","sample_rate":24000,"channels":1,"format":"pcm16","engine":"omnivoice"}`.
- Server → client, binary: raw **PCM16 mono @ 24 kHz** chunks (default 4800 samples ≈ 200 ms per chunk; configurable via `PARROT_STREAM_CHUNK`).
- Server → client, JSON `done`: `{"type":"done","duration_s":...,"gen_time_s":...,"samples":...,"sample_rate":24000,"engine":"omnivoice"}`.
- Server → client, JSON `error`: `{"type":"error","detail":"..."}`.

The connection stays open for subsequent synthesis requests (conversational mode). Generation runs on the same `_gpu_pool` as `/generate`. WS streaming does **not** write a `generation_history` row and does **not** apply effect presets — it applies only broadcast mastering + `-2.0 dBFS` normalization. A request missing `text` yields an `error` message and the socket stays open. The single-engine rule applies: no `engine` selection field is honored.

### `GET /healthz`

Liveness probe owned by the Rust supervisor (process lifecycle). Returns `{"status":"ok"}` only — fast, no torch import, no device field. Synthesis does not depend on it; the supervisor process model is documented in [architecture.md](./architecture.md).

---

## Profile Resolution

Resolution decides the `ref_audio_path`, `ref_text`, `instruct`, and `seed` actually passed to the model. An explicit request field always wins; the profile fills in only what the request left empty.

1. **No `profile_id`, with inline `ref_audio`** → write the upload to a temp `.wav`, use it as `ref_audio_path` (deleted after the request). `ref_text` / `instruct` / `seed` come from the request as-is.
2. **No `profile_id`, no `ref_audio`** → no reference; the model speaks in its default voice using only `text` (and any request `instruct`/`seed`). This is a valid request, not an error.
3. **`profile_id` given but not found** → fall through to default-voice behavior (no reference resolved); `resolved_profile_id` stays `null`, so the history row's `profile_id` is `NULL`.
4. **`profile_id` found and `is_locked` with a `locked_audio_path`** → use the locked clip at `parrot_data/voices/<locked_audio_path>`. Fill `ref_text`, `instruct`, and `seed` from the profile **only if** the request left them empty.
5. **`profile_id` found, not locked, with an `instruct` set** → instruct-style path: pull `instruct` (and `seed` if absent) from the profile; do not set a reference audio path.
6. **`profile_id` found, otherwise** → use `parrot_data/voices/<ref_audio_path>` if present; fill `ref_text`, `instruct`, `seed` from the profile where the request left them empty.
7. **When a profile resolved and `language == "Auto"`** → set `language = null` (model auto-detects).

The lock/unlock mechanics and what `locked_audio_path` captures are defined in [voice-profiles.md](./voice-profiles.md).

---

## DSP Effect Presets

`effect_preset` selects a post-inference DSP chain. Default `"broadcast"`. Unknown ids → `400`.

| id | Label | Summary |
|---|---|---|
| `broadcast` | Broadcast | Radio/podcast standard — warm, compressed, clear (default). |
| `cinematic` | Cinematic | Spacious reverb, gentle compression. |
| `podcast` | Podcast | Close-mic, heavy compression, no reverb. |
| `warm` | Warm | Boosted low-mids, cozy. |
| `bright` | Bright | Crisp high-end, presence boost. |
| `raw` | Raw | No processing — model output as-is. |

Pipeline (non-`raw`): broadcast mastering → preset effect chain → peak-normalize to `-2.0 dBFS`. `raw` short-circuits and returns model output untouched. DSP is best-effort: if the `pedalboard` library is unavailable, every effect degrades gracefully and returns the audio unmodified (audio still ships, just unprocessed).

---

## State Machines

Frontend Svelte store `synthesis` (`frontend/src/lib/stores/synthesis.ts`), driven by the typed client in `frontend/src/lib/api/`. The store has exactly **four** states and carries a `progress` field while a request is in flight:

```ts
type SynthState = "idle" | "submitting" | "done" | "error";
// store value: { state, result?, error?, oom?, progress? }
```

**Synthesis request lifecycle**

```
idle ──speak()──▶ submitting ──(WAV received)──▶ done ──reset()──▶ idle
                       │                            │
                       └──(4xx/5xx)──▶ error ──reset()/speak()──▶ idle
                                                                    ▲
   (user cancels / new speak(): AbortController fires — no error)───┘
```

- `idle` — initial and post-`reset()` state. The Speak button is enabled iff `text` is non-empty.
- `submitting` — one request is in flight (a single `AbortController` enforces one-at-a-time; a new `speak()` aborts the previous). Set with `progress: 0`. The page stays interactive because inference runs off the event loop server-side. **First-ever synthesis sits here while the model loads** (cold load + GPU inference); `progress` stays `0` during the cold model load, then climbs with the diffusion steps (see [§Progress](#progress)).
- `done` — the WAV stream returned; `result` carries the object URL + `X-*` header metadata (`X-Audio-Id`, `X-Seed`, `X-Audio-Duration`, `X-Gen-Time`). The UI refreshes its history list by re-fetching `GET /history` (Business Rule 9).
- `error` — shows the server `detail`. `oom` is set when the message matches the out-of-memory text, and the UI surfaces a **Flush & retry** affordance (Flush reloads the model; the model lifecycle is in [architecture.md](./architecture.md)).
- **Cancel is not an error.** A user navigating away (or firing a new `speak()`) aborts the in-flight fetch via `AbortController`; the aborted request stays quiet and does not transition to `error`.

There is **no** separate `waitingForModel`/`generating`/`streaming`/`playing` state — model load, inference, and streaming are all observed from the single `submitting` state, with the model-load-vs-stepping distinction surfaced through the `progress` field rather than a state transition. Playback is owned by the audio player component, not this store.

### Progress

While the store is `submitting`, the Speak UI shows a **real %-complete bar** instead of an indeterminate spinner, driven by the per-step SSE stream `GET /generate/progress-stream` (see [ipc-contract.md §3 Synthesis](./ipc-contract.md#3-synthesis)).

- **Field:** `progress` is a float `0.0–1.0` on the store value, present only while `submitting`.
- **How it moves:** `progress` starts at `0` (set when `speak()` enters `submitting`) and **stays `0` during the cold model load** — the engine emits no step events until inference begins. It then climbs with the diffusion steps (`pct = step / num_step`), reaching `1.0` only on the terminal `done` event. The tail work (token decode + DSP + WAV encode) is not step-granular, so the engine clamps the bar just under full (`_STEP_CEILING = 0.97`) until completion.
- **How it is wired:** the store opens the stream (`subscribeGenerationProgress`) **just before** `POST /generate` so the bar catches the `start` phase, and closes it when the request settles. The stream replays a tiny buffer on connect, which can include the *previous* generation's tail — the store ignores every event until **this** generation's `start` phase so the bar doesn't flash the old 100%.
- **Best-effort:** if the stream can't open, generation still runs; the bar just stays at its initial "preparing" value (indeterminate). The event shape (`{phase: start|step|done|error, step, total, pct}`), the broadcaster mechanics, and the loopback-only gating are specified in [ipc-contract.md §3 Synthesis](./ipc-contract.md#3-synthesis); the engine-side per-step counter lives in the `generation_progress` service.

**Engine/model status** is read separately from `GET /engine/status` (active engine + resolved device); it is not part of this request-lifecycle store.

---

## Edge Cases

- **Empty / whitespace text** — rejected before any model work. The UI disables Speak; a direct API call with missing `text` returns `400` from form validation.
- **Very long text** — the model handles internal chunking/segmentation; Parrot does not impose a hard character cap at the API. The `text` stored in `generation_history` is truncated to the first 200 chars (display only — the synthesized audio uses the full text). Expect proportionally longer `generation_time`; the request blocks on `_gpu_pool` for the duration. Long inputs are the main OOM trigger (see below).
- **No profile and no `ref_audio`** — valid: synthesizes in the model's default voice. Not an error. `profile_id` in history is `NULL`.
- **`profile_id` not found** — silently falls back to default voice; history `profile_id` is `NULL` (no 404 for this path).
- **Silent or undecodable reference clip** — NOT rejected at create time: the light `/profiles` path has no audio decoder and validates only the file extension (see [voice-cloning.md](./voice-cloning.md) EDGE-3/EDGE-5). An all-silence or corrupt reference therefore surfaces here, at first synthesis, as a generic recoverable `500` (distinct from the OOM 500 — the model is **not** flushed), carrying the "Couldn't synthesize audio…" message. Re-clone with a clearer sample.
- **Out-of-memory mid-generation** — inference aborts; the engine runs `gc.collect()` and empties the accelerator cache (CUDA), then returns `500` with the recoverable message: *"TTS engine stopped mid-generation. This usually means it ran out of memory. Try the Flush button to reload the model, then regenerate."* The UI shows Flush & retry. The GPU pool worker count is itself VRAM-aware (budgeted per job, capped at 4 on CUDA, 1 on CPU), which keeps concurrent jobs from overcommitting.
- **Model not loaded yet** — the first `/generate` (or the first WS request) awaits `get_model()`, which loads weights under an async lock. Concurrent requests wait on the same lock rather than triggering parallel loads. The UI stays in `submitting` with `progress == 0` (no step events arrive during the cold load) and shows the "Preparing model…" bar (see [§Progress](#progress)). If weights aren't downloaded yet, this is the setup/first-run path — see [first-run-setup.md](./first-run-setup.md) for the model-download (SSE) flow; synthesis should be attempted only after `models_ready` is true.
- **Model load failure** — surfaced via engine status `error` with a message; `/generate` raises `500` and the UI shows the failure rather than spinning forever.
- **Unknown `effect_preset`** — `400` with the valid-preset list; never a 500.
- **`pedalboard` missing** — audio still returns, unprocessed (graceful degradation); no error.
- **Path traversal in history file ops** — `audio_path` is validated to a basename resolving inside `parrot_data/outputs/` before any delete/read; out-of-tree names are ignored, not acted on.
- **Inline reference temp-file leak** — the temp `ref_audio` file is removed in a `finally` block even on error; an OS error during cleanup is suppressed.
- **WS client disconnects mid-stream** — the server stops sending chunks and closes the socket cleanly; the disconnect is logged server-side, and no history row is written for WS.

---

## Data

| Store / file | Touched by | How |
|---|---|---|
| `parrot_data/parrot.db` → `generation_history` | `POST /generate` (insert), `GET/DELETE /history`, `DELETE /history/{id}` | One row inserted per success; read newest-50; deleted on clear. |
| `parrot_data/parrot.db` → `voice_profiles` | `POST /generate`, `WS /ws/tts` | Read-only during [resolution](#profile-resolution). |
| `parrot_data/outputs/<id>.wav` | `POST /generate` (write), `DELETE /history*` (remove) | 24 kHz WAV written via the audio-IO save path; deleted when its history row is deleted. |
| `parrot_data/voices/<file>` | `POST /generate`, `WS /ws/tts` | Read-only: resolved `ref_audio_path` / `locked_audio_path` for a profile. |
| Temp dir | `POST /generate` (inline `ref_audio` only) | Transient `.wav`; deleted after the request. |
| In-memory model + `_gpu_pool` | `POST /generate`, `WS /ws/tts` | Lazy-loaded model on the GPU thread pool; idle-unloaded after the configured timeout to free VRAM. |

The `generation_history` table is owned and fully specified here; the cross-spec data-model overview lives in [../../CLAUDE.md](../../CLAUDE.md). `parrot_data/` must survive upgrades with no manual migration; schema changes go through alembic with a tested upgrade path (see [../../CLAUDE.md](../../CLAUDE.md) and [architecture.md](./architecture.md)).
