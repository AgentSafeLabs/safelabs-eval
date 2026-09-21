# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in `safelabs-eval` itself
(not a vulnerability *found by* using the tool against a third-party
agent — see below), please report it privately rather than opening a
public issue.

**Preferred: GitHub Private Vulnerability Reporting** — use the
"Report a vulnerability" button under this repo's Security tab.

Please include:
- A description of the vulnerability and its potential impact
- Steps to reproduce
- Affected version(s)

This is a solo-maintained open-source project. I aim to acknowledge
reports within 5 business days and will keep you updated on remediation
progress. There is no bug bounty program at this time.

## Scope

**In scope:**
- Detector logic (`safelabs/scoring/detectors/`) — e.g. a vulnerability
  that causes the framework to silently produce a false PASS/miss a
  real compliance signal in a way that misleads users about their own
  agent's safety
- Framework adapters (`safelabs/agents/`) — e.g. unsafe code execution,
  injection risks in adapter handling
- `agentport_bench` schema/validation logic — e.g. a validation bypass
  that lets malformed or malicious data into public submissions
- Supply-chain issues in the published PyPI package

**Out of scope:**
- Vulnerabilities *discovered in third-party agents* using this
  framework — that's the tool working as intended; report those to the
  relevant agent vendor, not here
- The adversarial prompt library itself (it's intentionally adversarial
  content by design)

## Supported Versions

Only the latest published release on PyPI receives security fixes.
This project does not currently maintain long-term support branches.
