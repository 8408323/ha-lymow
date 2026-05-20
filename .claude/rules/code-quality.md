---
alwaysApply: true
---

# Code Quality

## Anti-defaults (counter common Claude tendencies)

- No premature abstractions. Three similar lines beats a helper used once.
- Don't add features or improvements beyond what was asked.
- Don't refactor adjacent code while fixing a bug.
- No dead code or commented-out blocks. Git has history.
- WHY comments, never WHAT. If code needs a "what" comment, rename instead. House style is no comments unless the WHY is non-obvious; one short line max per function.
- `from __future__ import annotations` at the top of every module. Type hints throughout; `dict[str, Any]`, not `Dict`.

## Naming (Python / Home Assistant)

- `snake_case` for modules, functions, variables; `PascalCase` for classes; `SCREAMING_SNAKE_CASE` for constants. Match what already exists (e.g. `coordinator.py`, `LymowDataUpdateCoordinator`, `USER_CTRL_QUERY_*`).
- Booleans: `is` / `has` / `should` / `can` prefix. Functions: verb-first (`async_send_user_ctrl`, `decode_map_response`).
- Factories: `make_*` / `create_*`. Converters: `to_*`. Predicates: `is_*` / `has_*`.
- Abbreviations only when universally known here (`id`, `url`, `api`, `db`, `mqtt`, `ble`, `rtk`, `pb` for protobuf). Keep protocol field names verbatim as captured (`deviceThingName`, `wifiRssiDbm`) — don't snake-case wire keys.
- HA entity classes follow the platform convention: `Lymow<Thing><Platform>` (e.g. `LymowMapSensor`, `LockRobotButton`).

## Code Markers

`TODO(author): desc (#issue)` for planned work. `FIXME(author): desc (#issue)` for known bugs. `HACK(author): desc (#issue)` for ugly workarounds (explain the proper fix). `NOTE: desc` for non-obvious context. Owner and issue link required. Never `XXX`, `TEMP`, `REMOVEME`.

## File Organization

- Imports: stdlib, third-party, `homeassistant.*`, then local package (relative). Blank line between groups; `homeassistant` imports go at module top, never inside functions.
- Function order: public API first, then helpers in call order.
- One public class per file unless tightly related (e.g. small entity classes used together in a platform module).
