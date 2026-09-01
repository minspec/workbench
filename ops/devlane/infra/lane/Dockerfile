ARG PYTHON=3.12.14
FROM python:3.12-slim-bookworm@sha256:0f5b26b9518d002b6173fd61daad821fa340635ebfec5bba471013f9ca114579

ARG PYTHON=3.12.14

ARG UV=0.8.22
ARG UV_SHA256=741ff1f5742c5a4a25d2f829e8395355e43f7a5ae2ebc6368e9ae2df0efb69cf
ARG RUFF=0.16.5
ARG CUE=0.14.1
ARG CUE_SHA256=c7d29f5988d088627cf53bd6a223807c466066cf432c7cf5c36429ffc9e734f6
ARG GH=2.98.0
ARG GH_SHA256=f65a3fa2fa0eb2e97c445ee3f5e087a40aae03b64847f45a8f13805e504535d6
ARG RUSTUP=1.29.0
ARG RUSTUP_SHA256=4acc9acc76d5079515b46346a485974457b5a79893cfb01112423c89aeb5aa10
ARG RUST_TOOLCHAIN=1.89.0
ARG RUST_COMPONENTS="clippy rustfmt"
ARG CLAUDE_CLI=2.1.251
ARG CODEX_CLI=0.150.1
ARG GROK_CLI=1.0.5
ARG GROK_CLI_SHA256=9ba87444e1819e8f6104adbbf4676a870c204380aa5c3e1c38a926c4ea677238

RUN test -n "$UV" && test -n "$RUFF" && test -n "$CUE" \
    && test -n "$PYTHON" && python3 --version | grep -F "$PYTHON" \
    && test -n "$CUE_SHA256" && test -n "$GH" \
    && test -n "$UV_SHA256" && test -n "$GH_SHA256" \
    && test -n "$RUSTUP" && test -n "$RUSTUP_SHA256" \
    && test -n "$RUST_TOOLCHAIN" && test -n "$RUST_COMPONENTS" \
    && test -n "$CLAUDE_CLI" && test -n "$CODEX_CLI" \
    && test -n "$GROK_CLI" && test -n "$GROK_CLI_SHA256"

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git gosu npm pipx xz-utils \
    && rm -rf /var/lib/apt/lists/*

# uv from the release tarball its version names, digest-checked — never
# an installer piped to a shell.
RUN curl -fsSLo /tmp/uv.tar.gz \
      "https://github.com/astral-sh/uv/releases/download/${UV}/uv-x86_64-unknown-linux-gnu.tar.gz" \
    && echo "${UV_SHA256}  /tmp/uv.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/uv.tar.gz -C /usr/local/bin --strip-components=1 \
         uv-x86_64-unknown-linux-gnu/uv \
    && rm /tmp/uv.tar.gz \
    && uv tool install "ruff==${RUFF}" \
    && install -m 0755 /root/.local/bin/ruff /usr/local/bin/ruff

RUN curl -fsSLo /tmp/cue.tar.gz \
      "https://github.com/cue-lang/cue/releases/download/v${CUE}/cue_v${CUE}_linux_amd64.tar.gz" \
    && echo "${CUE_SHA256}  /tmp/cue.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/cue.tar.gz -C /usr/local/bin cue \
    && rm /tmp/cue.tar.gz

RUN curl -fsSLo /tmp/gh.deb \
      "https://github.com/cli/cli/releases/download/v${GH}/gh_${GH}_linux_amd64.deb" \
    && echo "${GH_SHA256}  /tmp/gh.deb" | sha256sum -c - \
    && apt-get update \
    && apt-get install -y --no-install-recommends /tmp/gh.deb \
    && rm -rf /var/lib/apt/lists/* /tmp/gh.deb

ENV RUSTUP_HOME=/usr/local/rustup \
    CARGO_HOME=/usr/local/cargo \
    PATH="/usr/local/cargo/bin:/usr/local/bin:${PATH}"

# rustup-init from the archive its version names, digest-checked — the
# sh.rustup.rs pipe binds no bytes.
RUN curl --proto '=https' --tlsv1.2 -fsSLo /tmp/rustup-init \
      "https://static.rust-lang.org/rustup/archive/${RUSTUP}/x86_64-unknown-linux-gnu/rustup-init" \
    && echo "${RUSTUP_SHA256}  /tmp/rustup-init" | sha256sum -c - \
    && chmod 0755 /tmp/rustup-init \
    && /tmp/rustup-init -y --default-toolchain "${RUST_TOOLCHAIN}" \
    && rm /tmp/rustup-init \
    && rustup component add ${RUST_COMPONENTS}

RUN npm install -g \
      "@anthropic-ai/claude-code@${CLAUDE_CLI}" \
      "@openai/codex@${CODEX_CLI}"

RUN curl -fsSLo /usr/local/bin/grok \
      "https://x.ai/cli/grok-${GROK_CLI}-linux-x86_64" \
    && echo "${GROK_CLI_SHA256}  /usr/local/bin/grok" | sha256sum -c - \
    && chmod 0755 /usr/local/bin/grok

COPY lane/entrypoint.sh /usr/local/bin/lane-entrypoint
ENTRYPOINT ["/usr/local/bin/lane-entrypoint"]
CMD ["bash"]

# Build tooling tags this image as lane:$(sha256sum pins.env | cut -d' ' -f1).
