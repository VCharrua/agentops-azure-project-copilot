# ADR 0005: Human approval is enforced by code structure, not by the prompt

Status: accepted

## Context

The agent can create tasks, save email drafts and log incidents. These must happen only after a
human approves. The agent also reads documents that an attacker can influence (a forwarded
supplier email is enough), so prompt injection has to be assumed, not merely discouraged.

Common approaches and why they fall short alone:

- *Instruct the model to ask first.* A successful injection overrides instructions.
- *Detect injections with a classifier.* Detection has false negatives.
- *Ask "are you sure?" in the chat.* The same channel the attacker controls gives the answer.

## Decision

Treat the model as untrusted and make unapproved execution unreachable:

1. Tools are classified `read` or `write` in the registry. Write tools have no handler.
2. The agent loop can only call `ApprovalGate.propose`. It is never given the executor.
3. Execution happens only inside `ApprovalGate.decide`, exposed only by
   `POST /approvals/{id}/decision`, which requires an approver identity supplied by the platform's
   authentication layer.
4. A decision is bound to a hash of the proposed arguments, is single-use and expires.
5. Every proposal, decision and refusal is written to a hash-chained audit log.
6. The response always ends with a system-generated list of pending actions.

## Consequences

- The worst outcome of a prompt injection is a pending proposal. Guardrails and risk flags then
  reduce how often a human sees one and help them judge it.
- The property is testable without a model: unit tests, API tests and eval cases drive the loop
  with scripted compromised models and assert zero side effects, including with guardrails off.
- Users need a second surface to approve. Today that is the approvals API; a small UI is on the
  roadmap.
- Approval fatigue is a real risk: a human who clicks "approve" on everything is not oversight.
  Mitigations in place are the full payload and risk flags on each proposal, and expiry. Rate
  limits per session would be a sensible addition.

## Known gaps

- State is in memory, so pending proposals do not survive a restart.
- Approver identity is only trustworthy when platform authentication is enabled. Without it the
  header can be spoofed, which is why that mode is limited to dev with synthetic data.
- There is no separation of duties rule yet (the requester may approve their own proposal).
