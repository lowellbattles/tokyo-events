"""Shared test bootstrap: src/ on sys.path once, for every test file.
Existing per-file inserts stay (harmless, and each file remains
runnable standalone); new files may rely on this alone."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
