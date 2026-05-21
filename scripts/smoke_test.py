"""End-to-end smoke test of the Lymow integration against the live cloud.

Exercises the integration's own modules (auth → AWS creds → REST → MQTT →
protobuf decode) the same way the HA integration does at runtime — but as a
plain script, so you can validate the full read path locally without
installing the integration in Home Assistant.

Read-only: it logs in, lists devices, fetches per-device info/feature/backup
metadata, connects MQTT, asks the robot for its map+state (USER_CTRL_QUERY_MAP,
a query — not a movement command) and decodes the reply. No command that moves
or reconfigures the robot is sent.

Usage:
    cp scripts/.env.example scripts/.env   # fill in LYMOW_USER / LYMOW_PASS
    uv run python scripts/smoke_test.py
Exit code 0 = all steps passed, 1 = at least one failed.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys

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
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        break


def _load(name: str, path: str) -> None:
    if name in sys.modules:
        return
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]


_load_dotenv()
_base = os.path.join(os.path.dirname(__file__), "..", "custom_components", "lymow")
for _m in ("const", "auth", "api", "protocol", "mqtt"):
    _load(f"lymow.{_m}", os.path.join(_base, f"{_m}.py"))

from lymow.api import LymowApiClient  # noqa: E402
from lymow.auth import LymowAuth  # noqa: E402
from lymow.const import REGION_CONFIG, USER_CTRL_QUERY_MAP  # noqa: E402
from lymow.mqtt import LymowMqttClient  # noqa: E402
from lymow.protocol import encode_query_map  # noqa: E402

_results: list[tuple[str, bool, str]] = []


def _check(name: str, ok: bool, detail: str = "") -> bool:
    _results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{f' — {detail}' if detail else ''}")
    return ok


async def main() -> int:
    username = os.environ.get("LYMOW_USER")
    password = os.environ.get("LYMOW_PASS")
    if not username or not password:
        print("Error: set LYMOW_USER and LYMOW_PASS in scripts/.env", file=sys.stderr)
        return 1

    async with aiohttp.ClientSession() as session:
        auth = LymowAuth(session)
        print("== auth ==")
        tokens = await auth.login(username, password)
        _check("login", bool(tokens.get("AccessToken")), f"region={tokens.get('region')}")
        creds = await auth.get_aws_credentials(tokens["IdToken"], tokens["region"])
        aws = creds["credentials"]
        _check("aws credentials", bool(aws.get("AccessKeyId")), f"identity={creds['identity_id'][:12]}…")

        client = LymowApiClient(session, tokens["AccessToken"], tokens["region"], creds["identity_id"])
        client.update_aws_credentials(aws["AccessKeyId"], aws["SecretKey"], aws["SessionToken"])

        print("== REST ==")
        devices = await client.get_devices()
        _check("device-list-query", isinstance(devices, list) and len(devices) > 0, f"{len(devices)} device(s)")

        things = [d["deviceThingName"] for d in devices if isinstance(d, dict) and "deviceThingName" in d]
        for thing in things:
            info = await client.get_device_info(thing)
            _check("get-device-info", isinstance(info, dict))
            feat = await client.get_device_feature(thing)
            _check("get-device-feature", isinstance(feat, dict))
            backups = await client.get_backup_map_list(thing)
            _check("get-backup-map", isinstance(backups, list), f"{len(backups)} backup(s)")

        print("== MQTT ==")
        iot_host = REGION_CONFIG[tokens["region"]]["iot_host"]
        states: dict[str, dict] = {}
        mqtt = LymowMqttClient(
            iot_host,
            tokens["region"],
            on_state=lambda t, s: states.setdefault(t, {}).update(s),
            on_online=lambda t, o: None,
        )
        await mqtt.connect(
            things=things,
            access_key=aws["AccessKeyId"],
            secret_key=aws["SecretKey"],
            session_token=aws.get("SessionToken"),
        )
        _check("mqtt connect", True)
        for thing in things:
            await mqtt.async_publish_command(thing, encode_query_map(USER_CTRL_QUERY_MAP))
        await asyncio.sleep(6)  # let pboutput replies arrive
        for thing in things:
            _check(f"pboutput state ({thing[:10]}…)", thing in states, f"keys={sorted(states.get(thing, {}))[:6]}")
        await mqtt.disconnect()

    failed = [n for n, ok, _ in _results if not ok]
    print(
        f"\n{'ALL PASS' if not failed else 'FAILURES: ' + ', '.join(failed)} "
        f"({sum(ok for _, ok, _ in _results)}/{len(_results)})"
    )
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
