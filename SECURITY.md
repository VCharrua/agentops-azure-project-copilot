# Security policy

This repository is a reference implementation that uses synthetic data only. It is not a product
and runs no hosted service.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting for this repository
(**Security > Report a vulnerability**) rather than a public issue. Include the affected file or
workflow, what an attacker could do, and a way to reproduce it.

Reports about the controls this project is meant to demonstrate are especially welcome:

- a way to execute a write action without a decision through the approvals API;
- a prompt-injection or data-exfiltration path that the evaluation suite does not cover;
- a workflow weakness (token permissions, untrusted input in `run:` steps, unpinned actions).

## Scope notes

- The threat model, including known gaps, is in [docs/threat-model.md](docs/threat-model.md).
- One synthetic document contains a prompt-injection payload on purpose; see
  [data/synthetic/README.md](data/synthetic/README.md).
