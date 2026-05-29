"""Synthesis orchestration — the `/generate` and `/ws/tts` pipelines.

Ties the pieces together: validate input → resolve the voice profile → run
inference off the event loop (tts_backend) → master/DSP (audio_dsp) → write the
24 kHz WAV (audio_io) → record one history row. Returns everything the router
needs to stream the WAV and set its `X-*` headers (synthesis.md).
"""

import asyncio
import time

from ..core import paths
from . import audio_dsp, audio_io, history, profiles, tts_backend
from .errors import ServiceError, new_id


def _normalize_language_for_model(language: str | None) -> str | None:
    """'Auto'/empty → None (let the model auto-detect). Anything else passes."""
    if language is None or language.strip() in ("", "Auto"):
        return None
    return language


async def generate(params: dict) -> dict:
    """Run a full synthesis and persist it. `params` is the validated form data.

    Returns `{id, audio_path, path, generation_time, duration_seconds, seed,
    sample_rate}`; the router streams `path` and reads metadata from the rest.
    """
    text = (params.get("text") or "").strip()
    if not text:
        raise ServiceError(400, "Text is required.")

    effect_preset = params.get("effect_preset", "broadcast")
    try:
        audio_dsp.validate_preset(effect_preset)  # fail fast, before inference
    except ValueError as e:
        raise ServiceError(400, str(e))

    request_language = params.get("language")
    resolved = profiles.resolve_for_generate(
        profile_id=params.get("profile_id"),
        ref_audio_path=params.get("ref_audio_path"),
        ref_text=params.get("ref_text"),
        instruct=params.get("instruct"),
        seed=params.get("seed"),
        language=request_language,
    )

    infer_params = {
        **params,
        "text": text,
        "ref_audio_path": resolved["ref_audio_path"],
        "ref_text": resolved["ref_text"],
        "instruct": resolved["instruct"],
        "seed": resolved["seed"],
        "language": _normalize_language_for_model(resolved["language"]),
    }

    result = await tts_backend.run(infer_params)

    # DSP + WAV encode + duration + the history INSERT are all blocking (numpy /
    # soundfile / sqlite); run the whole tail off the event loop so a long write
    # or a WAL-contended insert can't stall other requests. Only the await touches
    # the loop.
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, _finalize_and_persist, result, effect_preset, text, request_language, resolved
    )


def _finalize_and_persist(
    result: dict, effect_preset: str, text: str, request_language: str | None, resolved: dict
) -> dict:
    """Blocking tail of `generate()`: master/DSP → write WAV → record history.
    Runs in a threadpool (never on the event loop)."""
    samples = audio_dsp.process(result["samples"], result["sample_rate"], effect_preset)

    audio_id = new_id()
    filename = f"{audio_id}.wav"
    out_path = paths.outputs_dir() / filename
    audio_io.save_wav(samples, result["sample_rate"], out_path)
    duration = audio_io.duration_seconds(samples, result["sample_rate"])

    history.insert(
        {
            "id": audio_id,
            "text": text[:200],
            "language": request_language or "Auto",
            "profile_id": resolved["resolved_profile_id"],
            "audio_path": filename,
            "duration_seconds": duration,
            "generation_time": result["generation_time"],
            "seed": result["seed"],
            "created_at": time.time(),
        }
    )

    return {
        "id": audio_id,
        "audio_path": filename,
        "path": out_path,
        "generation_time": result["generation_time"],
        "duration_seconds": duration,
        "seed": result["seed"],
        "sample_rate": result["sample_rate"],
    }


async def generate_pcm(params: dict) -> dict:
    """WS path: synthesize + broadcast-master + normalize, NO preset, NO file, NO
    history row (synthesis.md §WS). Returns `{samples, sample_rate, seed,
    generation_time, duration_seconds}`."""
    text = (params.get("text") or "").strip()
    if not text:
        raise ServiceError(400, "Missing 'text' field in request")

    resolved = profiles.resolve_for_generate(
        profile_id=params.get("profile_id") or params.get("voice"),
        ref_audio_path=None,
        ref_text=params.get("ref_text"),
        instruct=params.get("instruct"),
        seed=params.get("seed"),
        language=params.get("language"),
    )
    infer_params = {
        **params,
        "text": text,
        "ref_audio_path": resolved["ref_audio_path"],
        "ref_text": resolved["ref_text"],
        "instruct": resolved["instruct"],
        "seed": resolved["seed"],
        "language": _normalize_language_for_model(resolved["language"]),
    }
    result = await tts_backend.run(infer_params)
    # WS applies only broadcast mastering + -2 dBFS normalize (no effect presets).
    # DSP is numpy/pedalboard-bound, so run it off the loop to keep the socket
    # responsive while the next request's start frame queues.
    loop = asyncio.get_running_loop()
    samples = await loop.run_in_executor(
        None, audio_dsp.process, result["samples"], result["sample_rate"], "broadcast"
    )
    return {
        "samples": samples,
        "sample_rate": result["sample_rate"],
        "seed": result["seed"],
        "generation_time": result["generation_time"],
        "duration_seconds": audio_io.duration_seconds(samples, result["sample_rate"]),
    }
