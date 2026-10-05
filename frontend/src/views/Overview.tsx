import { useEffect, useRef, useState } from "react";
import type { TabId } from "../App";
import { fireHassEvent, useEntity, useHassRef, type Schedule } from "../hass";
import { MapCanvas } from "../map/MapCanvas";
import { useI18n, useT } from "../i18n";
import { useMower, useMowerEntity } from "../mower";
import { RTK, formatState, mowerState, num, pad2, tzOffsetMinutes, weekday } from "../status";
import { Badge, Button, Card, Icon, cx } from "../ui";

export function OverviewView({ go }: { go: (t: TabId) => void }) {
  const t = useT();
  const { snap, ent } = useMower();
  const ref = useRef<HTMLDivElement>(null);
  const moreInfo = (key: string) => {
    const id = ent(key);
    if (id) fireHassEvent(ref.current, "hass-more-info", { entityId: id });
  };
  return (
    <div className="ly-grid ly-grid--overview" ref={ref}>
      <Hero />
      <div className="ly-tiles">
        <Tile k="battery" icon="mdi:battery" label={t("Battery")} moreInfo={moreInfo} />
        <Tile k="mow_progress" icon="mdi:progress-check" label={t("Progress")} moreInfo={moreInfo} />
        <Tile k="remaining_area" icon="mdi:grass" label={t("Area left")} moreInfo={moreInfo} />
        <Tile k="last_clean_at" icon="mdi:calendar-check" label={t("Last mow")} moreInfo={moreInfo} />
        <Tile k="last_clean_area" icon="mdi:texture-box" label={t("Last mow area")} moreInfo={moreInfo} />
        <RtkTile moreInfo={moreInfo} rtk={snap?.map.rtkStatus} />
        <Tile k="connectivity" icon="mdi:access-point-network" label={t("Connection")} moreInfo={moreInfo} />
        <Tile k="remain_clean_time" icon="mdi:timer-sand" label={t("Time left")} moreInfo={moreInfo} />
      </div>
      <Card
        title={t("Lawn")}
        icon="mdi:map-outline"
        className="ly-card--map"
        actions={
          <Button variant="ghost" icon="mdi:arrow-expand" onClick={() => go("map")}>
            {t("Open map")}
          </Button>
        }
      >
        {snap?.map && (snap.map.go_zones?.length || snap.map.charging_station) ? (
          <div className="ly-minimap" onClick={() => go("map")} role="button" tabIndex={0} onKeyDown={(e) => e.key === "Enter" && go("map")}>
            <MapCanvas map={snap.map} interactive={false} labels="name" />
          </div>
        ) : (
          <p className="ly-muted">{t("The map appears once the mower has sent it.")}</p>
        )}
      </Card>
      {snap?.schedules !== "hidden" && <NextSchedule schedules={snap?.schedules} go={go} />}
    </div>
  );
}

function Hero() {
  const t = useT();
  const { call } = useMower();
  const mower = useMowerEntity("mower");
  const progress = num(useMowerEntity("mow_progress")?.state);
  const error = num(useMowerEntity("error_code")?.state);
  const lifted = useMowerEntity("robot_lifted")?.state === "on";
  const charging = useMowerEntity("is_charging")?.state === "on";
  const state = mower?.state ?? "unknown";
  const st = mowerState(state);
  const errorText = mower?.attributes.error_description ?? mower?.attributes.error;
  const { snap, device } = useMower();
  const off = state === "unavailable" || snap?.online !== true || device.can_control === false || device.held === true;

  const start = () => call("lawn_mower", "start_mowing", {}, t("Mowing started"));
  const pause = () => call("lawn_mower", "pause", {}, t("Paused"));
  const resume = () => call("lymow", "resume", {}, t("Resumed"));
  const dock = () => call("lawn_mower", "dock", {}, t("Returning to the dock"));

  let sub = "";
  if (error) sub = errorText ? t("Error {code}: {text}", { code: error, text: errorText }) : t("Error code {code}", { code: error });
  else if (lifted) sub = t("The mower is lifted or tilted");
  else if (state === "mowing" && progress !== undefined) sub = t("{pct}% of the task done", { pct: Math.round(progress) });
  else if (state === "docked") sub = (charging ? t("Charging in the dock") : t("Resting in the dock"));
  else if (state === "paused") sub = t("Waiting — resume or send it home");
  else if (off) sub = t("The mower isn't reachable right now");

  return (
    <section className={cx("ly-hero", `ly-hero--${st.tone}`)}>
      <div className="ly-hero__icon">
        <Icon name={st.icon} size={40} />
      </div>
      <div className="ly-hero__text">
        <h1>{t(st.label)}</h1>
        {sub && <p>{sub}</p>}
        {state === "mowing" && progress !== undefined && (
          <div className="ly-progress" role="progressbar" aria-valuenow={progress} aria-valuemin={0} aria-valuemax={100}>
            <span style={{ width: `${Math.min(100, Math.max(0, progress))}%` }} />
          </div>
        )}
      </div>
      <div className="ly-hero__actions">
        {(state === "docked" || state === "unknown" || state === "error") && (
          <Button variant="primary" size="lg" icon="mdi:play" onClick={start} disabled={off}>
            {t("Start mowing")}
          </Button>
        )}
        {(state === "mowing" || state === "returning") && (
          <Button variant="secondary" size="lg" icon="mdi:pause" onClick={pause} disabled={off}>
            {t("Pause")}
          </Button>
        )}
        {state === "paused" && (
          <Button variant="primary" size="lg" icon="mdi:play" onClick={resume} disabled={off}>
            {t("Resume")}
          </Button>
        )}
        {state !== "docked" && state !== "returning" && !off && (
          <Button variant="secondary" size="lg" icon="mdi:home-import-outline" onClick={dock} disabled={off}>
            {t("Dock")}
          </Button>
        )}
      </div>
    </section>
  );
}

function Tile({ k, icon, label, moreInfo }: { k: string; icon: string; label: string; moreInfo: (k: string) => void }) {
  const e = useEntity(useMower().ent(k));
  const { t, locale } = useI18n();
  if (!e) return null;
  return (
    <button type="button" className="ly-tile" onClick={() => moreInfo(k)}>
      <Icon name={icon} />
      <span className="ly-tile__label">{t(label)}</span>
      <span className="ly-tile__value" title={formatState(e, t, locale)}>
        {formatState(e, t, locale)}
      </span>
    </button>
  );
}

function RtkTile({ rtk, moreInfo }: { rtk: number | undefined; moreInfo: (k: string) => void }) {
  const t = useT();
  if (rtk === undefined) return null;
  const r = RTK[rtk];
  const label = r ? t(r.label) : t("Status {n}", { n: rtk });
  return (
    <button type="button" className="ly-tile" onClick={() => moreInfo("rtk_status")}>
      <Icon name="mdi:satellite-variant" />
      <span className="ly-tile__label">{t("Positioning")}</span>
      <span className="ly-tile__value">
        <Badge tone={r?.tone ?? "neutral"}>{label}</Badge>
      </span>
    </button>
  );
}

/** Current wall-clock time in `timeZone`, as a Date whose local fields carry it. */
function wallClock(timeZone: string): Date {
  const n = new Date();
  return new Date(n.getTime() + (tzOffsetMinutes(timeZone, n) + n.getTimezoneOffset()) * 60000);
}

function nextRun(s: Schedule, now: Date): Date | null {
  // One-time schedules carry no date or run record, so a consumed one can't be told
  // from a pending one; don't predict them (the Schedules tab still lists them).
  if (s.isDisabled || s.isRepeated === false) return null;
  const days = s.dayOfWeek?.length ? s.dayOfWeek : [0, 1, 2, 3, 4, 5, 6];
  // A one-time schedule only has its occurrence within the coming week; once that
  // has passed it must not reappear as next week's mow.
  for (let add = 0; add < 8; add++) {
    const d = new Date(now);
    d.setDate(now.getDate() + add);
    d.setHours(s.hour, s.minute, 0, 0);
    if (days.includes(d.getDay()) && d > now) return d;
  }
  return null;
}

function NextSchedule({ schedules, go }: { schedules: Schedule[] | null | undefined; go: (t: TabId) => void }) {
  const { t, locale } = useI18n();
  const { zoneName } = useMower();
  // Schedule hours are the mower's local time; compare against "now" in Home
  // Assistant's timezone, not the browser's.
  const tz = useHassRef()().config.time_zone;
  // Re-render every minute so a passed slot moves on even without new data.
  const [, setTick] = useState(0);
  useEffect(() => {
    const id = window.setInterval(() => setTick((n) => n + 1), 60000);
    return () => window.clearInterval(id);
  }, []);
  const now = wallClock(tz);
  const upcoming = (schedules ?? [])
    .map((s) => ({ s, at: nextRun(s, now) }))
    .filter((x): x is { s: Schedule; at: Date } => x.at !== null)
    .sort((a, b) => +a.at - +b.at)[0];
  return (
    <Card
      title={t("Next mow")}
      icon="mdi:calendar-clock"
      actions={
        <Button variant="ghost" icon="mdi:pencil-outline" onClick={() => go("schedules")}>
          {t("Schedules")}
        </Button>
      }
    >
      {!schedules ? (
        <p className="ly-muted">{t("Loading schedules from the mower…")}</p>
      ) : upcoming ? (
        <div className="ly-next">
          <strong>
            {weekday(upcoming.at.getDay(), locale)} {pad2(upcoming.s.hour)}:{pad2(upcoming.s.minute)}
          </strong>
          <span className="ly-muted">{upcoming.s.zones?.length ? upcoming.s.zones.map(zoneName).join(", ") : t("All zones")}</span>
        </div>
      ) : (
        <p className="ly-muted">{t("No active schedule. Add one to mow automatically.")}</p>
      )}
    </Card>
  );
}
