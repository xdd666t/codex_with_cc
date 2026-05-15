#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "skills" / "codex-with-cc" / "scripts"
DELEGATE = SCRIPTS / "delegate_to_claude.py"
VERIFY_ARTIFACTS = SCRIPTS / "verify_delegate_artifacts.py"
VERIFY_WORKFLOW = SCRIPTS / "verify_delegate_workflow.py"


def write_task(root: Path, name: str, text: str = "dry run delegated task") -> Path:
    task = root / f"{name}.md"
    task.write_text(text, encoding="utf-8")
    return task


def run_python(script: Path, *args: str, cwd: Path = REPO) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        env={**os.environ, "CODEX_CLAUDE_CHILD_THREAD": "1", "PYTHONDONTWRITEBYTECODE": "1"},
    )


def run_id_from_output(output: str) -> str:
    for line in output.splitlines():
        if line.startswith("RunId:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError(f"RunId line missing from output:\n{output}")


def test_delegate_rejects_old_inline_task_and_requires_metadata() -> None:
    with tempfile.TemporaryDirectory(prefix="codex_with_cc_cli_contract_") as tmp:
        root = Path(tmp)
        artifact_root = root / "artifacts"
        task_file = write_task(root, "research")

        old_inline = run_python(
            DELEGATE,
            "-Task",
            "old inline task",
            "-WorkflowId",
            "wf-cli-contract",
            "-TaskId",
            "task-inline",
            "-Role",
            "researcher",
            "-SessionKey",
            "cli-contract",
            "-ArtifactRoot",
            str(artifact_root),
            "-DryRun",
        )
        assert old_inline.returncode != 0
        assert "TaskFile" in (old_inline.stdout + old_inline.stderr)

        missing_workflow = run_python(
            DELEGATE,
            "-TaskFile",
            str(task_file),
            "-TaskId",
            "task-missing-workflow",
            "-Role",
            "researcher",
            "-SessionKey",
            "cli-contract",
            "-ArtifactRoot",
            str(artifact_root),
            "-DryRun",
        )
        assert missing_workflow.returncode != 0
        assert "WorkflowId" in (missing_workflow.stdout + missing_workflow.stderr)

        missing_session = run_python(
            DELEGATE,
            "-TaskFile",
            str(task_file),
            "-WorkflowId",
            "wf-cli-contract",
            "-TaskId",
            "task-missing-session",
            "-Role",
            "researcher",
            "-ArtifactRoot",
            str(artifact_root),
            "-DryRun",
        )
        assert missing_session.returncode != 0
        assert "SessionKey" in (missing_session.stdout + missing_session.stderr)

        legacy_mode = run_python(
            DELEGATE,
            "-TaskFile",
            str(task_file),
            "-WorkflowId",
            "wf-cli-contract",
            "-TaskId",
            "task-legacy-mode",
            "-Role",
            "researcher",
            "-SessionKey",
            "cli-contract",
            "-Mode",
            "Review",
            "-ArtifactRoot",
            str(artifact_root),
            "-DryRun",
        )
        assert legacy_mode.returncode != 0
        assert "Mode" in (legacy_mode.stdout + legacy_mode.stderr)

        compliant = run_python(
            DELEGATE,
            "-TaskFile",
            str(task_file),
            "-WorkflowId",
            "wf-cli-contract",
            "-TaskId",
            "task-research",
            "-Role",
            "researcher",
            "-SessionKey",
            "cli-contract",
            "-ArtifactRoot",
            str(artifact_root),
            "-DryRun",
        )
        assert compliant.returncode == 0, compliant.stdout + compliant.stderr
        run_id = run_id_from_output(compliant.stdout)

        verified = run_python(VERIFY_ARTIFACTS, "-RunId", run_id, "-ArtifactRoot", str(artifact_root))

        assert verified.returncode == 0, verified.stdout + verified.stderr

        output = artifact_root / f"claude_{run_id}.md"
        output.write_text(
            output.read_text(encoding="utf-8").replace(
                f"Verification\n- dry-run artifact generation completed for RunId {run_id}",
                "Verification\nNone",
            ),
            encoding="utf-8",
        )
        missing_evidence = run_python(VERIFY_ARTIFACTS, "-RunId", run_id, "-ArtifactRoot", str(artifact_root))

        assert missing_evidence.returncode != 0
        assert "verification evidence" in (missing_evidence.stdout + missing_evidence.stderr).lower()


def test_workflow_verifier_requires_spec_and_quality_review_for_implementers() -> None:
    with tempfile.TemporaryDirectory(prefix="codex_with_cc_review_gate_") as tmp:
        root = Path(tmp)
        artifact_root = root / "artifacts"
        task_file = write_task(root, "implement", "Implement the delegated change.")

        implementer = run_python(
            DELEGATE,
            "-TaskFile",
            str(task_file),
            "-WorkflowId",
            "wf-review-gate",
            "-TaskId",
            "task-implement",
            "-Role",
            "implementer",
            "-SessionKey",
            "review-gate",
            "-ArtifactRoot",
            str(artifact_root),
            "-DryRun",
        )
        assert implementer.returncode == 0, implementer.stdout + implementer.stderr

        missing_reviews = run_python(
            VERIFY_WORKFLOW,
            "-WorkflowId",
            "wf-review-gate",
            "-ArtifactRoot",
            str(artifact_root),
        )

        assert missing_reviews.returncode != 0
        assert "spec" in (missing_reviews.stdout + missing_reviews.stderr).lower()
        assert "quality" in (missing_reviews.stdout + missing_reviews.stderr).lower()

        for review_kind in ("spec", "quality"):
            review_task = write_task(root, f"{review_kind}-review", f"Run the {review_kind} review.")
            reviewer = run_python(
                DELEGATE,
                "-TaskFile",
                str(review_task),
                "-WorkflowId",
                "wf-review-gate",
                "-TaskId",
                f"task-{review_kind}-review",
                "-Role",
                "reviewer",
                "-ReviewForTaskId",
                "task-implement",
                "-ReviewKind",
                review_kind,
                "-SessionKey",
                "review-gate",
                "-ArtifactRoot",
                str(artifact_root),
                "-DryRun",
            )
            assert reviewer.returncode == 0, reviewer.stdout + reviewer.stderr

        reviewed = run_python(
            VERIFY_WORKFLOW,
            "-WorkflowId",
            "wf-review-gate",
            "-ArtifactRoot",
            str(artifact_root),
        )

        assert reviewed.returncode == 0, reviewed.stdout + reviewed.stderr
