#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


repo = Path(__file__).resolve().parents[1]
delegate = repo / "skills" / "codex-with-cc" / "scripts" / "delegate_to_claude.py"


def make_fake_claude_bin(root: Path, stdin_capture: Path) -> Path:
    fake_bin = root / "fake-claude-bin"
    fake_bin.mkdir(parents=True, exist_ok=True)
    assistant = json.dumps(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": "Status\nDONE\n\nRole\nimplementer\n\nSummary\nok\n\nChanged Files\nNone\n\nVerification\n- fake\n\nFindings\n- fake long prompt run\n\nFinal Result\nDONE\n\nRisks Or Follow-ups\nNone",
                    }
                ],
            },
        },
        separators=(",", ":"),
    )
    result = json.dumps({"type": "result", "subtype": "success"}, separators=(",", ":"))
    (fake_bin / "claude.cmd").write_text(
        "@echo off\n"
        f"more > \"{stdin_capture}\"\n"
        f"echo {assistant}\n"
        f"echo {result}\n"
        "exit /b 0\n",
        encoding="utf-8",
    )
    return fake_bin


def test_delegate_sends_long_prompt_via_stdin() -> None:
    with tempfile.TemporaryDirectory(prefix="codex_with_cc_long_prompt_") as tmp:
        root = Path(tmp)
        artifact_root = root / "artifacts"
        stdin_capture = root / "stdin.txt"
        fake_bin = make_fake_claude_bin(root, stdin_capture)
        long_task = "audit long prompt\n" + ("0123456789abcdef" * 2000)
        task_file = root / "long-task.md"
        task_file.write_text(long_task, encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable,
                str(delegate),
                "-TaskFile",
                str(task_file),
                "-WorkflowId",
                "wf-long-prompt",
                "-TaskId",
                "task-long-prompt",
                "-Role",
                "implementer",
                "-ArtifactRoot",
                str(artifact_root),
                "-SessionKey",
                "long-prompt-session",
                "-BypassPermissions",
            ],
            cwd=repo,
            text=True,
            capture_output=True,
            env={
                **os.environ,
                "CODEX_CLAUDE_CHILD_THREAD": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
            },
        )
        if result.returncode != 0:
            raise AssertionError(result.stdout + result.stderr)

        run_id = next(line.split(":", 1)[1].strip() for line in result.stdout.splitlines() if line.startswith("RunId:"))
        status = json.loads((artifact_root / f"status_{run_id}.json").read_text(encoding="utf-8"))
        assert status["status"] == "completed"
        captured = stdin_capture.read_text(encoding="utf-8")
        assert "audit long prompt" in captured
        assert len(captured) > 10000
