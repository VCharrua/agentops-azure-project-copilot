"""Deterministic, offline guardrail.

Heuristic pattern matching for prompt-injection and a small harmful-content denylist. It runs in
every mode: locally it is the only guardrail, in Azure it is the first layer in front of Azure AI
Content Safety. Heuristics are easy to evade, which is exactly why the approval gate does not rely
on them: a missed injection can at most *propose* a write action, never execute one.
"""

from __future__ import annotations

import re

from copilot.guardrails.base import ALLOW, Verdict

_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:ignore|disregard|forget|override)\b.{0,40}\b(?:previous|prior|above|earlier|all|your)\b.{0,40}"
        r"\b(?:instructions?|rules?|prompts?|guidelines?|polic(?:y|ies))\b",
        r"\byou are now\b",
        r"\b(?:system|developer|maintenance|debug|god|dan)\s+(?:mode|note|override|prompt)\b",
        r"\b(?:reveal|print|show|repeat|leak|output)\b.{0,40}\b(?:system prompt|hidden instructions?|"
        r"your instructions?|initial prompt)\b",
        r"\b(?:do not|don't|never|without)\b.{0,20}\b(?:ask(?:ing)?|wait(?:ing)?|request(?:ing)?|need(?:ing)?|"
        r"human|user|any)\b.{0,15}\bapproval\b",
        r"\b(?:bypass|skip|disable|turn off|circumvent)\b.{0,40}\b(?:approval|guardrails?|safety|filters?|"
        r"human[- ]in[- ]the[- ]loop|review)\b",
        r"\bpre-?approved\b",
        r"\bact as (?:an? )?(?:unrestricted|jailbroken|different)\b",
        r"<\s*/?\s*(?:system|assistant|instructions?)\s*>",
    )
)

_HARMFUL_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:build|make|assemble|synthesi[sz]e)\b.{0,40}\b(?:bomb|explosive|nerve agent|bioweapon)\b",
        r"\b(?:falsify|forge|fake|backdate)\b.{0,40}\b(?:inspection|certificate|safety record|invoice|"
        r"test results?|signature)\b",
        r"\b(?:hide|conceal|cover up)\b.{0,40}\b(?:incident|injury|accident|asbestos|defect)\b",
    )
)


def find_injection(text: str) -> str | None:
    for pattern in _INJECTION_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(0)
    return None


class LocalGuardrail:
    def check_input(self, text: str) -> Verdict:
        if find_injection(text):
            return Verdict(
                allowed=False,
                category="prompt_injection",
                reason="The request looks like an attempt to override the assistant's instructions or controls.",
            )
        if any(pattern.search(text) for pattern in _HARMFUL_PATTERNS):
            return Verdict(
                allowed=False,
                category="harmful_content",
                reason="The request asks for help with something unsafe or dishonest.",
            )
        return ALLOW

    def check_documents(self, documents: list[str]) -> list[Verdict]:
        return [
            Verdict(
                allowed=False,
                category="indirect_prompt_injection",
                reason="Retrieved content contains instructions addressed to the assistant.",
            )
            if find_injection(document)
            else ALLOW
            for document in documents
        ]

    def check_output(self, text: str) -> Verdict:
        if any(pattern.search(text) for pattern in _HARMFUL_PATTERNS):
            return Verdict(allowed=False, category="harmful_content", reason="Unsafe content in the answer.")
        return ALLOW
