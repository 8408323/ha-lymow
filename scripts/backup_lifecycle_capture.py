"""End-to-end capture: list existing backups, create a NEW one, delete the new
one, list again. Safe — only the backup we created in this run is deleted; all
pre-existing backups are left untouched.

Run: uv run python scripts/backup_lifecycle_capture.py
Output goes to scripts/backup_lifecycle_capture.log.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import time
import uuid

import aiohttp


def _load_dotenv() -> None:
    for path in (
        os.path.join(os.path.dirname(__file__), ".env"),
        os.path.join(os.path.dirname(__file__), "..", ".env"),
    ):
        if not os.path.isfile(path):
            continue
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
        break


def _load(name: str, path: str) -> None:
    if name in sys.modules:
        return
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)


_load_dotenv()
_base = os.path.join(os.path.dirname(__file__), "..", "custom_components", "lymow")
for _m in ("const", "auth", "api", "protocol", "mqtt"):
    _load(f"lymow.{_m}", os.path.join(_base, f"{_m}.py"))

import aiomqtt  # noqa: E402
from lymow.api import LymowApiClient  # noqa: E402
from lymow.auth import LymowAuth  # noqa: E402
from lymow.const import REGION_CONFIG, USER_CTRL_FLOOR_BACKUP  # noqa: E402
from lymow.mqtt import build_presigned_ws_path  # noqa: E402
from lymow.protocol import encode_userctrl, wrap_envelope  # noqa: E402

LOG_PATH = os.path.join(os.path.dirname(__file__), "backup_lifecycle_capture.log")


def emit(f, line: str) -> None:
    print(line)
    f.write(line + "\n")
    f.flush()


async def run() -> None:
    user = os.environ["LYMOW_USER"]
    pwd = os.environ["LYMOW_PASS"]
    with open(LOG_PATH, "w") as logf:
        async with aiohttp.ClientSession() as session:
            auth = LymowAuth(session)
            tokens = await auth.login(user, pwd)
            region = tokens["region"]
            creds = await auth.get_aws_credentials(tokens["IdToken"], region)
            aws = creds["credentials"]
            api = LymowApiClient(session, tokens["AccessToken"], region, creds["identity_id"])
            devices = await api.get_devices()
            thing = devices[0]["deviceThingName"]
            emit(logf, f"→ thing={thing} region={region}")

            # STEP 1: list existing backups
            before = await api.get_backup_map_list(thing)
            before_keys = {b.get("map_file") for b in before}
            emit(logf, f"\n=== STEP 1: list existing backups ({len(before)} found) ===")
            for b in before:
                emit(logf, f"  {json.dumps(b, sort_keys=True)}")

            # STEP 2: send USER_CTRL_FLOOR_BACKUP via MQTT (same as the app's "Back up" tap)
            iot_host = REGION_CONFIG[region]["iot_host"]
            ws_path = build_presigned_ws_path(
                iot_host, region, aws["AccessKeyId"], aws["SecretKey"], aws.get("SessionToken")
            )
            async with aiomqtt.Client(
                hostname=iot_host,
                port=443,
                identifier=f"lymow-backup-lc-{uuid.uuid4().hex[:8]}",
                transport="websockets",
                websocket_path=ws_path,
                websocket_headers={"Host": iot_host},
                tls_params=aiomqtt.TLSParameters(),
                keepalive=30,
                timeout=20,
            ) as mqtt:
                cmd = encode_userctrl(USER_CTRL_FLOOR_BACKUP)
                env = wrap_envelope(cmd)
                emit(
                    logf,
                    f"\n=== STEP 2: send userCtrl={USER_CTRL_FLOOR_BACKUP} "
                    f"FLOOR_BACKUP pb_hex={cmd.hex()} envelope={env} ===",
                )
                await mqtt.publish(f"/device/{thing}/pbinput", env, qos=1)

            # STEP 3: poll until the new backup appears in the list (up to ~45s)
            emit(logf, "\n=== STEP 3: poll for new backup (max 45s) ===")
            new_entry = None
            for i in range(15):
                await asyncio.sleep(3)
                current = await api.get_backup_map_list(thing)
                current_keys = {b.get("map_file") for b in current}
                added = current_keys - before_keys
                emit(logf, f"  poll #{i + 1}: total={len(current)} new={len(added)}")
                if added:
                    new_key = next(iter(added))
                    new_entry = next(b for b in current if b.get("map_file") == new_key)
                    emit(logf, f"  NEW BACKUP: {json.dumps(new_entry, sort_keys=True)}")
                    break

            if new_entry is None:
                emit(logf, "  !! no new backup appeared after 45s — aborting (will NOT delete anything)")
                return

            new_key = new_entry["map_file"]

            # STEP 4: delete only the new backup
            emit(logf, f"\n=== STEP 4: delete the new backup ({new_key}) ===")
            t0 = time.time()
            try:
                resp = await api.delete_backup_map(new_key)
                emit(logf, f"  delete response ({time.time() - t0:.2f}s): {json.dumps(resp, sort_keys=True)}")
            except aiohttp.ClientResponseError as exc:
                emit(logf, f"  delete failed: HTTP {exc.status} {exc.message}")
                return

            # STEP 5: verify deletion
            await asyncio.sleep(2)
            final = await api.get_backup_map_list(thing)
            final_keys = {b.get("map_file") for b in final}
            emit(logf, f"\n=== STEP 5: final list ({len(final)} found) ===")
            for b in final:
                emit(logf, f"  {json.dumps(b, sort_keys=True)}")

            if new_key in final_keys:
                emit(logf, f"\n!! NEW BACKUP STILL PRESENT: {new_key}")
            else:
                emit(
                    logf, f"\n✓ new backup deleted; {len(final)} backups remain (matches pre-test count {len(before)})"
                )
            untouched = before_keys & final_keys
            emit(logf, f"  pre-existing backups untouched: {len(untouched)}/{len(before)}")


if __name__ == "__main__":
    asyncio.run(run())
