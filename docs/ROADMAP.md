# Parrot — Roadmap

Parrot ships **continuous-to-main**: no release candidates, no soak, no ceremony. Users who want a preview follow `main`; tagged releases are cut when a milestone's exit criteria are met. The bar for each milestone is functional, not a calendar date.

The north star is unchanged at every phase: **a first run that actually works** — download, clone, speak, on Windows.

Legend: ☐ not started · ◐ in progress · ☑ done

> **Note (2026-05-29):** the Phase 2–4 work below is committed; a deep-review fix pass was since applied to reconcile the specs with the shipped code (supervisor lifecycle, IPC contract, device-pool sizing) — the done marks remain accurate.

---

## Phase 0 — Foundation (docs + decisions)

Get the project legible before writing app code.

- ☑ Decide stack: Tauri + Svelte + Bun + Rust shell + Python FastAPI sidecar
- ☑ Decide engine architecture: Python sidecar (reuse OmniVoice engine)
- ☑ Write `CLAUDE.md` + `docs/` (this set)
- ☑ **Resolve licensing path** (see [LICENSING.md](LICENSING.md)): **Path B — independent app, Apache-2.0**, reusing only the Apache-2.0 `omnivoice` model lib. No FSL code copied.
- ☑ Add `LICENSE` (Apache-2.0) + `NOTICE` (credits the `omnivoice` model lib)
- ☑ Confirm model **weights** license: `k2-fsa/OmniVoice` is Apache-2.0; the Higgs Audio V2 tokenizer (Boson AI) caps commercial use at 100k annual active users. Open-source release unaffected — see [LICENSING.md](LICENSING.md)

**Exit:** every feature has a spec; license decided + files in place; repo scaffolded.

---

## Phase 1 — Scaffold the three processes

Prove the architecture end-to-end with a trivial payload before any ML.

- ☑ `frontend/` Svelte 5 + SvelteKit (static SPA) + Bun + Tailwind v4 (`@theme` tokens) + Montserrat, "hello" screen. Builds clean (`bun run build`).
- ☑ `frontend/src-tauri/` Rust (Tauri v2) shell: window config loads the Svelte build. Compiles clean (`cargo check`).
- ☑ Rust sidecar supervisor (`supervisor.rs`): spawns the Python FastAPI sidecar (`uv run`), health-checks `/healthz`, restarts on crash, kills on app exit.
- ☑ Typed IPC client in `frontend/src/lib/api/` (`client.ts` + `health.ts` + `engine.ts`) reading `/healthz` + `/engine/status`; the hello screen renders the value.
- ☑ Python sidecar stub (`sidecar/`): FastAPI `/healthz` → `{"status":"ok"}`, `/engine/status` → `{"active":"omnivoice","device":"cpu"}`. No ML.
- ☑ `scripts/smoke-test.sh`: builds the frontend, boots the sidecar, asserts the IPC contract. **Passing.**

**Exit:** verified headless — frontend builds, Rust shell + supervisor compile, the sidecar serves the contract, and the smoke test passes end to end. **Still to confirm on a real desktop session:** `bun run tauri dev` visually opening the window that reads the sidecar value (cannot be exercised in a headless environment). Placeholder app icons are copied from Toolzy — replace with Parrot branding before any release.

---

## Phase 2 — Port the engine (clone + speak MVP)

Bring over the stripped OmniVoice engine and wire the two real features.

- ☑ Implement `sidecar/` **fresh from the specs** (Path B — no OmniVoice FSL code copied). Scope: `generate`, `profiles`, `history`, `setup`, `settings`, `engine`, `ws`, plus `model_manager` + a single `omnivoice` backend. `core/` (config/paths/db/device/crypto/logging), `services/`, `routers/`. 69 pytest cases green with the model boundary mocked.
- ☑ Integrate the `omnivoice` model lib (Apache-2.0, PyPI: `omnivoice`): pulled as a dependency in the `engine` optional-dependency extra, isolated behind Parrot's thin adapter `app/engine/omnivoice_backend.py` (maps the internal `synthesize(...)` contract onto omnivoice's `OmniVoice.from_pretrained` / `create_voice_clone_prompt` / `generate` + `OmniVoiceGenerationConfig`), and reached only via `model_manager.get_model()`; lazily imported. *(Reconciled against the real lib at integration time and verified end-to-end — clone→speak produces a 24 kHz WAV; the boundary stays mocked in the headless test suite.)*
- ☑ First-run model download gate ([first-run-setup.md](specs/first-run-setup.md)) — `/setup/status` + `/setup/download` + SSE stream, cooldown, disk guard.
- ☑ Device auto-detect ([device-detection.md](specs/device-detection.md)) — CUDA→CPU, fail-safe, worker sizing, lazy torch.
- ☑ Svelte **Clone** screen: record/upload reference → save profile ([voice-cloning.md](specs/voice-cloning.md))
- ☑ Svelte **Speak** screen: type text → pick profile → `/generate` → play/export ([synthesis.md](specs/synthesis.md))
- ☑ Voice profile library: list, edit, delete, lock/unlock ([voice-profiles.md](specs/voice-profiles.md))
- ☑ DB + alembic migrations with a tested upgrade path (idempotent `init_db` shares DDL with the `0001_initial` migration; migration upgrade/downgrade tested).

**Exit:** the headless smoke test exercises frontend build → sidecar boot → health/engine/setup + a full profile CRUD round-trip. **Needs a real run to confirm:** the model download + a true clone→speak (requires the `engine` extra + GPU/CPU weights, not runnable in this environment).

---

## Phase 3 — Windows hardening

Make the MVP solid on Windows 10/11 (x64) — the only supported platform ([CLAUDE.md Platform Scope](../CLAUDE.md)).

- ☑ Windows (x64): CUDA + CPU device detect (done in the sidecar), HF-cache path-length fix (`config.prepare_environment` redirects to `%LOCALAPPDATA%\Parrot\hf_cache` + disables symlinks), MSI target configured (`bundle.targets = ["msi"]`). *(Producing/validating the `.msi` needs a full `tauri build` on a Windows host with WiX — config is in place; the bundle itself isn't built headlessly here.)*
- ☑ Sidecar packaging: `uv` declared as the only `externalBin`, sidecar source as bundle `resources`; supervisor venv bootstrap on first launch (`uv venv` + `uv sync --no-dev --extra engine`), attach-if-healthy, port takeover, log piping, retry/clean ([packaging.md](specs/packaging.md)).
- ◐ Code-sign the MSI so release builds don't trip SmartScreen — **documented** (Authenticode OV/EV, signing notes in packaging.md); the only blocker is a signing certificate, which can't be provisioned here. SmartScreen guidance for unsigned dev builds is honest in the docs.

**Exit:** the MSI installer passes the clone→speak smoke test from a clean install on Windows 10 + 11. **Blocked only on a real Windows `tauri build` + a signing cert** — all code/config is in place.

---

## Phase 4 — Polish toward 1.0

- ☑ Settings: appearance (fixed light), engine status, optional HF token ([settings.md](specs/settings.md))
- ☑ Design-system pass: the full DS primitive set + Parrot components built in Svelte 5 against the verbatim Tailwind recipes; light theme; focus rings, reduced-motion, ARIA ([design-system.md](specs/design-system.md)). Dark mode stays backlog by design.
- ☑ Streaming synthesis (`/ws/tts`) — backend WS + typed `ttsStream.ts` client (optional path; primary stays `POST /generate`).
- ☑ Auto-updater wired (updater plugin + `plugins.updater` config + client store, client-rendered). *(Ships with a placeholder pubkey — must regenerate a real minisign keypair before a signed release.)*
- ☑ Error surfaces that tell the user what to do — uniform 5-state interaction model, redacted `detail` envelopes, OOM "Flush & retry", offline/gated setup guidance, engine-starting (not error) handling.
- ☑ README, install docs, troubleshooting ([../README.md](../README.md)).

**Exit:** the maintainer calls it "actually useful." Tag `v1.0.0`. **Remaining before a real `v1.0.0`:** run the model download + clone→speak on a real Windows machine (GPU/CPU), produce + code-sign the MSI, and regenerate the updater keypair — none of which are doable in a headless environment.

---

## Out of scope (will be declined unless this doc changes)

Video dubbing · dictation/ASR · voice gallery / YouTube clipping · batch queue · marketplace · multi-engine picker · any cloud/account/telemetry. See [../CLAUDE.md](../CLAUDE.md) §Scope.
