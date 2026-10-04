import { useEffect, useState } from "react";
import { fireHassEvent, useDevices, useEntity, useSnapshot, type LymowDevice } from "./hass";
import type { Route } from "./main";
import { I18nProvider, useT } from "./i18n";
import { MowerProvider, useMower } from "./mower";
import { mowerState, num } from "./status";
import { Badge, Button, Empty, Icon, UiProvider, cx } from "./ui";
import { BackupsView } from "./views/Backups";
import { CameraView } from "./views/Camera";
import { DiagnosticsView } from "./views/Diagnostics";
import { MapView } from "./views/Map";
import { OverviewView } from "./views/Overview";
import { SchedulesView } from "./views/Schedules";
import { SettingsView } from "./views/Settings";

const TABS = [
  { id: "overview", label: "Overview", icon: "mdi:view-dashboard-outline" },
  { id: "map", label: "Map", icon: "mdi:map-outline" },
  { id: "schedules", label: "Schedules", icon: "mdi:calendar-clock" },
  { id: "camera", label: "Camera & drive", icon: "mdi:cctv" },
  { id: "backups", label: "Map backups", icon: "mdi:cloud-sync-outline" },
  { id: "settings", label: "Settings", icon: "mdi:tune-variant" },
  { id: "diagnostics", label: "Diagnostics", icon: "mdi:stethoscope" },
] as const;

export type TabId = (typeof TABS)[number]["id"];

export function navigate(route: Route, tab: TabId) {
  history.pushState(null, "", `${route.prefix}/${tab}`);
  window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }));
}

export function App(props: { narrow: boolean; route: Route; host: HTMLElement }) {
  return (
    <I18nProvider>
      <UiProvider>
        <Shell {...props} />
      </UiProvider>
    </I18nProvider>
  );
}

function Shell({ narrow, route, host }: { narrow: boolean; route: Route; host: HTMLElement }) {
  const t = useT();
  const [devices, reloadDevices] = useDevices();
  const [thing, setThing] = useState<string>();
  const device = devices?.find((d) => d.thing === thing) ?? devices?.[0];
  const snap = useSnapshot(device?.thing);
  useEffect(() => {
    if (snap?.gone) reloadDevices();
  }, [snap?.gone]);
  const seg = route.path.split("/").filter(Boolean)[0];
  const tab: TabId = (TABS.find((t) => t.id === seg)?.id ?? "overview") as TabId;

  useEffect(() => {
    host.classList.toggle("narrow", narrow);
  }, [narrow]);

  return (
    <div className={cx("ly-app", narrow && "ly-app--narrow")}>
        {device ? (
          <MowerProvider key={device.thing} device={device} snap={snap?.gone ? undefined : snap} reloadDevices={reloadDevices}>
            <TopBar narrow={narrow} host={host} devices={devices!} onPick={setThing} />
            <nav className="ly-tabs" aria-label={t("Sections")}>
              {TABS.map((item) => (
                <a
                  key={item.id}
                  href={`${route.prefix}/${item.id}`}
                  className={cx("ly-tab", item.id === tab && "ly-tab--on")}
                  aria-current={item.id === tab ? "page" : undefined}
                  onClick={(e) => {
                    e.preventDefault();
                    navigate(route, item.id);
                  }}
                >
                  <Icon name={item.icon} size={18} />
                  <span>{t(item.label)}</span>
                </a>
              ))}
            </nav>
            <main className={cx("ly-main", tab === "map" && "ly-main--full")}>
              {tab === "overview" && <OverviewView go={(to) => navigate(route, to)} />}
              {tab === "map" && <MapView />}
              {tab === "schedules" && <SchedulesView />}
              {tab === "camera" && <CameraView />}
              {tab === "backups" && <BackupsView />}
              {tab === "settings" && <SettingsView />}
              {tab === "diagnostics" && <DiagnosticsView host={host} />}
            </main>
          </MowerProvider>
        ) : (
          <>
            <header className="ly-topbar">
              {narrow && <Button variant="ghost" icon="mdi:menu" title={t("Menu")} onClick={() => fireHassEvent(host, "hass-toggle-menu")} />}
              <div className="ly-brand">
                <Icon name="mdi:robot-mower" /> Lymow
              </div>
            </header>
            <main className="ly-main">
              {devices === undefined ? (
                <div className="ly-loading">
                  <span className="ly-spinner" /> {t("Connecting to your mower…")}
                </div>
              ) : (
                <Empty icon="mdi:robot-mower-outline" title={t("No Lymow mower found")}>
                  {t("Once the Lymow integration has finished setting up, your mower appears here.")}
                </Empty>
              )}
            </main>
          </>
        )}
      </div>
  );
}

function TopBar({ narrow, host, devices, onPick }: { narrow: boolean; host: HTMLElement; devices: LymowDevice[]; onPick: (thing: string) => void }) {
  const { device, ent, call, snap } = useMower();
  const t = useT();
  const mower = useEntity(ent("mower"));
  const battery = num(useEntity(ent("battery"))?.state);
  const charging = useEntity(ent("is_charging"))?.state === "on";
  const st = mowerState(mower?.state);
  return (
    <header className="ly-topbar">
      {narrow && <Button variant="ghost" icon="mdi:menu" title={t("Menu")} onClick={() => fireHassEvent(host, "hass-toggle-menu")} />}
      <div className="ly-brand">
        <Icon name="mdi:robot-mower" />
        {devices.length > 1 ? (
          <select className="ly-brand__pick" value={device.thing} onChange={(e) => onPick(e.target.value)} aria-label={t("Mower")}>
            {devices.map((d) => (
              <option key={d.thing} value={d.thing}>
                {d.name}
              </option>
            ))}
          </select>
        ) : (
          <span>{device.name}</span>
        )}
      </div>
      <div className="ly-topbar__status">
        <Badge tone={st.tone} icon={st.icon}>
          {t(st.label)}
        </Badge>
        {battery !== undefined && (
          <Badge tone={battery < 20 ? "bad" : "neutral"} icon={charging ? "mdi:battery-charging" : batteryIcon(battery)}>
            {Math.round(battery)}%
          </Badge>
        )}
        {snap && !snap.online && (
          <Badge tone="bad" icon="mdi:cloud-off-outline">
            {t("Offline")}
          </Badge>
        )}
      </div>
      <Button
        variant="ghost"
        icon="mdi:refresh"
        title={t("Refresh map and schedules from the mower")}
        onClick={async () => {
          await call("lymow", "query_map");
          await call("lymow", "query_schedules", {}, t("Asked the mower for fresh data"));
        }}
      />
    </header>
  );
}

export function batteryIcon(pct: number): string {
  const step = Math.max(1, Math.min(10, Math.round(pct / 10))) * 10;
  return step === 100 ? "mdi:battery" : `mdi:battery-${step}`;
}
