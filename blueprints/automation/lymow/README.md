# Lymow automation blueprints

Ready-made Home Assistant automation blueprints for the Lymow mower. Each one
builds only on the integration's standard `lawn_mower` entity and services
(`lawn_mower.dock`, `lawn_mower.start_mowing`), so they work without any extra
configuration.

## Blueprints

- **Rain delay** (`rain_delay.yaml`) — docks the mower when a rain sensor turns
  on, optionally resuming when it's dry again.
- **Quiet hours** (`quiet_hours.yaml`) — docks the mower during a time window
  (e.g. overnight) and optionally resumes when it ends.

## Importing

In Home Assistant: **Settings → Automations & Scenes → Blueprints → Import
blueprint**, then paste the **raw** URL of the blueprint file, e.g.:

```
https://raw.githubusercontent.com/8408323/ha-lymow/main/blueprints/automation/lymow/rain_delay.yaml
```

Or copy the `.yaml` file into `config/blueprints/automation/lymow/` and reload
blueprints. Then create an automation from the imported blueprint and pick your
mower (and rain sensor / quiet window).
