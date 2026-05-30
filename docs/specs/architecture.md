# Architecture

The system map for **Parrot** — a fully-local desktop app that does two things: **clone a voice** from a short reference sample, and **speak typed text** in that voice. No accounts, no API keys, no cloud. This is the first spec a new contributor should read; every other spec assumes the process model, port model, and lifecycle described here.

Parrot is an independent, Apache-2.0 open-source app — **not** a code fork of OmniVoice Studio. It reuses **only** the Apache-2.0 `omnivoice` model library for inference and reimplements its own app (Svelte UI, Rust shell, Python sidecar) from these specs; OmniVoice's FSL-1.1-ALv2 app code is a design reference only, never copied (see [../LICENSING.md](../LICENSING.md)). Scoped to just clone-and-speak, it ships none of OmniVoice's other features (dubbing, general dictation/ASR, gallery, batch, marketplace, the multi-engine picker) — the one narrow exception is auto-transcribing the clone reference clip to fill `ref_text` (see [transcription.md](./transcription.md)), which serves cloning and is not a dictation feature. It ships **one** TTS backend: `omnivoice` (pure-Python via `transformers`/PyTorch). There is no optional C++ GGUF backend — the default build produces audio with no extra setup.

---

## 1 — The three-process model

Parrot runs as **three cooperating processes inside one Tauri window**. Each has a single, non-overlapping responsibility, and they communicate only over the documented IPC surface (§4). No process reaches into another's internals.

```
┌────────────────────────────────────────────────────────────────────────┐
│  Tauri window (one OS process tree)                                      │
│                                                                          │
│   ┌──────────────────────┐        Tauri command IPC                      │
│   │  Svelte UI (WebView)  │◄──────────────────────────────┐             │
│   │  Bun · SvelteKit SPA  │   file dialogs, reveal files,   │             │
│   │  TypeScript (strict)  │   tray, updater, fs glue        │             │
│   └──────────┬───────────┘                                 ▼             │
│              │  HTTP REST  ───────────────►   ┌────────────────────────┐  │
│              │  http://127.0.0.1:3900         │  Tauri shell (Rust)     │  │
│              │  WS  ws://127.0.0.1:3900/ws/tts│  • window / tray /      │  │
│              │                                │    updater / dialogs    │  │
│              ▼                                │  • SPAWNS + HEALTH-      │  │
│   ┌────────────────────────────────────┐     │    CHECKS + TEARS DOWN  │  │
│   │  Python FastAPI sidecar             │◄────┤    the Python sidecar   │  │
│   │  Python 3.11+ · PyTorch · uvicorn   │     │  • supervisor module    │  │
│   │  • the voice engine (OmniVoice)     │     │    OWNS process lifecycle│  │
│   │  • owns SQLite DB + on-disk audio   │     └───────────┬────────────┘  │
│   │  • only process that touches a GPU  │   GET /healthz   │ spawn/kill    │
│   │  • bound to 127.0.0.1:3900 only     │◄────────────────┘ (loopback)   │
│   └────────────────────────────────────┘                                 │
│              │                                                            │
│              ▼  reads/writes                                              │
│         parrot_data/  (voices · generated audio · parrot.db · settings)  │
└────────────────────────────────────────────────────────────────────────┘
```

### 1.1 — Svelte UI (the front of house)

- Built with **Bun** (SvelteKit in **SPA mode**, TypeScript `strict`). Renders inside the Tauri WebView.
- Talks to the engine over **HTTP REST** at `http://127.0.0.1:3900` and over **WebSocket** at `ws://127.0.0.1:3900/ws/tts`. It talks to the Rust shell over **Tauri commands** (native dialogs, reveal-in-folder, tray). Audio playback is the WebView's own HTML `<audio>` — not a native command.
- **Never imports Python or torch.** It knows only the IPC contract — request/response shapes, header names, status codes. It cannot tell whether the engine is loaded, what device it runs on, or where files live except through the contract.
- Typed IPC clients live in `frontend/src/lib/api/`. UI state lives in **Svelte stores** under `frontend/src/lib/stores/`.
- During development, the dev server runs on **`:3901`** (`bun run dev`); the WebView loads from there. In a packaged build the UI is served as static assets from the bundle, not from `:3901`.

### 1.2 — Tauri shell (the supervisor + native glue)

The Rust process. Two jobs:

1. **Native desktop integration** — the window, the tray, file open/save dialogs, reveal-in-folder, the auto-updater, and filesystem/path glue, all exposed to the UI as Tauri commands. (Audio *playback* is the WebView's HTML `<audio>`, not a native command — only the WAV export *save dialog* is native.)
2. **The single most critical responsibility: owning the Python sidecar lifecycle.** The Rust shell **spawns**, **health-checks**, **restarts**, and **tears down** the Python process. This is the *only* place in the system that owns the sidecar's lifetime — the UI cannot start or stop it, and the sidecar never forks itself. See §3.

> **Invariant.** Exactly one module (the backend/supervisor module in `src-tauri/`) owns the Python process handle. If you need to start, stop, or probe the sidecar, do it there — never by shelling out from the UI or from another Rust module.

### 1.3 — Python FastAPI sidecar (the engine)

- Python 3.11+, **PyTorch + `transformers`**, served by `uvicorn`. This is the actual voice engine — it wraps the **OmniVoice** model (default backend id `"omnivoice"`).
- **Why Python:** the model is PyTorch. There is no pure-Rust inference path, so the engine is a separate process the Rust shell babysits.
- **Owns all durable state**: the SQLite database, all reference/generated audio on disk, and the settings store (see §7). No other process writes to `parrot_data/`.
- The **only** process that needs a GPU. Device is auto-detected (**CUDA / CPU**) at model-load time and reported to the UI via `GET /engine/status` (§4); the UI and Rust shell are otherwise device-agnostic. See [device-detection.md](./device-detection.md).
- **Bound to `127.0.0.1` only** by default. Parrot ships **no authentication**; binding to `0.0.0.0` would expose every route to the LAN. Loopback-only is the security boundary.
- Model output is **24 kHz**. The model supports ~600 zero-shot languages (default `language = "Auto"`).

---

## 2 — IPC transports & the localhost port model

Three transports, each for a distinct interaction style:

| Transport | Between | Used for |
|-----------|---------|----------|
| **HTTP REST** | UI → sidecar | Request/response: generate, profiles CRUD, history, setup status, engine status. See §4 and [ipc-contract.md](./ipc-contract.md). |
| **WebSocket** | UI → sidecar | `ws://127.0.0.1:3900/ws/tts` — *optional* streaming synthesis (chunked PCM) for low-latency playback. This is a synthesis channel **only**, not an event bus. |
| **SSE (HTTP)** | UI → sidecar | First-run model-download progress stream (`GET /setup/download-stream`, one-way server push). |
| **Tauri command IPC** | UI → Rust shell | Native concerns: file dialogs, reveal-in-folder, tray, updater, and supervisor status (e.g. `bootstrap_status`). Playback is the WebView's HTML `<audio>`, not a Tauri command. |

### Port model

| Port | Bind | Role | Source of truth |
|------|------|------|-----------------|
| **3900** | `127.0.0.1` | Python sidecar (REST + WS + SSE). Picked to dodge common `8000` conflicts (Django/Rails/Jupyter). | Backend `uvicorn.run(host="127.0.0.1", port=3900)`; Rust `backend_port()` defaults to `3900` and **must stay in sync**. |
| **3901** | `localhost` | Frontend **dev server** only (`bun run dev`). Not present in packaged builds. | `devUrl` in `tauri.conf.json`. |

Both ports are overridable for power users via an env var (e.g. `PARROT_PORT` on the sidecar side); the Rust supervisor reads the same override so the spawn target and the health probe never diverge. CORS on the sidecar allows the dev origin (`http://localhost:3901`, `http://127.0.0.1:3901`) and the Tauri WebView origins (`tauri://localhost`, `http://tauri.localhost`, `https://tauri.localhost`).

---

## 3 — Sidecar lifecycle (owned by the Rust supervisor)

The supervisor runs the sidecar through a small state machine on a dedicated thread so the UI thread is never blocked. Stages are surfaced to the splash screen via a Tauri command (`bootstrap_status`, a pull/backfill of the current stage) plus two Tauri event streams the splash subscribes to (supervisor → splash only; this is not a sidecar pub-sub channel):

- **`bootstrap-stage`** — emitted on every stage transition (`checking → … → ready | failed`); the payload is the new stage string. This is the push counterpart to the `bootstrap_status` pull.
- **`bootstrap-log`** — a per-line log stream (each stage transition also emits a `[stage] <name>` log line). Backfillable via the `get_bootstrap_logs` command so a late-mounting splash doesn't miss early lines.

### 3.1 — Startup ordering

```
Checking
  → (first run only) CreatingVenv → InstallingDeps
  → StartingBackend → Ready
  → Failed { message }            (on any unrecoverable error)
```

1. **Checking.** Decide whether anything needs bootstrapping.
2. **Attach-if-already-healthy.** Before spawning, probe the port. If a healthy Parrot sidecar is *already* serving `:3900`, **attach to it** (don't spawn a second one) and jump straight to `Ready`. This makes `bun run dev` against a manually-started backend work, and makes a relaunch reuse a surviving sidecar.
3. **Port-in-use handling.** If *something* is listening on `:3900` but it is **not** a healthy Parrot sidecar (failed the health check), the supervisor **takes ownership**: it kills the orphan on that port, waits briefly, then proceeds to spawn. (See §3.5.)
4. **Ensure the venv is ready** (first run): using the **bundled** `uv` binary (shipped as the app's only `externalBin` — not downloaded at runtime), create a Python 3.11 venv at `parrot_data/.venv` and `uv sync` the locked dependencies (the venv location is pinned via `UV_PROJECT_ENVIRONMENT` so it lands under the writable data dir, never inside the read-only program-files bundle). On every subsequent launch the venv is reused; bundle source dirs are re-synced so app updates land without a full reinstall. Detail lives in [packaging.md](./packaging.md).
5. **StartingBackend.** Spawn the sidecar by launching the bootstrapped venv's Python **directly** (`parrot_data/.venv/Scripts/python.exe main.py`) — *not* via `uv run` — so the immediate child process **is** Python and the supervisor can reliably terminate it on exit. (A `uv run` wrapper forks Python as a *grandchild* that `child.kill()` cannot reach on Windows, orphaning the GPU-holding engine.) The port is passed as the `PARROT_PORT` env var (and `PARROT_DATA_DIR` so the two processes agree on the data dir); `main.py` internally calls `uvicorn.run(host="127.0.0.1", port=PARROT_PORT)`. The child's stdout/stderr are **piped to log files** in the app log dir — `backend.log` and `backend_err.log` respectively (§7) — not inherited from the parent.
6. **Poll for health** (§3.2). On success → `Ready` and the splash dismisses. On repeated crash-restart failures (or the deadline elapsing) → `Failed`. The supervisor emits a `sidecar-failed` event carrying the failure count once it gives up after `MAX_RAPID_FAILURES` (5) consecutive never-healthy starts. *(Attaching the tail of the sidecar's stderr to the failure event is a future refinement; the raw stderr is already on disk in `backend_err.log`.)*

> **Supervisor state note.** The supervisor (`src-tauri/src/supervisor.rs`) implements this full lifecycle: attach-if-already-healthy (step 2), port-in-use takeover (step 3), venv bootstrap (step 4), spawn with stdout/stderr piped to log files (step 5) → `/healthz` poll → restart-with-exponential-backoff → give up + `sidecar-failed` after `MAX_RAPID_FAILURES` never-healthy starts → park in `failed` until a Retry / Reset & retry command (§3.4) → kill-on-exit. Stages are surfaced to the splash via a `bootstrap-stage` event plus a `bootstrap-log` line stream (§3 intro, §5.1).

### 3.2 — `/healthz` polling

- The supervisor polls a health endpoint on the sidecar (`GET /healthz`) on a fixed interval (every ~500 ms) until it gets a positive response or hits the startup deadline (**300 s** — first-run model/dep download is slow).
- `GET /healthz` returns `{"status":"ok"}` and nothing else — it's a fast liveness probe with no torch import and **no device field** (device lives on `GET /engine/status`, §4). A response counts as healthy only if the body is exactly that ok payload (the supervisor inspects the body, not just "a socket accepted"). A foreign server squatting on `:3900` fails the check and triggers port takeover (§3.5).
- Distinguish two probes:
  - **Liveness / readiness (Rust → sidecar):** `GET /healthz` — *is the HTTP server up?* Cheap; returns immediately even before the model is loaded.
  - **Model status (UI → sidecar):** a separate status field reports `idle` / `loading` / `ready` with a sub-stage and a 0–100 download/load percentage. The HTTP server is healthy long before the model weights finish loading — the UI shows a "model loading" pill while `/healthz` is already green.

### 3.3 — Graceful shutdown on window close

- Closing the **main** window does **not** quit the app. The window is hidden and removed from the taskbar; the sidecar keeps running. Quit happens only via the tray **Quit** item, which sets an internal `quitting` flag and exits.
- On the real exit event, the supervisor shuts the sidecar down gracefully: it terminates the child process and `wait()`s on it so no zombie is left behind.
- The sidecar's own FastAPI lifespan shutdown unloads the model, frees GPU memory, runs GC, and closes connection pools — so a clean teardown releases VRAM promptly.

### 3.4 — Restart-on-crash with backoff

- If the sidecar exits **during startup polling**, the supervisor detects the dead child (it reaps the exit status) and counts it as a never-healthy start — it does not silently spin. After `MAX_RAPID_FAILURES` (5) consecutive never-healthy starts it moves to `Failed` and emits `sidecar-failed` carrying the **failure count**. The raw stderr is already on disk in `backend_err.log` (§7); attaching its tail to the failure event is a future refinement (see §3.1).
- Recovery is explicit and bounded: the splash exposes **Retry** and **Reset & retry** actions (Tauri commands) that reset the state machine and re-run the spawn sequence. **Reset & retry** additionally removes the bootstrapped `parrot_data/.venv` dir (corrupt venv) and kills any stale sidecar still holding the port before re-bootstrapping.
- Restart attempts use an **exponential backoff** between tries (a short initial delay, doubling, capped) so a hard-crashing engine doesn't hot-loop the GPU or flood the logs. A surviving-but-unhealthy sidecar is always killed before a fresh spawn so two engines never race for `:3900`.

### 3.5 — Port-in-use handling

| Situation on `:3900` | Supervisor action |
|----------------------|-------------------|
| Healthy Parrot sidecar already serving | **Attach** — reuse it, go to `Ready`. No second spawn. |
| Something listening, but **not** healthy Parrot | **Take ownership** — find the process holding `:3900` and kill it, wait ~500 ms, then spawn. |
| Nothing listening | Spawn normally. |

Single-instance is enforced at the app level: a second Parrot launch focuses the existing window instead of starting a competing sidecar.

---

## 4 — IPC Contract (engine surface)

The sidecar exposes a small REST surface (full shapes in [ipc-contract.md](./ipc-contract.md); this section is the index). All paths are relative to `http://127.0.0.1:3900`. Routes are **unprefixed** (no `/api/` prefix).

**Synthesis**
- `POST /generate` *(multipart form)* — speak typed text. Key params: `text` (required), `language?`, `ref_audio?` (file), `ref_text?`, `instruct?` (de-emphasized style; see [voice-profiles.md](./voice-profiles.md)), `duration?`, `num_step=16`, `guidance_scale=2.0`, `speed=1.0`, `denoise=true`, `postprocess_output=true`, `profile_id?`, `seed?`, `effect_preset="broadcast"` (+ advanced: `t_shift?`, `layer_penalty_factor?`, `position_temperature?`, `class_temperature?`). **Returns** a `StreamingResponse` of `audio/wav` with headers `X-Audio-Id`, `X-Gen-Time`, `X-Audio-Path`, `X-Seed`, `X-Audio-Duration`. **Profile resolution:** if `profile_id` is given and the profile is locked (`is_locked` + `locked_audio_path`), use the locked audio plus the stored `ref_text`/`seed`; otherwise use the profile's `ref_audio_path`. Writes one `generation_history` row.
- `WS /ws/tts` — optional streaming synthesis (chunked PCM). Synthesis channel only.

**Voice profiles** (clone → reusable profile) — see [voice-profiles.md](./voice-profiles.md)
- `GET /profiles` · `POST /profiles` *(form: `name`, `ref_audio` file, `ref_text`, `instruct`, `language`, `seed`)* · `GET /profiles/{id}` · `PUT /profiles/{id}` *(json: `name?`, `ref_text?`, `instruct?`, `language?`)* · `GET /profiles/{id}/audio` · `GET /profiles/{id}/usage` · `POST /profiles/{id}/lock` *(form: `history_id`, `seed?`)* · `POST /profiles/{id}/unlock` · `DELETE /profiles/{id}`
- `GET /profiles/{id}/usage` returns `{ "synth_recent": [≤20 most-recent generation_history rows], "synth_total": int }`.

**History**
- `GET /history` · `DELETE /history` · `DELETE /history/{id}`

**Setup / first-run** — see [first-run-setup.md](./first-run-setup.md)
- `GET /setup/status` → `{ "models_ready": bool, … }` · `POST /setup/download` (starts the model download) · `GET /setup/download-stream` (download-progress **SSE** stream).

**Settings (HF token)** — see [settings.md](./settings.md)
- `GET /settings/hf-token` (masked) · `POST /settings/hf-token` (set) · `DELETE /settings/hf-token` (clear). The token is optional and only needed for gated model download; resolution order is (1) the in-app encrypted setting in the `settings` table (per-install Fernet key, the default path) then (2) the `HF_TOKEN` environment variable (documented power-user override).

**Engine / device status (read-only)**
- `GET /engine/status` → `{ "active": "omnivoice", "device": "<id>" }` where `device ∈ {"cuda","cpu"}` (an optional human label may appear as `"device_label"`). Parrot ships a single fixed engine; there is **no** engine picker and no `backends` array. This is the **one** place the active device is reported to the UI.

**Health (supervisor only)**
- `GET /healthz` → `{"status":"ok"}` — used by the Rust supervisor for liveness polling (§3.2). Not part of the UI's normal flow; carries no device field.

### Error cases (engine surface)

- **4xx** — validation (e.g. `/generate` with no `text`, unknown `profile_id`, malformed multipart) → JSON `{ "detail": … }`.
- **5xx** — unhandled engine error (model load failure, OOM, decode crash). The sidecar serializes the traceback to a crash log and returns `{ "detail": <message> }`; CORS headers are attached so the WebView sees the real message rather than a bare CORS error.
- **Mid-stream client disconnect** (UI cancels a `<audio>`/range fetch) → treated as a benign disconnect: logged server-side, never returned as a status to the gone client.
- **Connection refused / unreachable** — the sidecar isn't up yet. The UI must treat this as "engine starting" and defer to the supervisor's `bootstrap_status`, not surface it as a hard error.

---

## 5 — State machines (frontend)

The UI mirrors two independent lifecycles in Svelte stores; both gate what the user can do.

### 5.1 — Bootstrap store (driven by Tauri `bootstrap-stage` + `bootstrap-log`, backfilled by `bootstrap_status` + `get_bootstrap_logs`)

```
checking
  → creating_venv ──► installing_deps ──► starting_backend
  → ready            (engine healthy — leave the splash, enter the app)
  → failed{message}  (show error + Retry / Reset & retry actions)
```

- Transitions are **push** (the `bootstrap-stage` event drives the state; the `bootstrap-log` line stream feeds the log tail) with a **pull** backfill (`bootstrap_status` for the current stage + `get_bootstrap_logs` for the log tail on mount) so a late-mounting splash doesn't miss early stages or lines.
- `failed → checking` is the only user-initiated transition (the `retry_bootstrap` / `clean_and_retry_bootstrap` commands behind the Retry / Reset & retry actions).

### 5.2 — Model store (driven by the sidecar's model-status field)

```
idle ──(first /generate or background preload)──► loading{sub_stage, progress}
loading ──► ready
loading ──► error{message}
ready ──(idle timeout on the engine)──► idle   (model unloaded to free VRAM)
```

- `ready` here is independent of bootstrap `ready`: the HTTP server (and `/healthz`) goes green well before the weights finish loading. The UI shows a non-blocking "model loading — N%" pill while still allowing navigation.
- After an idle period with no synthesis, the engine unloads the model and frees GPU memory; the next `/generate` re-loads it (`idle → loading → ready`). The UI should treat the first post-idle generation as "may be slow."

---

## 6 — Edge cases

- **Two engines racing for `:3900`.** Always kill an unhealthy squatter before spawning; never spawn a second sidecar while a healthy one is attached. Single-instance prevents a second app launch from competing.
- **Sidecar healthy but model not loaded.** `/healthz` green (`{"status":"ok"}`) ≠ ready to synthesize. The UI must read the model-status field, not infer readiness from health.
- **First-run download is slow / flaky.** Dep + model download can take many minutes; the 300 s health deadline plus visible per-line bootstrap logs keep the splash honest. A dead child during polling counts as a never-healthy start; repeated never-healthy starts → `Failed` (with the failure count; raw stderr in `backend_err.log`), not an infinite spinner.
- **Stale/zombie sidecar from a previous run** holding `:3900` after an unclean exit → port-takeover path (§3.5) reclaims it; Reset & retry kills it explicitly.
- **Window closed vs app quit.** Closing the main window only hides it (sidecar keeps running, VRAM stays held until idle-unload). Only tray-Quit triggers teardown. A spec or test that assumes "close window = engine stops" is wrong.
- **GPU OOM mid-generation.** Surfaces as a 5xx with the real message; the engine frees VRAM on the next idle sweep. Multi-job VRAM is bounded by the engine's worker pool sizing.
- **Foreign loopback hosts.** The sidecar binds `127.0.0.1` only; a `0.0.0.0` bind is an explicit power-user opt-in (env var) and is never the default — it would expose an unauthenticated API to the LAN.
- **Dev vs packaged divergence.** `:3901` exists only under `bun run dev`. In a packaged build the UI loads from bundled static assets; nothing should hardcode `:3901` as a runtime dependency. The dev and packaged builds must present the same user-visible lifecycle (Windows 10/11, x64).

---

## 7 — Data ownership & on-disk layout

The **Python sidecar is the sole owner** of durable state. The Rust shell and the UI never read or write the database or audio files directly — they go through the REST surface, and the shell additionally manages only its *own* logs and the bootstrapped `parrot_data/.venv` dir.

| Path (`parrot_data/`) | Owner | Contents |
|-----------------------|-------|----------|
| `parrot_data/voices/` | sidecar | Reference audio for voice profiles |
| `parrot_data/outputs/` | sidecar | Generated audio (WAV, 24 kHz) |
| `parrot_data/parrot.db` | sidecar | SQLite (WAL, `foreign_keys` ON) — `voice_profiles`, `generation_history`, `settings` |
| `parrot_data/` settings rows | sidecar | `settings` key/value store; secrets (e.g. HF token) stored encrypted |
| App log dir | Rust shell | `backend.log`, `backend_err.log` (sidecar stdout/stderr piped by the supervisor). A Tauri-side `parrot.log` is *planned* but **not yet written**: no Tauri logging plugin is registered, so `read_log_tail(source: "tauri")` returns `exists: false` until logging is wired. |
| `parrot_data/.venv` | Rust shell | Bootstrapped venv + synced backend sources (managed only by the supervisor; see [packaging.md](./packaging.md)) |

Data-dir location is `%APPDATA%\Parrot\…` (overridable via env var). **`parrot_data/` must survive upgrades with no manual migration** — the DB is created idempotently and evolved via alembic with a tested upgrade path (per [../../CLAUDE.md](../../CLAUDE.md)).

### Tables touched

- **`voice_profiles`** — `id`, `name`, `ref_audio_path`, `ref_text`, `language`, `instruct`, `locked_audio_path`, `seed`, `is_locked`, `created_at`. Written by the profiles routes; read during profile resolution in `/generate`. Schema and rules in [voice-profiles.md](./voice-profiles.md).
- **`generation_history`** — `id`, `text`, `language`, `profile_id` (FK → `voice_profiles(id)`, nulled on profile delete), `audio_path`, `duration_seconds`, `generation_time`, `seed`, `created_at`. One row per `/generate`. (There is no `mode` column.)
- **`settings`** — `key` (PK), `value` (encrypted for secrets), `updated_at`. Used for the HF token; **not** for appearance (theme/zoom are frontend-local — see [settings.md](./settings.md) / [design-system.md](./design-system.md)).

---

## 8 — How Python ships invisibly

The Python sidecar — interpreter, venv, and engine deps — is **not** something the user installs. The standalone `uv` binary is the only Tauri **`externalBin`** (Parrot has no dubbing/media-extraction path, and its reference-ASR decode uses PyAV's in-wheel ffmpeg libs, so the standalone `ffmpeg`/`ffprobe` binaries are still dropped — see [packaging.md](./packaging.md) Rule 5 and [transcription.md §3](./transcription.md)), and the backend source plus `pyproject.toml`/`uv.lock` ride along as bundle **resources**. On first run the supervisor uses `uv` to create a Python 3.11 venv at `parrot_data/.venv` and `uv sync` the locked dependencies into it; subsequent launches reuse it and only re-sync source on app update.

This is summarized here intentionally — full detail (externalBin layout, resource resolution, `uv` mirror/region fallbacks, build steps, signing) lives in [packaging.md](./packaging.md).

---

## Related specs

- [ipc-contract.md](./ipc-contract.md) — full REST request/response shapes and headers
- [voice-cloning.md](./voice-cloning.md) — the capture flow (record/upload, normalization, create→profile state machine)
- [voice-profiles.md](./voice-profiles.md) — VoiceProfile data model, `/profiles` CRUD + lock/unlock + usage rules
- [synthesis.md](./synthesis.md) — synthesis parameters & playback
- [first-run-setup.md](./first-run-setup.md) — model download & setup surface
- [packaging.md](./packaging.md) — bundle layout, `externalBin`, `uv` bootstrap detail
- [device-detection.md](./device-detection.md) — CUDA / CPU selection
- [settings.md](./settings.md) — HF token store & appearance (theme/zoom)
- [design-system.md](./design-system.md) — palette tokens, theme model
- [../../CLAUDE.md](../../CLAUDE.md) — project conventions & local-first constraints
