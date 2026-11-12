"""Microsoft Foundry model adapter using the OpenAI-compatible Responses API.

Two modes, chosen by configuration:

* inline (default): instructions and tool definitions ship inside the container image and are sent
  with every request. The prompt version is therefore the image version and rolls back with it.
* agent-bound: when ``COPILOT_FOUNDRY_AGENT_NAME`` is set, the OpenAI client is obtained with
  ``project.get_openai_client(agent_name=...)`` and the definition registered in Foundry Agent
  Service (see ``scripts/register_agent.py``) is used instead.

In both modes function tools are executed by this service, never by Foundry, which is what lets the
approval gate stay in our own code. Requests are stateless (``store=False``): conversation state
lives in this service.

Not yet validated on Azure. Shapes follow azure-ai-projects 2.x / the Responses API and are
unit-tested against a fake client.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from copilot.domain import Message, ModelTurn, ToolCall, Usage
from copilot.tools.registry import ToolSpec


class OpenAIClientLike(Protocol):
    """The part of ``openai.OpenAI`` used here: ``client.responses.create(**request)``."""

    @property
    def responses(self) -> Any: ...


def to_input_items(messages: list[Message]) -> list[dict[str, Any]]:
    """Convert provider-neutral messages to Responses API input items (system text is sent separately)."""
    items: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "system":
            continue
        if message.role == "tool":
            items.append({"type": "function_call_output", "call_id": message.tool_call_id, "output": message.content})
            continue
        if message.content:
            items.append({"role": message.role, "content": message.content})
        items.extend(
            {
                "type": "function_call",
                "call_id": call.call_id,
                "name": call.name,
                "arguments": json.dumps(call.arguments),
            }
            for call in message.tool_calls
        )
    return items


class FoundryResponsesModel:
    def __init__(
        self, client: OpenAIClientLike, *, deployment: str, instructions: str, agent_bound: bool = False
    ) -> None:
        self._client = client
        self._deployment = deployment
        self._instructions = instructions
        self._agent_bound = agent_bound

    def complete(self, messages: list[Message], tools: list[ToolSpec]) -> ModelTurn:
        request: dict[str, Any] = {"input": to_input_items(messages), "store": False}
        if not self._agent_bound:
            request |= {
                "model": self._deployment,
                "instructions": self._instructions,
                "tools": [tool.function_tool() for tool in tools],
            }
        response = self._client.responses.create(**request)

        tool_calls: list[ToolCall] = []
        for item in response.output:
            if getattr(item, "type", None) != "function_call":
                continue
            try:
                arguments = json.loads(item.arguments or "{}")
            except json.JSONDecodeError:
                arguments = {}  # fails validation in the agent loop and is reported back to the model
            tool_calls.append(
                ToolCall(
                    call_id=item.call_id,
                    name=item.name,
                    arguments=arguments if isinstance(arguments, dict) else {},
                )
            )
        usage = getattr(response, "usage", None)
        return ModelTurn(
            text="" if tool_calls else (response.output_text or ""),
            tool_calls=tool_calls,
            usage=Usage(
                input_tokens=getattr(usage, "input_tokens", 0) or 0,
                output_tokens=getattr(usage, "output_tokens", 0) or 0,
            ),
        )
