"""`/history` — the synthesis log (synthesis.md §/history)."""

from fastapi import APIRouter
from fastapi.responses import FileResponse

from ..services import history as svc

router = APIRouter(prefix="/history")


@router.get("")
def list_history() -> list[dict]:
    return svc.list_history(limit=50)


@router.get("/{history_id}/audio")
def history_audio(history_id: str):
    """Serve a past generation's WAV so the History list can replay it."""
    return FileResponse(str(svc.audio_path_for(history_id)), media_type="audio/wav")


@router.delete("")
def clear_history() -> dict:
    return svc.clear_all()


@router.delete("/{history_id}")
def delete_history(history_id: str) -> dict:
    return svc.delete_one(history_id)
