---
applyTo: "custom_components/lymow/**,scripts/**,tools/**"
---
<!-- dotclaude:managed — generated from .claude/rules/security.md by /dotclaude:init. Edit the rule, not this file. -->

# Security

This integration is a client of the Lymow cloud (AWS Cognito + IoT MQTT + REST), not a server — so the surface is credential handling and untrusted data from the cloud/device, not web endpoints.

- Validate and bound untrusted input at the boundary: decoded protobuf fields, MQTT payloads, and REST responses may be malformed or hostile. Never assume a field exists, has the expected type, or is in range.
- Never log secrets, tokens, PINs, or PII — access/refresh/session tokens, the Cognito identity ID, the device PIN, and GPS coordinates must never reach logs or `_LOGGER`. Redact before logging.
- Keep credentials out of source and out of committed fixtures. They belong in `LYMOW_USER` / `LYMOW_PASS` (env / gitignored `.env`) and in the HA config entry, never hardcoded.
- Use constant-time comparison (`hmac.compare_digest`) when comparing secrets or signatures.
- Don't build shell commands or paths from untrusted input. Use list-form `subprocess`, never `shell=True` with interpolation.
- Treat capture artifacts (`capture-*.txt`, `*.pcap`, `*.cfa`, `*.har`) as containing live secrets — keep them gitignored and never paste their raw contents into code, issues, or PRs.
