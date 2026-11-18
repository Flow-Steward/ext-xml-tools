"""Run the XML work in a separate process with a hard time limit.

libxml2 and libxslt run in C and cannot be interrupted from Python, so a heavy
XPath, stylesheet or schema could hold the step until the host kills it. The
work runs in a child Python process instead (no shell): it limits its own CPU
time, and the parent stops it when the step's time is up and answers ``timeout``.
"""

from __future__ import annotations

import contextlib
import json
import os

# Runs this bundle's own worker module, never a shell.
import subprocess  # nosec B404
import sys
import traceback
from pathlib import Path
from typing import Any

from . import errors
from .errors import ExtensionError

WORKER = Path(__file__).resolve().with_name("worker.py")
_STDERR_LIMIT = 2000


def where(exc: BaseException) -> str:
    """``TypeError at runtime/convert.py:123 in to_json`` — no values."""
    frames = traceback.extract_tb(exc.__traceback__)
    inside = [f for f in frames if "/runtime/" in f.filename.replace("\\", "/")]
    chosen = inside or list(frames)
    if not chosen:
        return type(exc).__name__
    frame = chosen[-1]
    path = frame.filename.replace("\\", "/")
    path = path[path.rfind("/runtime/") + 1 :] if "/runtime/" in path else path.rsplit("/", 1)[-1]
    return f"{type(exc).__name__} at {path}:{frame.lineno} in {frame.name}"


def _timeout(seconds: float) -> ExtensionError:
    return ExtensionError(
        errors.TIMEOUT, f"The work did not finish in {int(seconds)} seconds and was stopped"
    )


def run_worker(task: dict[str, Any], *, seconds: float, workdir: str) -> Any:
    """Run ``task`` in the worker process; its value, or its ExtensionError, or ``timeout``."""
    if seconds <= 1:
        raise ExtensionError(errors.TIMEOUT, "The step has no time left for the work")
    task_path = os.path.join(workdir, "task.json")
    result_path = os.path.join(workdir, "outcome.json")
    task = {**task, "seconds": seconds, "result_path": result_path}
    with open(task_path, "w", encoding="utf-8") as handle:
        json.dump(task, handle, ensure_ascii=False, default=str)
    root = str(WORKER.parents[1])
    environment = dict(os.environ)
    environment.pop("PYTHONSAFEPATH", None)
    inherited = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = root + (os.pathsep + inherited if inherited else "")
    try:
        # Fixed argv: this interpreter and this bundle's worker module, no shell.
        finished = subprocess.run(  # nosec B603
            [sys.executable, "-m", "runtime.worker", task_path],
            cwd=root,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise _timeout(seconds) from None
    if finished.stderr:
        sys.stderr.write(finished.stderr[-_STDERR_LIMIT:].decode("utf-8", "replace"))
    if finished.returncode < 0 and not os.path.exists(result_path):
        raise _timeout(seconds)
    try:
        with open(result_path, encoding="utf-8") as handle:
            outcome = json.load(handle)
    except (OSError, ValueError):
        raise ExtensionError(
            errors.INTERNAL_ERROR, "The work stopped without a result (out of memory?)"
        ) from None
    finally:
        with contextlib.suppress(OSError):
            os.unlink(result_path)
    if "error" in outcome:
        raise ExtensionError(outcome["error"]["code"], outcome["error"]["message"])
    return outcome["value"]


__all__ = ["WORKER", "run_worker", "where"]
