# OpenHands Desktop (v1.1 -- new Agent Server)

![OpenHands Desktop screenshot](docs/screenshot.png)

Native PySide6 desktop client for the [OpenHands](https://github.com/OpenHands/OpenHands) Agent Server -- talks directly to `openhands-agent-server`'s REST/WebSocket API (one long-running server hosting every conversation), not the older `app_server` the [1.0](../1.0) version uses.

**Requires `openhands-agent-server` >= 1.40.0** (and matching `openhands-sdk`/`openhands-tools`). This is the version this client was built and verified live against on 2026-08-01 -- see [MIGRATION_STATUS.md](MIGRATION_STATUS.md) for exactly what's been tested and what's still incomplete. Older agent-server versions may not expose all the endpoints this client calls (`/api/conversations`, `/api/profiles`, `/api/settings`, `/api/skills/installed`, `/api/settings/secrets`, `/api/desktop/url`, `/sockets/events/{id}`).

## Features

- Live conversation log with per-tool-call cards (collapsed code/results, click to expand)
- LLM profile management (LM Studio autodetect, sampling presets)
- Live browser preview via noVNC (see exactly what `browser_navigate` is doing)
- Workspace file access via a local MCP bridge -- lets the agent read/write a folder on your machine outside the sandbox, gated behind an explicit per-action confirmation dialog
- Supervised Agent: a watchdog control loop (hash-based repeat detection, progress scoring, verifier) around a real OpenHands conversation, so a stuck model gets nudged once and then stopped instead of looping forever
- Errors panel collecting every error in the current conversation in one place
- Mission Control task board across every conversation on the server

## Running

```bash
uv run python3 -m openhands_desktop.main
```

Requires a running OpenHands app-server (`uvicorn openhands.app_server.app:app`) reachable at `http://127.0.0.1:3000` by default.

See [CHANGELOG.md](CHANGELOG.md) for a list of fixes and changes.

## Supervised agent CLI

```bash
uv run supervised-agent --workspace <dir> --task "..."
```
