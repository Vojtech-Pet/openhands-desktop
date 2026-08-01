# OpenHands Desktop

Native PySide6 desktop client for [OpenHands](https://github.com/OpenHands/OpenHands). Two versions live side by side in this repo, targeting two different (and incompatible) backend generations:

| | Backend | Tested against | Status |
|---|---|---|---|
| [`1.0/`](1.0) | Classic `app_server` (REST API on port 3000, orchestrates one sandbox container per conversation) | `openhands-sdk`/`openhands-tools`/`openhands-agent-server` **1.40.0** | Stable, in daily use |
| [`1.1/`](1.1) | New `openhands-agent-server` directly (one long-running server hosting every conversation) | `openhands-agent-server`/`openhands-sdk`/`openhands-tools` **>= 1.40.0** | Experimental -- core golden path + settings/profiles/skills/secrets verified live end-to-end, some features still incomplete, see [1.1/MIGRATION_STATUS.md](1.1/MIGRATION_STATUS.md) |

## Why two versions

Upstream OpenHands underwent an "Agent Canvas migration" (July 2026) that deleted `openhands/app_server` entirely from its `main` branch -- the REST backend `1.0` depends on is gone for good from upstream, replaced by a different architecture where `openhands-agent-server` (previously just the per-sandbox-container service) now runs standalone as the single backend a client talks to.

`1.0` is pinned to the last SDK release line that still ships `app_server` and keeps working exactly as before. `1.1` is a from-scratch rewrite of the API/controller layer targeting the new architecture directly -- see [1.1/MIGRATION_STATUS.md](1.1/MIGRATION_STATUS.md) for exactly what's been verified live and what's still in progress.

Each directory is a complete, independent app (its own `pyproject.toml`, `src/`, `sandbox-image/`) -- run either one from inside its own directory.
