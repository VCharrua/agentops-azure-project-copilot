# ADR 0002: Own the agent loop; use Foundry through the Responses API

Status: accepted

## Context

Microsoft Foundry offers several ways to build an agent (checked against the documentation in
November 2026):

1. **Prompt agents in Foundry Agent Service**: the definition (model, instructions, tools) is
   registered with `project.agents.create_version(...)` and invoked through the OpenAI-compatible
   Responses API with a client bound to the agent.
2. **Hosted agents / Agent Framework**: agent code runs as a managed workload.
3. **Responses API directly** on a Foundry project, with instructions and tools sent per request.

Two requirements drive the choice. Write actions must be gated by our own code. And a change to
the prompt or tools must be tested, versioned and rolled back like any other code change.

## Decision

Keep a small agent loop in this service and call the model through the Foundry project's Responses
API. By default the definition is sent inline with every request (option 3). Function tools are
always executed by this service, never by the platform.

Option 1 remains available: `scripts/register_agent.py` publishes the same definition as an agent
version, and setting `COPILOT_FOUNDRY_AGENT_NAME` makes the adapter use the agent-bound client.

## Reasons

- **The approval gate stays in code we test.** Function calls come back to this service in both
  options, so the loop can refuse to execute write tools. Owning the loop also gives one place for
  input, document and output guardrails, argument validation and the step limit.
- **Atomic versioning.** With the definition in the image, the prompt version is the image
  version. The evals that gated a commit describe exactly what is deployed, and rolling back the
  image rolls back the prompt.
- **Testability.** The model is a port. A deterministic offline model and scripted "compromised"
  models plug into the same loop, which is how CI gates every pull request without a cloud.
- **Small surface.** The loop is short, and the Foundry adapter is a translation of messages to
  Responses API items.

## Trade-offs

- We do not get platform-managed conversation state, built-in tools (file search, code
  interpreter) or the portal's agent tooling by default.
- In agent-bound mode the registered version is not rolled back with the image.
- The loop is ours to maintain. If it grew multi-agent orchestration, checkpointing or long-running
  workflows, Agent Framework would be the better base.

## Validation status

The adapter is unit-tested against a fake client and built against `azure-ai-projects` 2.x. It has
not yet been run against a live Foundry project.
