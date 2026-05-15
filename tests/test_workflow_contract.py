#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "skills" / "codex-with-cc" / "scripts"
DELEGATE = SCRIPTS / "delegate_to_claude.py"
VERIFY_RUN = SCRIPTS / "verify_delegate_run.py"
VERIFY_WORKFLOW = SCRIPTS / "verify_delegate_workflow.py"
HOOK_SCRIPT = REPO / "hooks" / "subagent-gate-hook.mjs"
sys.path.insert(0, str(SCRIPTS))

from codex_with_cc_runtime.common import ARTIFACT_SCHEMA_VERSION, REPORT_STATUS_VALUES, WORKER_ROLES
from codex_with_cc_runtime.reports import parse_report_final_result, parse_report_role, parse_report_status, text_has_required_report_headings
from codex_with_cc_runtime.workflow import workflow_path


def workflow_report(status: str = "DONE", role: str = "researcher", final_result: str | None = None) -> str:
    final_result = final_result or status
    return "\n".join(
        (
            "Status",
            status,
            "",
            "Role",
            role,
            "",
            "Summary",
            "Completed the delegated workflow task.",
            "",
            "Changed Files",
            "None",
            "",
            "Verification",
            "- dry run artifact generation passed",
            "",
            "Findings",
            "None",
            "",
            "Final Result",
            final_result,
            "",
            "Risks Or Follow-ups",
            "None",
        )
    )


def run_hook(payload: dict) -> dict:
    result = subprocess.run(
        ["node", str(HOOK_SCRIPT)],
        input=json.dumps(payload),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=REPO,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr
    if not result.stdout.strip():
        return {}
    return json.loads(result.stdout)


def run_python(script: Path, *args: str, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=str(cwd),
        text=True,
        capture_output=True,
        env=env,
    )


def run_id_from_output(output: str) -> str:
    for line in output.splitlines():
        if line.startswith("RunId:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError(f"RunId line missing from output:\n{output}")


def test_readme_is_frozen_and_install_prompt_remains_available() -> None:
    current = (REPO / "README.md").read_text(encoding="utf-8")
    baseline = subprocess.run(
        ["git", "show", "HEAD:README.md"],
        cwd=REPO,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        check=True,
    ).stdout

    assert current == baseline
    assert "请把 https://github.com/aiskyhub/codex_with_cc 子代理工作流安装或更新到当前 Codex 环境。" in current


def test_report_contract_accepts_statuses_and_roles() -> None:
    assert ARTIFACT_SCHEMA_VERSION == 3
    assert REPORT_STATUS_VALUES == ("DONE", "DONE_WITH_CONCERNS", "NEEDS_CONTEXT", "BLOCKED", "FAIL")
    assert WORKER_ROLES == ("planner", "implementer", "researcher", "reviewer", "final-verifier")

    for status in REPORT_STATUS_VALUES:
        report = workflow_report(status=status)
        assert text_has_required_report_headings(report)
        assert parse_report_status(report) == status
        assert parse_report_final_result(report) == status
        assert parse_report_role(report) == "researcher"

    mismatched = workflow_report(status="DONE", role="reviewer", final_result="FAIL")
    assert parse_report_status(mismatched) == "DONE"
    assert parse_report_final_result(mismatched) == "FAIL"
    assert parse_report_role(mismatched) == "reviewer"


def test_delegate_dry_run_writes_workflow_artifacts_and_verifies_them() -> None:
    with tempfile.TemporaryDirectory(prefix="codex_with_cc_workflow_") as tmp:
        root = Path(tmp)
        artifact_root = root / "artifacts"
        env = {
            **os.environ,
            "CODEX_CLAUDE_CHILD_THREAD": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        task_file = root / "workflow-dry-run-task.md"
        task_file.write_text("workflow dry run task", encoding="utf-8")
        result = run_python(
            DELEGATE,
            "-TaskFile",
            str(task_file),
            "-WorkflowId",
            "wf-contract",
            "-TaskId",
            "task-contract",
            "-Role",
            "researcher",
            "-Scope",
            "skills/codex-with-cc",
            "-Tests",
            "python -m pytest",
            "-DependsOn",
            "task-prereq",
            "-ArtifactRoot",
            str(artifact_root),
            "-SessionKey",
            "workflow-contract",
            "-DryRun",
            cwd=REPO,
            env=env,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        run_id = run_id_from_output(result.stdout)

        config = json.loads((artifact_root / f"config_{run_id}.json").read_text(encoding="utf-8"))
        status = json.loads((artifact_root / f"status_{run_id}.json").read_text(encoding="utf-8"))
        report = (artifact_root / f"claude_{run_id}.md").read_text(encoding="utf-8")
        workflow = json.loads(workflow_path(artifact_root, "wf-contract").read_text(encoding="utf-8"))

        assert config["artifactSchema"] == 3
        assert status["artifactSchema"] == 3
        assert config["workflowId"] == "wf-contract"
        assert config["taskId"] == "task-contract"
        assert config["role"] == "researcher"
        assert status["workflowId"] == "wf-contract"
        assert status["taskId"] == "task-contract"
        assert status["role"] == "researcher"
        assert text_has_required_report_headings(report)
        assert workflow["artifactSchema"] == 3
        assert workflow["workflowId"] == "wf-contract"
        assert workflow["tasks"]["task-contract"]["role"] == "researcher"
        assert workflow["tasks"]["task-contract"]["lastReportStatus"] == "DONE"
        assert workflow["tasks"]["task-contract"]["lastReportFinalResult"] == "DONE"
        assert workflow["tasks"]["task-contract"]["reviewDecision"] == "accepted"
        assert workflow["tasks"]["task-contract"]["dependsOn"] == ["task-prereq"]
        assert workflow["runs"][run_id]["taskId"] == "task-contract"
        assert workflow["runs"][run_id]["reportStatus"] == "DONE"
        assert workflow["runs"][run_id]["reportFinalResult"] == "DONE"
        assert workflow["runs"][run_id]["reportRole"] == "researcher"
        assert workflow["runs"][run_id]["reviewDecision"] == "accepted"

        verify_run = run_python(VERIFY_RUN, "-RunId", run_id, "-ArtifactRoot", str(artifact_root), cwd=REPO, env=env)
        verify_workflow = run_python(VERIFY_WORKFLOW, "-WorkflowId", "wf-contract", "-ArtifactRoot", str(artifact_root), cwd=REPO, env=env)

        assert verify_run.returncode == 0, verify_run.stdout + verify_run.stderr
        assert verify_workflow.returncode == 0, verify_workflow.stdout + verify_workflow.stderr


def test_hook_gate_requires_workflow_payload_fields_and_write_scope_for_parallel() -> None:
    missing_workflow = run_hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {
                "command": (
                    "$env:CODEX_CLAUDE_CHILD_THREAD = '1'; "
                    "pwsh -NoProfile -File windows_scripts/delegate_to_claude.ps1 "
                    "-TaskFile .codex/codex_with_cc/tasks/20260514/120000000-task.md "
                    "-TaskId task-a -Role implementer"
                )
            },
        }
    )
    assert missing_workflow["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "WorkflowId" in missing_workflow["hookSpecificOutput"]["permissionDecisionReason"]

    parallel_without_scope = run_hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {
                "command": (
                    "$env:CODEX_CLAUDE_CHILD_THREAD = '1'; "
                    "pwsh -NoProfile -File windows_scripts/delegate_to_claude.ps1 "
                    "-TaskFile .codex/codex_with_cc/tasks/20260514/120000000-task.md "
                    "-WorkflowId wf-a -TaskId task-a -Role implementer -AllowParallel"
                )
            },
        }
    )
    assert parallel_without_scope["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "Scope" in parallel_without_scope["hookSpecificOutput"]["permissionDecisionReason"]

    compliant = run_hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {
                "command": (
                    "$env:CODEX_CLAUDE_CHILD_THREAD = '1'; "
                    "pwsh -NoProfile -File windows_scripts/delegate_to_claude.ps1 "
                    "-TaskFile .codex/codex_with_cc/tasks/20260514/120000000-task.md "
                    "-WorkflowId wf-a -TaskId task-a -Role researcher "
                    "-SessionKey wf-a "
                    "-Scope skills/codex-with-cc -SessionMode ParallelPool -AllowParallel"
                )
            },
        }
    )
    assert compliant == {}
