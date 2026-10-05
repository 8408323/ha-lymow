import type { Backup, Point } from "../hass";
import { bbox, pathD } from "../map/geometry";
import { useI18n, type T } from "../i18n";
import { useMower } from "../mower";
import { Button, Card, Empty, Icon, useUi } from "../ui";

function backupName(b: Backup, t: T): string {
  return b.name?.trim() || b.file.split("/").pop()?.replace(/\.pb$/, "") || t("Backup");
}

function Thumb({ b }: { b: Backup }) {
  const p = b.preview ?? {};
  const pts: Point[] = [...(p.goZones ?? []), ...(p.nogoZones ?? []), ...(p.channels ?? [])].flatMap((z) => z.polygon ?? []);
  const box = bbox(pts.map((q) => ({ x: q.x, y: -q.y })));
  if (!box || pts.length < 3) {
    return (
      <div className="ly-thumb ly-thumb--empty">
        <Icon name="mdi:map-outline" />
      </div>
    );
  }
  const pad = Math.max(box.maxX - box.minX, box.maxY - box.minY) * 0.06;
  return (
    <svg className="ly-thumb" viewBox={`${box.minX - pad} ${box.minY - pad} ${box.maxX - box.minX + 2 * pad} ${box.maxY - box.minY + 2 * pad}`} aria-hidden>
      {(p.channels ?? []).map((z, i) => (
        <path key={`c${i}`} d={pathD(z.polygon ?? [], false)} className="m-ch" />
      ))}
      {(p.goZones ?? []).map((z, i) => (
        <path key={`g${i}`} d={pathD(z.polygon ?? [])} className={z.isEnabled === false ? "m-go m-go--off" : "m-go"} />
      ))}
      {(p.nogoZones ?? []).map((z, i) => (
        <path key={`n${i}`} d={pathD(z.polygon ?? [])} className="m-nogo" />
      ))}
    </svg>
  );
}

export function BackupsView() {
  const { t, locale } = useI18n();
  const { snap, call, device } = useMower();
  const canAct = device.can_control !== false; // read-only users see the list only
  const ui = useUi();
  // Hidden by permissions: never offer backups a user can't see (they'd pile up unseen).
  if (snap?.backups === "hidden")
    return (
      <Empty icon="mdi:lock-outline" title={t("Backups not available")}>
        {t("Your Home Assistant user doesn't have access to this mower's map backups.")}
      </Empty>
    );
  const backups = [...(snap?.backups ?? [])].sort((a, b) => (b.backupTime ?? 0) - (a.backupTime ?? 0));
  return (
    <div className="ly-grid ly-grid--narrow">
      <Card
        title={t("Map backups")}
        icon="mdi:cloud-sync-outline"
        actions={
          <Button variant="primary" icon="mdi:cloud-upload-outline" disabled={snap?.online !== true || !canAct} onClick={() => call("lymow", "backup_map", {}, t("Backup requested — it shows up here within a few minutes"))}>
            {t("Back up now")}
          </Button>
        }
      >
        <p className="ly-muted">{t("Backups are stored in the Lymow cloud. Restoring replaces the mower's current map.")}</p>
        {!backups.length ? (
          <Empty icon="mdi:cloud-outline" title={t("No backups yet")}>
            {t("Make a backup before you change the map, so you can always go back.")}
          </Empty>
        ) : (
          <ul className="ly-list">
            {backups.map((b) => {
              const name = backupName(b, t);
              return (
                <li key={b.file} className="ly-list__item">
                  <Thumb b={b} />
                  <div className="ly-backup__info">
                    <strong>{name}</strong>
                    <span className="ly-muted">{b.backupTime ? new Date(b.backupTime * 1000).toLocaleString(locale) : t("Unknown date")}</span>
                  </div>
                  <div className="ly-btnrow">
                    <Button
                      disabled={!canAct}
                      icon="mdi:backup-restore"
                      onClick={async () => {
                        if (await ui.confirm({ title: t("Restore “{name}”?", { name }), body: t("The mower's current map is replaced by this backup. No-go areas are not always restored — check them on the map afterwards before mowing."), confirm: t("Restore"), danger: true }))
                          await call("lymow", "restore_backup_map", { object_key: b.file }, t("Backup restored"));
                      }}
                    >
                      {t("Restore")}
                    </Button>
                    <Button
                      disabled={!canAct}
                      variant="ghost"
                      icon="mdi:rename-outline"
                      title={t("Rename")}
                      onClick={async () => {
                        const n = await ui.prompt({ title: t("Rename backup"), label: t("Name"), value: b.name ?? "", maxLength: 40 });
                        if (n && n !== b.name) await call("lymow", "rename_backup_map", { object_key: b.file, name: n }, t("Backup renamed"));
                      }}
                    />
                    <Button
                      disabled={!canAct}
                      variant="ghost"
                      icon="mdi:delete-outline"
                      title={t("Delete")}
                      onClick={async () => {
                        if (await ui.confirm({ title: t("Delete “{name}”?", { name }), confirm: t("Delete"), danger: true })) await call("lymow", "delete_backup_map", { object_key: b.file }, t("Backup deleted"));
                      }}
                    />
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </Card>
    </div>
  );
}
