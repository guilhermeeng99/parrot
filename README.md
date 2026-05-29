<div align="center">

# 🦜 Parrot

**Clone a voice. Make it speak. Fully local.**

A desktop app that clones a voice from a short reference sample and speaks any text you type — on your own machine, no accounts, no API keys, no cloud.

Windows

</div>

---

## What it does

Parrot does exactly two things, and tries to do them without making you fight it:

1. **Clone** — record or upload a few seconds of a voice, save it as a reusable profile.
2. **Speak** — type text, pick a voice, get natural speech back. Play it or export a `.wav`.

Everything runs locally on CUDA / CPU (auto-detected). Your reference audio never leaves your machine.

> Parrot is an independent, Apache-2.0 open-source app built on the same Apache-2.0 voice model as [OmniVoice Studio](https://github.com/debpalash/OmniVoice-Studio) — scoped down to just clone-and-speak. It is not a code fork; see [docs/LICENSING.md](docs/LICENSING.md).

## Status

The clone-and-speak MVP is built end to end — engine (FastAPI sidecar), UI (Svelte), and shell (Rust/Tauri), all with tests. What still needs a real Windows machine + GPU/CPU weights: the one-time model download, an actual generation, and producing/code-signing the MSI. See the [Roadmap](docs/ROADMAP.md).

## Install

> Signed installers aren't published yet (pre-1.0). Build from source below. When released, Parrot ships as a single Windows **MSI**; unsigned dev builds trip SmartScreen — use **More info → Run anyway** (never disable SmartScreen globally).

## Build from source

Prerequisites: [Bun](https://bun.sh), [Rust](https://rustup.rs) (+ the [Tauri Windows prerequisites](https://tauri.app/start/prerequisites/): MSVC build tools, WebView2), [uv](https://docs.astral.sh/uv/).

```bash
git clone <your-parrot-repo> parrot && cd parrot

# Python engine (sidecar). The PyTorch ML stack lives in the `engine` extra:
cd sidecar && uv sync --extra engine && cd ..

# Frontend + shell
cd frontend && bun install
bun run tauri dev          # dev: Svelte UI + Rust shell + Python sidecar
bun run tauri build        # bundle the Windows MSI (needs WiX; emits Parrot_<ver>_x64_en-US.msi)
```

The first launch bootstraps a Python venv and downloads the voice model (~hundreds of MB) once, then works offline forever. Before a signed release, regenerate the updater key (`plugins.updater.pubkey` in `frontend/src-tauri/tauri.conf.json` is a placeholder) and drop a pinned `uv.exe` at `frontend/src-tauri/binaries/uv-x86_64-pc-windows-msvc.exe`.

## Develop & test

```bash
# Sidecar (from sidecar/) — model boundary is mocked, no GPU needed
uv run pytest

# Frontend (from frontend/)
bun run check              # svelte-check (types) — zero errors
bun run test               # vitest
bun run build              # production build

# Rust shell (from frontend/src-tauri/)
cargo clippy --all-targets # zero warnings
cargo test

# Whole-app contract smoke test (builds frontend, boots sidecar, exercises the IPC)
bash scripts/smoke-test.sh
```

## Troubleshooting

- **"Couldn't reach Hugging Face" on first run.** The one-time model download needs internet (`huggingface.co:443`). Check your connection / VPN / firewall, then retry. Everything after the download is offline.
- **"This model is gated."** Paste a Hugging Face token in the setup gate or Settings (or export `HF_TOKEN`). The default model is ungated, so most users never see this.
- **Running on CPU / slow generation.** Parrot auto-detects CUDA; with no NVIDIA GPU (or stale CUDA drivers) it falls back to CPU — fully functional, just slower. Settings → Engine shows the active device.
- **"The engine ran out of memory."** Use **Flush & retry** on the Speak screen (reloads the model), or shorten the text. The GPU worker pool is VRAM-budgeted to limit this.
- **Antivirus flags the sidecar.** Parrot spawns a local Python process bound to `127.0.0.1:3900` only (never the network). Allowlist the app's data folder (Settings → Data folder) and the loopback port if needed; a signed release reduces these warnings.
- **Engine won't start.** Settings → Engine → **View backend log**, or the boot splash's **Retry** / **Clean & Retry** (the latter wipes the bootstrapped venv and reclaims the port).
- **Where's my data?** Voices, generated audio, the SQLite DB, settings, and the bootstrapped venv live under `%APPDATA%\Parrot\parrot_data` (Settings → Data folder opens it). It survives upgrades.

## How it works

Parrot is three cooperating processes in one window:

```
Svelte UI (Bun)  ──HTTP/WS──>  Python FastAPI sidecar (PyTorch)
     │                                ▲
     ▼                                │ spawn / supervise
Tauri shell (Rust) ───────────────────┘
```

The Rust shell owns the window and supervises a local Python engine that runs the model. The UI talks to the engine over localhost. Full detail in [docs/specs/architecture.md](docs/specs/architecture.md).

## Documentation

All specs and the roadmap live in [`docs/`](docs/README.md). Parrot is spec-driven: the docs are the source of truth.

## License

Parrot is **Apache-2.0** — see [`LICENSE`](LICENSE). It is independent, OSI open-source app code that reuses only the Apache-2.0 `omnivoice` model library (credited in [`NOTICE`](NOTICE)). Details and the rationale: [docs/LICENSING.md](docs/LICENSING.md). Note: confirm the model **weights** license before any commercial use.

## Acknowledgments

Built on the Apache-2.0 `omnivoice` voice model (Han Zhu / k2-fsa), and inspired by [OmniVoice Studio](https://github.com/debpalash/OmniVoice-Studio). Thank you to those authors.
