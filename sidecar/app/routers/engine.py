"""Engine status — the single fixed-engine/device stub.

Per docs/specs/ipc-contract.md: `GET /engine/status` returns
`{"active": "omnivoice", "device": "<id>"}`. Phase 1 reports a fixed `cpu`
device with no torch import; real device auto-detect lands with the engine
(see docs/specs/device-detection.md).
"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/engine/status")
def engine_status() -> dict[str, str]:
    return {"active": "omnivoice", "device": "cpu"}
