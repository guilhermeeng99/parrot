"""Stateless audio utilities: WAV -> MP3 transcode (POST /audio/mp3)."""

import io

import numpy as np
import soundfile as sf


def _wav_bytes(n: int = 2400, sr: int = 24000) -> bytes:
    buf = io.BytesIO()
    samples = (np.sin(np.linspace(0.0, 1.0, n)) * 0.2).astype("float32")
    sf.write(buf, samples, sr, subtype="PCM_16", format="WAV")
    return buf.getvalue()


def test_audio_mp3_transcodes_wav(client):
    res = client.post("/audio/mp3", content=_wav_bytes(), headers={"Content-Type": "audio/wav"})
    assert res.status_code == 200
    assert res.headers["content-type"] == "audio/mpeg"
    # MP3 frame sync: first byte 0xFF, top 3 bits of the second byte set.
    assert res.content[0] == 0xFF and (res.content[1] & 0xE0) == 0xE0


def test_audio_mp3_empty_is_400(client):
    assert client.post("/audio/mp3", content=b"").status_code == 400


def test_audio_mp3_garbage_is_400(client):
    res = client.post("/audio/mp3", content=b"not a wav", headers={"Content-Type": "audio/wav"})
    assert res.status_code == 400
