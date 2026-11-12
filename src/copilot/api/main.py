"""ASGI entry point: ``uvicorn copilot.api.main:app``."""

from copilot.api.app import create_app

app = create_app()
