"""`/profiles` — voice library CRUD + lock/unlock + usage + audio.

Thin router over `services/profiles.py` (voice-profiles.md). Multipart for
create/lock (they carry a file or form id); JSON for the partial PUT.
"""

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..services import profiles as svc

router = APIRouter(prefix="/profiles")


class ProfilePatch(BaseModel):
    name: str | None = None
    ref_text: str | None = None
    instruct: str | None = None
    language: str | None = None


@router.get("")
def list_profiles() -> list[dict]:
    return svc.list_profiles()


@router.post("")
async def create_profile(
    name: str = Form(...),
    ref_audio: UploadFile = File(...),
    ref_text: str = Form(""),
    instruct: str = Form(""),
    language: str = Form("Auto"),
    seed: int | None = Form(None),
) -> dict:
    audio_bytes = await ref_audio.read()
    return svc.create_profile(
        name=name,
        audio_bytes=audio_bytes,
        original_filename=ref_audio.filename,
        ref_text=ref_text,
        instruct=instruct,
        language=language,
        seed=seed,
    )


@router.get("/{profile_id}")
def get_profile(profile_id: str) -> dict:
    return svc.get_profile_or_404(profile_id)


@router.put("/{profile_id}")
def update_profile(profile_id: str, patch: ProfilePatch) -> dict:
    return svc.update_profile(
        profile_id,
        name=patch.name,
        ref_text=patch.ref_text,
        instruct=patch.instruct,
        language=patch.language,
    )


@router.get("/{profile_id}/audio")
def profile_audio(profile_id: str):
    path = svc.audio_path_for(profile_id)
    return FileResponse(str(path), media_type="audio/wav")


@router.get("/{profile_id}/usage")
def profile_usage(profile_id: str) -> dict:
    return svc.usage(profile_id)


@router.post("/{profile_id}/lock")
def lock_profile(
    profile_id: str,
    history_id: str = Form(...),
    seed: int | None = Form(None),
) -> dict:
    return svc.lock_profile(profile_id, history_id, seed)


@router.post("/{profile_id}/unlock")
def unlock_profile(profile_id: str) -> dict:
    return svc.unlock_profile(profile_id)


@router.delete("/{profile_id}")
def delete_profile(profile_id: str) -> dict:
    return svc.delete_profile(profile_id)
