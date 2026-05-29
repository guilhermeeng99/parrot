"""The vendored voice-engine boundary.

This package wraps the Apache-2.0 `omnivoice` model lib (import path unchanged —
see CLAUDE.md and docs/LICENSING.md) behind the stable backend interface that
`services/model_manager.py` expects. It is the ONLY place the real model lib is
imported, and it is never imported in the test venv (tests mock `get_model`).
Installed via the `engine` extra (`uv sync --extra engine`).
"""
