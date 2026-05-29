"""Service layer — all business logic lives here.

Routers import services; services never import routers (CLAUDE.md). Services
raise `ServiceError` for expected failures (mapped to an HTTP status by the app
factory's exception handler) and keep the heavy ML import behind
`model_manager.get_model()` — the single entry point for model access.
"""
