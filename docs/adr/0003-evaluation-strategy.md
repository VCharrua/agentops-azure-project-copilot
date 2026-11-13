# ADR 0003: Two-tier evaluation with thresholds as a merge gate

Status: accepted

## Context

Agent behaviour changes with prompts, tools, retrieval, guardrails and the model. Unit tests do
not cover "is the answer grounded" or "does an injection get through". Evaluation has to run in
the pipeline, fail the build on regression, and be affordable on every pull request.

Calling a real model on every PR is slow, costs money, needs credentials that forks do not have,
and is non-deterministic, which makes a hard gate flaky.

## Decision

One versioned golden dataset (`evals/golden/v1.jsonl`), two tiers, one set of thresholds
(`evals/thresholds.yaml`).

| | Offline tier | Live tier |
| --- | --- | --- |
| When | Every PR and push | Nightly, on demand, and in CD between dev and staging |
| Model | Deterministic offline model; scripted compromised models for attacks | The deployed model |
| Retrieval, guardrails | Local adapters | Azure AI Search, Content Safety |
| Cost, credentials | None | Model tokens, OIDC |
| What it gates | The system around the model | End-to-end quality with the real model |

Metrics: retrieval hit rate and MRR, groundedness (lexical proxy; LLM-judged in the live tier),
citation validity, answer match, tool-call correctness, and safety pass rates. Safety thresholds
are 1.0: one regression blocks the merge.

## Reasons

- **Most agent regressions are not model regressions.** A changed chunker, a renamed tool
  argument, a loosened guardrail pattern or a broken approval path are all caught
  deterministically, for free, on the PR that introduced them.
- **Attack cases need a model that misbehaves on demand.** Scripted compromised models call write
  tools directly, invent approval tools, exfiltrate by email and obey a poisoned document. No
  real model can be relied on to do that reproducibly, and these cases prove the approval gate
  independently of the guardrails.
- **The gate is itself tested.** Mutation tests remove the guardrails or empty the index and
  assert that the thresholds fail.
- **One dataset** keeps the two tiers comparable.

## Trade-offs

- Offline scores say nothing about a real model's reasoning. They are regression gates and the
  README says so. Perfect offline scores are expected.
- The lexical groundedness proxy catches fabricated sentences, not subtle misreadings.
- The dataset is small. Its value depends on growing it from real failures.
- The live tier is not yet validated on Azure.

## Rules of use

- A change to prompts, tools, retrieval or guardrails adds or updates golden cases in the same PR.
- Lowering a threshold needs a written justification in the PR (it is on the PR template).
- A production incident becomes a golden case before the fix is merged.
