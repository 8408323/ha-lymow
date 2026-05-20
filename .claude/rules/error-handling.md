---
paths:
  - "custom_components/lymow/**"
---

# Error Handling

Follow Home Assistant conventions for an integration that polls a cloud API and merges an MQTT stream.

- Raise the right typed HA exception, not a bare `Exception`: `ConfigEntryAuthFailed` for expired/invalid credentials (triggers reauth), `ConfigEntryNotReady` for transient setup failures (triggers retry), `UpdateFailed` from the coordinator's update method, and `HomeAssistantError` for user-facing service/command failures.
- Never swallow errors silently. If you catch, either re-raise with added context or log at the appropriate level with what operation failed. A bare `except: pass` hides real bugs.
- Distinguish expected failures from bugs. Validation / "field absent in this payload" is expected — handle it locally. An unexpected decode error is a bug — let it propagate so it's visible, don't mask it with a default.
- Retry only transient failures (network timeouts, 5xx, MQTT reconnect) with backoff. Fail fast on auth and validation errors — retrying them just delays the reauth flow.
- Don't leak tokens, identity IDs, or raw payloads in exception messages or logs — they surface in the HA UI and logs.
- Every awaited call has an owner: don't fire-and-forget coroutines. Use `hass.async_create_task` (or `entry.async_create_background_task`) so failures are surfaced, not lost.
