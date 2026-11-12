"""Composition root: builds the object graph for ``local`` or ``azure`` mode.

Everything outside this module depends on ports (protocols). Only this file knows which adapter is
behind each port, so switching between mocks and Azure services is a configuration change.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from copilot.agent.model import ModelClient
from copilot.agent.offline_model import OfflineModel
from copilot.agent.orchestrator import Orchestrator
from copilot.approvals.audit import AuditLog
from copilot.approvals.store import ApprovalGate
from copilot.config import Settings
from copilot.guardrails.base import CompositeGuardrail, Guardrail
from copilot.guardrails.local import LocalGuardrail
from copilot.retrieval.base import Retriever
from copilot.retrieval.documents import load_chunks
from copilot.retrieval.local import LocalHybridRetriever
from copilot.tools.actions import ActionExecutor
from copilot.tools.catalog import build_registry
from copilot.tools.project_data import ProjectRepository
from copilot.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from azure.core.credentials import TokenCredential


@dataclass
class Container:
    settings: Settings
    repo: ProjectRepository
    tools: ToolRegistry
    executor: ActionExecutor
    audit: AuditLog
    approvals: ApprovalGate
    orchestrator: Orchestrator


def build_container(
    settings: Settings,
    *,
    model: ModelClient | None = None,
    retriever: Retriever | None = None,
    guardrail: Guardrail | None = None,
    persist: bool = True,
) -> Container:
    """Build the application. Explicit ``model`` / ``retriever`` / ``guardrail`` override the mode defaults
    (used by tests and by the evals to plug in, for example, a deliberately compromised model)."""
    repo = ProjectRepository(settings.data_dir)

    if settings.mode == "azure" and (model is None or retriever is None or guardrail is None):
        azure_model, azure_retriever, azure_guardrail = _azure_adapters(settings)
        model, retriever, guardrail = (
            model or azure_model,
            retriever or azure_retriever,
            guardrail or azure_guardrail,
        )
    model = model or OfflineModel(repo)
    retriever = retriever or LocalHybridRetriever(load_chunks(settings.data_dir))
    guardrail = guardrail or LocalGuardrail()

    tools = build_registry(repo, retriever, top_k=settings.retrieval_top_k)
    executor = ActionExecutor(settings.state_dir if persist else None)
    audit = AuditLog(settings.state_dir / "audit.jsonl" if persist else None)
    approvals = ApprovalGate(executor=executor, audit=audit, ttl=timedelta(minutes=settings.approval_ttl_minutes))
    orchestrator = Orchestrator(
        model=model,
        tools=tools,
        guardrail=guardrail,
        approvals=approvals,
        audit=audit,
        max_steps=settings.max_agent_steps,
        allowed_email_domains=settings.allowed_email_domains,
    )
    return Container(settings, repo, tools, executor, audit, approvals, orchestrator)


def azure_credential() -> TokenCredential:
    """Managed identity in Azure, developer login locally. No keys or connection strings anywhere."""
    from azure.identity import DefaultAzureCredential

    return DefaultAzureCredential()


def _azure_adapters(settings: Settings) -> tuple[ModelClient, Retriever, Guardrail]:  # pragma: no cover
    """Wire the real Azure services. Not exercised by the test suite: it needs live Azure resources."""
    import httpx
    from azure.ai.contentsafety import ContentSafetyClient
    from azure.ai.projects import AIProjectClient
    from azure.identity import get_bearer_token_provider
    from azure.search.documents import SearchClient

    from copilot.agent.foundry_model import FoundryResponsesModel
    from copilot.agent.prompts import SYSTEM_PROMPT
    from copilot.guardrails.azure_content_safety import COGNITIVE_SCOPE, AzureContentSafetyGuardrail
    from copilot.retrieval.azure_search import AzureSearchRetriever

    credential = azure_credential()
    project = AIProjectClient(endpoint=settings.require("foundry_project_endpoint"), credential=credential)
    plain_client = project.get_openai_client()
    agent_name = settings.foundry_agent_name
    chat_client = project.get_openai_client(agent_name=agent_name) if agent_name else plain_client

    def embed(text: str) -> list[float]:
        response = plain_client.embeddings.create(model=settings.foundry_embedding_deployment, input=text)
        return list(response.data[0].embedding)

    model = FoundryResponsesModel(
        chat_client,
        deployment=settings.foundry_model_deployment,
        instructions=SYSTEM_PROMPT,
        agent_bound=bool(agent_name),
    )
    retriever = AzureSearchRetriever(
        SearchClient(settings.require("search_endpoint"), settings.search_index, credential), embed
    )
    safety_endpoint = settings.require("content_safety_endpoint")
    guardrail = CompositeGuardrail(
        [
            LocalGuardrail(),
            AzureContentSafetyGuardrail(
                endpoint=safety_endpoint,
                analyzer=ContentSafetyClient(safety_endpoint, credential),
                token_provider=get_bearer_token_provider(credential, COGNITIVE_SCOPE),
                http=httpx.Client(),
                severity_threshold=settings.content_safety_severity_threshold,
                fail_closed=settings.guardrail_fail_closed,
            ),
        ]
    )
    return model, retriever, guardrail
