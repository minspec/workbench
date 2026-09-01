#!/bin/sh
set -eu

cd "$(dirname "$0")"
set -a
. ./pins.env
set +a

export LANE_TAG="$(sha256sum pins.env | cut -d ' ' -f 1)"
install -m 0600 /dev/null secrets/token
docker build --file lane/Dockerfile --tag "lane:${LANE_TAG}" \
  --build-arg "PYTHON=${PYTHON:?}" \
  --build-arg "UV=${UV:?}" --build-arg "UV_SHA256=${UV_SHA256:?}" \
  --build-arg "RUFF=${RUFF:?}" \
  --build-arg "CUE=${CUE:?}" --build-arg "CUE_SHA256=${CUE_SHA256:?}" \
  --build-arg "GH=${GH:?}" --build-arg "GH_SHA256=${GH_SHA256:?}" \
  --build-arg "RUSTUP=${RUSTUP:?}" --build-arg "RUSTUP_SHA256=${RUSTUP_SHA256:?}" \
  --build-arg "RUST_TOOLCHAIN=${RUST_TOOLCHAIN:?}" \
  --build-arg "RUST_COMPONENTS=${RUST_COMPONENTS:?}" \
  --build-arg "CLAUDE_CLI=${CLAUDE_CLI:?}" --build-arg "CODEX_CLI=${CODEX_CLI:?}" \
  --build-arg "GROK_CLI=${GROK_CLI:?}" --build-arg "GROK_CLI_SHA256=${GROK_CLI_SHA256:?}" \
  .

docker compose --profile ci --env-file pins.env build act_runner
