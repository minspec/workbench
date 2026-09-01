#!/bin/sh
set -eu

mkdir -p /var/lib/gitea/agent-lab /run/agent-lab

gitea web --config /etc/gitea/app.ini &
gitea_pid=$!
trap 'kill "$gitea_pid" 2>/dev/null || true' INT TERM EXIT

/usr/local/bin/agent-lab-bootstrap

wait "$gitea_pid"
