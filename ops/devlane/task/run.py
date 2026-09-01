#!/usr/bin/env python3
"""Run a declared job without inventing launch policy.

The adapter table exists because plausible harness flags caused real loss:
a writing task silently ran read-only, and a cache-sensitive token wall
killed useful work. Launch facts therefore stay data, real launches use the
existing battery, and missing facts are explicit refusals.

Raw output stays in the snapshot. The runner validates structured answers;
it never reads review prose or a work product to manufacture a verdict.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import envelope
import fileset
import verify

ADAPTERS = {
    "codex": {
        "argv": ["codex", "exec", "--sandbox", "{sandbox}", "-"],
        "prompt": "stdin",
        "sandbox": {"read": "read-only", "write": "workspace-write"},
                # `codex --help`: `-m, --model <MODEL>`. There is no effort flag;
        # only a generic `-c key=value`, whose reasoning key I will not
        # guess. A caller setting runtime.effort on codex is refused by
        # name rather than having the dial silently dropped.
        "dials": {"model": ["-m", "{model}"]},
        "store": "~/.codex/sessions",
        "stream": "rollout-*.jsonl",
        "stream_names_cwd": True,
    },
    "grok": {
        "argv": [
            "grok", "--prompt-file", "{prompt}",
            "--output-format", "plain",
            "--permission-mode", "{sandbox}",
        ],
        "prompt": "file",
        "sandbox": {"read": "plan", "write": "auto"},
                # `grok --help`: `-m, --model <MODEL>` and
        # `--reasoning-effort <EFFORT>` -- NOT `--effort`, which is what
        # a review reported; the CLI was asked directly.
        "dials": {"model": ["-m", "{model}"],
                  "effort": ["--reasoning-effort", "{effort}"]},
        "store": "~/.grok/sessions",
        "stream": "updates.jsonl",
        # Grok keys its store PATH by url-encoded cwd, and only `/` is
        # encoded, so the snapshot's directory NAME survives intact and
        # _stream_names_snapshot's marker test matches it. With this
        # False, _discover_stream fell back to "newest updates.jsonl
        # anywhere under the store" and a concurrent Grok session's
        # stream could be supervised, its spend charged here, or the
        # wrong task terminated (Codex + Grok, PR #49).
        "stream_names_cwd": True,
    },
    "claude": {
        "argv": ["claude", "--print", "--permission-mode", "{sandbox}"],
        "prompt": "stdin",
        "sandbox": {"read": "plan", "write": "acceptEdits"},
                # `claude --help`: `--model <model>` and `--effort <level>`.
        "dials": {"model": ["--model", "{model}"],
                  "effort": ["--effort", "{effort}"]},
        "store": "~/.claude/projects",
        "stream": "*.jsonl",
        "stream_names_cwd": True,
    },
    "stub": {"replay": True},
    "direct": {"direct": True},
}

JOBS_PATH = Path(__file__).resolve().parent / "jobs.json"
_BREAKER_PATH = (
    Path(__file__).resolve().parent.parent / "telemetry" / "breaker.py"
)
_UNRESOLVED_REF = "unresolved"
_DIRECT_LOCK = threading.Lock()


class RunError(Exception):
    """A run that would have misled the Conductor. Refuse, never repair."""


class _UsageError(Exception):
    """An argparse refusal that main translates to exit 64."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        raise _UsageError(f"{self.prog}: error: {message}")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _label(job):
    if isinstance(job, str) and job.strip():
        return job.strip()
    return "unknown-job"


def _stamp(ref, started, ended=None):
    return {
        "ref": ref or _UNRESOLVED_REF,
        "started": started,
        "ended": ended,
    }


def _invalid(job, note, *, ref=None, started=None, artifacts=None,
             spend=None):
    return envelope.build(
        _label(job),
        status="invalid",
        verdict=None,
        artifacts=artifacts,
        spend=spend,
        stamp=_stamp(ref, started, _now()),
        note=note,
    )


def load_jobs(path=JOBS_PATH) -> dict:
    """Load the registry, refusing malformed data instead of an empty lane."""
    try:
        with open(path, encoding="utf-8") as stream:
            registry = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RunError(
            f"job registry {os.fspath(path)!r} cannot load: {exc}"
        ) from exc
    if not isinstance(registry, dict):
        raise RunError("job registry must be a JSON object")
    for name, definition in registry.items():
        if not isinstance(name, str) or not name.strip():
            raise RunError("job registry contains an empty name")
        if not isinstance(definition, dict):
            raise RunError(f"job {name!r} must be a JSON object")
    return registry


def _lines(value, field):
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, (list, tuple)) or not all(
            isinstance(item, str) and item.strip() for item in value):
        raise RunError(f"{field} must be a string list")
    return list(value)


def render(job, context, require) -> str:
    """Render only declared prompt fields; missing facts are a refusal.

    `job` is whatever `run` takes: a NAME to look up, or an
    already-resolved definition. Accepting only the definition made the
    two entry points disagree about their first argument, which is the
    kind of seam a caller trips over once and never forgets.
    """
    if isinstance(job, str):
        registry = load_jobs()
        if job not in registry:
            raise RunError(f"no job named {job!r}")
        job = registry[job]
    if not isinstance(job, dict):
        raise RunError("job definition must be an object")
    if not isinstance(context, dict) or not isinstance(require, dict):
        raise RunError("context and require must be objects")

    template = job.get("prompt")
    if template is None:
        return ""
    if not isinstance(template, str) or not template.strip():
        raise RunError("job prompt must be a non-empty string or null")

    values = dict(context)
    values.update(require)
    try:
        prompt = template.format_map(values).strip()
    except (KeyError, ValueError) as exc:
        raise RunError(f"job prompt cannot render: {exc}") from exc

    deliverable = job.get("deliverable")
    if deliverable is not None:
        if not isinstance(deliverable, str) or not deliverable.strip():
            raise RunError("job deliverable must be non-empty")
        prompt += f"\n\nDeliverable: {deliverable.strip()}"

    constraints = _lines(
        job.get("constraints"), "job constraints"
    )
    constraints.extend(
        _lines(require.get("constraints"), "required constraints")
    )
    if constraints:
        prompt += "\n\nConstraints:\n" + "\n".join(
            f"- {item.strip()}" for item in constraints
        )
    return prompt + "\n"


def _snapshot(context):
    if not isinstance(context, dict):
        raise RunError("context must be an object")
    repo = context.get("repo")
    ref = context.get("ref")
    if repo is None or ref is None or not str(ref).strip():
        raise RunError("context.repo and context.ref are required")

    into = context.get("into")
    if into is None:
        # The generated destination is outside the source tree; fileset also
        # independently enforces that safety boundary.
        into = tempfile.mkdtemp(prefix="task-snapshot-")
    include = context.get("include")
    base = context.get("base")
    try:
        return fileset.snapshot(
            repo,
            ref,
            into,
            include=include,
            base=base,
            whole=(include is None and base is None),
        )
    except (fileset.FilesetError, OSError, TypeError, ValueError) as exc:
        raise RunError(
            f"snapshot for ref {ref!r} could not be built: {exc}"
        ) from exc


def _select_harness(definition, runtime):
    configured = definition.get("adapter")
    requested = runtime.get("harness")
    if requested is None:
        if configured in ("direct", "stub"):
            return configured
        raise RunError(
            "runtime.harness is required for a harness job"
        )
    if not isinstance(requested, str) or not requested.strip():
        raise RunError("runtime.harness must be a non-empty name")
    requested = requested.strip()
    if configured == "direct" and requested != "direct":
        raise RunError(
            f"job is direct and refuses harness {requested!r}"
        )
    if configured == "stub" and requested != "stub":
        raise RunError(
            f"job is a replay and refuses harness {requested!r}"
        )
    if configured not in ("direct", "stub", "harness"):
        raise RunError(f"job adapter {configured!r} is not declared")
    return requested


def _raw_path(manifest):
    return Path(manifest["root"]) / ".run.raw"


def _prompt_path(manifest):
    return Path(manifest["root"]) / ".run.prompt"


def _write(path, data):
    try:
        Path(path).write_bytes(data)
    except OSError as exc:
        raise RunError(
            f"raw output could not be written inside the snapshot: {exc}"
        ) from exc


def _candidate_envelope(value):
    if isinstance(value, dict) and tuple(value) == envelope.FIELDS:
        try:
            return envelope.validate(value)
        except envelope.EnvelopeError:
            return None
    if isinstance(value, dict):
        for key in ("envelope", "result", "output"):
            nested = value.get(key)
            if isinstance(nested, dict) and tuple(nested) == envelope.FIELDS:
                try:
                    return envelope.validate(nested)
                except envelope.EnvelopeError:
                    pass
    return None


def _parse_envelope(data):
    """Accept JSON records only; prose is never mined for a verdict."""
    text = data.decode("utf-8", "replace").strip()
    if not text:
        return None
    values = []
    try:
        values.append(json.loads(text))
    except json.JSONDecodeError:
        for line in text.splitlines():
            try:
                values.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    answer = None
    for value in values:
        candidate = _candidate_envelope(value)
        if candidate is not None:
            answer = candidate
    return answer


def _load_breaker():
    spec = importlib.util.spec_from_file_location(
        "_task_run_breaker", _BREAKER_PATH
    )
    if spec is None or spec.loader is None:
        raise RunError("supervision battery module could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _stream_spend(path, harness):
    """Use the battery accounting so supervision and the ledger agree."""
    try:
        battery = _load_breaker().Battery(SimpleNamespace(
            repeat_k=8, window=40, disable=""
        ))
        with open(path, errors="ignore") as stream:
            for line in stream:
                battery.feed(line)
    except (OSError, AttributeError, RunError) as exc:
        return None, (
            "spend is unknown: session stream could not be read: "
            f"{exc}"
        )

    observed = bool(
        battery.per_msg
        or battery.codex_total is not None
        or battery.grok_current
        or battery.grok_banked["total"]
        or battery.grok_banked["out"]
    )
    if not observed:
        return None, (
            "spend is unknown: session stream contained no usage record"
        )
    total = battery.total()
    out = battery.total_out()
    if (
        not isinstance(total, (int, float))
        or isinstance(total, bool)
        or not isinstance(out, (int, float))
        or isinstance(out, bool)
        or total < 0
        or out < 0
        or int(total) != total
        or int(out) != out
    ):
        return None, (
            "spend is unknown: session stream usage was not integral"
        )
    return {
        "harness": harness,
        "total": int(total),
        "out": int(out),
        "runs": 1,
    }, None


# Keys are the breaker's own flag names without the leading dashes, so a
# config author writes what the battery actually receives and nothing
# has to translate. The underscore spellings are accepted too, because
# JSON authors reach for them; the SPEC was silent on this and the two
# halves of this module chose differently, which cost 15 tests.
_CAP_FLAGS = {
    "cap": "--cap",
    "cap-out": "--cap-out",
    "size-mb": "--size-mb",
    "repeat-n": "--repeat-n",
    "repeat-k": "--repeat-k",
    "err-min": "--err-min",
    "total": "--cap",
    "cap_out": "--cap-out",
    "out": "--cap-out",
    "stall": "--stall",
    "size_mb": "--size-mb",
    "repeat_n": "--repeat-n",
    "repeat_k": "--repeat-k",
    "err_min": "--err-min",
    "window": "--window",
    "interval": "--interval",
    "disable": "--disable",
}


def _breaker_argv(stream, caps, *, pid=None, once=False,
                  tripped_file=None):
    if caps is None:
        caps = {}
    if not isinstance(caps, dict):
        raise RunError("runtime.caps must be an object")
    if "cap" in caps and "total" in caps:
        raise RunError("runtime.caps gives both cap and total")
    if "cap_out" in caps and "out" in caps:
        raise RunError("runtime.caps gives both cap_out and out")
    unknown = sorted(set(caps) - set(_CAP_FLAGS))
    if unknown:
        raise RunError(f"runtime.caps has unknown wires: {unknown}")

    argv = [sys.executable, str(_BREAKER_PATH), str(stream)]
    if pid is not None:
        argv.extend(["--pid", str(pid), "--terminate"])
    if once:
        argv.append("--once")
    if tripped_file is not None:
        argv.extend(["--tripped-file", str(tripped_file)])
    for name, value in caps.items():
        if value is not None:
            argv.extend([_CAP_FLAGS[name], str(value)])
    return argv


def _replay_trip(stream, caps):
    """Apply the same battery in-process: stub means no process launch."""
    # Building the CLI argv centralizes cap-name validation even though the
    # replay path deliberately does not execute that argv.
    _breaker_argv(stream, caps)
    values = {
        "cap": 0,
        "cap_out": 0,
        "stall": 900,
        "size_mb": 50,
        "repeat_n": 5,
        "repeat_k": 8,
        "err_min": 12,
        "window": 40,
        "interval": 2,
        "disable": "",
    }
    for name, value in (caps or {}).items():
        destination = {
            "total": "cap", "out": "cap_out"
        }.get(name, name)
        values[destination] = value
    try:
        battery = _load_breaker().Battery(SimpleNamespace(**values))
        with open(stream, errors="ignore") as records:
            for line in records:
                battery.feed(line)
        fired = battery.check(Path(stream))
    except (OSError, AttributeError, TypeError, ValueError) as exc:
        raise RunError(
            f"replay supervision could not read the stream: {exc}"
        ) from exc
    if fired is None:
        return None
    wire, detail = fired
    return f"TRIPWIRE {wire}: {detail}"


def _path_values(value, key=None):
    if isinstance(value, dict):
        for child_key, child in value.items():
            yield from _path_values(child, str(child_key).lower())
    elif isinstance(value, list):
        for child in value:
            yield from _path_values(child, key)
    elif isinstance(value, str) and key in {
        "cwd",
        "workdir",
        "working_directory",
        "workspace",
        "workspace_root",
    }:
        yield value


def _stream_names_snapshot(path, root):
    """Does this stream belong to the task running in `root`?

    A harness says so in one of two places and they differ by vendor:
    Codex records its cwd in a structured field INSIDE the stream, while
    Grok keys the store PATH by url-encoded cwd. Checking only the
    contents rejected a stream whose path named the snapshot, and
    checking only the path would reject Codex's flat `rollout-*.jsonl`.
    Both count.
    """
    expected = Path(root).resolve(strict=False)
    marker = expected.name
    if marker and marker in str(path):
        return True
    try:
        with open(path, errors="ignore") as stream:
            for line in stream:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for value in _path_values(record):
                    try:
                        named = (
                            Path(value).expanduser().resolve(strict=False)
                            == expected
                        )
                    except (OSError, TypeError, ValueError):
                        named = False
                    if named:
                        return True
    except OSError:
        pass
    return False


def _stream_files(adapter):
    try:
        store = Path(os.path.expanduser(adapter["store"]))
        pattern = adapter["stream"]
    except (KeyError, TypeError) as exc:
        raise RunError(
            f"adapter stream facts are incomplete: {exc}"
        ) from exc
    if not isinstance(pattern, str) or not pattern:
        raise RunError(
            "adapter stream pattern must be a non-empty string"
        )
    if not store.is_dir():
        return []
    try:
        return [path for path in store.rglob(pattern) if path.is_file()]
    except OSError:
        return []


def _stream_state(adapter):
    state = {}
    for path in _stream_files(adapter):
        try:
            stat = path.stat()
            state[path] = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            continue
    return state


def _discover_stream(adapter, before, root):
    candidates = []
    for path in _stream_files(adapter):
        try:
            stat = path.stat()
            current = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            continue
        names_us = (
            adapter.get("stream_names_cwd")
            and _stream_names_snapshot(path, root)
        )
        # Naming this snapshot is a stronger claim than being new: no
        # other session can name it. Newness was the weaker proxy used
        # before there was a name to match on, and requiring BOTH rejects
        # a stream that was already open when we launched.
        if before.get(path) == current and not names_us:
            continue
        if adapter.get("stream_names_cwd") and not names_us:
            # A fresh sibling stream is still the wrong evidence. The cwd
            # identity makes simultaneous sessions separable.
            continue
        candidates.append((stat.st_mtime_ns, str(path), path))
    return max(candidates)[2] if candidates else None


def _remember_group(process):
    """Record the child's process group while the parent is still alive."""
    if os.name != "posix":
        return
    with contextlib.suppress(OSError):
        process._task_pgid = os.getpgid(process.pid)


def _terminate(process):
    """Kill the harness, and its group even when the parent is already gone.

    The parent exiting is not the end of a runaway. The battery can
    terminate the harness parent before the runner observes the trip, so
    `poll()` is already non-null by the time we arrive -- and an early
    return on that skipped the group kill in exactly the case the guard
    exists for, leaving every descendant running while the task reported
    `tripped` (Codex, PR #49).

    The group is the one recorded at launch by `_remember_group`, not one
    derived here: after the parent is reaped `getpgid` raises, so a
    guard written that way silently does nothing -- measured, on the
    first attempt at this fix.
    """
    exited = process.poll() is not None
    pgid = getattr(process, "_task_pgid", None)
    if os.name == "posix" and pgid is not None:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pgid, signal.SIGKILL)
    elif not exited:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
    if not exited:
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=5)


def _adapter_argv(adapter, *, sandbox, prompt, root, runtime):
    raw = adapter.get("argv")
    if (
        not isinstance(raw, list)
        or not raw
        or not all(isinstance(part, str) for part in raw)
    ):
        raise RunError("adapter argv must be a non-empty string list")
    values = {
        "sandbox": sandbox,
        "prompt": str(prompt),
        "cwd": str(root),
        "model": runtime.get("model") or "",
        "effort": runtime.get("effort") or "",
        "role": runtime.get("role") or "",
    }
    # A dial the caller set and the template ignores is worse than one
    # that does not exist: CONTRACT.md sells `runtime.model` as what
    # makes "eight cheap ones" and "one careful one" the same
    # job, and every launch was silently using harness defaults
    # instead (Codex + Grok, PR #49). Refusing is the honest floor.
    # Wiring the real flags is a per-harness choice -- grok takes
    # `-m/--effort`, claude `--model`, codex `-m` with no settled effort
    # flag -- so the templates carry the placeholders and the job
    # author decides; until one does, a set-but-unused dial refuses.
    template = " ".join(raw)
    dials = adapter.get("dials") or {}
    extra = []
    for dial in ("model", "effort"):
        if not runtime.get(dial):
            continue
        if ("{%s}" % dial) in template:
            continue          # the template places it itself
        fragment = dials.get(dial)
        if not fragment:
            raise RunError(
                f"runtime.{dial}={runtime[dial]!r} was requested but this "
                f"adapter declares no way to pass it, so the launch would "
                f"silently use the harness default"
            )
        extra.extend(fragment)
    try:
        return [part.format_map(values) for part in [*raw, *extra]]
    except (KeyError, ValueError) as exc:
        raise RunError(f"adapter argv cannot render: {exc}") from exc


def _run_battery_once(stream, caps, tripped_file):
    argv = _breaker_argv(
        stream, caps, once=True, tripped_file=tripped_file
    )
    try:
        completed = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
            shell=False,
        )
    except (OSError, ValueError) as exc:
        raise RunError(
            f"supervision battery could not start: {exc}"
        ) from exc
    return (
        completed.returncode,
        completed.stderr.decode("utf-8", "replace").strip(),
    )


def _rebuild(job, parsed, manifest, started, raw, spend,
             extra_note=None, extra_artifacts=None):
    artifacts = dict(parsed.get("artifacts") or {})
    # The runner owns raw evidence; an emitted handle must not redirect the
    # caller to a file outside this snapshot.
    artifacts["raw"] = str(raw)
    artifacts.update(extra_artifacts or {})
    notes = [
        item for item in (parsed.get("note"), extra_note) if item
    ]
    return envelope.build(
        job,
        status=parsed["status"],
        verdict=parsed["verdict"],
        findings=parsed["findings"],
        artifacts=artifacts,
        spend=spend,
        stamp=_stamp(manifest["ref"], started, _now()),
        note="; ".join(notes) if notes else None,
    )


def _direct(require, runtime, manifest):
    """Delegate while supplying provenance verify's API cannot accept."""
    def raw_artifact():
        descriptor, path = tempfile.mkstemp(
            prefix="verify-",
            suffix=".raw",
            dir=manifest["root"],
        )
        os.close(descriptor)
        return path

    # These hooks only supply the snapshot-local facts absent from check's
    # signature. Serializing the short override keeps concurrent direct runs
    # from crossing raw paths or refs, and both hooks are restored unchanged.
    with _DIRECT_LOCK:
        original_raw = verify._raw_artifact
        original_ref = verify._ref
        verify._raw_artifact = raw_artifact
        verify._ref = lambda cwd: manifest["ref"]
        try:
            # `claim` and `command` are verify's own arguments and the
            # caller names them. Reading them out of `scope` and
            # `constraints` guessed: a constraints LIST is not an argv,
            # so every direct run came back invalid.
            return verify.check(
                require.get("claim", require.get("scope")),
                require.get("command"),
                cwd=manifest["root"],
                expect=require.get("expect"),
                expect_exit=require.get("expect_exit", 0),
                timeout=runtime.get("timeout", 300),
            )
        finally:
            verify._raw_artifact = original_raw
            verify._ref = original_ref


def _stub(job, runtime, manifest, started):
    raw = _raw_path(manifest)
    stream_name = runtime.get("replay") or runtime.get("stream")
    if (
        not isinstance(stream_name, (str, os.PathLike))
        or not os.fspath(stream_name)
    ):
        return _invalid(
            job,
            "stub replay requires runtime.stream",
            ref=manifest["ref"],
            started=started,
            artifacts={"raw": str(raw)},
            spend={
                "harness": "stub", "total": 0, "out": 0, "runs": 0
            },
        )
    try:
        data = Path(stream_name).read_bytes()
        _write(raw, data)
    except (OSError, RunError) as exc:
        return _invalid(
            job,
            f"stub replay stream {os.fspath(stream_name)!r} "
            f"could not be read: {exc}",
            ref=manifest["ref"],
            started=started,
            artifacts={"raw": str(raw)},
            spend={
                "harness": "stub", "total": 0, "out": 0, "runs": 0
            },
        )

    try:
        battery_note = _replay_trip(stream_name, runtime.get("caps"))
    except RunError as exc:
        return _invalid(
            job,
            str(exc),
            ref=manifest["ref"],
            started=started,
            artifacts={"raw": str(raw)},
            spend={
                "harness": "stub", "total": 0, "out": 0, "runs": 0
            },
        )

    spend, spend_note = _stream_spend(stream_name, "stub")
    parsed = _parse_envelope(data)
    if spend is None and parsed is not None:
        # Replays can preserve a validated recorded accounting record. Live
        # harnesses only trust their independent session stream.
        spend = parsed["spend"]
    if spend is None:
        spend = {
            "harness": "stub", "total": 0, "out": 0, "runs": 0
        }

    if battery_note is not None:
        return envelope.build(
            job,
            status="tripped",
            verdict=None,
            artifacts={"raw": str(raw)},
            spend=spend,
            stamp=_stamp(manifest["ref"], started, _now()),
            note=battery_note,
        )
    if parsed is None:
        return _invalid(
            job,
            "stub replay emitted no valid structured envelope",
            ref=manifest["ref"],
            started=started,
            artifacts={"raw": str(raw)},
            spend=spend,
        )
    return _rebuild(
        job,
        parsed,
        manifest,
        started,
        raw,
        spend,
        extra_note=(
            spend_note if parsed["spend"] == spend else None
        ),
    )


def _live(job, definition, context, require, runtime, adapter,
          harness, manifest, started):
    role = runtime.get("role") or definition.get("role")
    empty_spend = {
        "harness": harness, "total": 0, "out": 0, "runs": 0
    }
    if role not in ("read", "write"):
        return _invalid(
            job,
            "runtime.role must be 'read' or 'write'",
            ref=manifest["ref"],
            started=started,
            spend=empty_spend,
        )
    sandbox_map = adapter.get("sandbox")
    if not isinstance(sandbox_map, dict) or role not in sandbox_map:
        return _invalid(
            job,
            f"adapter {harness!r} has no sandbox for role {role!r}",
            ref=manifest["ref"],
            started=started,
            spend=empty_spend,
        )
    sandbox = sandbox_map[role]
    if not isinstance(sandbox, str) or not sandbox:
        return _invalid(
            job,
            f"adapter {harness!r} has an invalid {role!r} sandbox",
            ref=manifest["ref"],
            started=started,
            spend=empty_spend,
        )

    timeout = runtime.get("timeout", 900)
    if (
        not isinstance(timeout, (int, float))
        or isinstance(timeout, bool)
        or timeout <= 0
    ):
        return _invalid(
            job,
            "runtime.timeout must be a positive number of seconds",
            ref=manifest["ref"],
            started=started,
            spend=empty_spend,
        )
    try:
        # Cap errors are launch refusals, not failures discovered after the
        # harness has already been allowed to run unsupervised.
        _breaker_argv("", runtime.get("caps"))
    except RunError as exc:
        return _invalid(
            job,
            str(exc),
            ref=manifest["ref"],
            started=started,
            spend=empty_spend,
        )

    prompt_context = dict(context)
    prompt_context.update({
        "ref": manifest["ref"],
        "base": manifest["base"],
        "diff": manifest["diff"],
        "into": manifest["root"],
    })
    try:
        prompt_text = render(definition, prompt_context, require)
        prompt_path = _prompt_path(manifest)
        prompt_path.write_text(prompt_text, encoding="utf-8")
        argv = _adapter_argv(
            adapter,
            sandbox=sandbox,
            prompt=prompt_path,
            root=manifest["root"],
            runtime={**runtime, "role": role},
        )
    except (OSError, RunError) as exc:
        return _invalid(
            job,
            str(exc),
            ref=manifest["ref"],
            started=started,
            spend=empty_spend,
        )

    executable = shutil.which(argv[0])
    if executable is None:
        return _invalid(
            job,
            f"harness {harness!r} CLI {argv[0]!r} is not on PATH",
            ref=manifest["ref"],
            started=started,
            spend=empty_spend,
        )
    argv[0] = executable

    prompt_mode = adapter.get("prompt")
    if prompt_mode not in ("stdin", "file"):
        return _invalid(
            job,
            f"adapter {harness!r} has unknown prompt mode "
            f"{prompt_mode!r}",
            ref=manifest["ref"],
            started=started,
            spend=empty_spend,
        )

    raw = _raw_path(manifest)
    raw_handle = None
    stdin_handle = None
    try:
        before = _stream_state(adapter)
        # Not a context manager: both handles are handed to Popen and
        # must outlive this block for the child's lifetime. They are
        # closed in the finally below.
        raw_handle = open(raw, "wb")  # noqa: SIM115
        if prompt_mode == "stdin":
            stdin_handle = open(prompt_path, "rb")  # noqa: SIM115
        else:
            stdin_handle = subprocess.DEVNULL
        process = subprocess.Popen(
            argv,
            cwd=manifest["root"],
            stdin=stdin_handle,
            stdout=raw_handle,
            stderr=subprocess.STDOUT,
            shell=False,
            start_new_session=(os.name == "posix"),
        )
        # The group id is recorded HERE, while the parent is certainly
        # alive. Read later it is unavailable: once the parent is reaped
        # getpgid raises, and falling back to the pid risks a recycled
        # one. start_new_session makes the child its own leader, so the
        # group is its pid at this instant and never afterwards.
        _remember_group(process)
    except (OSError, ValueError, RunError) as exc:
        if raw_handle is not None:
            raw_handle.close()
        if hasattr(stdin_handle, "close"):
            stdin_handle.close()
        return _invalid(
            job,
            f"harness {harness!r} could not launch: {exc}",
            ref=manifest["ref"],
            started=started,
            artifacts={"raw": str(raw)},
            spend=empty_spend,
        )

    breaker = None
    breaker_stderr = b""
    session_stream = None
    trip_path = Path(manifest["root"]) / ".run-tripped.md"
    deadline = time.monotonic() + timeout
    tripped_note = None
    invalid_note = None
    try:
        while process.poll() is None:
            if breaker is None:
                session_stream = _discover_stream(
                    adapter, before, manifest["root"]
                )
                if session_stream is not None:
                    battery_argv = _breaker_argv(
                        session_stream,
                        runtime.get("caps"),
                        pid=process.pid,
                        tripped_file=trip_path,
                    )
                    breaker = subprocess.Popen(
                        battery_argv,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                        shell=False,
                    )
            elif breaker.poll() is not None:
                breaker_stderr = breaker.communicate()[1] or b""
                if breaker.returncode == 3:
                    tripped_note = (
                        breaker_stderr.decode(
                            "utf-8", "replace"
                        ).strip()
                        or "supervision battery tripped"
                    )
                else:
                    invalid_note = (
                        f"supervision battery exited "
                        f"{breaker.returncode} before the harness"
                    )
                _terminate(process)
                break

            if time.monotonic() >= deadline:
                tripped_note = (
                    f"harness exceeded timeout of {timeout} seconds"
                )
                _terminate(process)
                break
            time.sleep(0.05)

        # A stream and process may appear and finish in one poll. It still
        # gets a one-pass battery check so a fast runaway cannot evade R8.
        if session_stream is None:
            session_stream = _discover_stream(
                adapter, before, manifest["root"]
            )
            if session_stream is not None:
                code, note = _run_battery_once(
                    session_stream, runtime.get("caps"), trip_path
                )
                if code == 3:
                    tripped_note = note or "supervision battery tripped"
                elif code != 0:
                    invalid_note = (
                        f"supervision battery exited {code}: "
                        f"{note or 'no detail'}"
                    )

        if breaker is not None and breaker.poll() is None:
            try:
                _, breaker_stderr = breaker.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                breaker.kill()
                _, breaker_stderr = breaker.communicate()
                invalid_note = (
                    "supervision battery did not stop after the harness"
                )
        if breaker is not None and breaker.returncode == 3:
            tripped_note = (
                breaker_stderr.decode("utf-8", "replace").strip()
                or "supervision battery tripped"
            )
        elif (
            breaker is not None
            and breaker.returncode not in (None, 0)
            and tripped_note is None
        ):
            breaker_detail = breaker_stderr.decode(
                "utf-8", "replace"
            ).strip()
            invalid_note = (
                f"supervision battery exited {breaker.returncode}: "
                f"{breaker_detail or 'no detail'}"
            )
        return_code = process.wait()
    except (OSError, ValueError, RunError) as exc:
        _terminate(process)
        invalid_note = f"supervision failed: {exc}"
        return_code = process.wait()
    finally:
        raw_handle.close()
        if hasattr(stdin_handle, "close"):
            stdin_handle.close()

    artifacts = {"raw": str(raw)}
    if trip_path.exists():
        artifacts["trip"] = str(trip_path)
    if session_stream is None:
        spend = dict(empty_spend)
        spend_note = "spend is unknown: no session stream found"
    else:
        spend, spend_note = _stream_spend(session_stream, harness)
        if spend is None:
            spend = dict(empty_spend)

    if tripped_note is not None:
        if spend_note:
            tripped_note = f"{tripped_note}; {spend_note}"
        return envelope.build(
            job,
            status="tripped",
            verdict=None,
            artifacts=artifacts,
            spend=spend,
            stamp=_stamp(manifest["ref"], started, _now()),
            note=tripped_note,
        )
    if invalid_note is not None:
        if spend_note:
            invalid_note = f"{invalid_note}; {spend_note}"
        return _invalid(
            job,
            invalid_note,
            ref=manifest["ref"],
            started=started,
            artifacts=artifacts,
            spend=spend,
        )
    if return_code != 0:
        note = f"harness {harness!r} exited {return_code}"
        if spend_note:
            note = f"{note}; {spend_note}"
        return _invalid(
            job,
            note,
            ref=manifest["ref"],
            started=started,
            artifacts=artifacts,
            spend=spend,
        )
    try:
        data = raw.read_bytes()
    except OSError as exc:
        note = f"raw harness output could not be read: {exc}"
        if spend_note:
            note = f"{note}; {spend_note}"
        return _invalid(
            job,
            note,
            ref=manifest["ref"],
            started=started,
            artifacts=artifacts,
            spend=spend,
        )
    parsed = _parse_envelope(data)
    if parsed is None:
        note = "harness emitted no valid structured envelope"
        if spend_note:
            note = f"{note}; {spend_note}"
        return _invalid(
            job,
            note,
            ref=manifest["ref"],
            started=started,
            artifacts=artifacts,
            spend=spend,
        )
    return _rebuild(
        job,
        parsed,
        manifest,
        started,
        raw,
        spend,
        extra_note=spend_note,
        extra_artifacts={
            key: value for key, value in artifacts.items()
            if key != "raw"
        },
    )


def run(job, *, context, require, runtime, jobs=None,
        adapters=None) -> dict:
    """Snapshot, execute the selected adapter, and return one envelope."""
    started = _now()
    if not isinstance(runtime, dict):
        return _invalid(
            job, "runtime must be an object", started=started
        )
    if not isinstance(require, dict):
        return _invalid(
            job, "require must be an object", started=started
        )

    manifest = None
    try:
        registry = (
            load_jobs() if jobs is None else jobs
        )
        if not isinstance(registry, dict):
            raise RunError("jobs must be an object")
        if job not in registry:
            raise RunError(
                f"job {job!r} is absent from the registry"
            )
        definition = registry[job]
        if not isinstance(definition, dict):
            raise RunError(f"job {job!r} must be an object")
        # Resolve state before launch-policy checks so every runnable
        # job is stamped from the fileset manifest, including a later
        # refusal for an unknown or unavailable harness.
        manifest = _snapshot(context)
        harness = _select_harness(definition, runtime)
        table = ADAPTERS if adapters is None else adapters
        if not isinstance(table, dict):
            raise RunError("adapters must be an object")
        if harness not in table:
            raise RunError(f"unknown harness {harness!r}")
        adapter = table[harness]
        if not isinstance(adapter, dict):
            raise RunError(f"adapter {harness!r} must be an object")
    except (RunError, TypeError) as exc:
        return _invalid(
            job,
            str(exc),
            ref=manifest["ref"] if manifest is not None else None,
            started=started,
        )

    if adapter.get("direct") is True:
        # Direct verification owns its envelope, including zero token spend;
        # rebuilding it here would make delegation only approximate.
        try:
            return _direct(require, runtime, manifest)
        except (OSError, TypeError, ValueError) as exc:
            return _invalid(
                job,
                f"direct verification could not run: {exc}",
                ref=manifest["ref"],
                started=started,
                spend={
                    "harness": None,
                    "total": 0,
                    "out": 0,
                    "runs": 0,
                },
            )
    if adapter.get("replay") is True:
        return _stub(job, runtime, manifest, started)
    return _live(
        job,
        definition,
        context,
        require,
        runtime,
        adapter,
        harness,
        manifest,
        started,
    )


def _parser():
    parser = _Parser(prog="run.py")
    parser.add_argument("job")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--base")
    parser.add_argument("--harness")
    parser.add_argument("--role", choices=("read", "write"))
    parser.add_argument("--into")
    return parser


def main(argv=None) -> int:
    try:
        arguments = _parser().parse_args(argv)
    except _UsageError as exc:
        print(exc, file=sys.stderr)
        return 64
    except SystemExit as exc:
        # argparse owns help output while the callable API retains control.
        return int(exc.code)

    result = run(
        arguments.job,
        context={
            "repo": arguments.repo,
            "ref": arguments.ref,
            "base": arguments.base,
            "include": None,
            "into": arguments.into,
        },
        require={
            "scope": "the requested fileset",
            "constraints": [],
        },
        runtime={
            "harness": arguments.harness,
            "model": None,
            "effort": None,
            "role": arguments.role,
            "caps": {},
            "timeout": 900,
        },
    )
    print(json.dumps(result))
    if result["verdict"] == "approve":
        return 0
    if result["verdict"] == "changes":
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
