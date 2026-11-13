<!-- Title must follow Conventional Commits, e.g. "feat(agent): add schedule tool". CI checks it. -->

## What and why

<!-- What changes, and the reason for it. Link the issue if there is one. -->

## How it was verified

- [ ] `make lint` and `make test` pass locally
- [ ] `make eval` passes (required when prompts, tools, retrieval or guardrails change)

## Risk review

- [ ] No secrets, tenant ids, subscription ids or real personal data added
- [ ] Agent behaviour change: prompt version bumped and golden cases added or updated
- [ ] New or changed tool: classified as read or write; write tools go through the approval gate
- [ ] Eval thresholds lowered: justified below
- [ ] Infrastructure change: Bicep what-if comment reviewed

## Notes for the reviewer

<!-- Anything unverified, any follow-up, anything you want a second opinion on. -->
