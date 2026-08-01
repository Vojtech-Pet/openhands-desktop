"""Live verification of the settings/profiles/skills/secrets client methods
added for settings_dialog.py, against the real dev agent-server. Not part
of the shipped app.
"""

import asyncio
import sys

sys.path.insert(0, "src")

from openhands_desktop.api.client import AppServerClient  # noqa: E402


async def main() -> None:
    client = AppServerClient(base_url="http://127.0.0.1:8010", session_api_key="dev-key-123")
    try:
        profiles, active = await client.list_llm_profiles()
        print(f"profiles: {profiles}, active={active}")

        await client.save_profile(
            "test-lmstudio",
            model="openai/qwen3.6-35b-a3b-iq4_nl",
            base_url="http://172.17.0.1:1234/v1",
            api_key="lm-studio",
        )
        print("save_profile: OK")

        detail = await client.get_profile_detail("test-lmstudio")
        print(f"get_profile_detail: name={detail.get('name')}, model={detail.get('config', {}).get('model')}")

        await client.activate_profile("test-lmstudio")
        print("activate_profile: OK")

        profiles, active = await client.list_llm_profiles()
        print(f"profiles after activate: {profiles}, active={active}")

        await client.delete_profile("test-lmstudio")
        print("delete_profile: OK")

        settings = await client.get_settings()
        print(f"get_settings: keys={list(settings.keys())}")

        await client.update_settings({"agent_settings_diff": {"mcp_config": {}}})
        print("update_settings: OK")

        skills = await client.list_skills()
        print(f"list_skills: {len(skills)} items")

        await client.create_secret("TEST_SECRET", "value123", "a test secret")
        print("create_secret: OK")
        secrets = await client.list_secrets()
        print(f"list_secrets: {secrets}")
        await client.delete_secret("TEST_SECRET")
        print("delete_secret: OK")

        print("\nALL SETTINGS CLIENT TESTS PASSED")
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
