#!/usr/bin/env bash
# Diagnose and optionally fix Docker DNS for OpenHands sandboxes.
#
# Symptom this targets:
#   socket.gaierror: [Errno -3] Temporary failure in name resolution
#
# Why here: OpenHands Code agents run commands inside Docker sandbox
# containers. If Docker's embedded DNS cannot resolve external hostnames,
# Python/curl inside the sandbox fail even though the desktop app itself is
# fine.
set -euo pipefail

DNS_SERVERS='["1.1.1.1", "8.8.8.8"]'
DAEMON_JSON="/etc/docker/daemon.json"

test_dns() {
    echo "==> testing DNS from a fresh Docker container"
    if docker run --rm python:3.12-slim python - <<'PY'
import socket
for host in ("www.google.com", "github.com"):
    print(host, socket.getaddrinfo(host, 443)[0][4][0])
print("DNS OK")
PY
    then
        echo "==> Docker DNS is already working"
        return 0
    fi
    echo "==> Docker DNS is failing"
    return 1
}

apply_fix() {
    echo "==> writing Docker DNS fallback to ${DAEMON_JSON}"
    sudo python3 - "$DAEMON_JSON" "$DNS_SERVERS" <<'PY'
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

path = Path(sys.argv[1])
dns_servers = json.loads(sys.argv[2])
if path.exists():
    data = json.loads(path.read_text() or "{}")
    backup = path.with_name(path.name + "." + datetime.now().strftime("%Y%m%d-%H%M%S") + ".bak")
    shutil.copy2(path, backup)
    print(f"backup: {backup}")
else:
    data = {}
    path.parent.mkdir(parents=True, exist_ok=True)

data["dns"] = dns_servers
path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
PY

    echo "==> restarting Docker"
    sudo systemctl restart docker
    test_dns
}

case "${1:-}" in
    --apply)
        if test_dns; then
            exit 0
        fi
        apply_fix
        ;;
    ""|--check)
        test_dns
        ;;
    *)
        echo "Usage: $0 [--check|--apply]" >&2
        exit 2
        ;;
esac
