# Security Policy

## Reporting a vulnerability

Use GitHub's **"Report a vulnerability"** button on the [Security tab](https://github.com/8408323/ha-lymow/security/advisories/new). That opens a private advisory visible only to maintainers — please do not file public issues for suspected security bugs.

Useful info to include:
- Affected version (tag or commit SHA) and Home Assistant version
- Reproduction steps or proof-of-concept
- Impact (what an attacker can read, modify, or trigger)

This is a hobby integration maintained by one person; I'll aim to acknowledge within a week and follow up on a fix as time allows.

## Supported versions

Only the latest commit on `main` is supported. Older tagged releases will not receive backported fixes.

## Scope

In scope:
- The integration code under [`custom_components/lymow/`](custom_components/lymow/)
- The capture tooling under [`tools/`](tools/) and [`scripts/`](scripts/), if a flaw there could leak credentials beyond what the user explicitly captured
- CI workflows under [`.github/workflows/`](.github/workflows/)

Out of scope:
- Bugs in the upstream Lymow app, cloud API, or robot firmware — report those to the vendor
- Bugs in Home Assistant core — report at [home-assistant/core](https://github.com/home-assistant/core/security)
- Leaks resulting from a user committing their own capture artifacts (these are gitignored by default; see [`.gitignore`](.gitignore))
