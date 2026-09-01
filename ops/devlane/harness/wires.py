"""What supervises a dispatched role, and why each threshold is that number.

`breaker.py` watches a running dispatch and terminates it when a wire
trips. The wires are good; every failure here was a threshold set from
intuition and never re-derived. So the thresholds live as data with the
measurement behind them, and re-deriving is running one function rather
than remembering a conversation.

TWO JOBS, AND THEY ARE NOT THE SAME. Confusing them is what caused
every misfire on 2026-08-22/23:

  runaway detection   a loop, a storm of failures, a hang. These have a
                      SHAPE -- repetition, error density, silence -- and
                      shape is what detects them. A runaway is
                      repetitive; a thorough job is not.

  budget backstop     "this has produced more than any legitimate run of
                      its kind." A volume number, generous, and a last
                      resort rather than a control.

A volume cap asked to do runaway detection will always be wrong, because
the quantity it measures rises with legitimate depth.

THE ASYMMETRY THAT SETS THE NUMBERS. A cap set too high costs tokens on
a subscription already paid for. A cap set too low costs the whole run,
reads as agent failure, and sends the next person diagnosing the wrong
thing. Two such kills cost 198,317 output tokens, 13% of everything
twenty dispatches produced, and neither was an agent fault. So bias
generous, and put the effort into making a kill survivable instead --
see NON_DESTRUCTIVE below.
"""

from __future__ import annotations

# --------------------------------------------------------------------
# The measurement the budget rests on. Output tokens per dispatch,
# read from the harnesses' own trace files on 2026-08-23 across twenty
# dispatches. Re-derive with ops/devlane/telemetry/usage.py rather than
# editing these by hand; they are evidence, not configuration.
# --------------------------------------------------------------------

OBSERVED_OUTPUT = {
    "planner":  {"n": 4, "max": 256_839, "top": "plan-wf2"},
    "author":   {"n": 5, "max": 213_582, "top": "extract-a"},
    "contract": {"n": 3, "max": 96_797,  "top": "contract-b"},
    "reviewer": {"n": 1, "max": 13_208,  "top": "author-tests"},
}

# Largest legitimate output observed anywhere: 256,839.
CAP_OUT = 500_000

# Deliberately FLAT rather than per-role, though the per-role maxima
# above would support tighter numbers. Four data points for planners and
# ONE for reviewers is not enough to set a threshold that kills work: a
# per-role cap derived from n=1 is false precision, and the first
# legitimate run that exceeds it looks exactly like a bug in the agent.
# Ratified by the owner on 2026-08-23 at 1.9x the largest legitimate
# run. Revisit when a role class has enough dispatches to have a real
# distribution rather than a maximum.

# --------------------------------------------------------------------
# Wires that are OFF, and why. A disabled wire needs a reason on the
# record, or the next person re-enables it and repeats the failure.
# --------------------------------------------------------------------

DISABLED = {
    "tokens":
        "Sums cache RE-READS, which were 93.4% of all counted tokens "
        "across twenty dispatches -- 58,760,434 of 62,887,712, or 14.2x "
        "the entire non-cached traffic. It therefore measures how many "
        "turns an agent took, not what it produced, and rises with "
        "legitimate depth. No threshold separates a runaway from a "
        "thorough job. It killed an extractor at 3,083,403 'total' whose "
        "real output was 108,241. Off permanently; tokens-out is the "
        "signal, because it counts work produced rather than context "
        "resent.",
}

# --------------------------------------------------------------------
# Per-role wires. Silence means different things to different roles, so
# a single stall threshold cannot be right for all of them.
# --------------------------------------------------------------------

ROLE_WIRES = {
    # Writes once, after thinking for a long time. Silence is its normal
    # working state. A known-good planner had a 340s gap on a 155-line
    # source; 600s then killed one reading a 611-line source at 19
    # minutes, and `claude -p` emits stdout only at the end, so the whole
    # plan was lost.
    "planner":  {"stall": 2400},
    # Uses tools constantly, so it writes to its trace constantly.
    "author":   {"stall": 1200},
    "contract": {"stall": 1200},
    "reviewer": {"stall": 900},
}

DEFAULT_STALL = 1200

# --------------------------------------------------------------------

NON_DESTRUCTIVE = """
The threshold matters far less than whether tripping it destroys the
work, and that is where the effort belongs.

An extractor killed mid-run had already written 2 of its 7 modules to
disk; those survived. What died was its REPORT, because `claude -p`
writes stdout only when the turn ends -- so an hour of reasoning left a
zero-byte file. The cap was wrong, but the cap being wrong only cost an
hour because the report had nowhere to land.

Have a role write its report to a file as it goes, and a trip costs one
turn instead of a run. Then the cap can be generous without anyone
minding, which is the point.
"""


def budget(role=None):
    """Output-token cap for a role. Flat today; the argument is accepted
    so callers do not have to change when it stops being flat."""
    return CAP_OUT


def stall(role=None):
    return ROLE_WIRES.get(role, {}).get("stall", DEFAULT_STALL)


def disabled_wires():
    return sorted(DISABLED)


def _main(argv=None):
    """`wires.py --sh <role>` emits the breaker settings for a launcher,
    so the numbers are not written down twice and cannot drift apart."""
    import argparse
    import json
    import shlex

    ap = argparse.ArgumentParser(description="supervision settings for a role")
    ap.add_argument("--sh", metavar="ROLE", nargs="?", const="", default=None)
    args = ap.parse_args(argv)
    if args.sh is None:
        print(json.dumps({
            "cap_out": CAP_OUT, "disabled": disabled_wires(),
            "role_wires": ROLE_WIRES, "observed_output": OBSERVED_OUTPUT,
        }, indent=2, sort_keys=True))
        return 0
    role = args.sh or None
    print(f"WIRE_CAP_OUT={budget(role)}")
    print(f"WIRE_STALL={stall(role)}")
    print(f"WIRE_DISABLE={shlex.quote(','.join(disabled_wires()))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
