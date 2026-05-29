# Parrot — Documentation

This is the source of truth for Parrot. Code conforms to these docs, not the other way around. Read the spec before you touch the feature.

## Start here

| Doc | What it covers |
| --- | --- |
| [specs/architecture.md](specs/architecture.md) | The 3-process model (Tauri/Rust shell · Svelte/Bun UI · Python sidecar), IPC, lifecycle, packaging boundary. **Read this first.** |
| [ROADMAP.md](ROADMAP.md) | Phased plan from scaffold → clone+speak MVP → 1.0. |
| [LICENSING.md](LICENSING.md) | The FSL fork situation and the two licensing paths. Read before distributing. |

## Feature specs

| Spec | Feature |
| --- | --- |
| [specs/voice-cloning.md](specs/voice-cloning.md) | Clone a voice from a reference sample → reusable profile. |
| [specs/synthesis.md](specs/synthesis.md) | Speak typed text in a cloned voice (the `/generate` path). |
| [specs/voice-profiles.md](specs/voice-profiles.md) | The voice library: CRUD, lock/unlock for reproducibility. |
| [specs/first-run-setup.md](specs/first-run-setup.md) | First-run model download, venv bootstrap, sidecar spawn, HF token. |
| [specs/device-detection.md](specs/device-detection.md) | GPU/CPU auto-detect (CUDA/CPU) on Windows. |
| [specs/ipc-contract.md](specs/ipc-contract.md) | The complete frontend↔sidecar API + Tauri command surface. |
| [specs/settings.md](specs/settings.md) | Appearance, engine status, optional HF token entry. |
| [specs/design-system.md](specs/design-system.md) | The design system: Calendly "Sky Blueprint" light theme (adopted from Toolzy), Tailwind v4 `@theme` tokens, Montserrat, the Svelte component inventory. |
| [specs/ui-ux.md](specs/ui-ux.md) | Application UI/UX: app shell, screen-by-screen flows (Setup/Clone/Speak/Profile/Settings), interaction states, microcopy, accessibility-in-context. |
| [specs/packaging.md](specs/packaging.md) | Tauri bundle + Python sidecar, Windows MSI installer, updater. |

## How to use these specs

Every spec follows the same shape:

- **Entity Contract** — fields, types, invariants
- **Business Rules** — numbered, testable
- **IPC Contract** — endpoints/commands: method, path, params, returns, errors
- **State Machines** — frontend store states + transitions (where relevant)
- **Edge Cases** — the failures that must be handled
- **Data** — tables/files touched

When requirements change, update the spec in the same change as the code. A spec that lies is worse than no spec.

## Landing page

A static landing page lives in [`../site/`](../site/) and auto-deploys to GitHub Pages at <https://guilhermeeng99.github.io/parrot/> on every push to `main`.

See [../CLAUDE.md](../CLAUDE.md) for project conventions, the tech stack, and the post-change checklist.
