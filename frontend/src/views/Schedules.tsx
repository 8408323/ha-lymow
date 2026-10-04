import { useState } from "react";
import { useI18n, useT, type T } from "../i18n";
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
  const { locale } = useI18n();
  const { snap, call, zoneName } = useMower();
  const ui = useUi();
  const [adding, setAdding] = useState(false);
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
                variant="ghost"
                icon="mdi:delete-sweep-outline"
                onClick={async () => {
                  if (await ui.confirm({ title: t("Delete all schedules?"), body: t("The mower stops mowing automatically until you add a new schedule."), confirm: t("Delete all"), danger: true }))
                    await call("lymow", "clear_schedules", {}, t("All schedules deleted"));
                }}
              >
                {t("Clear all")}
              </Button>
            )}
            <Button variant="primary" icon="mdi:plus" onClick={() => setAdding(true)}>
              {t("Add")}
            </Button>
          </>
        }
      >
        {adding && <AddSchedule onDone={() => setAdding(false)} />}
        {!schedules.length && !adding ? (
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
                  <span className="ly-muted">{s.zones?.length ? s.zones.map(zoneName).join(", ") : "All zones"}</span>
                </div>
                <Toggle
                  label={t("Schedule active")}
                  checked={!s.isDisabled}
                  onChange={(on) => call("lymow", "toggle_schedule", { id: s.id, disabled: !on }, on ? t("Schedule turned on") : t("Schedule paused"))}
                />
                <Button
                  variant="ghost"
                  icon="mdi:delete-outline"
                  title={t("Delete schedule")}
                  onClick={async () => {
                    if (await ui.confirm({ title: `Delete the ${pad2(s.hour)}:${pad2(s.minute)} schedule?`, confirm: t("Delete"), danger: true }))
                      await call("lymow", "delete_schedule", { id: s.id }, t("Schedule deleted"));
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

function AddSchedule({ onDone }: { onDone: () => void }) {
  const t = useT();
  const { locale } = useI18n();
  const { snap, call } = useMower();
  const zones = snap?.map.go_zones ?? [];
  const [days, setDays] = useState<number[]>([1, 3, 5]);
  const [time, setTime] = useState("09:00");
  const [picked, setPicked] = useState<string[]>([]);
  const [repeat, setRepeat] = useState(true);
  const toggleDay = (d: number) => setDays(days.includes(d) ? days.filter((x) => x !== d) : [...days, d]);
  const toggleZone = (id: string) => setPicked(picked.includes(id) ? picked.filter((x) => x !== id) : [...picked, id]);
  const allZones = picked.length === 0;
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
      <Field label={t("Zones")} hint={zones.length ? undefined : "No zones found on the map yet."}>
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
      <div className="ly-btnrow">
        <Button
          variant="primary"
          icon="mdi:check"
          disabled={!days.length || !time || !zones.length}
          onClick={async () => {
            const [hour, minute] = time.split(":").map(Number);
            const ok = await call(
              "lymow",
              "add_schedule",
              { hour, minute, day_of_week: days, repeated: repeat, disabled: false, zones: allZones ? zones.map((z) => z.hashId) : picked },
              t("Schedule added"),
            );
            if (ok) onDone();
          }}
        >
          {t("Save schedule")}
        </Button>
        <Button variant="ghost" onClick={onDone}>
          {t("Cancel")}
        </Button>
      </div>
    </div>
  );
}
