"""Core sidecar infrastructure: paths, DB, device detection, crypto, logging.

These modules are imported by services (never by routers directly). They hold no
FastAPI handlers and never import torch at module scope — the heavy ML import is
deferred to first model access so `/healthz` stays instant during cold start.
"""
