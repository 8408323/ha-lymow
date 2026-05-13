"""CLI to test Lymow auth and API responses.

Credentials are read from LYMOW_USER / LYMOW_PASS environment variables.
Create scripts/.env (see scripts/.env.example) and they will be loaded
automatically — no need to export variables each time.

Usage:
    uv run python scripts/cli.py
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys

import aiohttp


def _load_dotenv() -> None:
    """Load key=value pairs from scripts/.env into os.environ.

    Does not override variables that are already set. Exits with an error
    message if the file exists but cannot be read.
    """
    candidates = [os.path.join(os.path.dirname(__file__), ".env")]
    for path in candidates:
        if not os.path.isfile(path):
            continue
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = value
        except OSError as exc:
            print(f"Error: could not read {path}: {exc}", file=sys.stderr)
            sys.exit(1)
        break


def _load(module_name: str, path: str) -> None:
    if module_name in sys.modules:
        return
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]


_load_dotenv()

_base = os.path.join(os.path.dirname(__file__), "..", "custom_components", "lymow")
_load("lymow.const", os.path.join(_base, "const.py"))
_load("lymow.auth", os.path.join(_base, "auth.py"))
_load("lymow.api", os.path.join(_base, "api.py"))

from lymow.auth import LymowAuth  # noqa: E402
from lymow.api import LymowApiClient  # noqa: E402


async def main() -> None:
    username = os.environ.get("LYMOW_USER")
    password = os.environ.get("LYMOW_PASS")

    if not username or not password:
        print(
            "Error: LYMOW_USER and LYMOW_PASS must be set.\n"
            "Copy scripts/.env.example to scripts/.env and fill in your credentials.",
            file=sys.stderr,
        )
        sys.exit(1)

    async with aiohttp.ClientSession() as session:
        print("--- Logging in ---")
        auth = LymowAuth(session)
        tokens = await auth.login(username, password)
        print(f"Region:  {tokens['region']}")
        print(f"Expires: {tokens['ExpiresIn']}s")

        print("\n--- Getting AWS credentials ---")
        creds_data = await auth.get_aws_credentials(tokens["IdToken"], tokens["region"])
        print(f"Identity ID: {creds_data['identity_id']}")

        client = LymowApiClient(
            session=session,
            access_token=tokens["AccessToken"],
            region=tokens["region"],
            identity_id=creds_data["identity_id"],
        )

        print("\n--- Device list ---")
        devices = await client.get_devices()
        print(json.dumps(devices, indent=2))

        for device in (devices if isinstance(devices, list) else []):
            thing = device.get("deviceThingName") or device.get("thingName") or list(device.values())[0]
            print(f"\n--- Device info: {thing} ---")
            info = await client.get_device_info(thing)
            print(json.dumps(info, indent=2))

            print(f"\n--- Device feature: {thing} ---")
            feature = await client.get_device_feature(thing)
            print(json.dumps(feature, indent=2))


def main_sync() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    main_sync()
