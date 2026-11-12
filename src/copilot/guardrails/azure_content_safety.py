"""Azure AI Content Safety guardrail: harm categories via the SDK, Prompt Shields via REST.

Prompt Shields (``text:shieldPrompt``) is called over REST because azure-ai-contentsafety 1.0.0 only
exposes ``analyze_text`` / ``analyze_image``. Authentication is Entra ID (managed identity): no keys.

Not yet validated on Azure. The request and response shapes follow the published REST reference
(api-version 2024-09-01) and are unit-tested against fakes.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Protocol

import httpx

from copilot.guardrails.base import ALLOW, Verdict

logger = logging.getLogger(__name__)

SHIELD_API_VERSION = "2024-09-01"
SHIELD_MAX_DOCUMENTS = 5
SHIELD_MAX_CHARS = 10_000
COGNITIVE_SCOPE = "https://cognitiveservices.azure.com/.default"


class TextAnalyzer(Protocol):
    """The part of ``ContentSafetyClient`` this guardrail uses."""

    def analyze_text(self, options: Any, **kwargs: Any) -> Any: ...


class AzureContentSafetyGuardrail:
    def __init__(
        self,
        *,
        endpoint: str,
        analyzer: TextAnalyzer,
        token_provider: Callable[[], str],
        http: httpx.Client,
        severity_threshold: int = 2,
        fail_closed: bool = True,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._analyzer = analyzer
        self._token_provider = token_provider
        self._http = http
        self._threshold = severity_threshold
        self._fail_closed = fail_closed

    # -- helpers ---------------------------------------------------------------------------------
    def _unavailable(self, operation: str) -> Verdict:
        """Service errors block by default: an unscreened prompt is treated as unsafe."""
        logger.exception("content safety %s failed", operation)
        if self._fail_closed:
            return Verdict(allowed=False, category="guardrail_unavailable", reason="The safety service is unavailable.")
        return ALLOW

    def _harm(self, text: str) -> Verdict:
        try:
            result = self._analyzer.analyze_text({"text": text[:SHIELD_MAX_CHARS]})
            flagged = [
                str(item.category)
                for item in result.categories_analysis
                if item.severity is not None and item.severity >= self._threshold
            ]
        except Exception:
            return self._unavailable("analyze_text")
        if flagged:
            return Verdict(
                allowed=False,
                category="harmful_content",
                reason=f"Content Safety flagged: {', '.join(sorted(flagged))}.",
            )
        return ALLOW

    def _shield(self, user_prompt: str, documents: list[str]) -> dict[str, Any]:
        response = self._http.post(
            f"{self._endpoint}/contentsafety/text:shieldPrompt",
            params={"api-version": SHIELD_API_VERSION},
            headers={"Authorization": f"Bearer {self._token_provider()}"},
            json={
                "userPrompt": user_prompt[:SHIELD_MAX_CHARS],
                "documents": [d[:SHIELD_MAX_CHARS] for d in documents],
            },
            timeout=10.0,
        )
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        return payload

    # -- Guardrail protocol ----------------------------------------------------------------------
    def check_input(self, text: str) -> Verdict:
        try:
            analysis = self._shield(text, [])
        except Exception:
            return self._unavailable("shieldPrompt")
        if analysis.get("userPromptAnalysis", {}).get("attackDetected"):
            return Verdict(
                allowed=False,
                category="prompt_injection",
                reason="Prompt Shields detected a user prompt attack.",
            )
        return self._harm(text)

    def check_documents(self, documents: list[str]) -> list[Verdict]:
        verdicts: list[Verdict] = []
        for start in range(0, len(documents), SHIELD_MAX_DOCUMENTS):
            batch = documents[start : start + SHIELD_MAX_DOCUMENTS]
            try:
                analysis = self._shield("", batch)
                flags = [bool(item.get("attackDetected")) for item in analysis.get("documentsAnalysis", [])]
                if len(flags) != len(batch):
                    raise ValueError("documentsAnalysis length does not match the request")
            except Exception:
                verdicts.extend([self._unavailable("shieldPrompt")] * len(batch))
                continue
            verdicts.extend(
                Verdict(
                    allowed=False,
                    category="indirect_prompt_injection",
                    reason="Prompt Shields detected a document attack.",
                )
                if flagged
                else ALLOW
                for flagged in flags
            )
        return verdicts

    def check_output(self, text: str) -> Verdict:
        return self._harm(text)
