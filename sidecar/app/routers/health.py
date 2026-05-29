"""Liveness probe — the contract the Rust supervisor polls on startup.

Per docs/specs/ipc-contract.md: `GET /healthz` returns exactly
`{"status": "ok"}` — fast, no torch import, no device field.
"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
