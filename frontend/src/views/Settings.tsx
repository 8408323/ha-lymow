import { useEffect, useMemo, useState } from "react";
import { ENTITY_TEXT, entityLabel } from "../entityText";
import { useEntity, useHass, useHassRef } from "../hass";
import { LANGUAGES, useI18n, useT } from "../i18n";
import { shiftClock, tzOffsetMinutes } from "../status";
import { useMower, useMowerEntity } from "../mower";
import { Button, Card, Field, Segmented, Select, Slider, TextInput, Toggle, useUi } from "../ui";

// Per-zone entities ({hashId}_cut_height / _enabled) are edited on the map instead.
const PER_ZONE = /^[A-Za-z0-9]{8}_(cut_height|enabled)$/;
// Covered by dedicated controls elsewhere in the panel.
const COVERED = new Set(["mow_pattern", "restore_backup_map"]);
// Buttons that discard work, move the dock or cut connectivity get a confirmation.
const DANGEROUS_BUTTONS = new Set([
  "restore_factory",
  "clear_all_zones_channels",
  "force_reinit",
  "abort_ota",
  "dock_and_forget_progress",
  "lock_robot",
  "toggle_lte_airplane",
  "cancel_task",
  "charging_station_reset",
  "set_charging_station_here",
]);

export function SettingsView() {
  return (
    <div className="ly-grid ly-grid--settings">
      <LanguageCard />
      <MowingDefaults />
      <LiveAdjust />
      <Headlight />
      <EntityControls />
      <ActionButtons />
      <Advanced />
    </div>
  );
}

function LanguageCard() {
  const { t, choice, setChoice, haLanguage } = useI18n();
  const haName = LANGUAGES.find((l) => l.code === haLanguage)?.name ?? "English";
  return (
    <Card title={t("Language")} icon="mdi:translate" className="ly-card--wide">
      <div className="ly-form ly-form--cols">
        <Field label={t("Panel language")} hint={t("Saved in this browser. Automatic follows your Home Assistant language.")}>
          <Select
            value={choice}
            onChange={setChoice}
            options={[{ value: "auto", label: t("Automatic ({language})", { language: haName }) }, ...LANGUAGES.map((l) => ({ value: l.code, label: l.name }))]}
          />
        </Field>
      </div>
    </Card>
  );
}

function MowingDefaults() {
  const t = useT();
  const { snap, call } = useMower();
  const ms = snap?.map.mowing_settings;
  const initial = useMemo(
    () => ({
      move_speed: ms?.moveSpeed ?? 0.6,
      path_spacing: ms?.pathSpacing ?? 30,
      perimeter_mow_laps: ms?.perimeterMowLaps ?? 1,
      nogo_mow_laps: ms?.noGoMowLaps ?? 1,
      perimeter_mow_dir: ms?.perimeterMowDir ?? 2,
      obs_dec_mode: ms?.obsDecMode ?? 2,
      clean_mode: ms?.cleanMode || 1,
      stripe_angle: ms?.stripeAngle ?? -1,
      safe_margin_mode: Boolean(ms?.safeMarginMode),
      turn_off_outer_motor: Boolean(ms?.turnOffOuterMotor),
      path_order: Boolean(ms?.pathOrder),
      cut_speed: ms?.cutSpeed ?? 4,
    }),
    [JSON.stringify(ms)],
  );
  // Only the fields the user touched: everything else keeps mirroring the mower, so
  // values arriving after an edit (or fallbacks shown before the first map reply)
  // are never sent back as if the user had chosen them.
  const [edits, setEdits] = useState<Partial<typeof initial>>({});
  const v = { ...initial, ...edits };
  // Drop edits the mower now reports (saved, or set the same elsewhere).
  useEffect(() => {
    const left = Object.fromEntries(Object.entries(edits).filter(([k, val]) => val !== (initial as any)[k]));
    if (Object.keys(left).length !== Object.keys(edits).length) setEdits(left);
  }, [initial]);
  const set = <K extends keyof typeof v>(k: K, val: (typeof v)[K]) => setEdits({ ...edits, [k]: val });
  const changed = Object.fromEntries(Object.entries(edits).filter(([k, val]) => val !== (initial as any)[k]));
  // Saving patches the cached settings right away, so the edits above vanish at once.
  // Keep what was sent until the mower's own map reply agrees; if a later reply
  // differs (rejected or normalised) or none ever matches, give the edits back.
  const ui = useUi();
  // `after`: the map reply time at save. Only a newer reply from the mower counts,
  // not the coordinator's optimistic patch.
  const [pending, setPending] = useState<{ sent: Record<string, unknown>; matched: boolean; after: number } | null>(null);
  const receivedAt = snap?.map_received_at ?? 0;
  const restore = (sent: Record<string, unknown>) => {
    setEdits((e) => ({ ...sent, ...e }));
    setPending(null);
    ui.toast(t("The mower didn't keep all of these settings. Check them and save again."), "bad");
  };
  useEffect(() => {
    if (!pending || receivedAt <= pending.after) return;
    const same = Object.entries(pending.sent).every(([k, val]) => (initial as any)[k] === val);
    if (same && !pending.matched) setPending({ ...pending, matched: true });
    else if (!same && pending.matched) restore(pending.sent);
  }, [initial, pending, receivedAt]);
  useEffect(() => {
    if (!pending) return;
    const id = window.setTimeout(() => (pending.matched ? setPending(null) : restore(pending.sent)), 30000);
    return () => window.clearTimeout(id);
  }, [pending?.sent, pending?.matched]);
  const save = async () => {
    if (await call("lymow", "set_task_config", changed, t("Mowing defaults saved"))) {
      setPending({ sent: changed, matched: false, after: receivedAt });
      call("lymow", "query_map"); // ask for the mower's own copy to confirm against
    }
  };

  return (
    <Card title={t("Mowing defaults")} icon="mdi:robot-mower-outline" className="ly-card--wide">
      <p className="ly-muted">{t("How the mower cuts every zone that doesn't have its own settings (zone settings live on the map).")}</p>
      <div className="ly-form ly-form--cols">
        <Field label={t("Pattern")}>
          <Select
            value={v.clean_mode}
            onChange={(x) => set("clean_mode", x)}
            options={[
              { value: 1, label: t("Zigzag") },
              { value: 2, label: t("Adaptive zigzag") },
              { value: 3, label: t("Chessboard") },
              { value: 4, label: t("Perimeter laps only") },
            ]}
          />
        </Field>
        <Field label={t("Mowing direction")} hint={t("Optimized lets the mower pick the stripe direction for each zone.")}>
          <Segmented
            value={v.stripe_angle < 0 ? "auto" : "fixed"}
            onChange={(x) => set("stripe_angle", x === "auto" ? -1 : 90)}
            options={[
              { value: "auto", label: t("Optimized") },
              { value: "fixed", label: t("Fixed angle") },
            ]}
          />
          {v.stripe_angle >= 0 && <Slider value={v.stripe_angle} min={0} max={179} step={1} unit="°" onChange={(x) => set("stripe_angle", x)} />}
        </Field>
        <Field label={t("Speed")}>
          <Slider value={v.move_speed} min={0.3} max={1} step={0.1} unit="m/s" format={(x) => x.toFixed(1)} onChange={(x) => set("move_speed", x)} />
        </Field>
        <Field label={t("Path spacing")} hint={t("Overlap between passes — smaller means a neater cut.")}>
          <Slider value={v.path_spacing} min={25} max={35} unit="cm" onChange={(x) => set("path_spacing", x)} />
        </Field>
        <Field label={t("Blade speed")}>
          <Slider value={v.cut_speed} min={0} max={10} onChange={(x) => set("cut_speed", x)} />
        </Field>
        <Field label={t("Edge laps")}>
          <Slider value={v.perimeter_mow_laps} min={0} max={3} onChange={(x) => set("perimeter_mow_laps", x)} />
        </Field>
        <Field label={t("Laps around no-go areas")}>
          <Slider value={v.nogo_mow_laps} min={0} max={3} onChange={(x) => set("nogo_mow_laps", x)} />
        </Field>
        <Field label={t("Edge direction")}>
          <Segmented
            value={v.perimeter_mow_dir}
            onChange={(x) => set("perimeter_mow_dir", x)}
            options={[
              { value: 0, label: t("Clockwise") },
              { value: 1, label: t("Counter") },
              { value: 2, label: t("Alternate") },
            ]}
          />
        </Field>
        <Field label={t("Obstacle handling")}>
          <Select
            value={v.obs_dec_mode}
            onChange={(x) => set("obs_dec_mode", x)}
            options={[
              { value: 0, label: t("Off") },
              { value: 1, label: t("Bump only") },
              { value: 2, label: t("Smart avoidance") },
              { value: 3, label: t("Smart, medium sensitivity") },
              { value: 4, label: t("Smart, low sensitivity") },
            ]}
          />
        </Field>
        <div className="ly-row">
          <span>{t("Keep a safety margin from edges")}</span>
          <Toggle checked={v.safe_margin_mode} onChange={(x) => set("safe_margin_mode", x)} label={t("Safety margin")} />
        </div>
        <div className="ly-row">
          <span>{t("Mow the edges before the main area")}</span>
          <Toggle checked={v.path_order} onChange={(x) => set("path_order", x)} label={t("Edges first")} />
        </div>
        <div className="ly-row">
          <span>{t("Turn off outer blade at edges")}</span>
          <Toggle checked={v.turn_off_outer_motor} onChange={(x) => set("turn_off_outer_motor", x)} label={t("Outer blade off")} />
        </div>
      </div>
      <div className="ly-btnrow">
        <Button variant="primary" icon="mdi:check" disabled={!Object.keys(changed).length || (pending !== null && !pending.matched)} onClick={save}>
          {t("Save changes")}
        </Button>
        <Button variant="ghost" icon="mdi:undo" disabled={!Object.keys(changed).length} onClick={() => setEdits({})}>
          {t("Discard")}
        </Button>
        <span className="ly-spacer" />
        <span className="ly-muted">{t("Cutting height")}</span>
        <Button icon="mdi:arrow-up-bold" title={t("Raise cutting height")} onClick={() => call("lymow", "set_task_config", { raise_cut_height: true }, t("Raising cutting height"))} />
        <Button icon="mdi:arrow-down-bold" title={t("Lower cutting height")} onClick={() => call("lymow", "set_task_config", { lower_cut_height: true }, t("Lowering cutting height"))} />
      </div>
    </Card>
  );
}

function LiveAdjust() {
  const t = useT();
  const { call, snap } = useMower();
  const mowing = useMowerEntity("mower")?.state === "mowing";
  const rtc = snap?.run_time_config;
  // Only send what the user moved: an untouched slider must not overwrite the
  // running task's value with a default.
  const [cut, setCut] = useState<number | null>(null);
  const [speed, setSpeed] = useState<number | null>(null);
  const changes = { ...(cut !== null && { cut_height: cut }), ...(speed !== null && { move_speed: speed }) };
  return (
    <Card title={t("Adjust the current mow")} icon="mdi:tune-vertical">
      <p className="ly-muted">{mowing ? t("Changes apply right away to the mow in progress.") : t("Only takes effect while the mower is mowing.")}</p>
      <Field label={t("Cutting height")}>
        <Slider value={cut ?? rtc?.cutHeight ?? 50} min={20} max={100} step={5} unit="mm" onChange={setCut} />
      </Field>
      <Field label={t("Speed")}>
        <Slider value={speed ?? rtc?.moveSpeed ?? 0.6} min={0.1} max={1.5} step={0.1} unit="m/s" format={(x) => x.toFixed(1)} onChange={setSpeed} />
      </Field>
      <Button
        variant="primary"
        icon="mdi:send"
        disabled={!mowing || !Object.keys(changes).length}
        onClick={async () => {
          if (await call("lymow", "set_run_time_config", changes, t("Sent to the mower"))) {
            setCut(null);
            setSpeed(null);
          }
        }}
      >
        {t("Apply now")}
      </Button>
    </Card>
  );
}

function Headlight() {
  const t = useT();
  const { call } = useMower();
  const mower = useMowerEntity("mower");
  const a = mower?.attributes ?? {};
  // The robot config arrives over MQTT after load (and some firmware never reports
  // the headlight window). Until the user edits, the form mirrors the mower, and
  // Save needs an explicit edit, so opening the page never writes defaults.
  const known = typeof a.headlight_enabled === "boolean";
  // The mower stores the window in UTC; show and edit it in Home Assistant's timezone.
  const offset = tzOffsetMinutes(useHassRef()().config.time_zone);
  const live = {
    on: Boolean(a.headlight_enabled),
    start: a.headlight_start ? shiftClock(a.headlight_start as string, offset) : "21:00",
    end: a.headlight_end ? shiftClock(a.headlight_end as string, offset) : "23:00",
  };
  const [draft, setDraft] = useState<typeof live | null>(null);
  // After Save the robot echoes its config later (or never, on some firmware):
  // keep showing what was sent, and drop the draft once the mower reports it.
  const [saved, setSaved] = useState(false);
  const v = draft ?? live;
  const edit = (part: Partial<typeof live>) => {
    setSaved(false);
    setDraft({ ...v, ...part });
  };
  const liveKey = JSON.stringify(live);
  useEffect(() => {
    if (draft && JSON.stringify(draft.on ? draft : { ...live, on: false }) === liveKey) {
      setDraft(null);
      setSaved(false);
    }
  }, [liveKey, saved]);
  // Firmware that never echoes: keep the draft but let the user save it again.
  useEffect(() => {
    if (!saved) return;
    const id = window.setTimeout(() => setSaved(false), 20000);
    return () => window.clearTimeout(id);
  }, [saved]);
  return (
    <Card title={t("Headlight")} icon="mdi:car-light-high">
      {!known && <p className="ly-muted">{t("The mower hasn't reported its headlight schedule. Saving here replaces whatever is set in the Lymow app.")}</p>}
      <div className="ly-row">
        <span>{t("Light on a schedule")}</span>
        <Toggle checked={v.on} onChange={(on) => edit({ on })} label={t("Headlight schedule")} />
      </div>
      {v.on && (
        <div className="ly-form ly-form--cols">
          <Field label={t("On at")}>
            <input type="time" className="ly-input ly-input--time" value={v.start} onChange={(e) => edit({ start: e.target.value })} />
          </Field>
          <Field label={t("Off at")}>
            <input type="time" className="ly-input ly-input--time" value={v.end} onChange={(e) => edit({ end: e.target.value })} />
          </Field>
        </div>
      )}
      <Button
        variant="primary"
        icon="mdi:check"
        disabled={!draft || saved}
        onClick={async () => {
          if (await call("lymow", "set_headlight_schedule", v.on ? { enable: true, start: shiftClock(v.start, -offset), end: shiftClock(v.end, -offset) } : { enable: false }, t("Headlight schedule saved"))) setSaved(true);
        }}
      >
        {t("Save")}
      </Button>
    </Card>
  );
}

function useEntitiesOf(domains: string[]): [string, string][] {
  const { device } = useMower();
  return Object.entries(device.entities)
    .filter(([k, id]) => domains.includes(id.split(".")[0]) && !PER_ZONE.test(k) && !COVERED.has(k))
    .sort(([, a], [, b]) => a.localeCompare(b));
}

function EntityControls() {
  const t = useT();
  const list = useEntitiesOf(["switch", "select", "number"]);
  const hass = useHass();
  const { device } = useMower();
  const live = list.filter(([, id]) => hass.states[id]);
  const sorted = [...live].sort(([, a], [, b]) => entityLabel(hass.states[a], device.name, t).localeCompare(entityLabel(hass.states[b], device.name, t)));
  return (
    <Card title={t("Mower features")} icon="mdi:toggle-switch-outline" className="ly-card--wide">
      <div className="ly-controls">
        {sorted.map(([k, id]) => (
          <EntityControl key={k} id={id} />
        ))}
      </div>
    </Card>
  );
}

function EntityControl({ id }: { id: string }) {
  const t = useT();
  const e = useEntity(id);
  const { device, call } = useMower();
  const [pending, setPending] = useState<number | null>(null);
  if (!e) return null;
  const domain = id.split(".")[0];
  const name = entityLabel(e, device.name, t);
  const off = e.state === "unavailable";
  if (domain === "switch")
    return (
      <div className="ly-control">
        <span>{name}</span>
        <Toggle disabled={off} checked={e.state === "on"} label={name} onChange={(v) => call("switch", v ? "turn_on" : "turn_off", { entity_id: id })} />
      </div>
    );
  if (domain === "select")
    return (
      <div className="ly-control ly-control--stack">
        <span>{name}</span>
        <Select
          disabled={off}
          value={e.state === "unknown" ? undefined : e.state}
          options={(e.attributes.options ?? []).map((o: string) => ({ value: o, label: ENTITY_TEXT.has(o) ? t(o) : o }))}
          onChange={(o) => call("select", "select_option", { entity_id: id, option: o }, `${name}: ${o}`)}
        />
      </div>
    );
  const a = e.attributes;
  const val = pending ?? Number(e.state);
  return (
    <div className="ly-control ly-control--stack">
      <span>{name}</span>
      <div className="ly-control__num">
        <Slider value={Number.isFinite(val) ? val : a.min} min={a.min} max={a.max} step={a.step} unit={a.unit_of_measurement} onChange={setPending} />
        {pending !== null && pending !== Number(e.state) && (
          <Button
            variant="primary"
            size="sm"
            icon="mdi:check"
            title={t("Apply")}
            onClick={async () => {
              // Keep the draft if the call failed, so the user doesn't have to redo it.
              if (await call("number", "set_value", { entity_id: id, value: pending }, t("{name} set to {value}", { name, value: pending }))) setPending(null);
            }}
          />
        )}
      </div>
    </div>
  );
}

function ActionButtons() {
  const t = useT();
  const list = useEntitiesOf(["button"]);
  const hass = useHass();
  const { device, call } = useMower();
  const ui = useUi();
  const live = list.filter(([, id]) => hass.states[id]);
  const safe = live.filter(([k]) => !DANGEROUS_BUTTONS.has(k));
  const danger = live.filter(([k]) => DANGEROUS_BUTTONS.has(k));
  const press = async (id: string, dangerous: boolean) => {
    const name = entityLabel(hass.states[id], device.name, t);
    if (dangerous && !(await ui.confirm({ title: `${name}?`, body: t("This can't be undone from Home Assistant."), confirm: name, danger: true }))) return;
    await call("button", "press", { entity_id: id }, t("{name} sent", { name }));
  };
  return (
    <Card title={t("Actions")} icon="mdi:gesture-tap-button" className="ly-card--wide">
      <div className="ly-actions">
        {safe.map(([k, id]) => (
          <Button key={k} icon="mdi:gesture-tap" onClick={() => press(id, false)}>
            {entityLabel(hass.states[id], device.name, t)}
          </Button>
        ))}
      </div>
      {danger.length > 0 && (
        <details className="ly-details">
          <summary>{t("Maintenance & reset")}</summary>
          <div className="ly-actions">
            {danger.map(([k, id]) => (
              <Button key={k} variant="danger" icon="mdi:alert-outline" onClick={() => press(id, true)}>
                {entityLabel(hass.states[id], device.name, t)}
              </Button>
            ))}
          </div>
        </details>
      )}
    </Card>
  );
}

function Advanced() {
  const t = useT();
  const { call, device, reloadDevices } = useMower();
  const ui = useUi();
  const [name, setName] = useState(device.name);
  useEffect(() => setName(device.name), [device.name]);
  const [pin, setPin] = useState("");
  const [showPin, setShowPin] = useState(false);
  const [base, setBase] = useState("");
  const [ssid, setSsid] = useState("");
  const [pw, setPw] = useState("");
  const [lat, setLat] = useState("");
  const [lon, setLon] = useState("");
  return (
    <Card title={t("Mower setup")} icon="mdi:cog-outline" className="ly-card--wide">
      <div className="ly-form ly-form--cols">
        <Field label={t("Mower name")}>
          <div className="ly-inline">
            <TextInput value={name} onChange={(e) => setName(e.target.value)} maxLength={32} />
            <Button disabled={!name.trim() || name === device.name} onClick={async () => (await call("lymow", "set_device_name", { name: name.trim() }, t("Name saved"))) && reloadDevices()}>
              {t("Rename")}
            </Button>
          </div>
        </Field>
        <Field label={t("Screen PIN")} hint={t("4 digits. The current PIN can't be read back.")}>
          <div className="ly-inline">
            <TextInput type={showPin ? "text" : "password"} inputMode="numeric" maxLength={4} value={pin} onChange={(e) => setPin(e.target.value.replace(/\D/g, ""))} />
            <Button variant="ghost" icon={showPin ? "mdi:eye-off-outline" : "mdi:eye-outline"} title={showPin ? t("Hide PIN") : t("Show PIN")} onClick={() => setShowPin(!showPin)} />
            <Button disabled={!/^\d{4}$/.test(pin)} onClick={async () => (await call("lymow", "set_pin", { pin }, t("PIN changed"))) && setPin("")}>
              {t("Set PIN")}
            </Button>
          </div>
        </Field>
        <Field label={t("RTK base station")} hint={t("The ID printed on the base, e.g. LK000000000000.")}>
          <div className="ly-inline">
            <TextInput value={base} placeholder={t("LK…")} onChange={(e) => setBase(e.target.value.trim())} />
            <Button disabled={!base} onClick={() => call("lymow", "bind_rtk", { base_id: base }, t("Base station bound"))}>
              {t("Bind")}
            </Button>
          </div>
        </Field>
        <Field label={t("Wi-Fi network")} hint={t("Sent over Bluetooth — Home Assistant must be within Bluetooth range of the mower.")}>
          <div className="ly-inline ly-inline--wrap">
            <TextInput value={ssid} placeholder={t("Network name")} onChange={(e) => setSsid(e.target.value)} />
            <TextInput type="password" value={pw} placeholder={t("Password")} onChange={(e) => setPw(e.target.value)} />
            <Button
              disabled={!ssid}
              onClick={async () => {
                if (await ui.confirm({ title: t("Connect the mower to “{ssid}”?", { ssid }), body: t("Its current Wi-Fi settings are replaced. If the details are wrong the mower can go offline."), confirm: t("Connect") }))
                  await call("lymow", "set_wifi", { ssid, password: pw }, t("Wi-Fi details sent"));
              }}
            >
              {t("Connect")}
            </Button>
          </div>
        </Field>
        <Field label={t("Anti-theft geofence centre")} hint={t("The radius is the “Geofence radius” control under Mower features.")}>
          <div className="ly-inline ly-inline--wrap">
            <TextInput type="number" step="0.000001" placeholder={t("Latitude")} value={lat} onChange={(e) => setLat(e.target.value)} />
            <TextInput type="number" step="0.000001" placeholder={t("Longitude")} value={lon} onChange={(e) => setLon(e.target.value)} />
            <Button disabled={!lat || !lon} onClick={() => call("lymow", "set_geofence", { latitude: Number(lat), longitude: Number(lon) }, t("Geofence saved"))}>
              {t("Save")}
            </Button>
          </div>
        </Field>
      </div>
    </Card>
  );
}
