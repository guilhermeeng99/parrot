"""Phase-1 hardening: redaction of short HF tokens + HF-cache env resolution."""

from pathlib import Path

from app import config
from app.core.logging import redact


def test_redact_scrubs_short_hf_token():
    """Real HF tokens are long, but a short/truncated `hf_`+8 must still be scrubbed
    (logging.py: {8,}, not {30,} — over-redacting a non-secret is harmless)."""
    secret = "hf_" + "a1b2c3d4"  # exactly 8 chars after the prefix
    msg = f"download failed for token {secret} on retry"
    out = redact(msg)
    assert secret not in out
    assert "hf_***REDACTED***" in out


def test_redact_leaves_too_short_prefix_alone():
    # < 8 chars after hf_ isn't matched (and isn't a real token anyway).
    assert redact("hf_short") == "hf_short"


def test_hf_cache_dir_honors_huggingface_hub_cache(monkeypatch, tmp_path):
    """hf_cache_dir resolves HF_HUB_CACHE → HUGGINGFACE_HUB_CACHE (legacy alias the
    HF library still honors) → HF_HOME/hub. With only the legacy alias set, it wins
    over HF_HOME so the reported cache stays in sync with the real one."""
    legacy = tmp_path / "legacy_hub"
    monkeypatch.delenv("HF_HUB_CACHE", raising=False)
    monkeypatch.setenv("HUGGINGFACE_HUB_CACHE", str(legacy))
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf_home"))  # must be ignored
    assert config.hf_cache_dir() == str(Path(legacy))


def test_hf_cache_dir_prefers_hf_hub_cache(monkeypatch, tmp_path):
    primary = tmp_path / "primary_hub"
    monkeypatch.setenv("HF_HUB_CACHE", str(primary))
    monkeypatch.setenv("HUGGINGFACE_HUB_CACHE", str(tmp_path / "legacy_hub"))
    assert config.hf_cache_dir() == str(Path(primary))
