# Parrot — Roadmap

Parrot ships **continuous-to-main**: no release candidates, no soak, no ceremony. Users who want a preview follow `main`; tagged releases are cut when a milestone's exit criteria are met. The bar for each milestone is functional, not a calendar date.

The north star is unchanged at every phase: **a first run that actually works** — download, clone, speak, on every platform.

Legend: ☐ not started · ◐ in progress · ☑ done

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

- ☐ Implement `sidecar/` **fresh from the specs** (Path B — do **not** copy OmniVoice FSL code; reference its design only). Scope: `generate`, `profiles`, `setup`, `model_manager`, a single `omnivoice` backend. No GGUF, no dub/ASR/gallery/batch/marketplace/multi-engine.
- ☐ Vendor the `omnivoice` model lib (Apache-2.0) as a dependency, import path unchanged
- ☐ First-run model download gate ([first-run-setup.md](specs/first-run-setup.md))
- ☐ Device auto-detect ([device-detection.md](specs/device-detection.md))
- ☐ Svelte **Clone** screen: record/upload reference → save profile ([voice-cloning.md](specs/voice-cloning.md))
- ☐ Svelte **Speak** screen: type text → pick profile → `/generate` → play/export ([synthesis.md](specs/synthesis.md))
- ☐ Voice profile library: list, edit, delete, lock/unlock ([voice-profiles.md](specs/voice-profiles.md))
- ☐ DB + alembic migrations carried over with a tested upgrade path

**Exit:** on a clean machine, a user downloads the model, clones their voice, types a sentence, and hears it spoken. The smoke test exercises this whole path.

---

## Phase 3 — Cross-platform hardening

Make the MVP work identically on the three OSes.

- ☐ macOS (Apple Silicon + Intel): MPS detect, signing/notarization, dmg
- ☐ Windows (x64): CUDA + CPU, HF-cache path-length fix, msi
- ☐ Linux: CUDA/ROCm/CPU, AppImage + deb
- ☐ Sidecar packaging: `uv` as `externalBin`, venv bootstrap on first launch ([packaging.md](specs/packaging.md))
- ☐ Parity audit: every default-mode feature behaves the same on all three (CLAUDE.md strict rule)

**Exit:** installers for all three platforms each pass the clone→speak smoke test from a clean install.

---

## Phase 4 — Polish toward 1.0

- ☐ Settings: appearance, engine status, optional HF token ([settings.md](specs/settings.md))
- ☐ Design-system pass: consistent components, dark/light, accessibility ([design-system.md](specs/design-system.md))
- ☐ Streaming synthesis (`/ws/tts`) for low-latency playback (optional)
- ☐ Auto-updater wired to the project's own release endpoint
- ☐ Error surfaces that tell the user exactly what to do (the OmniVoice failure mode this fork exists to avoid)
- ☐ README, install docs, troubleshooting — kept honest by the smoke test

**Exit:** the maintainer calls it "actually useful." Tag `v1.0.0`.

---

## Out of scope (will be declined unless this doc changes)

Video dubbing · dictation/ASR · voice gallery / YouTube clipping · batch queue · marketplace · multi-engine picker · any cloud/account/telemetry. See [../CLAUDE.md](../CLAUDE.md) §Scope.
