"""Sidecar entry point.

Run standalone for development:
    uv run python main.py

The Rust supervisor spawns this same command (with PARROT_PORT set) and polls
`/healthz` until it answers before letting the UI talk to the engine.
"""

import uvicorn

from app import config, create_app

app = create_app()


def main() -> None:
    uvicorn.run(app, host=config.HOST, port=config.port(), log_level="info")


if __name__ == "__main__":
    main()
