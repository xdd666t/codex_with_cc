from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ARTIFACT_SCHEMA_VERSION = 2
INVOCATION_CONTRACT = "spawn_agent_child_only"
CHILD_MARKER_NAME = "CODEX_CLAUDE_CHILD_THREAD"
CHILD_MARKER_VALUE = "1"
REPORT_HEADINGS = (
    "Process Log",
    "Summary",
    "Changed Files",
    "Verification",
    "Final Result",
    "Risks Or Follow-ups",
)
SKILL_NAME = "codex-with-cc"
DEFAULT_CLAUDE_ARTIFACT_DIR = "claude-delegate"
DEFAULT_OPENCODE_ARTIFACT_DIR = "opencode-delegate"
OPENCODE_CHILD_MARKER_NAME = "CODEX_OPENCODE_CHILD_THREAD"
OPENCODE_CHILD_MARKER_VALUE = "1"



class DelegateError(RuntimeError):
    pass



def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()



def boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y")
    return bool(value)



def same_path(a: str | Path, b: str | Path) -> bool:
    left = os.path.normcase(os.path.realpath(os.path.abspath(os.fspath(a))))
    right = os.path.normcase(os.path.realpath(os.path.abspath(os.fspath(b))))
    return left == right
