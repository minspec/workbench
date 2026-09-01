ARG GITEA=1.24.6
FROM gitea/gitea:${GITEA}-rootless@sha256:91f2b27e080739f0d19dba716c3214f17257c632cf762e545b44ca577e37052c

COPY --chown=1000:1000 gitea/runtime/ /run/agent-lab/
COPY --chown=1000:1000 --chmod=0700 gitea/runtime/ /run/runner-token/
COPY --chown=1000:1000 --chmod=0600 gitea/runner-token.seed /run/runner-token/token
COPY gitea/app.ini /tmp/agent-lab-app.ini
COPY --chown=1000:1000 --chmod=0640 gitea/app.ini /etc/gitea/app.ini
COPY --chmod=755 gitea/bootstrap.sh /usr/local/bin/agent-lab-bootstrap
COPY --chmod=755 gitea/entrypoint.sh /usr/local/bin/agent-lab-gitea

CMD ["/usr/local/bin/agent-lab-gitea"]
