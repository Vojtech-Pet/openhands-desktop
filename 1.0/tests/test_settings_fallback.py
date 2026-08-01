import asyncio
import json
from pathlib import Path

import httpx

from openhands_desktop.api.client import AppServerClient


class FailingClient:
    async def get(self, *args, **kwargs):
        raise httpx.ConnectError("offline")

    async def post(self, *args, **kwargs):
        raise httpx.ConnectError("offline")

    async def delete(self, *args, **kwargs):
        raise httpx.ConnectError("offline")

    async def aclose(self):
        return None


def test_update_settings_falls_back_to_local_cache(tmp_path: Path) -> None:
    client = AppServerClient(base_url="http://127.0.0.1:3000")
    client._settings_cache_path = tmp_path / "settings-cache.json"
    client._client = FailingClient()

    asyncio.run(client.update_settings({"language": "sk"}))

    payload = json.loads(client._settings_cache_path.read_text())
    assert payload["language"] == "sk"

    cached_settings = asyncio.run(client.get_settings())
    assert cached_settings["language"] == "sk"
