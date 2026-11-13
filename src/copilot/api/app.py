"""HTTP API.

Two deliberately separate channels:

* ``POST /chat`` talks to the agent. Nothing reachable from here can execute a write action.
* ``POST /approvals/{id}/decision`` is the only way to execute one, and it needs an approver
  identity from a header set by the platform's authentication layer (see docs/threat-model.md).

Note: no ``from __future__ import annotations`` here. FastAPI resolves route annotations at
definition time and the dependency alias below is local to ``create_app``.
"""

import threading
from collections import OrderedDict
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from copilot import __version__
from copilot.agent.prompts import PROMPT_VERSION
from copilot.approvals.store import ApprovalError
from copilot.bootstrap import Container, build_container
from copilot.config import Settings, get_settings
from copilot.domain import ActionStatus, AgentResponse, Message, PendingAction
from copilot.observability.telemetry import configure_telemetry
from copilot.tools.project_data import UnknownProjectError

MAX_SESSIONS = 500
MAX_HISTORY_MESSAGES = 12


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = Field(default=None, pattern=r"^[\w-]{1,64}$")


class DecisionRequest(BaseModel):
    decision: Literal["approve", "reject"]


class SessionStore:
    """Small in-memory conversation memory (user and assistant text only), bounded in both dimensions."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: OrderedDict[str, list[Message]] = OrderedDict()

    def history(self, session_id: str | None) -> list[Message]:
        with self._lock:
            return list(self._sessions.get(session_id or "", []))

    def record(self, session_id: str, user: str, assistant: str) -> None:
        with self._lock:
            messages = self._sessions.setdefault(session_id, [])
            messages += [Message(role="user", content=user), Message(role="assistant", content=assistant)]
            del messages[:-MAX_HISTORY_MESSAGES]
            self._sessions.move_to_end(session_id)
            while len(self._sessions) > MAX_SESSIONS:
                self._sessions.popitem(last=False)


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    settings = settings or get_settings()
    telemetry_enabled = configure_telemetry()
    container = container or build_container(settings)
    sessions = SessionStore()

    app = FastAPI(
        title="Project Delivery Copilot",
        version=__version__,
        description="Synthetic-data reference implementation. All answers are AI-generated.",
    )
    if telemetry_enabled:  # pragma: no cover - requires an Application Insights connection string
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz")

    def get_container() -> Container:
        return container

    Deps = Annotated[Container, Depends(get_container)]  # noqa: N806 - type alias

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/version")
    def version() -> dict[str, str]:
        return {"version": settings.app_version, "mode": settings.mode, "prompt_version": PROMPT_VERSION}

    @app.post("/chat")
    def chat(body: ChatRequest, deps: Deps) -> AgentResponse:
        response = deps.orchestrator.run(
            body.message, session_id=body.session_id, history=sessions.history(body.session_id)
        )
        if not response.blocked:
            sessions.record(response.session_id, body.message, response.answer)
        return response

    @app.get("/projects")
    def projects(deps: Deps) -> list[dict[str, Any]]:
        return deps.repo.list_projects()

    @app.get("/projects/{project_id}/status-report")
    def status_report(project_id: str, deps: Deps) -> dict[str, Any]:
        try:
            return deps.repo.status_report(project_id)
        except UnknownProjectError:
            raise HTTPException(status_code=404, detail="unknown project") from None

    @app.get("/approvals")
    def list_approvals(deps: Deps, status: ActionStatus | None = None) -> list[PendingAction]:
        return deps.approvals.list(status)

    @app.post("/approvals/{action_id}/decision")
    def decide(action_id: str, body: DecisionRequest, request: Request, deps: Deps) -> PendingAction:
        approver = request.headers.get(settings.approver_header, "")
        if not approver.strip():
            raise HTTPException(status_code=401, detail=f"missing approver identity ({settings.approver_header})")
        if deps.approvals.get(action_id) is None:
            raise HTTPException(status_code=404, detail="unknown action")
        try:
            return deps.approvals.decide(action_id, approve=body.decision == "approve", approver=approver)
        except ApprovalError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None

    @app.get("/audit")
    def audit(deps: Deps) -> dict[str, Any]:
        return {"chain_valid": deps.audit.verify(), "records": deps.audit.records()}

    return app
