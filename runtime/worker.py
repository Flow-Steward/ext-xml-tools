"""The worker process: does one step's XML work under a CPU-time limit.

Started by ``runtime/sandbox.py`` as ``python -m runtime.worker <task.json>`` from the
bundle's root.
It reads only the files the parent prepared and writes only the result file and
its outcome.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import resource
import sys
import time
from typing import Any

from runtime import errors
from runtime.errors import ExtensionError
from runtime.files import Source
from runtime.job import Job
from runtime.sandbox import where


def _source(entry: Any) -> Any:
    if isinstance(entry, list):
        return [_source(item) for item in entry]
    return Source(
        label=entry["label"],
        path=entry["path"],
        from_text=bool(entry["from_text"]),
        filename=str(entry.get("filename") or ""),
    )


def run(task: dict[str, Any]) -> dict[str, Any]:
    from runtime.runners import RUNNERS

    seconds = float(task["seconds"])
    job = Job(
        values=task["values"],
        sources={key: _source(entry) for key, entry in task["sources"].items()},
        out_path=task["out_path"],
        deadline=time.monotonic() + seconds - 1,
    )
    value = RUNNERS[task["operation_id"]](job)
    return {"result": value, "warnings": job.warnings}


def main(argv: list[str]) -> int:
    with open(argv[1], encoding="utf-8") as handle:
        task = json.load(handle)
    cpu = max(1, math.ceil(float(task["seconds"])))
    with contextlib.suppress(ValueError, OSError):
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))
    try:
        outcome: dict[str, Any] = {"value": run(task)}
    except ExtensionError as exc:
        outcome = {"error": {"code": exc.code, "message": exc.message}}
    except MemoryError:
        outcome = {
            "error": {
                "code": errors.LIMIT_EXCEEDED,
                "message": "The work needs more memory than the step may use",
            }
        }
    except Exception as exc:  # noqa: BLE001 — reported as a safe internal error, no data
        print(f"flowsteward.xml internal error: {where(exc)}", file=sys.stderr)
        outcome = {
            "error": {
                "code": errors.INTERNAL_ERROR,
                "message": f"The operation could not be completed ({where(exc)})",
            }
        }
    result_path = task["result_path"]
    temporary = result_path + ".part"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(outcome, handle, ensure_ascii=False, default=str)
    os.replace(temporary, result_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
