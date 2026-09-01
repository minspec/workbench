"""Guard against process-doc recipes whose tool paths or flags silently rot."""

import ast
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
DOC = REPO / "ops/process/cross-review.md"
BREAKER = REPO / "ops/devlane/telemetry/breaker.py"
BREAKER_PATH = "ops/devlane/telemetry/breaker.py"

CLAUDE_ROW = (
    "| Claude | newest `*.jsonl` under `~/.claude/projects/<slug>/`, where "
    "`<slug>` is `$SNAP` with every `/` and `.` replaced by `-` | all six |"
)
CODEX_ROW = (
    "| Codex | newest `~/.codex/sessions/*/*/*/rollout-*.jsonl` | "
    "tokens, tokens-out, stall, size |"
)
GROK_ROW = (
    "| Grok | `updates.jsonl` in the newest session dir "
    "under `~/.grok/sessions/<url-encoded $SNAP>/` | "
    "tokens, tokens-out, stall, size |"
)


def fenced_logical_blocks(text):
    """Return fenced blocks containing (first physical line, logical line)."""
    blocks = []
    current = None
    start = None
    parts = []

    for number, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            if current is None:
                current = []
            else:
                if parts:
                    current.append((start, " ".join(parts)))
                    start = None
                    parts = []
                blocks.append(current)
                current = None
            continue
        if current is None:
            continue

        if start is None:
            start = number
        stripped = line.rstrip()
        continued = stripped.endswith("\\")
        parts.append((stripped[:-1] if continued else line).strip())
        if not continued:
            current.append((start, " ".join(parts)))
            start = None
            parts = []

    if current is not None:
        if parts:
            current.append((start, " ".join(parts)))
        blocks.append(current)
    return blocks


def fenced_logical_lines(text):
    """Return (first physical line, logical line) pairs from fenced blocks."""
    return [item for block in fenced_logical_blocks(text) for item in block]


def fenced_lines(text):
    return fenced_logical_lines(text)


def breaker_invocations(text):
    return [
        (number, line)
        for number, line in fenced_logical_lines(text)
        if re.search(r"\bbreaker\.py\b", line)
    ]


def breaker_lines(text):
    return breaker_invocations(text)


def launch_fenced_blocks(text):
    return [
        block
        for block in fenced_logical_blocks(text)
        if any(
            not line.lstrip().startswith("#") and re.search(r"\bnohup\b", line)
            for _, line in block
        )
    ]


def _touches_launch_marker(line):
    return re.search(
        r"^\s*touch\s+[\"']?launched[\"']?(?:\s|$)",
        line,
    ) is not None


def launch_form_errors(text):
    errors = []
    blocks = launch_fenced_blocks(text)
    if not blocks:
        return ["no fenced reviewer launch uses nohup"]

    for block in blocks:
        code_only = "\n".join(
            "" if line.lstrip().startswith("#") else line.split("#", 1)[0]
            for _, line in block
        )
        if re.search(r"\$\(\s*cat\s+prompt\.txt\s*\)", code_only):
            errors.append("launch fence uses $(cat prompt.txt)")

        launches = [
            (index, number, line)
            for index, (number, line) in enumerate(block)
            if not line.lstrip().startswith("#") and re.search(r"\bnohup\b", line)
        ]
        for index, number, line in launches:
            if re.search(r"\bcd\b.*&&.*\bnohup\b.*&", line):
                errors.append(f"line {number}: compound cd && nohup launch")
            marker_precedes_launch = any(
                prior_index < index and _touches_launch_marker(prior_line)
                for prior_index, (_, prior_line) in enumerate(block)
            )
            if not marker_precedes_launch:
                errors.append(f"line {number}: touch launched does not precede nohup")
            if not line.split("#", 1)[0].rstrip().endswith("&"):
                errors.append(
                    f"line {number}: nohup launch is not backgrounded"
                    " with a trailing &"
                )
            enters_snapshot = any(
                prior_index < index
                and re.match(r'\s*cd\s+"\$SNAP"', prior_line)
                for prior_index, (_, prior_line) in enumerate(block)
            )
            if not enters_snapshot:
                errors.append(
                    f"line {number}: launch does not cd into the"
                    ' snapshot ("$SNAP") first'
                )
            captures_pid = any(
                later_index > index and re.match(r"\s*RPID=\$!", later_line)
                for later_index, (_, later_line) in enumerate(block)
            )
            if not captures_pid:
                errors.append(
                    f"line {number}: nohup launch never captures RPID=$!"
                )
    return errors


def declared_flags(source):
    flags = set()
    for node in ast.walk(ast.parse(source)):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_argument"
        ):
            continue
        for argument in node.args:
            if (
                isinstance(argument, ast.Constant)
                and isinstance(argument.value, str)
                and argument.value.startswith("--")
            ):
                flags.add(argument.value)
    return flags


def markdown_tables(text):
    tables = []
    table = []
    for number, line in enumerate(text.splitlines(), 1):
        if line.startswith("|"):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            table.append((number, cells))
        elif table:
            tables.append(table)
            table = []
    if table:
        tables.append(table)
    return tables


def prose_blocks(text):
    lines = []
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            lines.append("")
        elif in_fence:
            lines.append("")
        else:
            lines.append(line)
    return [
        block.strip()
        for block in re.split(r"\n\s*\n", "\n".join(lines))
        if block.strip()
    ]


def _normalise_cell(cell):
    return re.sub(r"\s+", " ", re.sub(r"[`*_]", "", cell)).strip().lower()


def _listed_wires(cell):
    return {
        item.strip()
        for item in re.split(r"\s*,\s*|\s+and\s+", _normalise_cell(cell))
        if item.strip()
    }


def wire_table_errors(text):
    errors = []
    candidates = []
    for table in markdown_tables(text):
        if not table:
            continue
        header = table[0][1]
        if (
            len(header) >= 3
            and _normalise_cell(header[0]) == "reviewer"
            and _normalise_cell(header[2]) == "wires"
        ):
            candidates.append(table)

    if len(candidates) != 1:
        return [f"expected one reviewer wire table, found {len(candidates)}"]

    rows = candidates[0][2:]
    paired = {}
    for reviewer in ("claude", "codex", "grok"):
        matches = [
            (number, cells)
            for number, cells in rows
            if cells and _normalise_cell(cells[0]) == reviewer
        ]
        if len(matches) != 1:
            errors.append(f"expected one {reviewer.title()} row, found {len(matches)}")
        else:
            paired[reviewer] = matches[0]

    if "claude" in paired:
        number, cells = paired["claude"]
        if len(cells) < 3:
            errors.append(f"line {number}: Claude row has fewer than three cells")
        else:
            store, wires = cells[1], cells[2]
            slug_rule = (
                "<slug>" in store
                and "$SNAP" in store
                and re.search(
                    r"\bevery\b.*`/`.*`\.`.*\breplaced\s+by\b.*`-`",
                    store,
                    re.IGNORECASE,
                )
            )
            if "~/.claude/projects" not in store:
                errors.append(f"line {number}: Claude store root")
            if "*.jsonl" not in store:
                errors.append(f"line {number}: Claude stream glob")
            if not slug_rule:
                errors.append(f"line {number}: Claude slug rule")
            if _normalise_cell(wires) != "all six":
                errors.append(f"line {number}: Claude wires are not exactly all six")

    if "codex" in paired:
        number, cells = paired["codex"]
        if len(cells) < 3:
            errors.append(f"line {number}: Codex row has fewer than three cells")
        else:
            store, wires = cells[1], cells[2]
            if "~/.codex/sessions" not in store:
                errors.append(f"line {number}: Codex store root")
            if "rollout-*.jsonl" not in store:
                errors.append(f"line {number}: Codex rollout glob")
            if _listed_wires(wires) != {
                "tokens",
                "tokens-out",
                "stall",
                "size",
            }:
                errors.append(
                    f"line {number}: Codex wires must be exactly tokens, "
                    "tokens-out, stall, and size"
                )

    if "grok" in paired:
        number, cells = paired["grok"]
        if len(cells) < 3:
            errors.append(f"line {number}: Grok row has fewer than three cells")
        else:
            store, wires = cells[1], cells[2]
            if "~/.grok/sessions" not in store:
                errors.append(f"line {number}: Grok store root")
            affirmed = set()
            for match in re.finditer(r"[\w*-]+\.jsonl\b", store):
                lookback = store[max(0, match.start() - 16):match.start()]
                # "(not `events.jsonl`)" is a warning, not an offer
                if re.search(r"\b(?:not|never)\b[^,;]{0,14}$", lookback):
                    continue
                affirmed.add(match.group(0))
            if affirmed != {"updates.jsonl"}:
                # only updates.jsonl carries usage; offering ANY
                # alternative ("or events.jsonl", "or session.jsonl")
                # silently zeroes the token wires the row promises.
                errors.append(
                    f"line {number}: Grok row must name updates.jsonl as"
                    f" the only offered stream (found {sorted(affirmed)})"
                )
            if not re.search(r"\burl-encod\w*\b", store, re.IGNORECASE):
                errors.append(f"line {number}: Grok URL-encoded snapshot")
            listed = _listed_wires(wires)
            if listed != {"tokens", "tokens-out", "stall", "size"}:
                errors.append(
                    f"line {number}: Grok wires must be exactly tokens,"
                    " tokens-out, stall, size"
                )
            if re.search(r"\ball\s+six\b", wires, re.IGNORECASE):
                errors.append(
                    f"line {number}: Grok cannot claim all six —"
                    " repeat-loop and error-storm stay claude-only"
                )

    return errors


def flag_values(invocation, flag):
    pattern = re.compile(
        rf"(?<![\w-]){re.escape(flag)}(?:\s+|=)([^\s;&|]+)"
    )
    return [value.strip("\"'") for value in pattern.findall(invocation)]


def carries_nonzero_integer_flag(invocation, flag):
    values = flag_values(invocation, flag)
    return (
        len(values) == 1
        and re.fullmatch(r"[+-]?\d+", values[0]) is not None
        and int(values[0]) != 0
    )


_DISCOVERY_SHAPE = re.compile(
    # The whole handoff: wait UNTIL a stream exists OR the reviewer is
    # dead, and the stream selected is the NEWEST by mtime — any flip
    # of until/while, ||/&&, -n/-z, the ! on the liveness probe, or a
    # first-match/oldest-match selection is the concurrent-sibling race.
    r"\buntil\s+STREAM=\$\(\s*find\b[^;|]*(?<![\w-])-newer\b[^;|]*"
    r"-printf\s+'%T@ %p\\n'\s*\|\s*sort\s+-n\s*\|\s*tail\s+-1\s*\|"
    r"\s*cut\b[^;]*\)\s*;\s*"
    r"\[\s*-n\s+\"?\$\{?STREAM\}?\"?\s*\]\s*\|\|\s*!\s*kill\s+-0\s+"
    r"\"?\$\{?RPID\}?\"?\s*;\s*do\b"
)


def discovery_loop_has_kill_zero(line):
    return _DISCOVERY_SHAPE.search(line) is not None


def _breaker_stream_argument(line):
    after = line.split(BREAKER_PATH, 1)[1]
    tokens = after.split()
    return tokens[0] if tokens else ""


# The supervision recipe fence, verbatim. Shell has unbounded
# spellings for every one-line subversion (declare -x, readonly,
# IFS= prefixes, -iname, -path, -o alternation ...), so the fence
# is pinned by EQUALITY like the paragraph: any edit must update
# this constant in the same change. The feature checks remain as
# nets for any OTHER fence that arms the battery.
RECIPE_FENCE = (
    "STORE=~/.grok/sessions    # store root and stream pattern for this\n"
    "PATTERN=updates.jsonl     # reviewer's harness \u2014 see the table below\n"
    "until STREAM=$(find \"$STORE\" -name \"$PATTERN\" -newer launched \\\n"
    "        -printf '%T@ %p\\n' | sort -n | tail -1 | cut -d' ' -f2-); \\\n"
    "      [ -n \"$STREAM\" ] || ! kill -0 \"$RPID\"; do sleep 2; done\n"
    "[ -n \"$STREAM\" ] && python3 ops/devlane/telemetry/breaker.py \"$STREAM\" --pid \"$RPID\" \\\n"
    "  --cap 2000000 --cap-out 150000 --stall 600 --size-mb 50 --terminate \\\n"
    "  --tripped-file TRIPPED.md 2>> breaker.log &\n"
)


def recipe_fence_errors(text):
    """The pinned executable contract: sh has unbounded spellings for
    one-line subversion, so the LIVE doc's recipe fence is checked by
    equality; battery_wiring_errors stays the feature net for
    synthetic corpora and any other fence."""
    errors = []
    fences = re.findall(r"```sh\n(.*?)```", text, re.DOTALL)
    recipe_fences = [f for f in fences
                     if "STORE=" in f and BREAKER_PATH in f]
    if len(recipe_fences) != 1:
        return [(f"expected one supervision recipe fence, found"
                 f" {len(recipe_fences)}")]
    if recipe_fences[0] != RECIPE_FENCE:
        errors.append(
            "the supervision recipe fence diverged from the pinned"
            " contract — update the doc and RECIPE_FENCE together,"
            " deliberately")
    return errors


def battery_wiring_errors(text):
    errors = []
    logical_lines = [
        (number, line.split("#", 1)[0])
        for number, line in fenced_logical_lines(text)
    ]
    stores = []
    patterns = []
    for number, line in logical_lines:
        assign = re.match(r"\s*(?:export\s+)?STORE=(\S+)", line)
        if assign:
            stores.append((number, assign.group(1)))
        assign = re.match(r"\s*(?:export\s+)?PATTERN=(\S+)", line)
        if assign:
            patterns.append((number, assign.group(1)))
    for number, line in logical_lines:
        stream_find = re.search(
            r"\bSTREAM=\$\(\s*find\b[^;|]*?-name\s+(\S+)", line)
        # the recipe's PATTERN value is only meaningful if the find
        # actually reads it: an inlined literal ("-name events.jsonl")
        # detaches the checked assignment from the selected stream
        # (Grok, PR #29 delta round 4)
        if (stream_find and patterns
                and not re.fullmatch(r"\"?\$\{?PATTERN\}?\"?",
                                     stream_find.group(1))):
            errors.append(
                f"line {number}: stream discovery ignores the PATTERN"
                f" assignment and selects {stream_find.group(1)}"
            )

    for p_number, p_value in patterns:
        # pair each PATTERN with the nearest STORE in either
        # direction — assignment order is not load-bearing
        near = min(stores, key=lambda item: abs(item[0] - p_number),
                   default=None)
        # events.jsonl (and any sibling) carries no usage: a grok
        # recipe pointing the battery elsewhere zeroes the token
        # wires while the table still promises them.
        if (near and abs(near[0] - p_number) <= 5
                and ".grok/sessions" in near[1]
                and p_value != "updates.jsonl"):
            errors.append(
                f"line {p_number}: grok recipe PATTERN is"
                f" {p_value}, not the stream that carries"
                " usage (updates.jsonl)"
            )

    invocations = [
        (number, line)
        for number, line in logical_lines
        if BREAKER_PATH in line
    ]
    if not invocations:
        return [f"no fenced {BREAKER_PATH} invocation"]

    armed = [
        (number, line)
        for number, line in invocations
        if BREAKER_PATH in line and re.search(r"(?<![\w-])--pid\b", line)
    ]
    if not armed:
        errors.append(f"no {BREAKER_PATH} invocation carries --pid")
    else:
        if not all(
            re.fullmatch(r'"?\$\{?STREAM\}?"?', _breaker_stream_argument(line))
            for _, line in armed
        ):
            errors.append(
                "armed breaker invocation does not take the discovered"
                " stream as its stream argument"
            )
        for number, line in armed:
            if re.search(r"(?<![\w-])--once\b", line):
                errors.append(
                    f"line {number}: armed battery one-shots with --once"
                    " instead of supervising"
                )
        for number, line in armed:
            line = line.split("#", 1)[0]
            if not re.match(r'\s*\[\s*-n\s+"\$STREAM"\s*\]\s*&&', line):
                errors.append(
                    f"line {number}: armed invocation is not guarded by"
                    ' [ -n "$STREAM" ] &&'
                )
            for flag in ("--cap", "--cap-out"):
                if not carries_nonzero_integer_flag(line, flag):
                    errors.append(
                        f"line {number}: armed breaker {flag} is missing or zero"
                    )
            tripped_values = flag_values(line, "--tripped-file")
            if not (
                len(tripped_values) == 1
                and tripped_values[0]
                and not tripped_values[0].startswith("/dev/")
            ):
                errors.append(
                    f"line {number}: armed breaker --tripped-file is"
                    " missing or discards the trip evidence"
                )
            for value in flag_values(line, "--disable"):
                # Post-flip every reviewer in the table has token wires,
                # so any --disable on the armed line understates the
                # table's promise, not only stall/size.
                errors.append(
                    f"line {number}: armed breaker disables a wire the"
                    f" table promises ({value})"
                )
            pid_values = flag_values(line, "--pid")
            if not (
                len(pid_values) == 1
                and re.fullmatch(r"\$\{?RPID\}?", pid_values[0])
            ):
                errors.append(
                    f"line {number}: armed breaker --pid is not the"
                    " captured $RPID"
                )
            if not re.search(r"(?<![\w-])--terminate\b", line):
                errors.append(
                    f"line {number}: armed breaker invocation lacks --terminate"
                )

    discovery = [
        (number, line)
        for number, line in logical_lines
        if re.search(r"\bSTREAM\s*=\s*\$?\(\s*find\b", line)
        and re.search(r"(?<![\w-])-newer\b", line)
    ]
    if not discovery:
        errors.append("no fenced find invocation discovers a new reviewer stream")
    else:
        for number, line in discovery:
            if not discovery_loop_has_kill_zero(line):
                errors.append(f"line {number}: discovery loop lacks kill -0 on $RPID")
            if re.search(r"(?<![\w-])-quit\b", line):
                errors.append(
                    f"line {number}: discovery takes the first stream"
                    " found, not the newest (concurrent-session race)"
                )
        if armed and min(n for n, _ in armed) < min(n for n, _ in discovery):
            # Armed before the wait means $STREAM is empty and the
            # guard silently skips — the reviewer runs unsupervised.
            errors.append(
                "armed breaker invocation precedes the stream discovery"
            )
    return errors


def caps_prose_errors(text):
    """The paragraph explaining the caps must keep its polarity: a
    reader told the caps need not be armed copies a battery whose token
    wires never fire (adversarial-run survivor, PR #26)."""
    errors = []
    affirmed = False
    for block in prose_blocks(text):
        normalised = re.sub(r"\s+", " ", block)
        if "--cap" not in normalised or not re.search(
            r"\b(?:un)?arm\w*\b", normalised, re.IGNORECASE
        ):
            continue
        if re.search(
            r"\b(?:need\s+not|do(?:es)?\s+not\s+(?:need|have)\s+to)\b"
            r"[^.!?]{0,40}\barm"
            r"|\b(?:can|may)\s+be\s+left\s+unarmed\b"
            r"|\bleft\s+unarmed\b",
            normalised,
            re.IGNORECASE,
        ):
            errors.append("caps prose says arming is optional")
        if re.search(r"\bmust\s+be\s+armed\b", normalised, re.IGNORECASE):
            affirmed = True
    if not affirmed:
        errors.append("no caps paragraph affirms that the caps must be armed")
    return errors


def breaker_flag_errors(text, source):
    invocations = breaker_invocations(text)
    if not invocations:
        return ["no fenced breaker.py invocation"]
    accepted = declared_flags(source)
    if not accepted:
        return ["breaker.py declares no long flags"]

    token_pattern = re.compile(r"(?<![\w-])--[A-Za-z0-9][A-Za-z0-9_-]*")
    errors = []
    for number, invocation in invocations:
        for flag in token_pattern.findall(invocation):
            if flag not in accepted:
                errors.append(f"line {number}: {flag}")
    return errors


_NEGATOR = (
    r"(?:not|never|without|does\s+not|doesn't|will\s+not|won't|cannot|can't|"
    r"must\s+not|mustn't|should\s+not|shouldn't)"
)
_EXIT_THREE = re.compile(
    r"\bexit(?:s|\s+code|\s+status)?\s*(?:with\s+)?3\b",
    re.IGNORECASE,
)
_NEGATED_EXIT_THREE = re.compile(
    (
        rf"\b{_NEGATOR}\b[^.!?;:]{{0,50}}"
        r"\bexit(?:s|\s+code|\s+status)?\s*(?:with\s+)?3\b"
    ),
    re.IGNORECASE,
)
_TRIPPED_FILE_WRITE = re.compile(
    (
        r"\bwrit(?:e|es|ing|ten)\b[^.!?;:]{0,80}\bTRIPPED\s+file\b"
        r"|\bTRIPPED\s+file\b[^.!?;:]{0,50}\bwrit(?:e|es|ing|ten)\b"
    ),
    re.IGNORECASE,
)
_NEGATED_TRIPPED_FILE_WRITE = re.compile(
    (
        rf"\b{_NEGATOR}\b[^.!?;:]{{0,40}}\bwrit(?:e|es|ing|ten)\b"
        r"[^.!?;:]{0,80}\bTRIPPED\s+file\b"
        r"|\bTRIPPED\s+file\b[^.!?;:]{0,50}"
        rf"\b{_NEGATOR}\b[^.!?;:]{{0,30}}\bwrit(?:e|es|ing|ten)\b"
        r"|\bnot\s+the\s+TRIPPED\s+file\b"
    ),
    re.IGNORECASE,
)
_REVIEWER_KILL = re.compile(
    # Direct object only: "kills the wrapper while the reviewer runs on"
    # kills the wrong thing and must not satisfy this.
    r"\bkill(?:s|ed|ing)?\s+(?:the\s+)?(?:runaway\s+)?reviewer\b"
    r"(?!['\u2019]s\b)",
    re.IGNORECASE,
)
_NEGATED_REVIEWER_KILL = re.compile(
    (
        rf"\b{_NEGATOR}\b[^.!?;:]{{0,40}}\bkill(?:s|ed|ing)?\b"
        r"[^.!?;:]{0,80}\b(?:runaway\s+)?reviewer\b"
        r"|\bkill(?:s|ed|ing)?\b[^.!?;:]{0,30}\bno\b"
        r"[^.!?;:]{0,30}\breviewer\b"
    ),
    re.IGNORECASE,
)


def affirms_exit_three(text):
    return bool(_EXIT_THREE.search(text)) and not _NEGATED_EXIT_THREE.search(text)


def affirms_tripped_file_write(text):
    return bool(_TRIPPED_FILE_WRITE.search(text)) and not (
        _NEGATED_TRIPPED_FILE_WRITE.search(text)
    )


def affirms_reviewer_kill(text):
    return bool(_REVIEWER_KILL.search(text)) and not _NEGATED_REVIEWER_KILL.search(
        text
    )


def trip_reporting_errors(text):
    errors = []
    blocks = prose_blocks(text)
    trip_blocks = [
        block for block in blocks if re.search(r"\bA\s+trip\b", block, re.IGNORECASE)
    ]
    if len(trip_blocks) != 1:
        return [f"expected one trip-reporting paragraph, found {len(trip_blocks)}"]

    paragraph = re.sub(r"\s+", " ", trip_blocks[0])
    if not affirms_exit_three(paragraph):
        errors.append("trip paragraph does not affirm exit code 3")
    if not affirms_tripped_file_write(paragraph):
        errors.append("trip paragraph does not affirm that the TRIPPED file is written")
    if not affirms_reviewer_kill(paragraph):
        errors.append("trip paragraph does not affirm that the runaway reviewer is killed")

    affirmative = re.search(
        r"\bname\s+the\s+tripped\s+reviewer\b.{0,120}\bPR\s+body\b",
        paragraph,
        re.IGNORECASE,
    )
    negated = re.search(
        (
            r"\b(?:do\s+not|don't|never|must\s+not|should\s+not)\s+"
            r"name\s+the\s+tripped\s+reviewer\b"
        ),
        paragraph,
        re.IGNORECASE,
    )
    if not affirmative or negated:
        errors.append("trip paragraph lacks an affirmative tripped-reviewer PR rule")

    unreachable_blocks = [
        re.sub(r"\s+", " ", block)
        for block in blocks
        if re.search(r"\bunreach\w*\b", block, re.IGNORECASE)
        and re.search(r"\bPR\s+body\b", block, re.IGNORECASE)
    ]
    unreachable_positive = any(
        re.search(
            (
                r"\bname\s+the\s+(?:missing|unreachable)\s+reviewer\b"
                r".{0,100}\bPR\s+body\b"
            ),
            block,
            re.IGNORECASE,
        )
        and not re.search(
            (
                r"\b(?:do\s+not|don't|never|must\s+not|should\s+not)\s+"
                r"name\s+the\s+(?:missing|unreachable)\s+reviewer\b"
            ),
            block,
            re.IGNORECASE,
        )
        for block in unreachable_blocks
    )
    if not unreachable_positive:
        errors.append("no affirmative unreachable-reviewer PR rule")

    if not any(
        BREAKER_PATH in invocation
        and re.search(r"(?<![\w-])--tripped-file\b", invocation)
        for _, invocation in breaker_invocations(text)
    ):
        errors.append("breaker invocation does not request the TRIPPED file")
    return errors


_ABSENT_TELEMETRY = re.compile(
    # Refuted 2026-08-21: updates.jsonl turn_completed events carry
    # usage. Scoped to clauses whose subject region names grok — a
    # denial about some OTHER subject ("cancelled turns carry no
    # token usage", "events.jsonl carries no token usage") can be
    # true and must stay writable inside grok-discussing blocks.
    r"\bgrok\b[^.!?]{0,60}?"
    r"(?:\b(?:records?|stores?|has|have|keeps?)\s+no"
    r"\s+(?:token\s+usage|spend)\b"
    r"|\bcarr(?:y|ies)\s+no\s+(?:token\s+usage|spend)\b"
    r"|\bdo(?:es)?\s+not\s+(?:record|store|carry)\b[^.!?]{0,30}"
    r"\b(?:token\s+usage|spend)\b)",
    re.IGNORECASE,
)


_STALE_LIMITATION = re.compile(
    # Retired 2026-08-21 late: the battery DOES parse grok usage now.
    # The unsupported-telemetry alternation was dropped: field notes
    # like "reasoningTokens remain unsupported telemetry" are true.
    r"\bnot\s+(?:yet\s+)?parsed?\b"
    r"|\bdoes\s+not\s+(?:yet\s+)?parse\b",
    re.IGNORECASE,
)


_CAP_DENIAL = re.compile(
    # The flip armed --cap/--cap-out for grok; a denial that grok can
    # take or enforce a token cap is the retired cap-promise lie with
    # its polarity reversed. Scanned per sentence, and a sentence that
    # conditions the denial on arming or the zero default
    # (_CAP_CONDITION) is a truth this checker must not touch.
    r"\b(?:has|have)\s+no\s+token\s+caps?\b"
    r"|\btoken\s+caps?\s+(?:are|is)\s+unavailable\b"
    r"|\b(?:cannot|can\s*not|can't)\s+(?:enforce|take|apply|honou?r)\b"
    r"[^.!?]{0,30}\btoken\s+caps?\b"
    r"|\bdo\s+not\s+apply\b[^.!?]{0,30}\btoken\s+caps?\b"
    r"|\bdo(?:es)?\s+not\s+(?:have|take|apply|enforce|honou?r|use|get)\b"
    r"[^.!?]{0,30}\btoken\s+caps?\b"
    r"|\btoken\s+caps?\s+do\s+not\s+apply\b"
    r"|\boperates?\s+without\s+token\s+caps?\b"
    r"|\btokens\s+are\s+not\s+capped\b"
    r"|(?:(?<![\w-])--cap\b|\btoken\s+caps?\b)[^.!?]{0,60}"
    r"\bgrok\s+do(?:es)?\s+not\b"
    r"(?:\s+(?:take|have|apply|enforce|honou?r|use|get)\b[^.!?]{0,20})?"
    r"\s*(?:[.!?;:]|$)",
    re.IGNORECASE,
)


_CAP_CONDITION = re.compile(
    # "no token cap until --cap is armed" and "a zero cap disables
    # that wire" are true statements about the default, not denials.
    # when/while were dropped: "even when --cap is passed" is a lie
    # wearing a condition word.
    r"\b(?:until|unless|before)\b|\bzero\b|\bdefaults?\b",
    re.IGNORECASE,
)


def grok_claim_errors(text):
    """Doc-wide grok claims, refused anywhere grok is discussed —
    including module docstrings the tests feed here: absent-telemetry
    (refuted), the retired not-yet-parsed limitation (stale), and
    token-cap denials whose sentence does not condition them on the
    zero default."""
    errors = []
    for block in prose_blocks(text):
        normalised = re.sub(r"\s+", " ", block)
        if not re.search(r"\bgrok\b", normalised, re.IGNORECASE):
            continue
        if _ABSENT_TELEMETRY.search(normalised):
            errors.append(
                f"absent-telemetry claim (refuted): {normalised[:60]!r}")
        if _STALE_LIMITATION.search(normalised):
            errors.append(
                f"stale not-yet-parsed claim: {normalised[:60]!r}")
        for sentence in re.split(r"(?<=[.!?])\s+", normalised):
            if (_CAP_DENIAL.search(sentence)
                    and not _CAP_CONDITION.search(sentence)):
                errors.append(
                    f"token-cap denial (the caps are armed):"
                    f" {sentence[:60]!r}")
    return errors


# The pinned contract paragraph, normalized exactly as
# grok_gap_errors normalizes the live doc. Update it ONLY
# together with the doc edit it ratifies.
GROK_WIRES_PARAGRAPH = (
    "Grok records its spend (updates.jsonl `turn_completed` "
    "events, measured 2026-08-21) and the battery parses it \u2014 "
    "cumulative per run, runs split when a reported total "
    "shrinks \u2014 so grok streams feed the token walls (`--cap`, "
    "`--cap-out`) like the others. Only repeat-loop and "
    "error-storm stay claude-only: grok streams carry no "
    "tool_use or tool_result records to feed them."
)


def grok_gap_errors(text):
    """Doc-wide grok claims (grok_claim_errors), plus the pinned
    contract: the token-walls paragraph is load-bearing and its
    paraphrase space is unbounded — three skeptic rounds each found
    sentences the previous feature regexes misread in BOTH
    directions. So the paragraph is pinned by EQUALITY: any edit,
    true or false, must update GROK_WIRES_PARAGRAPH here in the same
    change (the doc and the guard flip atomically or not at all)."""
    errors = grok_claim_errors(text)

    wires_blocks = []
    for block in prose_blocks(text):
        normalised = re.sub(r"\s+", " ", block)
        if re.search(r"\bgrok\b", normalised, re.IGNORECASE) and re.search(
            r"\btoken\s+walls?\b", normalised, re.IGNORECASE
        ):
            wires_blocks.append(normalised)
    if len(wires_blocks) != 1:
        errors.append(
            f"expected one grok token-walls paragraph, found {len(wires_blocks)}")
        return errors
    if wires_blocks[0] != GROK_WIRES_PARAGRAPH:
        errors.append(
            "the grok token-walls paragraph diverged from the pinned"
            " contract — update the doc and GROK_WIRES_PARAGRAPH"
            " together, deliberately")
    return errors


class BatteryWiringTests(unittest.TestCase):
    def test_launch_fence_uses_the_safe_plain_background_shape(self):
        text = DOC.read_text(encoding="utf-8")
        errors = launch_form_errors(text)
        self.assertFalse(errors, f"{DOC}: " + "; ".join(errors))

    def test_fenced_recipe_arms_breaker_over_the_discovered_stream(self):
        text = DOC.read_text(encoding="utf-8")
        errors = battery_wiring_errors(text)
        self.assertFalse(errors, f"{DOC}: " + "; ".join(errors))

    def test_fenced_lines_are_joined_before_invocation_scanning(self):
        text = (
            "```sh\n"
            "until STREAM=$(find store -newer marker -printf '%T@ %p\\n' | sort -n | tail -1 | cut -f2-); "
            '[ -n "$STREAM" ] || ! kill -0 "$RPID"; do :; done\n'
            f'[ -n "$STREAM" ] && python3 {BREAKER_PATH} $STREAM \\\n'
            "  --stall 10 \\\n"
            '  --pid "$RPID" --cap 1 --cap-out 1 --terminate --tripped-file TRIPPED.md\n'
            "```\n"
        )
        self.assertEqual(
            fenced_logical_lines(text),
            [
                (
                    2,
                    (
                        "until STREAM=$(find store -newer marker -printf '%T@ %p\\n' | sort -n | tail -1 | cut -f2-); "
                        '[ -n "$STREAM" ] || ! kill -0 "$RPID"; do :; done'
                    ),
                ),
                (
                    3,
                    (
                        f'[ -n "$STREAM" ] && python3 {BREAKER_PATH} $STREAM --stall 10 '
                        '--pid "$RPID" --cap 1 --cap-out 1 --terminate --tripped-file TRIPPED.md'
                    ),
                ),
            ],
        )
        self.assertFalse(battery_wiring_errors(text))


class LaunchFormUnitTests(unittest.TestCase):
    def test_safe_launch_is_accepted(self):
        text = (
            "```sh\n"
            'cd "$SNAP"\n'
            "touch launched\n"
            "nohup grok --prompt-file prompt.txt > review.txt 2> review.err &\n"
            "RPID=$!\n"
            "```\n"
        )
        self.assertFalse(launch_form_errors(text))

    def test_unsafe_launch_forms_are_rejected(self):
        unsafe = (
            (
                "prompt substitution",
                (
                    "```sh\n"
                    "touch launched\n"
                    'nohup grok "$(cat prompt.txt)" > review.txt &\n'
                    "```\n"
                ),
            ),
            (
                "compound background list",
                (
                    "```sh\n"
                    "touch launched\n"
                    'cd "$SNAP" && nohup grok < prompt.txt > review.txt &\n'
                    "```\n"
                ),
            ),
            (
                "late launch marker",
                (
                    "```sh\n"
                    "nohup grok < prompt.txt > review.txt &\n"
                    "touch launched\n"
                    "```\n"
                ),
            ),
        )
        for label, text in unsafe:
            with self.subTest(label=label):
                self.assertTrue(
                    launch_form_errors(text),
                    f"unsafe launch form survived: {label}",
                )


class BatteryWiringUnitTests(unittest.TestCase):
    def test_nonzero_integer_flag_requires_one_literal_nonzero_value(self):
        self.assertTrue(carries_nonzero_integer_flag("breaker --cap 12", "--cap"))
        invalid = (
            "breaker",
            "breaker --cap 0",
            'breaker --cap "0"',
            'breaker --cap "$CAP"',
            "breaker --cap 1 --cap 2",
        )
        for invocation in invalid:
            with self.subTest(invocation=invocation):
                self.assertFalse(
                    carries_nonzero_integer_flag(invocation, "--cap")
                )

    def test_discovery_liveness_probe_requires_kill_zero_on_reviewer_pid(self):
        self.assertTrue(
            discovery_loop_has_kill_zero(
                "until STREAM=$(find store -newer launched -printf '%T@ %p\\n' | sort -n | tail -1 | cut -f2-); "
                '[ -n "$STREAM" ] || ! kill -0 "$RPID"; do :; done'
            )
        )
        self.assertFalse(
            discovery_loop_has_kill_zero(
                "until STREAM=$(find store -newer launched -printf '%T@ %p\\n' | sort -n | tail -1 | cut -f2-); "
                '[ -n "$STREAM" ] || ! kill -9 "$RPID"; do :; done'
            )
        )

    def test_battery_contract_rejects_zero_caps_and_missing_probe(self):
        valid = (
            "```sh\n"
            "until STREAM=$(find store -newer launched -printf '%T@ %p\\n' | sort -n | tail -1 | cut -f2-); "
            '[ -n "$STREAM" ] || ! kill -0 "$RPID"; do :; done\n'
            f'[ -n "$STREAM" ] && python3 {BREAKER_PATH} "$STREAM" --pid "$RPID" '
            "--cap 1 --cap-out 1 --terminate --tripped-file TRIPPED.md\n"
            "```\n"
        )
        self.assertFalse(battery_wiring_errors(valid))
        mutants = (
            valid.replace("--cap 1", "--cap 0"),
            valid.replace("--cap-out 1", "--cap-out 0"),
            valid.replace("kill -0", "kill -9"),
        )
        for mutant in mutants:
            with self.subTest(mutant=mutant):
                self.assertTrue(battery_wiring_errors(mutant))


class BreakerFlagContractTests(unittest.TestCase):
    def test_documented_breaker_flags_are_declared_by_the_cli(self):
        text = DOC.read_text(encoding="utf-8")
        errors = breaker_flag_errors(text, BREAKER.read_text(encoding="utf-8"))
        self.assertFalse(
            errors,
            "cross-review recipe uses flags breaker.py does not declare: "
            + ", ".join(errors),
        )

    def test_ast_flag_discovery_accepts_short_first_groups_and_raw_strings(self):
        source = (
            "parser.add_argument('-s', '--stall')\n"
            "group = parser.add_argument_group('process')\n"
            "group.add_argument(r'--pid')\n"
            "other.add_argument('--size-mb', dest='size_mb')\n"
        )
        self.assertEqual(
            declared_flags(source),
            {"--stall", "--pid", "--size-mb"},
        )


class TripReportingContractTests(unittest.TestCase):
    def test_trips_are_evidenced_and_named_in_the_pr_body(self):
        text = DOC.read_text(encoding="utf-8")
        errors = trip_reporting_errors(text)
        self.assertFalse(
            errors,
            f"{DOC}: trip reporting contract is incomplete: " + ", ".join(errors),
        )


class TripPolarityUnitTests(unittest.TestCase):
    def test_affirmative_trip_statements_are_recognised(self):
        paragraph = (
            "A trip is exit code 3. The battery writes the TRIPPED file and "
            "kills the runaway reviewer."
        )
        self.assertTrue(affirms_exit_three(paragraph))
        self.assertTrue(affirms_tripped_file_write(paragraph))
        self.assertTrue(affirms_reviewer_kill(paragraph))

    def test_negated_trip_statements_are_not_affirmative(self):
        cases = (
            (
                "A trip is not exit code 3.",
                affirms_exit_three,
            ),
            (
                "The battery never writes the TRIPPED file.",
                affirms_tripped_file_write,
            ),
            (
                "The battery never kills the runaway reviewer.",
                affirms_reviewer_kill,
            ),
        )
        for sentence, predicate in cases:
            with self.subTest(sentence=sentence):
                self.assertFalse(predicate(sentence))


class HarnessStreamContractTests(unittest.TestCase):
    def test_each_reviewer_row_pairs_its_stream_and_wires(self):
        text = DOC.read_text(encoding="utf-8")
        errors = wire_table_errors(text)
        self.assertFalse(
            errors,
            f"{DOC}: reviewer stream wiring is missing or ambiguous: "
            + ", ".join(errors),
        )


class WireSetUnitTests(unittest.TestCase):
    def reviewer_table(self, claude="all six", codex=None, grok=None):
        if grok is None:
            grok = "tokens, tokens-out, stall, size"
        if codex is None:
            codex = "tokens, tokens-out, stall, size"
        rows = (
            "| reviewer | store root / pattern for the stream | wires |",
            "|:--|:--|:--|",
            CLAUDE_ROW.replace("| all six |", f"| {claude} |"),
            CODEX_ROW.replace(
                "| tokens, tokens-out, stall, size |",
                f"| {codex} |",
            ),
            GROK_ROW.replace(
                "| tokens, tokens-out, stall, size |", f"| {grok} |"),
        )
        return "\n".join(rows)

    def test_each_reviewer_wire_set_rejects_subsets_and_supersets(self):
        self.assertFalse(wire_table_errors(self.reviewer_table()))
        mutants = (
            ("Claude subset", {"claude": "stall, size"}),
            ("Claude superset", {"claude": "all six, rate"}),
            ("Codex subset", {"codex": "tokens, tokens-out, stall"}),
            (
                "Codex superset",
                {"codex": "tokens, tokens-out, stall, size, rate"},
            ),
            ("Grok subset", {"grok": "tokens, stall, size"}),
            ("Grok superset", {"grok": "all six"}),
        )
        for label, changes in mutants:
            with self.subTest(label=label):
                self.assertTrue(
                    wire_table_errors(self.reviewer_table(**changes)),
                    f"wire-set mutant survived: {label}",
                )



class GrokTelemetryGapTests(unittest.TestCase):
    def test_grok_gap_is_stated_without_promising_token_caps(self):
        text = DOC.read_text(encoding="utf-8")
        errors = grok_gap_errors(text)
        self.assertFalse(
            errors,
            f"{DOC}: Grok telemetry gap is not stated honestly: "
            + " | ".join(errors),
        )


class GrokClaimNetUnitTests(unittest.TestCase):
    """The doc-wide nets are best-effort regexes over unbounded
    paraphrase space: this corpus documents what they catch and what
    they deliberately leave alone. The load-bearing grok claims are
    NOT guarded here — they live in the equality-pinned paragraph
    (GrokParagraphPinTests), the wire table, and the armed line."""

    CARRIER = (
        "Grok records its spend and the battery parses it, so grok "
        "streams feed the token walls. Only repeat-loop and "
        "error-storm stay claude-only."
    )

    def test_refuted_and_stale_and_denial_claims_fire(self):
        lies = (
            self.CARRIER.replace("records its spend", "records no spend"),
            self.CARRIER + " Grok sessions have no token usage.",
            self.CARRIER + " Grok carries no spend.",
            self.CARRIER + " The battery does not yet parse that shape.",
            self.CARRIER + " Grok usage is not parsed.",
            self.CARRIER + " Grok has no token cap.",
            self.CARRIER + " Grok cannot enforce a token cap.",
            self.CARRIER + " Grok token caps are unavailable.",
            self.CARRIER + " Do not apply a token cap to Grok.",
            self.CARRIER + " Claude uses --cap; Grok does not.",
            self.CARRIER + " Grok does not have a token cap.",
            self.CARRIER + " Token caps do not apply to grok.",
            self.CARRIER + " Grok operates without token caps.",
            self.CARRIER + " Grok's tokens are not capped.",
            self.CARRIER + " Grok has no token cap even when --cap"
                           " is passed.",
        )
        for lie in lies:
            with self.subTest(lie=lie[-60:]):
                self.assertNotEqual(lie, self.CARRIER)
                self.assertTrue(grok_claim_errors(lie))

    def test_true_claims_stay_quiet(self):
        truths = (
            self.CARRIER + " Grok's token cap trips like the others.",
            self.CARRIER + " Grok has no token cap until --cap is"
                           " armed.",
            self.CARRIER + " Grok cannot enforce a token cap of zero.",
            self.CARRIER + " Grok token caps are unavailable until"
                           " armed.",
            self.CARRIER + " Claude uses --cap; Grok does not skip it.",
            self.CARRIER + " Cancelled turns carry no token usage.",
            self.CARRIER + " events.jsonl carries no token usage.",
            self.CARRIER + " reasoningTokens remain unsupported"
                           " telemetry.",
        )
        for truth in truths:
            with self.subTest(truth=truth[-60:]):
                self.assertFalse(grok_claim_errors(truth))


class GrokParagraphPinTests(unittest.TestCase):
    def test_the_live_paragraph_matches_the_pinned_contract(self):
        self.assertFalse(grok_gap_errors(DOC.read_text(encoding="utf-8")))

    def test_any_edit_to_the_paragraph_is_refused(self):
        live = DOC.read_text(encoding="utf-8")
        anchor = "so grok streams feed the token walls"
        self.assertEqual(live.count(anchor), 1)
        edits = (
            live.replace(anchor, anchor + " generously"),
            live.replace(anchor, "so grok streams never feed the"
                                 " token walls"),
            live.replace("stay claude-only:", "stay claude-only,"
                         " though repeat-loop can fire for every"
                         " reviewer:"),
            live.replace("like the others. Only",
                         "like the others. All six wires fire for"
                         " every reviewer. Only"),
        )
        for mutated in edits:
            with self.subTest(edit=mutated[:40]):
                self.assertNotEqual(mutated, live)
                self.assertTrue(grok_gap_errors(mutated))


class RecipeFencePinTests(unittest.TestCase):
    def test_the_live_fence_matches_the_pinned_contract(self):
        self.assertFalse(
            recipe_fence_errors(DOC.read_text(encoding="utf-8")))

    def test_any_one_sided_fence_edit_is_refused(self):
        live = DOC.read_text(encoding="utf-8")
        edits = (
            ("PATTERN=updates.jsonl",
             "declare -x PATTERN=events.jsonl"),
            ("PATTERN=updates.jsonl",
             "PATTERN=updates.jsonl && PATTERN=events.jsonl"),
            ('-name "$PATTERN"', "-iname events.jsonl"),
            ('-name "$PATTERN"',
             '\\( -name "$PATTERN" -o -name events.jsonl \\)'),
            ('-name "$PATTERN"', "-path '*/events.jsonl'"),
        )
        for anchor, replacement in edits:
            with self.subTest(edit=replacement[:40]):
                self.assertEqual(live.count(anchor) >= 1, True, anchor)
                mutated = live.replace(anchor, replacement, 1)
                self.assertNotEqual(mutated, live)
                self.assertTrue(recipe_fence_errors(mutated))


class BreakerDocstringClaimTests(unittest.TestCase):
    def test_breaker_docstring_carries_no_refuted_grok_claims(self):
        module = ast.parse(BREAKER.read_text(encoding="utf-8"))
        doc = ast.get_docstring(module) or ""
        self.assertTrue(doc, "breaker.py has no module docstring")
        self.assertFalse(
            grok_claim_errors(doc),
            "breaker.py's own docstring makes a refuted grok claim")

    def test_a_reverted_not_yet_parsed_docstring_is_refused(self):
        plant = ("Grok's usage records are not yet parsed by this"
                 " battery; grok rows gate on stall and size only.")
        self.assertTrue(grok_claim_errors(plant))


class SkepticDocMutantBatteryTests(unittest.TestCase):
    def plant(self, label, anchor, replacement):
        text = DOC.read_text(encoding="utf-8")
        matches = text.count(anchor)
        self.assertEqual(
            matches,
            1,
            f"INVALID {label}: anchor matched {matches} times, expected exactly once",
        )
        mutated = text.replace(anchor, replacement)
        self.assertNotEqual(mutated, text, f"INVALID {label}: mutation did not land")
        return mutated

    def assert_rejected(self, label, errors):
        self.assertTrue(errors, f"{label} SURVIVED: relevant guard accepted mutant")

    def insert_before_trip(self, label, sentence):
        anchor = "\nA trip is exit code 3:"
        return self.plant(
            label,
            anchor,
            f"\n{sentence}\n\nA trip is exit code 3:",
        )

    def test_m1_continuation_flags_are_scanned(self):
        anchor = (
            "  --cap 2000000 --cap-out 150000 --stall 600 --size-mb 50 "
            "--terminate \\\n"
            "  --tripped-file TRIPPED.md"
        )
        replacement = (
            "  --cap 2000000 --cap-out 100000 --not-a-flag 600 --bogus-mb 50 "
            "--kill-it \\\n"
            "  --tripped TRIPPED.md"
        )
        mutated = self.plant("M1 continuation flags", anchor, replacement)
        self.assert_rejected(
            "M1 continuation flags",
            breaker_flag_errors(mutated, BREAKER.read_text(encoding="utf-8")),
        )

    def test_m2_swapped_reviewer_paths_are_rejected(self):
        anchor = f"{CLAUDE_ROW}\n{CODEX_ROW}\n{GROK_ROW}"
        replacement = "\n".join(
            (
                CLAUDE_ROW.replace("~/.claude/projects", "~/.codex/sessions"),
                CODEX_ROW.replace("~/.codex/sessions", "~/.grok/sessions"),
                GROK_ROW.replace("~/.grok/sessions", "~/.claude/projects"),
            )
        )
        mutated = self.plant("M2 swapped paths", anchor, replacement)
        self.assert_rejected("M2 swapped paths", wire_table_errors(mutated))

    def test_m3_claude_basename_slug_rule_is_rejected(self):
        mutated = self.plant(
            "M3 Claude slug",
            "where `<slug>` is `$SNAP` with every `/` and `.` replaced by `-`",
            "where `<slug>` is the basename of `$SNAP`",
        )
        self.assert_rejected("M3 Claude slug", wire_table_errors(mutated))

    def test_m42_feed_negation_in_the_live_doc_is_rejected(self):
        mutated = self.plant(
            "M42 feed negated",
            "so grok streams feed the token walls",
            "so grok streams never feed the token walls",
        )
        self.assert_rejected("M42 feed negated", grok_gap_errors(mutated))

    def test_m43_claude_only_contradiction_in_the_live_doc_is_rejected(self):
        mutated = self.plant(
            "M43 pair contradicted",
            "error-storm stay claude-only: grok streams carry no tool_use or",
            "error-storm stay claude-only, though repeat-loop can also fire"
            " for grok: grok streams carry no tool_use or",
        )
        self.assert_rejected(
            "M43 pair contradicted", grok_gap_errors(mutated))

    def test_m44_cap_denial_in_the_live_doc_is_rejected(self):
        mutated = self.plant(
            "M44 cap denial",
            "like the others. Only",
            "like the others. Grok has no token cap. Only",
        )
        self.assert_rejected("M44 cap denial", grok_gap_errors(mutated))

    def test_m45_disable_tokens_on_the_armed_line_is_rejected(self):
        mutated = self.plant(
            "M45 disable tokens",
            "--tripped-file TRIPPED.md 2>> breaker.log &",
            "--disable tokens --tripped-file TRIPPED.md 2>> breaker.log &",
        )
        self.assert_rejected(
            "M45 disable tokens", battery_wiring_errors(mutated))

    def test_m46_recipe_pattern_swap_is_rejected(self):
        mutated = self.plant(
            "M46 recipe pattern",
            "PATTERN=updates.jsonl",
            "PATTERN=events.jsonl",
        )
        self.assert_rejected(
            "M46 recipe pattern", battery_wiring_errors(mutated))

    def test_m47_alternative_stream_offer_is_rejected(self):
        mutated = self.plant(
            "M47 alternative stream",
            "| Grok | `updates.jsonl` in",
            "| Grok | `updates.jsonl` (or `session.jsonl`) in",
        )
        self.assert_rejected(
            "M47 alternative stream", wire_table_errors(mutated))

    def test_m48_no_longer_feed_in_the_live_doc_is_rejected(self):
        mutated = self.plant(
            "M48 no-longer-feed",
            "so grok streams feed the token walls",
            "so grok streams no longer feed the token walls",
        )
        self.assert_rejected(
            "M48 no-longer-feed", grok_gap_errors(mutated))

    def test_m49_pattern_swap_survives_assignment_reordering(self):
        anchor = ("STORE=~/.grok/sessions    # store root and stream"
                  " pattern for this\nPATTERN=updates.jsonl")
        text = DOC.read_text(encoding="utf-8")
        self.assertEqual(text.count(anchor), 1,
                         "the recipe assignment anchor moved")
        mutated = text.replace(
            anchor,
            "PATTERN=events.jsonl\nSTORE=~/.grok/sessions    # store"
            " root and stream pattern for this")
        self.assertNotEqual(mutated, text)
        self.assert_rejected(
            "M49 reordered pattern swap", battery_wiring_errors(mutated))

    def test_m50_negated_stream_mention_is_a_warning_not_an_offer(self):
        mutated = self.plant(
            "M50 negated mention",
            "| Grok | `updates.jsonl` in",
            "| Grok | `updates.jsonl` (not `events.jsonl`) in",
        )
        self.assertFalse(
            wire_table_errors(mutated),
            "a negated mention is a true warning and must stay quiet")

    def test_m51_inlined_discovery_filename_is_rejected(self):
        mutated = self.plant(
            "M51 inlined -name",
            'find "$STORE" -name "$PATTERN" -newer launched',
            'find "$STORE" -name events.jsonl -newer launched',
        )
        self.assert_rejected(
            "M51 inlined -name", battery_wiring_errors(mutated))

    def test_m52_export_prefixed_pattern_swap_is_rejected(self):
        mutated = self.plant(
            "M52 export pattern",
            "PATTERN=updates.jsonl",
            "export PATTERN=events.jsonl",
        )
        self.assert_rejected(
            "M52 export pattern", battery_wiring_errors(mutated))

    def test_m41_events_jsonl_token_promise_is_rejected(self):
        mutated = self.plant(
            "M41 Grok events alternative",
            "| Grok | `updates.jsonl` in",
            "| Grok | `updates.jsonl` (or `events.jsonl`) in",
        )
        self.assert_rejected(
            "M41 Grok events alternative", wire_table_errors(mutated))

    def test_m4_grok_all_six_wires_are_rejected(self):
        mutated = self.plant(
            "M4 Grok all six",
            GROK_ROW,
            GROK_ROW.replace(
                "| tokens, tokens-out, stall, size |", "| all six |"),
        )
        self.assert_rejected("M4 Grok all six", wire_table_errors(mutated))

    def test_m5_breaker_without_stream_discovery_is_rejected(self):
        anchor = (
            'STORE=~/.grok/sessions    # store root and stream pattern for this\n'
            'PATTERN=updates.jsonl     # reviewer\'s harness — see the table below\n'
            'until STREAM=$(find "$STORE" -name "$PATTERN" -newer launched \\\n'
            "        -printf '%T@ %p\\n' | sort -n | tail -1 | cut -d' ' -f2-); \\\n"
            '      [ -n "$STREAM" ] || ! kill -0 "$RPID"; do sleep 2; done\n'
            '[ -n "$STREAM" ] && python3 ops/devlane/telemetry/breaker.py "$STREAM" '
            '--pid "$RPID" \\\n'
            '  --cap 2000000 --cap-out 150000 --stall 600 --size-mb 50 '
            '--terminate \\\n'
            '  --tripped-file TRIPPED.md 2>> breaker.log &'
        )
        replacement = (
            "python3 ops/devlane/telemetry/breaker.py /dev/null --pid "
            '"$RPID" --stall 600'
        )
        mutated = self.plant("M5 drop find", anchor, replacement)
        self.assert_rejected("M5 drop find", battery_wiring_errors(mutated))

    def test_m10_negated_trip_reporting_rule_is_rejected(self):
        mutated = self.plant(
            "M10 inverted trip rule",
            "finish: name the tripped reviewer in the PR body",
            "finish: do not name the tripped reviewer in the PR body",
        )
        self.assert_rejected(
            "M10 inverted trip rule", trip_reporting_errors(mutated)
        )

    def test_m14_claude_wrong_glob_is_rejected(self):
        mutated = self.plant(
            "M14 Claude glob",
            CLAUDE_ROW,
            CLAUDE_ROW.replace("`*.jsonl`", "`*.log`"),
        )
        self.assert_rejected("M14 Claude glob", wire_table_errors(mutated))

    def test_m15_codex_wrong_pattern_is_rejected(self):
        mutated = self.plant(
            "M15 Codex pattern",
            CODEX_ROW,
            CODEX_ROW.replace(
                "~/.codex/sessions/*/*/*/rollout-*.jsonl",
                "~/.codex/sessions/*.json",
            ),
        )
        self.assert_rejected("M15 Codex pattern", wire_table_errors(mutated))

    def test_m16_grok_without_url_encoding_is_rejected(self):
        mutated = self.plant(
            "M16 Grok encoding",
            GROK_ROW,
            GROK_ROW.replace("<url-encoded $SNAP>", "$SNAP"),
        )
        self.assert_rejected("M16 Grok encoding", wire_table_errors(mutated))

    def test_wrapped_pid_remains_a_valid_logical_invocation(self):
        anchor = (
            'python3 ops/devlane/telemetry/breaker.py "$STREAM" --pid "$RPID" \\\n'
            "  --cap 2000000"
        )
        replacement = (
            'python3 ops/devlane/telemetry/breaker.py "$STREAM" \\\n'
            '  --pid "$RPID" --cap 2000000'
        )
        mutated = self.plant("M17 wrapped pid variant", anchor, replacement)
        errors = battery_wiring_errors(mutated)
        self.assertFalse(
            errors,
            "valid wrapped --pid invocation was rejected: " + ", ".join(errors),
        )
        self.assertFalse(
            breaker_flag_errors(mutated, BREAKER.read_text(encoding="utf-8"))
        )

    def test_m20_claude_stall_only_wires_are_rejected(self):
        mutated = self.plant(
            "M20 Claude wires",
            CLAUDE_ROW,
            CLAUDE_ROW.replace("| all six |", "| stall |"),
        )
        self.assert_rejected("M20 Claude wires", wire_table_errors(mutated))

    def test_m21_missing_pid_is_rejected(self):
        mutated = self.plant(
            "M21 missing pid",
            'breaker.py "$STREAM" --pid "$RPID" \\',
            'breaker.py "$STREAM" \\',
        )
        self.assert_rejected("M21 missing pid", battery_wiring_errors(mutated))

    def test_m23_trip_prose_without_tripped_file_is_rejected(self):
        mutated = self.plant(
            "M23 trip prose",
            "the battery prints its evidence to stderr, writes\n"
            "the TRIPPED file named by `--tripped-file` into the snapshot, and with",
            "the battery prints its evidence to stderr, and with",
        )
        self.assert_rejected("M23 trip prose", trip_reporting_errors(mutated))

    def test_m26_missing_trip_exit_code_is_rejected(self):
        mutated = self.plant(
            "M26 exit code",
            "A trip is exit code 3:",
            "A trip is a nonzero exit:",
        )
        self.assert_rejected("M26 exit code", trip_reporting_errors(mutated))

    def test_m27_recipe_without_tripped_file_flag_is_rejected(self):
        mutated = self.plant(
            "M27 tripped flag",
            "  --tripped-file TRIPPED.md 2>> breaker.log &",
            "  2>> breaker.log &",
        )
        self.assert_rejected("M27 tripped flag", trip_reporting_errors(mutated))

    def test_m28_prompt_substitution_in_launch_is_rejected(self):
        mutated = self.plant(
            "M28 prompt substitution",
            "nohup grok --prompt-file prompt.txt",
            'nohup grok "$(cat prompt.txt)"',
        )
        self.assert_rejected("M28 prompt substitution", launch_form_errors(mutated))

    def test_m29_compound_launch_is_rejected(self):
        mutated = self.plant(
            "M29 compound launch",
            "nohup grok --prompt-file prompt.txt",
            'cd "$SNAP" && nohup grok --prompt-file prompt.txt',
        )
        self.assert_rejected("M29 compound launch", launch_form_errors(mutated))

    def test_m30_late_launch_marker_is_rejected(self):
        anchor = (
            "touch launched  # marker: the supervisor finds the stream this launch opens\n"
            "nohup grok --prompt-file prompt.txt --output-format plain --max-turns 40 \\"
        )
        replacement = (
            "nohup grok --prompt-file prompt.txt --output-format plain --max-turns 40 \\\n"
            "touch launched  # marker: the supervisor finds the stream this launch opens"
        )
        mutated = self.plant("M30 late marker", anchor, replacement)
        self.assert_rejected("M30 late marker", launch_form_errors(mutated))

    def test_m31_zero_input_cap_is_rejected(self):
        mutated = self.plant(
            "M31 zero cap",
            "--cap 2000000",
            "--cap 0",
        )
        self.assert_rejected("M31 zero cap", battery_wiring_errors(mutated))

    def test_m32_discovery_without_kill_zero_is_rejected(self):
        mutated = self.plant(
            "M32 kill mode",
            'kill -0 "$RPID"',
            'kill -9 "$RPID"',
        )
        self.assert_rejected("M32 kill mode", battery_wiring_errors(mutated))

    def test_m33_codex_wire_subset_is_rejected(self):
        mutated = self.plant(
            "M33 Codex subset",
            CODEX_ROW,
            CODEX_ROW.replace(", size |", " |"),
        )
        self.assert_rejected("M33 Codex subset", wire_table_errors(mutated))

    def test_m34_codex_wire_superset_is_rejected(self):
        mutated = self.plant(
            "M34 Codex superset",
            CODEX_ROW,
            CODEX_ROW.replace("stall, size |", "stall, size, rate |"),
        )
        self.assert_rejected("M34 Codex superset", wire_table_errors(mutated))

    def test_m37_negated_exit_three_is_rejected(self):
        mutated = self.plant(
            "M37 exit polarity",
            "A trip is exit code 3:",
            "A trip is not exit code 3:",
        )
        self.assert_rejected("M37 exit polarity", trip_reporting_errors(mutated))

    def test_m38_negated_tripped_file_write_is_rejected(self):
        mutated = self.plant(
            "M38 TRIPPED polarity",
            "the battery prints its evidence to stderr, writes\n"
            "the TRIPPED file",
            "the battery prints its evidence to stderr, never writes\n"
            "the TRIPPED file",
        )
        self.assert_rejected(
            "M38 TRIPPED polarity", trip_reporting_errors(mutated)
        )

    def test_m39_negated_kill_semantics_are_rejected(self):
        mutated = self.plant(
            "M39 kill polarity",
            "it kills the runaway reviewer",
            "it never kills the runaway reviewer",
        )
        self.assert_rejected("M39 kill polarity", trip_reporting_errors(mutated))


class RoundThreeSkepticShapeTests(unittest.TestCase):
    """The round-3 skeptic bypasses and false-fails, pinned as units."""

    GAP = (
        "Grok records spend the battery does not yet parse, so only the\n"
        "vendor-agnostic wires (stall, size) can fire for it — supervision\n"
        "still catches the hour-long hang.\n"
    )

    def test_kill_takes_the_reviewer_as_direct_object(self):
        self.assertTrue(affirms_reviewer_kill("it kills the runaway reviewer"))
        self.assertFalse(affirms_reviewer_kill(
            "kills the wrapper while the reviewer runs on"))
        self.assertFalse(affirms_reviewer_kill(
            "kills the wrapper, not the reviewer"))
        self.assertFalse(affirms_reviewer_kill(
            "never kills the runaway reviewer"))

    def test_tripped_file_contrast_negation_is_caught(self):
        self.assertTrue(affirms_tripped_file_write(
            "writes the TRIPPED file named by --tripped-file"))
        self.assertFalse(affirms_tripped_file_write(
            "writes the log, not the TRIPPED file"))

    def test_comment_mentions_of_cat_are_not_launch_errors(self):
        safe = (
            "```sh\n"
            'cd "$SNAP"\n'
            "touch launched\n"
            "nohup grok --prompt-file prompt.txt > review.txt 2> review.err &\n"
            'RPID=$!\n'
            '# prompt via stdin, never "$(cat prompt.txt)":\n'
            "```\n"
        )
        self.assertFalse(launch_form_errors(safe))
        unsafe = safe.replace(
            "--prompt-file prompt.txt", '"$(cat prompt.txt)"')
        self.assertNotEqual(unsafe, safe)
        self.assertTrue(launch_form_errors(unsafe))

    def test_possessive_reviewer_is_not_the_kill_object(self):
        self.assertFalse(affirms_reviewer_kill("kills the reviewer's wrapper"))

    def test_guarded_dev_null_once_and_dropped_rpid_are_rotted(self):
        text = DOC.read_text(encoding="utf-8")
        devnull = text.replace('breaker.py "$STREAM" --pid', "breaker.py /dev/null --pid", 1)
        self.assertNotEqual(devnull, text)
        self.assertTrue(battery_wiring_errors(devnull))
        onceshot = text.replace(' --pid "$RPID"', ' --once --pid "$RPID"', 1)
        self.assertNotEqual(onceshot, text)
        self.assertTrue(battery_wiring_errors(onceshot))
        no_pid_capture = text.replace("RPID=$!", "true", 1)
        self.assertNotEqual(no_pid_capture, text)
        self.assertTrue(launch_form_errors(no_pid_capture))

    def test_commented_pid_and_probe_do_not_count(self):
        text = DOC.read_text(encoding="utf-8")
        hidden_pid = text.replace(
            '--pid "$RPID" \\\n', '\\\n', 1).replace(
            "2>> breaker.log &", '2>> breaker.log & # --pid "$RPID"', 1)
        self.assertNotEqual(hidden_pid, text)
        self.assertTrue(battery_wiring_errors(hidden_pid))
        hidden_probe = text.replace(
            ' || ! kill -0 "$RPID"', "", 1).replace(
            "do sleep 2; done", 'do sleep 2; done # kill -0 "$RPID"', 1)
        self.assertNotEqual(hidden_probe, text)
        self.assertTrue(battery_wiring_errors(hidden_probe))

    def test_launch_must_enter_the_snapshot_first(self):
        text = DOC.read_text(encoding="utf-8")
        no_cd = text.replace('cd "$SNAP"', "true", 1)
        self.assertNotEqual(no_cd, text)
        self.assertTrue(launch_form_errors(no_cd))

    def test_tripped_file_must_keep_evidence_and_resist_comments(self):
        text = DOC.read_text(encoding="utf-8")
        devnull = text.replace(
            "--tripped-file TRIPPED.md", "--tripped-file /dev/null", 1)
        self.assertNotEqual(devnull, text)
        self.assertTrue(battery_wiring_errors(devnull))
        hidden = text.replace(
            " --tripped-file TRIPPED.md", "", 1).replace(
            "2>> breaker.log &", "2>> breaker.log & # --tripped-file TRIPPED.md", 1)
        self.assertNotEqual(hidden, text)
        self.assertTrue(battery_wiring_errors(hidden))

    def test_armed_line_must_follow_the_discovery_wait(self):
        text = DOC.read_text(encoding="utf-8")
        lines = text.splitlines(keepends=True)
        starts = [i for i, l in enumerate(lines)
                  if l.startswith('[ -n "$STREAM" ] && python3')]
        self.assertEqual(len(starts), 1, "armed-line anchor moved")
        start = starts[0]
        end = start + 1
        while lines[end - 1].rstrip("\n").endswith("\\"):
            end += 1
        armed_block = lines[start:end]
        until = next(i for i, l in enumerate(lines) if l.startswith("until "))
        self.assertLess(until, start, "fixture assumption broke")
        reordered = (lines[:until] + armed_block
                     + lines[until:start] + lines[end:])
        mutated = "".join(reordered)
        self.assertNotEqual(mutated, text)
        self.assertTrue(battery_wiring_errors(mutated))

    def test_first_match_and_oldest_match_discovery_are_rotted(self):
        text = DOC.read_text(encoding="utf-8")
        pipeline = "-printf '%T@ %p\\n' | sort -n | tail -1 | cut -d' ' -f2-"
        self.assertEqual(text.count(pipeline), 1, "pipeline anchor moved")
        for wrong in ("-print -quit", ("-printf '%T@ %p\\n' | sort -n "
                      "| head -1 | cut -d' ' -f2-"), "-print | head -1"):
            with self.subTest(wrong=wrong):
                mutated = text.replace(pipeline, wrong, 1)
                self.assertNotEqual(mutated, text)
                self.assertTrue(battery_wiring_errors(mutated))

    def test_absent_telemetry_claim_is_refused_doc_wide(self):
        text = DOC.read_text(encoding="utf-8")
        preceding = text.replace(
            "Grok records its spend",
            "Grok records no token usage anywhere in its store.\n\n"
            "Grok records its spend", 1)
        self.assertNotEqual(preceding, text)
        self.assertTrue(grok_gap_errors(preceding))
        stale = text.replace(
            "like the others.",
            "like the others. The battery does not yet parse that shape.", 1)
        self.assertNotEqual(stale, text)
        self.assertTrue(grok_gap_errors(stale))
        no_spend = text.replace("Grok records its spend",
                                "Grok records no spend", 1)
        self.assertNotEqual(no_spend, text)
        self.assertTrue(grok_gap_errors(no_spend))

    def test_caps_optionality_is_caught_in_any_block(self):
        text = DOC.read_text(encoding="utf-8")
        later = text.replace(
            "Which stream, and which wires can fire",
            "In practice --cap can be left unarmed.\n\n"
            "Which stream, and which wires can fire", 1)
        self.assertNotEqual(later, text)
        self.assertTrue(caps_prose_errors(later))

    def test_caps_prose_keeps_its_polarity(self):
        text = DOC.read_text(encoding="utf-8")
        self.assertFalse(caps_prose_errors(text))
        mutated = text.replace("must be armed explicitly",
                               "need not be armed explicitly", 1)
        self.assertNotEqual(mutated, text)
        self.assertTrue(caps_prose_errors(mutated))

    def test_launch_must_end_backgrounded(self):
        text = DOC.read_text(encoding="utf-8")
        mutated = text.replace("> review.txt 2> review.err &",
                               "> review.txt 2> review.err", 1)
        self.assertNotEqual(mutated, text)
        self.assertTrue(launch_form_errors(mutated))

    def test_discovery_handoff_polarity_is_pinned(self):
        text = DOC.read_text(encoding="utf-8")
        flips = (
            ("|| ! kill -0", "|| kill -0"),
            ("|| ! kill -0", "&& ! kill -0"),
            ('[ -n "$STREAM" ] || ! kill', '[ -z "$STREAM" ] || ! kill'),
            ("until STREAM=$(find", "while STREAM=$(find"),
            ('[ -n "$STREAM" ] && python3', '[ -z "$STREAM" ] && python3'),
        )
        for old, new in flips:
            with self.subTest(flip=new):
                mutated = text.replace(old, new, 1)
                self.assertNotEqual(mutated, text, old)
                self.assertTrue(battery_wiring_errors(mutated))
        self.assertFalse(battery_wiring_errors(text))

    def test_armed_invocation_cannot_disable_the_agnostic_wires(self):
        text = DOC.read_text(encoding="utf-8")
        armed_anchor = "--size-mb 50 --terminate"
        self.assertEqual(text.count(armed_anchor), 1,
                         "the armed-line anchor no longer matches the doc")
        for form in ("--disable stall", "--disable size",
                     "--disable stall,size", "--disable=stall",
                     "--disable tokens", "--disable tokens,tokens-out",
                     "--disable=tokens"):
            with self.subTest(form=form):
                mutated = text.replace(
                    armed_anchor, f"{armed_anchor} {form}", 1)
                self.assertNotEqual(mutated, text)
                self.assertTrue(battery_wiring_errors(mutated))

    def test_armed_pid_must_be_the_captured_rpid(self):
        text = DOC.read_text(encoding="utf-8")
        for wrong in ('--pid 1', '--pid 0', '--pid "$PPID"', '--pid "$PID"'):
            with self.subTest(pid=wrong):
                mutated = text.replace('--pid "$RPID"', wrong, 1)
                self.assertNotEqual(mutated, text)
                self.assertTrue(battery_wiring_errors(mutated))

    def test_commented_terminate_is_still_a_rotted_invocation(self):
        text = DOC.read_text(encoding="utf-8")
        mutated = text.replace(" --terminate", "  # --terminate", 1)
        self.assertNotEqual(mutated, text)
        self.assertTrue(battery_wiring_errors(mutated))

    def test_dropping_terminate_from_the_armed_invocation_is_rotted(self):
        text = DOC.read_text(encoding="utf-8")
        self.assertFalse(battery_wiring_errors(text))
        mutated = text.replace(" --terminate", " ", 1)
        self.assertNotEqual(mutated, text)
        self.assertTrue(battery_wiring_errors(mutated))


if __name__ == "__main__":
    unittest.main()
