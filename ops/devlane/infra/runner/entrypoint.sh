#!/bin/sh
set -eu

labels=${RUNNER_LABELS:?RUNNER_LABELS is required}
old_ifs=$IFS
IFS=,
for label in $labels; do
    case "$label" in
        *:host|*:docker://*) ;;
        *) echo "invalid runner label: $label" >&2; exit 64 ;;
    esac
done
IFS=$old_ifs

if [ -n "${LANE_TAG:-}" ]; then
    labels=$(printf '%s' "$labels" | sed "s/\${LANE_TAG}/${LANE_TAG}/g")
fi

until [ -s /run/runner-token/token ]; do
    sleep 1
done

cd /data
if [ ! -f /data/.runner ]; then
    act_runner register --no-interactive \
        --instance "$GITEA_INSTANCE_URL" \
        --token "$(cat /run/runner-token/token)" \
        --name agent-lab \
        --labels "$labels"
fi

exec act_runner daemon --config "${CONFIG_FILE:?CONFIG_FILE is required}"
