# Custom OpenHands agent-server sandbox image with user-defined subagents and
# skills baked in.
#
# Why baking is required: sandboxes are ephemeral containers with no bind
# mounts. Nothing syncs the host's ~/.openhands into them, and
# /workspace/project does not survive between conversations (both confirmed
# live 2026-07-29). Separately, agent_settings.agent_context is discarded by
# the app-server, so load_user_skills / skills set there never take effect.
# Files inside the image are therefore the only path that reaches the agent.
#
# Paths are the ones the SDK and agent-server actually scan inside the sandbox
# (HOME=/home/openhands):
#   ~/.openhands/agents/      -> sub-agent definitions (openhands.sdk.subagent)
#   ~/.openhands/microagents/ -> user skills (agent_server.skills_service)
#
# Build and point the app-server at it:
#   ./build.sh
FROM ghcr.io/openhands/agent-server:1.37.1-python

# Create ~/.openhands owned by the runtime user BEFORE copying into it.
# COPY --chown only sets ownership on what it copies; the parent directories
# it implicitly creates stay root-owned. That breaks the agent at runtime
# with "Permission denied: /home/openhands/.openhands/profiles" (reproduced
# 2026-07-29), because it can no longer create its profiles/ and cache/
# subdirectories in a root-owned parent.
USER root
RUN mkdir -p /home/openhands/.openhands/agents /home/openhands/.openhands/microagents \
    && chown -R 10001:10001 /home/openhands/.openhands

COPY --chown=10001:10001 agents/      /home/openhands/.openhands/agents/
COPY --chown=10001:10001 microagents/ /home/openhands/.openhands/microagents/

USER 10001
