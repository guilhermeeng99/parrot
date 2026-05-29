<div align="center">

# 🦜 Parrot

**Clone a voice. Make it speak. Fully local.**

A desktop app that clones a voice from a short reference sample and speaks any text you type — on your own machine, no accounts, no API keys, no cloud.

macOS · Windows · Linux

</div>

---

## What it does

Parrot does exactly two things, and tries to do them without making you fight it:

1. **Clone** — record or upload a few seconds of a voice, save it as a reusable profile.
2. **Speak** — type text, pick a voice, get natural speech back. Play it or export a `.wav`.

Everything runs locally on CUDA / Apple MPS / ROCm / CPU (auto-detected). Your reference audio never leaves your machine.

> Parrot is an independent, Apache-2.0 open-source app built on the same Apache-2.0 voice model as [OmniVoice Studio](https://github.com/debpalash/OmniVoice-Studio) — scoped down to just clone-and-speak. It is not a code fork; see [docs/LICENSING.md](docs/LICENSING.md).

## Status

Early development. Follow `main` for previews. See the [Roadmap](docs/ROADMAP.md) for what's built and what's next.

## Install

> Installers are not published yet (pre-1.0). Build from source for now.

## Build from source

Prerequisites: [Bun](https://bun.sh), [Rust](https://rustup.rs) (+ Tauri prerequisites for your OS), [uv](https://docs.astral.sh/uv/).

```bash
git clone <your-parrot-repo> parrot && cd parrot

# Python engine (sidecar)
cd sidecar && uv sync && cd ..

# Frontend + shell
cd frontend && bun install
bun run tauri dev      # opens the app: Svelte UI + Rust shell + Python sidecar
```

The first launch downloads the voice model (~hundreds of MB) once, then works offline.

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
