/*
 * Lymow backup card — list, restore, rename, and delete map backups.
 *
 * Config:
 *   type: custom:lymow-backup-card
 *   mower_entity: lawn_mower.lymow_THING   # required
 *   backup_sensor: sensor.THING_backup_maps # optional — auto-derived if omitted
 *   title: Map Backups                      # optional
 *
 * Reads: sensor.THING_backup_maps attributes.backups
 * Writes: lymow.restore_backup_map, delete_backup_map, rename_backup_map
 *         button.THING_back_up_map (backup now via existing button entity)
 */

class LymowBackupCard extends HTMLElement {
  setConfig(config) {
    if (!config || !(config.mower_entity || config.mower)) {
      throw new Error("lymow-backup-card: 'mower_entity' is required");
    }
    this._config = {
      ...config,
      mower_entity: config.mower_entity || config.mower,
    };
    this._built = false;
  }

  set hass(hass) {
    this._hass = hass;
    if (this._built) this._refresh();
  }

  connectedCallback() { this._build(); }
  getCardSize() { return 4; }

  _sensorId() {
    if (this._config.backup_sensor) return this._config.backup_sensor;
    const base = this._config.mower_entity.split(".")[1];
    if (!this._hass) return `sensor.${base}_backup_maps`;
    const found = Object.keys(this._hass.states).find(
      id => id.startsWith("sensor.") && id.includes(base) &&
            Array.isArray(this._hass.states[id].attributes?.backups)
    );
    return found || `sensor.${base}_backup_maps`;
  }

  _backupButtonId() {
    const base = this._config.mower_entity.split(".")[1];
    // Find "back_up_map" button entity
    if (!this._hass) return null;
    const candidates = Object.keys(this._hass.states).filter(
      id => id.startsWith("button.") && id.includes(base) && id.includes("back_up")
    );
    return candidates[0] || null;
  }

  _backups() {
    const st = this._hass?.states[this._sensorId()];
    return st?.attributes?.backups || [];
  }

  _build() {
    if (this._built) return;
    const root = this.attachShadow({ mode: "open" });
    root.innerHTML = `
      <style>
        ha-card { overflow: hidden; }
        .header { display:flex; align-items:center; justify-content:space-between; padding:12px 16px 0; }
        .title { font-weight:600; font-size:16px; }
        .backup-now-btn { border:0; background:var(--primary-color,#03a9f4); color:#fff; padding:4px 12px; border-radius:16px; cursor:pointer; font-size:13px; }
        .list { padding:8px 0; }
        .row { padding:8px 16px; border-bottom:1px solid var(--divider-color,#e0e0e0); }
        .row:last-child { border-bottom:0; }
        .row-top { display:flex; align-items:center; gap:8px; }
        .row-name { flex:1; font-size:14px; font-weight:500; cursor:pointer; }
        .row-name.editing { display:none; }
        .name-input { flex:1; background:var(--card-background-color,#fff); color:var(--primary-text-color); border:1px solid var(--primary-color,#03a9f4); border-radius:4px; padding:2px 6px; font-size:14px; }
        .name-input.hidden { display:none; }
        .row-date { font-size:11px; color:var(--secondary-text-color); margin-top:2px; }
        .row-btns { display:flex; gap:4px; }
        .icon-btn { border:0; background:transparent; cursor:pointer; padding:3px 5px; border-radius:4px; font-size:15px; color:var(--primary-text-color); }
        .icon-btn:hover { background:var(--secondary-background-color); }
        .icon-btn.del { color:var(--error-color,#f44336); }
        .icon-btn.restore { color:var(--success-color,#4caf50); }
        .empty { padding:24px 16px; text-align:center; color:var(--secondary-text-color); font-size:13px; }
        .status { padding:4px 16px 8px; font-size:12px; color:var(--secondary-text-color); min-height:18px; }
        .status.err { color:var(--error-color,#f44336); }
      </style>
      <ha-card>
        <div class="header">
          <span class="title"></span>
          <button class="backup-now-btn">Backup now</button>
        </div>
        <div class="list"></div>
        <div class="status"></div>
      </ha-card>`;

    this._root = root;
    root.querySelector(".title").textContent = this._config.title || "Map Backups";
    root.querySelector(".backup-now-btn").addEventListener("click", () => this._backupNow());
    this._built = true;
    this._refresh();
  }

  _refresh() {
    if (!this._root) return;
    const backups = this._backups();
    const list = this._root.querySelector(".list");
    if (!backups.length) {
      list.innerHTML = `<div class="empty">No backups yet. Tap "Backup now" to create one.</div>`;
      return;
    }
    list.innerHTML = "";
    backups.forEach(b => {
      // API returns {file, name, backupTime} — 'file' is the S3 object key
      const key = b.file || b.map_file || b.object_key || "";
      const name = (b.name && b.name.trim()) ? b.name : (key.split("/").pop() || "Backup");
      const rawTs = b.backupTime || b.backup_time;
      const ts = rawTs ? new Date(rawTs * 1000).toLocaleString() : "";

      const row = document.createElement("div");
      row.className = "row";
      row.innerHTML = `
        <div class="row-top">
          <span class="row-name" title="Click to rename">${name}</span>
          <input class="name-input hidden" type="text" value="${name}">
          <div class="row-btns">
            <button class="icon-btn" title="Rename">✏️</button>
            <button class="icon-btn restore" title="Restore this backup">↩️</button>
            <button class="icon-btn del" title="Delete">🗑️</button>
          </div>
        </div>
        <div class="row-date">${ts}</div>`;

      const nameSpan = row.querySelector(".row-name");
      const nameInput = row.querySelector(".name-input");
      const [renameBtn, restoreBtn, deleteBtn] = row.querySelectorAll(".icon-btn");

      // Rename: click pencil or name to enter edit mode
      const startEdit = () => {
        nameSpan.classList.add("editing");
        nameInput.classList.remove("hidden");
        nameInput.focus();
        nameInput.select();
      };
      const commitEdit = () => {
        const newName = nameInput.value.trim();
        nameSpan.classList.remove("editing");
        nameInput.classList.add("hidden");
        if (newName && newName !== name) {
          this._callService("rename_backup_map", { object_key: key, name: newName })
            .then(() => { nameSpan.textContent = newName; this._setStatus("Renamed."); })
            .catch(e => this._setStatus(String(e), true));
        }
      };
      renameBtn.addEventListener("click", startEdit);
      nameSpan.addEventListener("click", startEdit);
      nameInput.addEventListener("blur", commitEdit);
      nameInput.addEventListener("keydown", e => { if (e.key === "Enter") commitEdit(); if (e.key === "Escape") { nameInput.classList.add("hidden"); nameSpan.classList.remove("editing"); } });

      restoreBtn.addEventListener("click", () => {
        if (!confirm(`Restore backup "${name}"? This will replace the current map.`)) return;
        this._callService("restore_backup_map", { object_key: key })
          .then(() => this._setStatus("Restore initiated."))
          .catch(e => this._setStatus(String(e), true));
      });

      deleteBtn.addEventListener("click", () => {
        if (!confirm(`Delete backup "${name}"?`)) return;
        this._callService("delete_backup_map", { object_key: key })
          .then(() => this._setStatus("Deleted."))
          .catch(e => this._setStatus(String(e), true));
      });

      list.appendChild(row);
    });
  }

  _backupNow() {
    const btnId = this._backupButtonId();
    if (btnId) {
      this._hass.callService("button", "press", {}, { entity_id: btnId })
        .then(() => this._setStatus("Backup started."))
        .catch(e => this._setStatus(String(e), true));
    } else {
      this._callService("backup_map", {})
        .then(() => this._setStatus("Backup started."))
        .catch(e => this._setStatus(String(e), true));
    }
  }

  _callService(service, data) {
    return this._hass.callService("lymow", service, data, { entity_id: this._config.mower_entity });
  }

  _setStatus(msg, isErr = false) {
    const s = this._root.querySelector(".status");
    s.textContent = msg;
    s.classList.toggle("err", isErr);
    if (msg && !isErr) setTimeout(() => { if (s.textContent === msg) s.textContent = ""; }, 3000);
  }
}

if (!customElements.get("lymow-backup-card")) {
  customElements.define("lymow-backup-card", LymowBackupCard);
}
window.customCards = window.customCards || [];
if (!window.customCards.find(c => c.type === "lymow-backup-card")) {
  window.customCards.push({ type: "lymow-backup-card", name: "Lymow Backups", description: "Manage map backups." });
}
console.info("%c LYMOW-BACKUP-CARD ", "background:#e65100;color:#fff;border-radius:3px");
