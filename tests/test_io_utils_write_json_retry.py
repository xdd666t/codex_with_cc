#!/usr/bin/env python3
import os
import sys
import tempfile
from pathlib import Path


repo = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo / "skills" / "codex-with-cc" / "scripts"))

from codex_with_cc_runtime import io_utils


def test_write_json_retries_permission_errors() -> None:
    with tempfile.TemporaryDirectory(prefix="codex_with_cc_write_json_retry_") as tmp:
        target = Path(tmp) / "status.json"
        calls: list[tuple[str, str]] = []
        real_replace = os.replace

        def flaky_replace(src: str | bytes, dst: str | bytes) -> None:
            calls.append((os.fspath(src), os.fspath(dst)))
            if len(calls) <= 8:
                raise PermissionError(5, "Access is denied", os.fspath(dst))
            real_replace(src, dst)

        os.replace = flaky_replace
        try:
            io_utils.write_json(target, {"status": "ok"})
        finally:
            os.replace = real_replace

        assert target.exists()
        assert '"status": "ok"' in target.read_text(encoding="utf-8")
        assert len(calls) >= 2
