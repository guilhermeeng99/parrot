# Parrot — Project Conventions

**Parrot** is a fully-local desktop app that does exactly two things well: **clone a voice from a short reference sample**, and **make that voice speak any text you type**. No accounts, no API keys, no cloud. It runs on the user's own machine (CUDA / MPS / ROCm / CPU auto-detect) across macOS, Windows, and Linux.

Parrot is an independent, **Apache-2.0** open-source app built on the same Apache-2.0 voice model as [OmniVoice Studio](https://github.com/debpalash/OmniVoice-Studio). It is **not a code fork** — it reimplements its own app and is scoped to just clone-and-speak, with none of OmniVoice's other features (video dubbing, dictation/ASR, voice gallery, batch, marketplace, multi-engine). OmniVoice is a **design reference only**; its FSL-licensed app code is not copied. See [docs/specs/architecture.md](docs/specs/architecture.md) for the full picture and [docs/LICENSING.md](docs/LICENSING.md) for the licensing rationale.

**Core value:** *a first run that actually works.* Download, install, clone your voice, hear it speak — without hitting a wall. Everything downstream is secondary to that path staying reliable on every platform.

---

## Scope

**In scope (the whole product):**

1. **Clone** — record or upload a reference sample, store it as a reusable voice profile.
2. **Speak** — type text, pick a profile (or a one-off reference), synthesize speech, play/export it.

**Explicit non-goals** (do not add these without changing this doc first):

- Video dubbing, lip-sync, subtitle handling
- Real-time dictation / speech-to-text (ASR)
- Voice "gallery" / YouTube clipping
- Batch/queue processing
- Voice marketplace / bundle import-export
- Multi-engine TTS picker (Parrot ships **one** engine: OmniVoice)
- Any cloud call, account, or telemetry endpoint

When a feature request lands, the first question is "does this serve clone-or-speak?" If not, it is declined or deferred — not built.

---

## Architecture

Parrot is **three cooperating processes** inside one desktop window. This is the single most important thing to understand before touching code.

```
┌─────────────────────────────────────────────────────────────┐
│  Tauri window (one OS process tree)                           │
│                                                               │
│   Svelte UI (Bun-built)  ──HTTP/WS──>  Python FastAPI sidecar │
│        │  (webview)                         │  (PyTorch)      │
│        │                                    ▲                 │
│        ▼                                    │ spawn /         │
│   Tauri shell (Rust)  ──────supervise───────┘ health / kill   │
│        - window, tray, file dialogs, updater                  │
│        - spawns + supervises the Python sidecar               │
│        - native glue (audio playback, fs, paths)              │
└─────────────────────────────────────────────────────────────┘
```

- **Svelte + Bun** — the UI. Talks to the sidecar over `http://127.0.0.1:<port>` (REST) and `ws://127.0.0.1:<port>/ws/tts` (streaming synthesis). Never imports Python or torch; it only knows the IPC contract.
- **Rust (Tauri shell)** — the native host. Owns the window/tray/dialogs/updater, and is responsible for **spawning, health-checking, and tearing down** the Python sidecar. Native glue (audio playback, filesystem, path resolution) lives here as Tauri commands.
- **Python FastAPI sidecar** — the voice engine. Wraps the OmniVoice model (PyTorch + transformers). Loads weights, runs inference, owns the SQLite DB and the on-disk voice/audio files. This is the only process that needs a GPU.

The engine is Python because the model is PyTorch — there is no pure-Rust path without reimplementing the model. The sidecar is shipped invisibly inside the bundle (see [docs/specs/packaging.md](docs/specs/packaging.md)); the user never sees Python.

### Repository layout

```
parrot/
├── frontend/                 # Svelte app (Bun) + Tauri shell
│   ├── src/                  # Svelte components, routes, stores, IPC clients
│   └── src-tauri/            # Rust: shell, sidecar supervisor, commands, bundler config
├── sidecar/                  # Python FastAPI engine — Parrot's own code, built from the specs
│   ├── api/routers/          # generate, profiles, setup, engine-status
│   ├── core/                 # config (device detect), db, event bus
│   ├── services/             # model_manager, tts_backend, audio_dsp
│   └── parrot_engine/        # the OmniVoice model lib (Apache-2.0) — vendored, import name unchanged
├── docs/                     # specs + roadmap (this is the source of truth — read before coding)
└── scripts/                  # bootstrap (uv venv), smoke test, packaging helpers
```

> Naming note: the vendored model package keeps its original `omnivoice` import path (it is the Apache-2.0 model lib — see LICENSING). Only the *app* is rebranded to Parrot. Renaming the Python package is invasive churn for zero benefit.

---

## Tech stack

| Aspect | Choice | Notes |
| --- | --- | --- |
| **Desktop shell** | Tauri v2 (Rust) | Window, tray, dialogs, updater, sidecar supervisor |
| **Frontend** | Svelte + SvelteKit (SPA mode) | Mode/route-based UI; no SSR (it's a desktop app) |
| **Frontend tooling** | Bun | Package manager + dev server + build |
| **Frontend lang** | TypeScript (strict) | All UI + IPC clients typed |
| **Engine** | Python 3.11+ + FastAPI | Local sidecar; REST + WS |
| **ML runtime** | PyTorch + transformers | OmniVoice model; CUDA/MPS/ROCm/CPU auto-detect |
| **Python env** | uv | Bootstraps the venv; shipped as a Tauri `externalBin` sidecar |
| **Storage** | SQLite (WAL) + on-disk audio | `voice_profiles`, `generation_history`, `settings` |
| **Migrations** | alembic + idempotent `CREATE TABLE IF NOT EXISTS` | Tested upgrade path; never break existing user data |
| **Audio I/O** | torchaudio / soundfile (Python), Web Audio + Rust playback (frontend) | 24 kHz model output |

---

## Code Style

Shared principles across all three languages:

- **One responsibility per function/module.** Functions 5–40 lines; split when longer.
- **Files under ~400–600 lines.** The OmniVoice `App.jsx` was 1138 lines — do not repeat that.
- **Early returns over nested conditionals.** Max 2 levels of indentation.
- **Specific, intention-revealing names.** Avoid `data`, `manager`, `handler`, `utils2`.

### Svelte / TypeScript

- Components small and composable; one concern each.
- All UI state in Svelte stores (`src/lib/stores/`); components stay presentational.
- IPC lives behind typed clients in `src/lib/api/` — components never call `fetch` directly.
- No hardcoded user-facing strings if i18n is enabled; otherwise centralize copy.

### Rust (Tauri)

- Tauri commands are thin; heavy logic in dedicated modules.
- The sidecar supervisor is the only owner of the Python process lifecycle — no other module spawns it.
- Return typed errors to the frontend; never `unwrap()` on a path the user can hit.

### Python (sidecar)

- Routers thin; logic in `services/`. Routers import services, services never import routers.
- `model_manager.get_model()` is the single entry point for model access — never instantiate the model elsewhere.
- Redact secrets (`*TOKEN*`, `*KEY*`, `*SECRET*`) from any error surfaced over the API.

---

## Comments

- Write **WHY**, not WHAT.
- Preserve decisions and non-obvious context (especially cross-platform workarounds — e.g. the Windows HF-cache path-length fix).
- Do not strip meaningful comments during refactors.
- Public IPC handlers document: intent, params, return shape, error cases.

---

## Commands

```bash
# Frontend (from frontend/)
bun install                 # install deps
bun run dev                 # Svelte dev server (localhost:3901)
bun run build               # production build
bun run tauri dev           # full app: shell + frontend + sidecar
bun run tauri build         # bundle installers (dmg/msi/deb/appimage)

# Sidecar (from sidecar/)
uv sync                     # create/refresh the Python venv
uv run uvicorn main:app --port 3900   # run the engine standalone
uv run pytest               # engine tests

# Rust (from frontend/src-tauri/)
cargo check                 # typecheck the shell
cargo clippy                # lint (zero warnings)
cargo test                  # shell tests

# Whole-app smoke test
bash scripts/smoke-test.sh  # wipe data, build, verify first-run + a real generation
```

---

## Post-Change Checklist

After every change, before considering it done:

1. **Frontend:** `bun run build` clean; `bunx tsc --noEmit` zero errors.
2. **Rust:** `cargo clippy` zero warnings; `cargo test` green.
3. **Python:** `uv run pytest` green; secrets never logged.
4. **If the IPC contract changed:** update [docs/specs/ipc-contract.md](docs/specs/ipc-contract.md) **and** the typed client in `frontend/src/lib/api/` in the same change.
5. **If the DB schema changed:** add an alembic migration with a tested upgrade from the previous version. Existing `parrot_data/` must keep working with no manual migration.
6. **Cross-platform:** if you touched a default-mode feature, confirm it behaves identically on macOS/Windows/Linux, or move it behind explicit opt-in (see rule below).
7. Run `scripts/smoke-test.sh` for anything touching the first-run, sidecar lifecycle, or generation path.

---

## Spec-Driven Development

Every feature MUST have a spec in `docs/specs/<feature>.md` **before** writing code or tests. Specs are the source of truth; code conforms to them, not the reverse.

### Workflow

1. Write or update the spec (entities, business rules, IPC contract, state machines, edge cases).
2. Write tests from the spec.
3. Implement until tests pass.
4. Update the spec if requirements change — never let code and spec drift.

### Spec structure (every spec follows this)

- **Entity Contract** — fields, types, invariants.
- **Business Rules** — numbered, testable statements.
- **IPC Contract** — endpoints/commands: method, path, params, return shape, errors.
- **State Machines** — frontend store states + transitions (where relevant).
- **Edge Cases** — the failures and corner cases that must be handled.
- **Data** — tables/files touched.

Cross-link related specs with relative links: `[device-detection.md](./device-detection.md)`.

---

## Testing Rules

- Every new behavior gets a test; every bug fix gets a regression test.
- Tests follow F.I.R.S.T: Fast, Independent, Repeatable, Self-validating, Timely.
- **Frontend:** Vitest for stores/logic; Playwright for the clone→speak happy path.
- **Rust:** unit tests for the sidecar supervisor (spawn, health, kill, restart-on-crash).
- **Python:** pytest; mock at boundaries (model loading mocked so engine tests don't need a GPU).
- Use factories for test data — never hardcode entities.

---

## Cross-Platform Parity (strict rule)

A feature that ships in **default mode** — out of the box, no toggle, no opt-in — must behave **identically on macOS, Windows, and Linux.** Platform-specific *implementation code* is allowed (OS APIs, shells, packaging); the user-visible *default behavior* cannot diverge.

Platform-only capabilities (e.g. a macOS-only global shortcut, a Windows-only path picker) must sit behind explicit opt-in: a Settings toggle, env var, or CLI flag. When a default doesn't work on a platform, that's a **P0 bug** — fix it on the missing platform or move it behind opt-in. There is no third option.

---

## Local-First Guarantee

- No required cloud calls, accounts, or API keys. The app is fully functional offline (after the one-time model download).
- The only network calls are: (a) the first-run model download from Hugging Face, (b) the optional auto-updater check. Both are visible and the second is the user's choice.
- No third-party telemetry endpoint, ever. Crash/usage data stays local unless the user explicitly exports it.

---

## Licensing & Attribution

Parrot is licensed **Apache-2.0** (decided 2026-05-29 — "Path B", see [docs/LICENSING.md](docs/LICENSING.md)). It is an independent OSI open-source app, **not** a code fork:

- Parrot's own code (Svelte UI, Rust shell, Python sidecar) is **Apache-2.0** — see the root `LICENSE`.
- It reuses **only** the Apache-2.0 `omnivoice` model library. The root `NOTICE` credits it (Han Zhu / k2-fsa) as Apache-2.0 §4 requires.
- **Do not copy OmniVoice's FSL-1.1-ALv2 app code.** Use it as a design reference and reimplement from the specs in `docs/`.
- Don't reuse the OmniVoice name/logo.
- **Open item:** confirm the model *weights* license before any commercial use — Apache-2.0 on the model *code* does not guarantee the *weights* are commercially licensed. See LICENSING.md.

---

## Data Model

SQLite (WAL mode, foreign keys on). Schema is created idempotently and migrated via alembic.

```
voice_profiles
  id TEXT PK
  name TEXT NOT NULL
  ref_audio_path TEXT            # reference sample filename in voices dir
  ref_text TEXT DEFAULT ''       # transcript of the reference (improves cloning)
  language TEXT DEFAULT 'Auto'
  instruct TEXT DEFAULT ''       # optional style hint (kept optional; Parrot de-emphasizes it)
  locked_audio_path TEXT DEFAULT ''  # pinned output for reproducible voice
  seed INTEGER DEFAULT NULL
  is_locked INTEGER DEFAULT 0
  created_at REAL

generation_history
  id TEXT PK
  text TEXT
  language TEXT
  profile_id TEXT  → voice_profiles(id)   # FK, nulled on profile delete
  audio_path TEXT
  duration_seconds REAL
  generation_time REAL
  seed INTEGER DEFAULT NULL
  created_at REAL

settings
  key TEXT PK
  value TEXT NOT NULL            # encrypted for secrets (HF token)
  updated_at REAL NOT NULL
```

User data lives under `parrot_data/` (voices, generated audio, DB, settings). It must survive upgrades without manual migration. See [docs/specs/voice-cloning.md](docs/specs/voice-cloning.md) and [docs/specs/synthesis.md](docs/specs/synthesis.md) for the contracts that read/write these tables.

---

## Where to Start

1. Read [docs/specs/architecture.md](docs/specs/architecture.md) — the 3-process model.
2. Read [docs/specs/ipc-contract.md](docs/specs/ipc-contract.md) — the frontend↔sidecar surface.
3. Read [docs/ROADMAP.md](docs/ROADMAP.md) — what ships when.
4. Pick a spec, write tests, implement.
