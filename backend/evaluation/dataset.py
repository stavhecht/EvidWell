"""Load the JSONL datasets, resolve references, and filter what a run executes.

One case per line. A line ``{"ref": "<id>"}`` re-uses a case defined in another
file — ``regression.jsonl`` is built that way, so a case is written once and the
regression suite is a stable list of ids rather than a second copy that drifts.
Blank lines and lines starting with ``//`` are ignored, so a file can carry
section notes.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from evaluation.config import EVAL_ROOT
from evaluation.schema import EvalCase

DATASETS_DIR = EVAL_ROOT / "datasets"


class DatasetError(ValueError):
    """A dataset line that does not parse, a duplicate id, or a dangling ref."""


def _lines(path: Path) -> Iterable[tuple[int, dict[str, object]]]:
    for number, raw in enumerate(path.read_text().splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        try:
            yield number, json.loads(line)
        except json.JSONDecodeError as exc:
            raise DatasetError(f"{path.name}:{number}: not JSON ({exc})") from exc


def load_all(directory: Path = DATASETS_DIR) -> dict[str, list[EvalCase]]:
    """Every dataset file, by name (``basic`` for ``basic.jsonl``).

    Ids are unique across *all* files, so a ``ref`` is unambiguous and a
    baseline keyed by id can never confuse two cases.
    """
    defined: dict[str, EvalCase] = {}
    origin: dict[str, str] = {}
    refs: dict[str, list[tuple[str, int, str]]] = {}
    order: dict[str, list[str]] = {}

    for path in sorted(directory.glob("*.jsonl")):
        name = path.stem
        order[name] = []
        for number, record in _lines(path):
            if set(record) == {"ref"}:
                refs.setdefault(name, []).append((path.name, number, str(record["ref"])))
                order[name].append(str(record["ref"]))
                continue
            try:
                case = EvalCase.model_validate(record)
            except ValueError as exc:
                raise DatasetError(f"{path.name}:{number}: {exc}") from exc
            if case.id in defined:
                raise DatasetError(
                    f"{path.name}:{number}: duplicate id {case.id!r} "
                    f"(first defined in {origin[case.id]})"
                )
            defined[case.id] = case
            origin[case.id] = path.name
            order[name].append(case.id)

    for entries in refs.values():
        for filename, number, ref in entries:
            if ref not in defined:
                raise DatasetError(f"{filename}:{number}: ref to unknown case {ref!r}")

    return {name: [defined[case_id] for case_id in ids] for name, ids in order.items()}


def select(
    datasets: dict[str, list[EvalCase]],
    names: list[str],
    *,
    test_ids: list[str] | None = None,
    categories: list[str] | None = None,
    limit: int | None = None,
) -> list[EvalCase]:
    """The cases a run executes, in dataset order, each at most once."""
    unknown = [name for name in names if name not in datasets]
    if unknown:
        raise DatasetError(f"unknown dataset(s): {', '.join(unknown)}")

    seen: set[str] = set()
    chosen: list[EvalCase] = []
    pool = [case for name in names for case in datasets[name]]
    if test_ids:
        everything = {case.id: case for cases in datasets.values() for case in cases}
        missing = [case_id for case_id in test_ids if case_id not in everything]
        if missing:
            raise DatasetError(f"unknown test id(s): {', '.join(missing)}")
        pool = [everything[case_id] for case_id in test_ids]

    for case in pool:
        if case.id in seen:
            continue
        if categories and case.category.value not in categories:
            continue
        seen.add(case.id)
        chosen.append(case)
    return chosen[:limit] if limit else chosen
