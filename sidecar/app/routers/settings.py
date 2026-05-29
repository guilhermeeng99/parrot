"""`/settings/hf-token` — the optional HF token (settings.md). Loopback-gated.

Reads only ever expose the masked TokenState; the raw token never leaves the
sidecar through these endpoints.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..services import hf_token as svc
from .deps import require_loopback

router = APIRouter(prefix="/settings", dependencies=[Depends(require_loopback)])


class TokenBody(BaseModel):
    token: str


@router.get("/hf-token")
def get_token() -> dict:
    return svc.get_state()


@router.post("/hf-token")
def set_token(body: TokenBody) -> dict:
    return svc.set_token(body.token)


@router.delete("/hf-token")
def clear_token() -> dict:
    return svc.clear_token()
