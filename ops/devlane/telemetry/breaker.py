#!/usr/bin/env python3
"""Live tripwires over a harness session stream.

    breaker.py <stream> [--pid P] [--terminate] [--once]
               [--cap N] [--cap-out N] [--stall S] [--size-mb M]
               [--repeat-n N] [--repeat-k K] [--err-min N] [--window W]
               [--interval S] [--disable w1,w2] [--tripped-file PATH]

usage.py learns after the fact; this catches failure while it is
happening. Adapted from loopstrap's token-breaker.py: the incremental
tail, the per-message-id accounting (a snapshot that re-emits a message id
must not double-count it), cumulative-last-wins for Codex token_count
events, the wire battery with per-wire disable, the distinct trip exit,
and drain-then-exit when the watched process dies. Deliberately NOT
carried over: the owner-override lane, SIGSTOP pause, and filesystem
progress checkpoints — loopstrap machinery this repo has no seat for.

Wires, and which harness streams can feed them:

    tokens       total spend > --cap          claude, codex, grok
    tokens-out   output spend > --cap-out     claude, codex, grok
    repeat-loop  same (tool, input) call --repeat-n times
                 in the last --repeat-k       claude
    error-storm  >= --err-min errored tool results in the
                 last --window                claude
    stall        no stream growth for --stall seconds while
                 --pid lives                  any format
    size         stream file > --size-mb      any format

Grok usage (updates.jsonl turn_completed events, cumulative per run,
runs split on a reported totals shrink) feeds the token walls; only
repeat-loop and error-storm stay claude-only, since grok streams carry
no tool_use/tool_result records. Exit codes: 0 the watched process ended (or --once found
nothing), 3 a wire tripped, 64 usage.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import signal
import sys
import time
from collections import deque
from pathlib import Path

EXIT_TRIPPED = 3


class Battery:
    def __init__(self, args):
        self.args = args
        self.per_msg = {}
        self.per_out = {}
        self.codex_total = None
        # Grok run banking (accounting shipped with slice 2a): usage is
        # cumulative within a run; a REPORTED totalTokens shrink ends
        # the run; last report per currency wins within it.
        self.grok_banked = {"total": 0, "out": 0}
        self.grok_current = {}
        self.grok_prev_total = None
        self.calls = deque(maxlen=args.repeat_k)
        self.results = deque(maxlen=args.window)
        self.disabled = {x.strip() for x in (args.disable or "").split(",")
                            if x.strip()}

    # -- accounting --------------------------------------------------------

    def total(self):
        spent = sum(self.per_msg.values())
        if self.codex_total is not None:
            spent += self.codex_total.get("total_tokens", 0)
        spent += self.grok_banked["total"] + self.grok_current.get(
            "totalTokens", 0)
        return spent

    def total_out(self):
        out = sum(self.per_out.values())
        if self.codex_total is not None:
            out += self.codex_total.get("output_tokens", 0)
        out += self.grok_banked["out"] + self.grok_current.get(
            "outputTokens", 0)
        return out

    def feed_grok(self, update):
        usage = update.get("usage")
        if update.get("sessionUpdate") != "turn_completed" or not isinstance(
            usage, dict
        ):
            # Cancelled turns carry no usage; usage dicts on other
            # update kinds are not accounting records.
            return
        # Only numeric counters enter the accounting: a null or string
        # value would crash a later comparison or sum, killing the
        # battery while the reviewer runs on unsupervised.
        usage = {
            key: value for key, value in usage.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        }
        if "totalTokens" in usage:
            total = usage["totalTokens"]
            if self.grok_prev_total is not None and total < self.grok_prev_total:
                self.grok_banked["total"] += self.grok_current.get(
                    "totalTokens", 0)
                self.grok_banked["out"] += self.grok_current.get(
                    "outputTokens", 0)
                self.grok_current = {}
            self.grok_prev_total = total
        for key in ("totalTokens", "outputTokens"):
            if key in usage:
                self.grok_current[key] = usage[key]

    def feed(self, line):
        line = line.strip()
        if not line:
            return
        try:
            ev = json.loads(line)
        except ValueError:
            return
        if not isinstance(ev, dict):
            # a JSON-RPC batch ([...]) or bare scalar line is not a
            # record; it must not kill the battery mid-watch
            return
        params = ev.get("params")
        # positional JSON-RPC params (a list) must not crash the
        # battery before the record is even identified as grok
        update = params.get("update") if isinstance(params, dict) else None
        if isinstance(update, dict):
            self.feed_grok(update)
            return
        payload = ev.get("payload") or {}
        if payload.get("type") == "token_count":
            # Codex: cumulative — the last event IS the spend so far.
            info = payload.get("info") or {}
            self.codex_total = (info.get("total_token_usage")
                                or self.codex_total)
            return
        message = ev.get("message") or {}
        usage = message.get("usage")
        mid = message.get("id")
        if usage and mid:
            # Keyed by message id: a snapshot overwrite re-emits the same
            # id, and summing both copies would double the spend.
            self.per_msg[mid] = (
                (usage.get("input_tokens") or 0)
                + (usage.get("cache_creation_input_tokens") or 0)
                + (usage.get("cache_read_input_tokens") or 0)
                + (usage.get("output_tokens") or 0))
            self.per_out[mid] = usage.get("output_tokens") or 0
        for c in message.get("content") or []:
            if not isinstance(c, dict):
                continue
            if c.get("type") == "tool_use":
                digest = hashlib.sha256(json.dumps(
                    c.get("input"), sort_keys=True, default=str
                ).encode()).hexdigest()
                self.calls.append((c.get("name"), digest))
            if c.get("type") == "tool_result":
                self.results.append("err" if c.get("is_error") else "ok")

    # -- wires -------------------------------------------------------------

    def check(self, stream: Path):
        a = self.args
        if "tokens" not in self.disabled and a.cap and self.total() > a.cap:
            return "tokens", f"{self.total():,} total > cap {a.cap:,}"
        if ("tokens-out" not in self.disabled and a.cap_out
                and self.total_out() > a.cap_out):
            return ("tokens-out",
                    f"{self.total_out():,} output > cap {a.cap_out:,}")
        if ("repeat-loop" not in self.disabled
                and len(self.calls) >= a.repeat_n):
            top = max(self.calls, key=self.calls.count)
            n = self.calls.count(top)
            if n >= a.repeat_n:
                return ("repeat-loop",
                        (f"identical call x{n} in last {len(self.calls)}:"
                        f" {top[0]}"))
        if "error-storm" not in self.disabled and self.results:
            errors = sum(1 for r in self.results if r != "ok")
            if errors >= a.err_min:
                return ("error-storm",
                        f"{errors}/{len(self.results)} tool results errored")
        if "size" not in self.disabled:
            try:
                size = stream.stat().st_size
            except OSError:
                size = 0
            if size > a.size_mb * 1024 * 1024:
                return ("size",
                        f"{size / 1048576:.1f} MB > --size-mb {a.size_mb}")
        return None


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def terminate_tree(pid):
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    for _ in range(20):
        if not alive(pid):
            return
        time.sleep(0.5)
    with contextlib.suppress(OSError):
        os.kill(pid, signal.SIGKILL)


def trip(args, battery, wire, detail):
    evidence = (f"TRIPWIRE {wire}: {detail}\n"
                f"  stream : {args.stream}\n"
                f"  tokens : {battery.total():,} total"
                f" / {battery.total_out():,} output\n")
    print(evidence, file=sys.stderr, end="")
    if args.tripped_file:
        Path(args.tripped_file).write_text(
            f"# TRIPPED — {wire}\n\n{evidence}\n"
            f"Written by breaker.py; the stream tail around the trip is the"
            f" evidence. Tune the wire's flag or --disable it if the"
            f" pattern was legitimate.\n")
    if args.terminate and args.pid:
        terminate_tree(args.pid)
    return EXIT_TRIPPED


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[1].strip())
    parser.add_argument("stream")
    parser.add_argument("--pid", type=int, default=None)
    parser.add_argument("--terminate", action="store_true")
    parser.add_argument("--once", action="store_true",
                        help="one pass over the existing stream, then exit")
    parser.add_argument("--cap", type=int, default=0)
    parser.add_argument("--cap-out", dest="cap_out", type=int, default=0)
    parser.add_argument("--stall", type=float, default=900)
    parser.add_argument("--size-mb", dest="size_mb", type=float, default=50)
    parser.add_argument("--repeat-n", dest="repeat_n", type=int, default=5)
    parser.add_argument("--repeat-k", dest="repeat_k", type=int, default=8)
    parser.add_argument("--err-min", dest="err_min", type=int, default=12)
    parser.add_argument("--window", type=int, default=40)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--disable", default="")
    parser.add_argument("--tripped-file", dest="tripped_file", default=None)
    args = parser.parse_args(argv)

    battery = Battery(args)
    stream = Path(args.stream)
    pos = 0
    last_growth = time.time()

    def drain():
        nonlocal pos
        grew = False
        try:
            with open(stream, errors="ignore") as handle:
                handle.seek(pos)
                for line in handle:
                    battery.feed(line)
                    grew = True
                pos = handle.tell()
        except FileNotFoundError:
            pass
        return grew

    while True:
        if drain():
            last_growth = time.time()
        fired = battery.check(stream)
        if fired:
            return trip(args, battery, *fired)
        if args.once:
            return 0
        if args.pid is not None and not alive(args.pid):
            # The watched process ended: drain whatever landed last, give
            # the wires one final look, and report clean.
            drain()
            fired = battery.check(stream)
            return trip(args, battery, *fired) if fired else 0
        if ("stall" not in battery.disabled and args.pid is not None
                and time.time() - last_growth > args.stall):
            return trip(args, battery, "stall",
                        f"no stream growth for"
                        f" {int(time.time() - last_growth)} s"
                        f" (--stall {args.stall:g})")
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
