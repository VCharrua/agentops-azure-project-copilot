"""Publish the agent definition (instructions + function tools) to Foundry Agent Service.

    python scripts/register_agent.py --dry-run             # print the definition, no Azure calls
    python scripts/register_agent.py --name project-delivery-copilot

Optional. By default the API sends its instructions and tools inline with every request, so the
prompt version always equals the image version. Registering a version makes the same definition
visible and testable in the Foundry portal; set COPILOT_FOUNDRY_AGENT_NAME to make the API use it.
See docs/adr/0002-foundry-responses-vs-managed-agent.md for the trade-off.

Not yet validated on Azure.
"""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING

from copilot.agent.prompts import PROMPT_VERSION, SYSTEM_PROMPT
from copilot.bootstrap import build_container
from copilot.config import Settings

if TYPE_CHECKING:
    from azure.ai.projects.models import PromptAgentDefinition


def build_definition(settings: Settings) -> PromptAgentDefinition:
    from azure.ai.projects.models import FunctionTool, PromptAgentDefinition, Tool

    registry = build_container(settings.model_copy(update={"mode": "local"}), persist=False).tools
    tools: list[Tool] = [
        FunctionTool(name=spec.name, description=spec.description, parameters=spec.json_schema(), strict=True)
        for spec in registry.specs()
    ]
    return PromptAgentDefinition(model=settings.foundry_model_deployment, instructions=SYSTEM_PROMPT, tools=tools)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", default="project-delivery-copilot")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    settings = Settings()
    definition = build_definition(settings)
    tool_count = len(definition.tools or [])
    if args.dry_run:
        print(f"Agent '{args.name}': model {definition.model}, prompt {PROMPT_VERSION}, {tool_count} function tools.")
        return 0

    from azure.ai.projects import AIProjectClient

    from copilot.bootstrap import azure_credential

    with AIProjectClient(
        endpoint=settings.require("foundry_project_endpoint"), credential=azure_credential()
    ) as project:
        agent = project.agents.create_version(agent_name=args.name, definition=definition)
    print(f"Registered {agent.name} version {agent.version} (prompt {PROMPT_VERSION}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
