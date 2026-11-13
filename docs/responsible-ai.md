# Responsible AI: EU AI Act and GDPR considerations

This is an engineering note. It records how the design relates to regulatory themes so that a
compliance or legal function has something concrete to assess. **It is not legal advice and
reaches no legal conclusion.** Classification under the EU AI Act depends on the actual deployer,
purpose and context of use, none of which exist for a synthetic reference implementation.

## 1. What the system does, in regulatory terms

- A general-purpose language model, accessed as a service, is integrated into an assistant for
  project managers.
- It retrieves and summarises internal documents, computes project figures with deterministic
  code, and prepares drafts.
- It can propose three kinds of record changes. A human decides on each one.
- It does not make or recommend decisions about individuals.

## 2. Risk classification: the reasoning to bring to counsel

Questions a real deployer would work through, with what this design implies for each:

| Question | Observation for this use case |
| --- | --- |
| Is any prohibited practice involved (manipulation, social scoring, and so on)? | Nothing in the functionality points that way. |
| Does the use fall in a high-risk area listed in Annex III (for example employment and worker management, access to essential services, critical infrastructure)? | The described purpose is project information and administrative drafting, which is not obviously in a listed area. **It could move closer** if the same system were used to evaluate or allocate work to individual workers, to make safety-critical determinations, or as a safety component of infrastructure. Those uses are outside the intended purpose and the documentation says so. |
| Is it a safety component of a regulated product? | No. Incident logging here is record-keeping, not a safety function. A deployer must not rely on the assistant to detect or classify safety events. |
| Do transparency obligations for systems interacting with people apply? | Users interact with an AI system directly, so the design assumes disclosure is needed and provides it (section 3). |
| Who is the provider and who is the deployer? | The model provider, the party that builds this application and the organisation that uses it may be different entities with different obligations. This repository does not settle that. |

The intended purpose is deliberately narrow and written down (README, system prompt), because
classification follows purpose. Extending the purpose should trigger a new assessment.

## 3. How design choices map to the themes

| Theme | What the design does | Where |
| --- | --- | --- |
| Transparency to users | Every response carries an AI disclosure and citations to source documents; drafts are labelled as drafts; the agent is instructed to say it is an AI when asked | `AgentResponse.ai_disclosure`, `agent/prompts.py` |
| Human oversight | Write actions require an explicit decision by a named person on a separate channel; the approver sees the full payload and risk flags; proposals expire; the system never claims an action was done | `approvals/store.py`, [ADR 0005](adr/0005-approval-gate-in-code.md) |
| Record-keeping and traceability | Hash-chained audit log of proposals, decisions, refusals and guardrail blocks; traces of every agent run with tool calls and token usage; prompt version on each trace | `approvals/audit.py`, `observability/telemetry.py` |
| Accuracy and robustness | Figures come from deterministic tools, not from the model; answers are restricted to retrieved content; a versioned evaluation suite gates changes; adversarial cases are part of it | `tools/project_data.py`, [evaluation.md](evaluation.md) |
| Cybersecurity | Threat model with tested controls for prompt injection, exfiltration and tool abuse; keyless authentication; scanned and pinned supply chain | [threat-model.md](threat-model.md) |
| Risk management as a process | Thresholds as merge gates; incidents become test cases; ADRs record decisions and their limits | [ADR 0003](adr/0003-evaluation-strategy.md) |
| Technical documentation | Architecture, ADRs, limitations and an explicit verified/unverified table | `docs/`, README |

Known gap against the oversight theme: approvers are not yet authorised by role, and the requester
can approve their own proposal.

## 4. GDPR considerations

The repository contains no personal data: names are invented and addresses use reserved example
domains. A real deployment would process personal data (employee and subcontractor names in site
reports, contact details in emails, possibly health information in incident descriptions). Points
a deployer would need to address, and what the design offers:

| Principle | Design support | Still to be decided by the deployer |
| --- | --- | --- |
| Lawfulness and purpose limitation | Narrow, documented purpose | Legal basis; whether incident data includes special categories |
| Data minimisation | Prompts, answers and document content are kept out of telemetry; logs and audit records are redacted for common identifiers | Whether documents should be minimised or pseudonymised before indexing |
| Storage limitation | Log retention is a parameter (30 days in dev and staging, 90 in prod); conversation memory is bounded and in memory only | Retention for the index, the audit trail and the systems of record |
| Integrity and confidentiality | Entra ID authentication with managed identity; key access disabled; least-privilege roles; encryption at rest and in transit by the platform | Network isolation; per-user access control on documents (a stated gap) |
| Data subject rights | Documents are identified by `doc_id` in the index, so a document can be found and removed | A process for access, rectification and erasure across index, logs and audit records; note that an append-only audit log and erasure pull in different directions |
| Automated decision-making | The system does not take decisions about individuals; humans decide on every action | Confirm this stays true for the deployed purpose |
| International transfers and processors | Region is a parameter (default in the EU) | Data residency of the chosen model deployment type (global deployment types can process outside the region), processor agreements |
| Data protection impact assessment | This document, the threat model and the architecture are inputs | Whether a DPIA is required and its outcome |

Two specifics worth flagging to a privacy function:

1. **Model deployment type and residency.** The template defaults to a global model deployment
   SKU for availability. If processing must stay in a geography, choose a data-zone or regional
   deployment type and confirm against current Microsoft documentation.
2. **Audit log versus erasure.** The audit trail stores redacted event data, not conversation
   content, to reduce this tension. Free-text arguments of a proposed action (an incident
   description, an email body) can still contain personal data.

## 5. What would change for a real deployment

- A documented intended purpose owned by the deploying organisation, and an assessment against it.
- Authentication on by default, role-based approver authorisation, per-user document access.
- A managed PII detection service in front of telemetry.
- User-facing notice and guidance on appropriate use, including what the assistant must not be
  relied on for.
- Monitoring of live quality and of approval behaviour (for example approval rate per approver as
  a signal of rubber-stamping).
