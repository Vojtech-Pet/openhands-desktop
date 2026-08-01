#!/usr/bin/env bash
# Build the custom sandbox image and point the running app-server at it.
#
# The app-server picks the sandbox image from AGENT_SERVER_IMAGE_REPOSITORY /
# AGENT_SERVER_IMAGE_TAG (see sandbox_spec_service.get_agent_server_image).
# A custom repository name is required, not just a custom tag: for the default
# repository the app-server rewrites the tag's version prefix to match its
# bundled SDK, which would silently pull the stock image instead of this one.
set -euo pipefail

REPO="openhands-desktop/agent-server"
TAG="1.40.0-python"
cd "$(dirname "$0")"

echo "==> building ${REPO}:${TAG}"
docker build -t "${REPO}:${TAG}" .

echo
echo "==> testing DNS inside ${REPO}:${TAG}"
if docker run --rm --entrypoint python "${REPO}:${TAG}" - <<'PY'
import socket
socket.getaddrinfo("www.google.com", 443)
print("DNS OK")
PY
then
    echo "==> sandbox DNS works"
else
    echo "==> sandbox DNS failed"
    echo "    Run ../scripts/fix_docker_dns.sh to diagnose it."
    echo "    Run ../scripts/fix_docker_dns.sh --apply to install fallback Docker DNS servers."
fi

echo
echo "==> built. To use it, the openhands-app container needs:"
echo "      AGENT_SERVER_IMAGE_REPOSITORY=${REPO}"
echo "      AGENT_SERVER_IMAGE_TAG=${TAG}"
echo
echo "    The app-server also tries to *pull* unknown images; this one is"
echo "    local-only, so pulling fails harmlessly and the local image is used."
echo
echo "    Recreate the app container with those vars added, keeping its"
echo "    existing mounts (docker.sock and ~/.openhands)."
