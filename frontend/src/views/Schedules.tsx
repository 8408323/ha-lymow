import { useEffect, useRef, useState } from "react";
import { useI18n, useT, type T } from "../i18n";
import type { Schedule } from "../hass";
import { useMower, zoneLabel } from "../mower";
import { pad2, weekday } from "../status";
import { Button, Card, Chip, Empty, Field, Toggle, useUi } from "../ui";

const WEEK_ORDER = [1, 2, 3, 4, 5, 6, 0]; // Monday first

function daysText(days: number[] | undefined, t: T, locale: string): string {
  if (!days?.length || days.length === 7) return t("Every day");
  const set = new Set(days);
  if (set.size === 5 && [1, 2, 3, 4, 5].every((d) => set.has(d))) return t("Weekdays");
  if (set.size === 2 && set.has(0) && set.has(6)) return t("Weekends");
  return WEEK_ORDER.filter((d) => set.has(d))
    .map((d) => weekday(d, locale))
    .join(", ");
}

export function SchedulesView() {
  const t = useT();
  const { snap } = useMower();
  // Hidden by permissions (no read access to the schedules sensor): say so instead
  // of waiting on a list that can never become visible.
  if (snap?.schedules === "hidden")
    return (
      <Empty icon="mdi:lock-outline" title={t("Schedules not available")}>
        {t("Your Home Assistant user doesn't have access to this mower's schedules.")}
      </Empty>
    );
  return <ScheduleList />;
}

function ScheduleList() {
  const t = useT();
  const { locale } = useI18n();
  const { snap: raw, call, zoneName, device } = useMower();
  // "hidden" is handled by SchedulesView above.
  const snap = raw && raw.schedules === "hidden" ? { ...raw, schedules: null } : (raw as (typeof raw & { schedules: Schedule[] | null }) | undefined);
  const ui = useUi();
  // A submitted draft (`before` set) reopens closed; it's only waiting for confirmation.
  const [adding, setAdding] = useState(() => {
    const d = scheduleDrafts.get(device.thing);
    return !!d && !d.before;
  });
  // A kept draft whose schedule the mower now reports is done.
  useEffect(() => {
    const d = scheduleDrafts.get(device.thing);
    if (!d || (adding && !d.before) || !Array.isArray(snap?.schedules)) return;
    const [h, m] = d.time.split(":").map(Number);
    const same = (a: unknown[] = [], b: unknown[] = []) => JSON.stringify([...a].sort()) === JSON.stringify([...b].sort());
    // A matching row must agree on everything submitted, and be new (not in the list before).
    const match = (s: Schedule) =>
      s.hour === h && s.minute === m && same(s.dayOfWeek, d.days) && (s.isRepeated ?? true) === d.repeat && !s.isDisabled && (d.picked.length === 0 || same(s.zones, d.picked));
    if (snap.schedules.some((s) => match(s) && !(d.before ?? []).includes(s.id)))
      scheduleDrafts.delete(device.thing);
  }, [snap?.schedules, adding]);
  const snapRef = useRef(snap);
  snapRef.current = snap;
  // Editing before the mower has reported its schedules would overwrite them
  // (add_schedule writes the whole list), so everything waits for the reply.
  const loading = !snap || snap.schedules === null;
  // Opened with the list still unknown (e.g. a lost reply while on another tab): ask again.
  // Also when the first snapshot arrives after mount; once per unknown phase.
  // One schedule change at a time: each service call rewrites the mower's whole
  // list from the cache, so overlapping edits would undo each other. Controls stay
  // locked until the call returns and the mower has re-reported its schedules.
  const [busy, setBusy] = useState(false);
  // After a successful call, stay locked until the mower reports a list newer than
  // the one the call was based on (the null "querying" phase may be throttled away).
  const [awaitingAfter, setAwaitingAfter] = useState<unknown>(undefined);
  // Also runs when the wait is armed: the reply can arrive before the service call returns.
  useEffect(() => {
    // By content: every snapshot carries a fresh array, even when the schedules didn't change.
    if (awaitingAfter !== undefined && snap?.schedules && JSON.stringify(snap.schedules) !== JSON.stringify(awaitingAfter)) setAwaitingAfter(undefined);
  }, [snap?.schedules, awaitingAfter]);
  useEffect(() => {
    if (awaitingAfter === undefined) return;
    // Never lock forever: if the confirming reply was lost, ask the mower again.
    const t = window.setTimeout(() => {
      setAwaitingAfter(undefined);
      if (snapRef.current?.schedules === null) call("lymow", "query_schedules"); // latest, not the armed render's
    }, 30000);
    return () => window.clearTimeout(t);
  }, [awaitingAfter]);
  // Offline: the write would be queued at the broker and the confirming query never answered.
  // Keeps retrying with backoff (10 s → 60 s) until a list arrives or the mower goes offline.
  // Only users who may control the mower can ask it (the call would just be refused).
  const unknown = !!snap && snap.schedules === null && snap.online && device.can_control !== false && !device.held;
  useEffect(() => {
    if (!unknown || awaitingAfter !== undefined) return;
    let delay = 10000;
    let id = 0;
    const ask = () => {
      call("lymow", "query_schedules");
      id = window.setTimeout(ask, delay);
      delay = Math.min(60000, delay * 2);
    };
    ask();
    return () => window.clearTimeout(id);
  }, [unknown, awaitingAfter !== undefined]);
  const locked = busy || loading || awaitingAfter !== undefined || snap?.online !== true;
  const mutate = async (fn: () => Promise<boolean>) => {
    const before = snap?.schedules;
    setBusy(true);
    try {
      if (await fn()) setAwaitingAfter(before);
    } finally {
      setBusy(false);
    }
  };
  const schedules = [...(snap?.schedules ?? [])].sort((a, b) => a.hour * 60 + a.minute - (b.hour * 60 + b.minute));

  return (
    <div className="ly-grid ly-grid--narrow">
      <Card
        title={t("Mowing schedules")}
        icon="mdi:calendar-clock"
        actions={
          <>
            {schedules.length > 0 && (
              <Button
                disabled={locked}
                variant="ghost"
                icon="mdi:delete-sweep-outline"
                onClick={async () => {
                  if (await ui.confirm({ title: t("Delete all schedules?"), body: t("The mower stops mowing automatically until you add a new schedule."), confirm: t("Delete all"), danger: true }))
                    await mutate(() => call("lymow", "clear_schedules", {}, t("All schedules deleted")));
                }}
              >
                {t("Clear all")}
              </Button>
            )}
            <Button variant="primary" icon="mdi:plus" disabled={locked} onClick={() => setAdding(true)}>
              {t("Add")}
            </Button>
          </>
        }
      >
        {/* Kept while a refresh is loading, so a half-filled form isn't lost. */}
        {adding && <AddSchedule onDone={() => setAdding(false)} mutate={mutate} locked={locked} />}
        {loading ? (
          <div className="ly-loading">
            <span className="ly-spinner" /> {t("Loading schedules from the mower…")}
          </div>
        ) : !schedules.length && !adding ? (
          <Empty icon="mdi:calendar-blank-outline" title={t("No schedules yet")}>
            {t("Add a schedule and the mower starts on its own at the chosen times.")}
          </Empty>
        ) : (
          <ul className="ly-list">
            {schedules.map((s) => (
              <li key={s.id} className={s.isDisabled ? "ly-list__item ly-list__item--off" : "ly-list__item"}>
                <div className="ly-sched__time">
                  {pad2(s.hour)}:{pad2(s.minute)}
                </div>
                <div className="ly-sched__info">
                  <strong>
                    {daysText(s.dayOfWeek, t, locale)}
                    {s.isRepeated === false && <span className="ly-muted"> · {t("once")}</span>}
                  </strong>
                  <span className="ly-muted">{s.zones?.length ? s.zones.map(zoneName).join(", ") : t("All zones")}</span>
                </div>
                <Toggle
                  label={t("Schedule active")}
                  checked={!s.isDisabled}
                  disabled={locked}
                  onChange={(on) => mutate(() => call("lymow", "toggle_schedule", { id: s.id, disabled: !on }, on ? t("Schedule turned on") : t("Schedule paused")))}
                />
                <Button
                  variant="ghost"
                  icon="mdi:delete-outline"
                  title={t("Delete schedule")}
                  disabled={locked}
                  onClick={async () => {
                    if (await ui.confirm({ title: t("Delete the {time} schedule?", { time: `${pad2(s.hour)}:${pad2(s.minute)}` }), confirm: t("Delete"), danger: true }))
                      await mutate(() => call("lymow", "delete_schedule", { id: s.id }, t("Schedule deleted")));
                  }}
                />
              </li>
            ))}
          </ul>
        )}
        <p className="ly-muted ly-note">{t("Times are in your local time. The mower may take a minute to confirm changes.")}</p>
      </Card>
    </div>
  );
}

// A half-filled add form outlives the view per mower (tab switch, HA sidebar, other mower).
type ScheduleDraft = { days: number[]; time: string; picked: string[]; repeat: boolean; before?: number[] };
const scheduleDrafts = new Map<string, ScheduleDraft>();

function AddSchedule({ onDone, mutate, locked }: { onDone: () => void; mutate: (fn: () => Promise<boolean>) => Promise<void>; locked: boolean }) {
  const t = useT();
  const { locale } = useI18n();
  const { snap, call, device } = useMower();
  const zones = snap?.map.go_zones ?? [];
  const kept = scheduleDrafts.get(device.thing);
  const [days, setDays] = useState<number[]>(kept?.days ?? [1, 3, 5]);
  const [time, setTime] = useState(kept?.time ?? "09:00");
  const [picked, setPicked] = useState<string[]>(kept?.picked ?? []);
  const [repeat, setRepeat] = useState(kept?.repeat ?? true);
  useEffect(() => {
    scheduleDrafts.set(device.thing, { days, time, picked, repeat, before: scheduleDrafts.get(device.thing)?.before });
  }, [days, time, picked, repeat]);
  const done = () => {
    scheduleDrafts.delete(device.thing);
    onDone();
  };
  const toggleDay = (d: number) => setDays(days.includes(d) ? days.filter((x) => x !== d) : [...days, d]);
  const toggleZone = (id: string) => setPicked(picked.includes(id) ? picked.filter((x) => x !== id) : [...picked, id]);
  // Zones removed from the map since they were picked are dropped, not sent.
  const live = picked.filter((id) => zones.some((z) => z.hashId === id));
  // "All zones" is an explicit choice (nothing picked). Picked zones that all vanished
  // must not silently widen the schedule to the whole lawn.
  const allZones = picked.length === 0;
  const lostZones = !allZones && live.length === 0;
  return (
    <div className="ly-form">
      <Field label={t("Days")}>
        <div className="ly-chips">
          {WEEK_ORDER.map((d) => (
            <Chip key={d} on={days.includes(d)} onClick={() => toggleDay(d)}>
              {weekday(d, locale)}
            </Chip>
          ))}
        </div>
      </Field>
      <Field label={t("Start time")}>
        <input type="time" className="ly-input ly-input--time" value={time} onChange={(e) => setTime(e.target.value)} />
      </Field>
      <Field label={t("Zones")} hint={zones.length ? undefined : t("No zones found on the map yet.")}>
        <div className="ly-chips">
          <Chip on={allZones} onClick={() => setPicked([])} icon="mdi:select-all">
            {t("All zones")}
          </Chip>
          {zones.map((z, i) => (
            <Chip key={z.hashId} on={picked.includes(z.hashId)} onClick={() => toggleZone(z.hashId)}>
              {zoneLabel(z, i, t)}
            </Chip>
          ))}
        </div>
      </Field>
      <div className="ly-row">
        <span>{t("Repeat every week")}</span>
        <Toggle checked={repeat} onChange={setRepeat} label={t("Repeat every week")} />
      </div>
      {lostZones && <p className="ly-muted">{t("The zones you picked are no longer on the map. Pick the zones again.")}</p>}
      <div className="ly-btnrow">
        <Button
          variant="primary"
          icon="mdi:check"
          disabled={locked || !days.length || !time || !zones.length || lostZones}
          onClick={async () => {
            const [hour, minute] = time.split(":").map(Number);
            let ok = false;
            await mutate(async () => {
              ok = await call(
              "lymow",
              "add_schedule",
              { hour, minute, day_of_week: days, repeated: repeat, disabled: false, zones: allZones ? zones.map((z) => z.hashId) : live },
              t("Schedule added"),
              );
              return ok;
            });
            // Close the form, but keep the draft until the mower's list shows the new
            // schedule; if it never does, the next Add starts from these values.
            if (ok) {
              // Rows already present can't be the confirmation of this new one.
              const d = scheduleDrafts.get(device.thing);
              if (d) scheduleDrafts.set(device.thing, { ...d, before: (Array.isArray(snap?.schedules) ? snap.schedules : []).map((x) => x.id) });
              onDone();
            }
          }}
        >
          {t("Save schedule")}
        </Button>
        <Button variant="ghost" onClick={done}>
          {t("Cancel")}
        </Button>
      </div>
    </div>
  );
}
