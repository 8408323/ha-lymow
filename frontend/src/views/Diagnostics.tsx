import { useState } from "react";
import { fireHassEvent, useHass } from "../hass";
import { useI18n } from "../i18n";
import { useMower } from "../mower";
import { formatState } from "../status";
import { Card, Icon, TextInput } from "../ui";

const GROUPS: [string, RegExp][] = [
  ["Positioning", /rtk|gnss|pose|location|lora|satellite/],
  ["Connectivity", /wifi|wi_fi|lte|cellular|ip_address|mac|bt_|bluetooth|connectivity|ssid|sim/],
  ["Mowing history", /last_|clean|mow|mission|area|session/],
  ["Device", /.*/],
];

export function DiagnosticsView({ host }: { host: HTMLElement }) {
  const { t, locale } = useI18n();
  const hass = useHass();
  const { device } = useMower();
  const [q, setQ] = useState("");
  const rows = Object.entries(device.entities)
    .filter(([k, id]) => /^(sensor|binary_sensor|device_tracker)\./.test(id) && k !== "map" && hass.states[id])
    .map(([k, id]) => {
      const e = hass.states[id];
      const full: string = e.attributes.friendly_name ?? id;
      const name = full.startsWith(`${device.name} `) ? full.slice(device.name.length + 1) : full;
      return { k, id, name, value: formatState(e, t, locale) };
    })
    .filter((r) => !q || `${r.name} ${r.value}`.toLowerCase().includes(q.toLowerCase()))
    .sort((a, b) => a.name.localeCompare(b.name));

  const grouped = GROUPS.map(([title, re]) => [title, [] as typeof rows, re] as const);
  for (const r of rows) grouped.find(([, , re]) => re.test(r.k))![1].push(r);

  return (
    <div className="ly-grid">
      <div className="ly-search">
        <Icon name="mdi:magnify" />
        <TextInput placeholder={t("Search sensors")} value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      {grouped
        .filter(([, list]) => list.length)
        .map(([title, list]) => (
          <Card key={title} title={t(title)} className="ly-card--wide">
            <dl className="ly-diag">
              {list.map((r) => (
                <button type="button" key={r.id} className="ly-diag__row" onClick={() => fireHassEvent(host, "hass-more-info", { entityId: r.id })}>
                  <dt>{r.name}</dt>
                  <dd>{r.value}</dd>
                </button>
              ))}
            </dl>
          </Card>
        ))}
    </div>
  );
}
