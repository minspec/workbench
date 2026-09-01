ARG ACT_RUNNER=0.2.13
ARG LANE_TAG
FROM gitea/act_runner:${ACT_RUNNER}@sha256:8477d5b61b655caad4449888bae39f1f34bebd27db56cb15a62dccb3dcf3a944 AS upstream-runner

FROM lane:${LANE_TAG}

COPY --from=upstream-runner /usr/local/bin/act_runner /usr/local/bin/act_runner

COPY runner/config.yaml /usr/local/bin/config.yaml
COPY runner/entrypoint.sh /usr/local/bin/agent-lab-runner
ENTRYPOINT ["/usr/local/bin/agent-lab-runner"]
