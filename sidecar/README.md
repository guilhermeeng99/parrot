# Parrot sidecar

Parrot's voice engine: the local FastAPI process the Rust shell spawns and
supervises. It does the two things Parrot does — **clone** a voice from a short
reference sample, and **speak** any text in that voice — and owns the SQLite DB
and the on-disk voice/audio files.

## Router groups

The HTTP/WS surface is grouped by router. See `../docs/specs/ipc-contract.md`
for the authoritative contracts (methods, params, return shapes, errors) — this
list is just the map:

- `health` — liveness; the supervisor's probe.
- `engine` — active engine + device (CUDA/CPU) status.
- `generate` — synthesis (REST).
- `profiles` — voice-profile CRUD.
- `history` — generation history.
- `setup` — first-run/model-download gating.
- `settings` — app settings (incl. the Hugging Face token).
- `ws` — streaming synthesis over WebSocket.

`PARROT_PORT` overrides the port (the supervisor sets it).

## The model boundary

The heavy ML stack — the `omnivoice` model lib plus torch/transformers — lives
in the optional **`engine`** extra and is imported lazily through
`model_manager.get_model()`. Because nothing imports the model at startup, the
default `uv sync` and the test suite stay light and run with the model boundary
mocked, so no GPU is needed. Production/first-run installs the extra with
`uv sync --no-dev --extra engine`.

## Run + test

```bash
uv sync                                # create the venv + install deps (no ML)
uv run uvicorn main:app --port 3900    # serve standalone on http://127.0.0.1:3900
uv run pytest                          # full router + service suite (model mocked)
```
