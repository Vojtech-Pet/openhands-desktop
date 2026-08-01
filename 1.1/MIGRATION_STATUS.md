# Backend Status

This branch currently targets the existing OpenHands app-server, not the
standalone new `openhands-agent-server`.

Active communication path:

- Main backend: `http://127.0.0.1:3000`
- Settings/profiles/secrets/conversations: `/api/v1/...`
- Conversation start: `POST /api/v1/app-conversations`, then poll
  `/api/v1/app-conversations/start-tasks/search`
- Live events and run control: the per-conversation `conversation_url` and
  `session_api_key` returned by the app-server
- Browser preview: `/api/v1/sandboxes?id=<sandbox_id>`

The standalone Agent Server experiment is intentionally parked for now. Do
not point this desktop app at an alternate Agent Server URL unless the API
layer is migrated deliberately again.
