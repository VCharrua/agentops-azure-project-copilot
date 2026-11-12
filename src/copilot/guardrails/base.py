"""Guardrail port and the composite that chains several guardrails."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel


class Verdict(BaseModel):
    allowed: bool
    category: str | None = None
    reason: str | None = None


ALLOW = Verdict(allowed=True)


class Guardrail(Protocol):
    def check_input(self, text: str) -> Verdict:
        """Screen a user message (direct prompt injection, harmful content)."""
        ...

    def check_documents(self, documents: list[str]) -> list[Verdict]:
        """Screen retrieved content (indirect prompt injection). One verdict per document."""
        ...

    def check_output(self, text: str) -> Verdict:
        """Screen the final answer before it leaves the service."""
        ...


class CompositeGuardrail:
    """Run guardrails in order; the first block wins. Used for defence in depth in Azure mode."""

    def __init__(self, guardrails: list[Guardrail]) -> None:
        self._guardrails = guardrails

    def check_input(self, text: str) -> Verdict:
        for guardrail in self._guardrails:
            verdict = guardrail.check_input(text)
            if not verdict.allowed:
                return verdict
        return ALLOW

    def check_documents(self, documents: list[str]) -> list[Verdict]:
        verdicts = [ALLOW] * len(documents)
        for guardrail in self._guardrails:
            for index, verdict in enumerate(guardrail.check_documents(documents)):
                if verdicts[index].allowed and not verdict.allowed:
                    verdicts[index] = verdict
        return verdicts

    def check_output(self, text: str) -> Verdict:
        for guardrail in self._guardrails:
            verdict = guardrail.check_output(text)
            if not verdict.allowed:
                return verdict
        return ALLOW
