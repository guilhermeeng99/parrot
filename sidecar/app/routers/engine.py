"""Engine + device status — the single fixed-engine/device endpoint.

`GET /engine/status` → `{"active":"omnivoice","device":"<id>"}` (device ∈
{cuda, cpu}, optional `device_label`). Device resolution is owned by
core/device.py and lazily triggers the torch import on the first call; this
endpoint never throws — on any internal error it reports a safe `cpu` default
(device-detection.md / settings.md).
"""

from fastapi import APIRouter, Depends

from ..core import device
from .deps import require_loopback

router = APIRouter()


@router.get("/engine/status")
def engine_status(_: None = Depends(require_loopback)) -> dict:
    return device.engine_status()
