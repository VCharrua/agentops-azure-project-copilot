"""Runtime configuration.

Every value comes from environment variables (prefix ``COPILOT_``). There are no secrets here:
in Azure the app authenticates with a managed identity, so only endpoints and names are configured.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="COPILOT_", env_file=".env", extra="ignore")

    mode: Literal["local", "azure"] = "local"
    app_version: str = "dev"
    data_dir: Path = Path("data/synthetic")
    state_dir: Path = Path(".local/state")

    # Agent loop
    max_agent_steps: int = Field(default=6, ge=1, le=20)
    retrieval_top_k: int = Field(default=5, ge=1, le=20)

    # Human-in-the-loop
    approval_ttl_minutes: int = Field(default=60, ge=1)
    approver_header: str = "X-Approver"
    allowed_email_domains: list[str] = ["alvora-construction.example"]

    # Guardrails
    guardrail_fail_closed: bool = True
    content_safety_endpoint: str | None = None
    content_safety_severity_threshold: int = Field(default=2, ge=0, le=7)

    # Microsoft Foundry (azure mode)
    foundry_project_endpoint: str | None = None
    foundry_model_deployment: str = "gpt-4.1-mini"
    foundry_embedding_deployment: str = "text-embedding-3-small"
    foundry_agent_name: str | None = None

    # Azure AI Search (azure mode)
    search_endpoint: str | None = None
    search_index: str = "project-docs"

    def require(self, name: str) -> str:
        """Return a setting that is mandatory in azure mode, with a clear error if it is missing."""
        value = getattr(self, name)
        if not value:
            raise RuntimeError(f"COPILOT_{name.upper()} must be set when COPILOT_MODE=azure")
        return str(value)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
