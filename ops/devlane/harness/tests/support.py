"""Put the harness modules on `sys.path` so a test can import them by name.

`unittest discover -s ops/devlane/harness/tests` inserts the START directory
on the path, not its parent, so `import probe` fails from here without
this. The workflow suite solves it the same way, and its import order is
load-bearing for the same reason: `import support` must come first.

This exists because of a gap in a CONTRACT, not a gap in a test. An
independent author was handed the modules' promises and told to import
them "the way the guide describes" — and the guide describes a RUN
command, never an import path. Two authors of one seam, given the shape
of the data and not the shape of the door: the same defect this app was
built to make impossible, committed while staging the work to test it.
"""

import sys
from pathlib import Path

HARNESS = Path(__file__).resolve().parents[1]
if str(HARNESS) not in sys.path:
    sys.path.insert(0, str(HARNESS))
