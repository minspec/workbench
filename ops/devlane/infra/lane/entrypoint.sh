#!/bin/sh
set -eu

if find "${HOME:?HOME is required}" -type f \
    \( -name '.credentials*' -o -name auth.json -o -name .gitconfig \
       -o -name '*.token' \) -print -quit | grep -q .; then
    echo "refusing image-baked credentials under HOME" >&2
    exit 64
fi

HOST_UID=${HOST_UID:-$(id -u)}
HOST_GID=${HOST_GID:-$(id -g)}

if [ "$(id -u)" -eq 0 ] && { [ "$HOST_UID" -ne 0 ] || [ "$HOST_GID" -ne 0 ]; }; then
    HOME="/tmp/lane-home-${HOST_UID}"
    mkdir -p "$HOME"
    chown "$HOST_UID:$HOST_GID" "$HOME"
    export HOME
    exec gosu "$HOST_UID:$HOST_GID" env HOME="$HOME" "$@"
fi
exec "$@"
