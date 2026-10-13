"""Run with python -I so the consumer checkout cannot shadow dependencies."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from outcomeci_sync.main import main  # noqa: E402

raise SystemExit(main())
