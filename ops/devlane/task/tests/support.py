"""Load a task-app module by path, the way the other suites do.

The dev-lane apps are standalone scripts, not an installed package:
`ops/devlane/task/envelope.py` has no importable dotted name. Every suite
here loads it from its path, so a test file can be run on its own
(`python3 ops/devlane/task/tests/test_envelope.py`) without a sys.path
ceremony repeated in each one.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
REPO = APP.parents[2]

# The app dir goes on sys.path so its standalone scripts can import one
# another plainly — `import envelope` inside verify.py. This mirrors
# ops/devlane/workflow/tests/support.py, which does the same for the same
# reason. Without it a module loaded BY PATH cannot resolve its
# siblings, and the failure looks like a missing dependency rather
# than a missing path entry.
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


def load(name):
    """Import <app>/<name>.py under a task_ prefix and return it."""
    path = APP / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"task_{name}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
