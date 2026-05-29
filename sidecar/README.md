# Parrot sidecar

The local voice engine, served as a FastAPI process the Rust shell spawns and
supervises. **Phase 1 is a stub** — liveness + a fixed engine-status, no ML.

```bash
uv sync                 # create the venv + install deps
uv run python main.py   # serve on http://127.0.0.1:3900
```

Endpoints (see `../docs/specs/ipc-contract.md`):

- `GET /healthz` → `{"status":"ok"}` — liveness (the supervisor's probe).
- `GET /engine/status` → `{"active":"omnivoice","device":"cpu"}` — stub.

`PARROT_PORT` overrides the port (the supervisor sets it).
