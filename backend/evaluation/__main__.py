"""``python -m evaluation`` is the same command as ``python -m evaluation.run``."""

from __future__ import annotations

from evaluation.run import main

if __name__ == "__main__":
    raise SystemExit(main())
