from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .artifacts import verify_artifacts
from .claude_cli import retry_decision
from .common import ARTIFACT_SCHEMA_VERSION, CHILD_MARKER_NAME, INVOCATION_CONTRACT, REPORT_HEADINGS, DelegateError, boolish, now_iso
from .io_utils import load_json, read_text, write_json, write_text
from .paths import repo_root, runtime_python_root
from .real_chain import run_real_chain_validation
from .reports import text_has_required_report_headings
from .sessions import acquire_session_lease, release_session_lease, reset_session_lease_for_fresh_session
from .workflow import workflow_path



def assert_true(condition: bool, name: str) -> None:
    if not condition:
        raise DelegateError(f"[{name}] assertion failed")



def assert_equal(actual: Any, expected: Any, name: str) -> None:
    if actual != expected:
        raise DelegateError(f"[{name}] expected '{expected}' but got '{actual}'")



def run_delegate_subprocess(args: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(
        [sys.executable, str(runtime_python_root() / "delegate_to_claude.py"), *args],
        cwd=str(repo_root()),
        text=True,
        capture_output=True,
        env=merged_env,
    )



def make_fake_claude_bin(temp_root: Path, body: str) -> Path:
    bin_dir = temp_root / "fake-claude-bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        fake = bin_dir / "claude.cmd"
        write_text(fake, body)
    else:
        fake = bin_dir / "claude"
        write_text(fake, body)
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    return bin_dir


def write_task_file(root: Path, task_id: str, text: str) -> Path:
    task_file = root / f"{task_id}.md"
    write_text(task_file, text)
    return task_file


def delegate_task_args(root: Path, task_id: str, text: str, role: str = "researcher", workflow_id: str = "wf-selftest") -> list[str]:
    return [
        "-TaskFile",
        str(write_task_file(root, task_id, text)),
        "-WorkflowId",
        workflow_id,
        "-TaskId",
        task_id,
        "-Role",
        role,
    ]



def wait_for_pid_file(path: Path, name: str) -> int:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if path.exists():
            text = read_text(path).strip()
            if text:
                return int(text)
        time.sleep(0.05)
    raise DelegateError(f"[{name}] timed out waiting for pid file: {path}")



def process_is_running(pid: int) -> bool:
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            text=True,
            capture_output=True,
        )
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False



def wait_for_process_exit(pid: int, name: str) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if not process_is_running(pid):
            return
        time.sleep(0.05)
    raise DelegateError(f"[{name}] process still running: {pid}")



def run_test_runtime(_: argparse.Namespace) -> int:
    with tempfile.TemporaryDirectory(prefix="codex_with_cc_delegate_runtime_") as tmp:
        temp_root = Path(tmp)
        cleanup_child = temp_root / "cleanup_child.py"
        cleanup_pid = temp_root / "cleanup_child.pid"
        write_text(
            cleanup_child,
            (
                "import os, pathlib, time\n"
                f"pathlib.Path({str(cleanup_pid)!r}).write_text(str(os.getpid()), encoding='utf-8')\n"
                "while True:\n"
                "    time.sleep(1)\n"
            ),
        )
        cleanup_driver = temp_root / "cleanup_driver.py"
        write_text(
            cleanup_driver,
            (
                "import pathlib, subprocess, sys, time\n"
                f"sys.path.insert(0, {str(runtime_python_root())!r})\n"
                "from codex_with_cc_runtime.process_cleanup import install_child_process_cleanup\n"
                "install_child_process_cleanup()\n"
                f"subprocess.Popen([sys.executable, {str(cleanup_child)!r}], cwd={str(repo_root())!r}, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')\n"
                f"pid_file = pathlib.Path({str(cleanup_pid)!r})\n"
                "deadline = time.monotonic() + 10\n"
                "while not pid_file.exists() and time.monotonic() < deadline:\n"
                "    time.sleep(0.05)\n"
            ),
        )
        driver = subprocess.Popen([sys.executable, str(cleanup_driver)], cwd=str(repo_root()))
        child_pid = wait_for_pid_file(cleanup_pid, "entry-cleanup-starts")
        driver.wait(timeout=10)
        wait_for_process_exit(child_pid, "entry-cleanup-terminates")

        if os.name == "nt":
            hard_kill_child = temp_root / "hard_kill_child.py"
            hard_kill_driver = temp_root / "hard_kill_driver.py"
            hard_kill_pid = temp_root / "hard_kill_child.pid"
            hard_kill_ready = temp_root / "hard_kill_driver.ready"
            write_text(
                hard_kill_child,
                (
                    "import os, pathlib, time\n"
                    f"pathlib.Path({str(hard_kill_pid)!r}).write_text(str(os.getpid()), encoding='utf-8')\n"
                    "while True:\n"
                    "    time.sleep(1)\n"
                ),
            )
            write_text(
                hard_kill_driver,
                (
                    "import subprocess, sys, time\n"
                    f"sys.path.insert(0, {str(runtime_python_root())!r})\n"
                    "from codex_with_cc_runtime.process_cleanup import install_child_process_cleanup\n"
                    "install_child_process_cleanup()\n"
                    f"subprocess.Popen([sys.executable, {str(hard_kill_child)!r}], cwd={str(repo_root())!r}, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')\n"
                    f"open({str(hard_kill_ready)!r}, 'w', encoding='utf-8').write('1')\n"
                    "while True:\n"
                    "    time.sleep(1)\n"
                ),
            )
            driver = subprocess.Popen([sys.executable, str(hard_kill_driver)], cwd=str(repo_root()))
            hard_child_pid = wait_for_pid_file(hard_kill_pid, "managed-process-hard-kill-starts")
            wait_for_pid_file(hard_kill_ready, "managed-process-hard-kill-ready")
            driver.kill()
            driver.wait(timeout=10)
            wait_for_process_exit(hard_child_pid, "managed-process-hard-kill-terminates")

        missing = run_delegate_subprocess(
            [
                *delegate_task_args(temp_root, "marker-rejection", "marker rejection probe"),
                "-ArtifactRoot",
                str(temp_root / "marker"),
                "-SessionKey",
                "marker",
                "-DryRun",
            ],
            env={CHILD_MARKER_NAME: ""},
        )
        assert_true(missing.returncode != 0, "missing-child-thread-marker-fails")
        assert_true(f"{CHILD_MARKER_NAME}=1" in (missing.stdout + missing.stderr), "missing-child-thread-marker-names-required-marker")

        dry_root = temp_root / "dry"
        dry = run_delegate_subprocess(
            [
                *delegate_task_args(temp_root, "dry-run-probe", "dry run probe"),
                "-ArtifactRoot",
                str(dry_root),
                "-SessionKey",
                "dry",
                "-SessionMode",
                "PrimaryReuse",
                "-MaxRetryCount",
                "7",
                "-DryRun",
            ],
            env={CHILD_MARKER_NAME: "1"},
        )
        assert_equal(dry.returncode, 0, "dry-run-succeeds")
        config = load_json(next(dry_root.glob("config_*.json")))
        status = load_json(next(dry_root.glob("status_*.json")))
        assert_true("effort" not in config, "dry-run-config-omits-effort")
        assert_equal(int(config["maxRetryCount"]), 7, "dry-run-config-records-max-retry")
        assert_equal(int(status["maxRetryCount"]), 7, "dry-run-status-records-max-retry")

        if os.name == "nt":
            fake_body = '@echo off\nmore > nul\necho {"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"I inspected the tests."}]}}\necho {"type":"result","subtype":"success"}\nexit /b 0\n'
        else:
            fake_body = '#!/bin/sh\necho \'{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"I inspected the tests."}]}}\'\necho \'{"type":"result","subtype":"success"}\'\nexit 0\n'
        fake_bin = make_fake_claude_bin(temp_root, fake_body)
        run_root = temp_root / "unstructured"
        env = {CHILD_MARKER_NAME: "1", "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}"}
        run = run_delegate_subprocess(
            [
                *delegate_task_args(temp_root, "unstructured-rejection", "unstructured success rejection probe", "implementer"),
                "-ArtifactRoot",
                str(run_root),
                "-SessionKey",
                "unstructured",
                "-MaxRetryCount",
                "0",
            ],
            env=env,
        )
        assert_true(run.returncode != 0, "unstructured-run-fails")
        output_text = read_text(next(run_root.glob("claude_*.md")))
        status = load_json(next(run_root.glob("status_*.json")))
        assert_true(text_has_required_report_headings(output_text), "unstructured-output-has-report-headings")
        assert_equal(status["status"], "failed", "unstructured-status-failed")
        assert_equal(status["failureDisposition"], "NEED_HUMAN_INTERVENTION", "unstructured-failure-disposition")
        assert_true("unstructured_success_report" in status["failureSummary"], "unstructured-failure-summary-records-reason")
        markdown_report = "\n".join(f"**{heading}**" for heading in REPORT_HEADINGS)
        assert_true(not text_has_required_report_headings(markdown_report), "markdown-report-headings-rejected")
        missing_summary = "\n".join(REPORT_HEADINGS).replace("Summary\n", "")
        assert_true(not text_has_required_report_headings(missing_summary), "missing-report-heading-rejected")

        retry_report = "\n".join(
            (
                "Status",
                "DONE",
                "",
                "Role",
                "implementer",
                "",
                "Summary",
                "Structured retry succeeded.",
                "",
                "Changed Files",
                "None",
                "",
                "Verification",
                "- fake verification passed",
                "",
                "Findings",
                "- repaired the report format",
                "",
                "Final Result",
                "DONE",
                "",
                "Risks Or Follow-ups",
                "None",
            )
        )
        unstructured_record = json.dumps(
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "I inspected the tests."}]}},
            separators=(",", ":"),
        )
        structured_record = json.dumps(
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": retry_report}]}},
            separators=(",", ":"),
        )
        result_record = json.dumps({"type": "result", "subtype": "success"}, separators=(",", ":"))
        retry_state = temp_root / "unstructured_retry_seen.txt"
        if os.name == "nt":
            retry_fake_body = (
                "@echo off\n"
                "more > nul\n"
                f'if exist "{retry_state}" goto structured\n'
                f'echo seen>"{retry_state}"\n'
                f"echo {unstructured_record}\n"
                f"echo {result_record}\n"
                "exit /b 0\n"
                ":structured\n"
                f"echo {structured_record}\n"
                f"echo {result_record}\n"
                "exit /b 0\n"
            )
        else:
            state_text = str(retry_state).replace("'", "'\"'\"'")
            retry_fake_body = (
                "#!/bin/sh\n"
                f"if [ -f '{state_text}' ]; then\n"
                f"  echo '{structured_record}'\n"
                "else\n"
                f"  touch '{state_text}'\n"
                f"  echo '{unstructured_record}'\n"
                "fi\n"
                f"echo '{result_record}'\n"
                "exit 0\n"
            )
        retry_fake_bin = make_fake_claude_bin(temp_root, retry_fake_body)
        retry_root = temp_root / "unstructured-retry"
        retry_env = {CHILD_MARKER_NAME: "1", "PATH": f"{retry_fake_bin}{os.pathsep}{os.environ.get('PATH', '')}"}
        retry_run = run_delegate_subprocess(
            [
                *delegate_task_args(temp_root, "unstructured-repair", "unstructured success report repair probe", "implementer"),
                "-ArtifactRoot",
                str(retry_root),
                "-SessionKey",
                "unstructured-retry",
                "-MaxRetryCount",
                "1",
            ],
            env=retry_env,
        )
        assert_equal(retry_run.returncode, 0, "unstructured-report-repair-succeeds")
        retry_status = load_json(next(retry_root.glob("status_*.json")))
        retry_output = read_text(next(retry_root.glob("claude_*.md")))
        assert_equal(retry_status["status"], "completed", "unstructured-report-repair-status-completed")
        assert_equal(int(retry_status["retryCount"]), 1, "unstructured-report-repair-retry-count")
        assert_equal(retry_status["attempts"][0]["retryReason"], "unstructured_success_report", "unstructured-report-repair-reason")
        assert_true(boolish(retry_status["attempts"][1]["resume"]), "unstructured-report-repair-resumes-session")
        assert_true(text_has_required_report_headings(retry_output), "unstructured-report-repair-output-structured")

        decision = retry_decision(
            ["Error: stream-json output requires the --verbose flag when printing"],
            False,
            1,
            False,
            False,
            False,
        )
        assert_true(decision["shouldRetry"], "stream-json-startup-retries")
        assert_equal(decision["retryReason"], "stream_json_startup", "stream-json-reason")
        false_positive = retry_decision(
            ['{"type":"user","message":{"role":"user","content":[{"type":"tool_result","content":"No conversation found with session ID"}]}}'],
            True,
            0,
            True,
            True,
            True,
        )
        assert_true(not false_positive["shouldRetry"], "tool-result-content-does-not-trigger-retry")

        verify_root = temp_root / "verify"
        run_id = "artifact-verify-test"
        session_key = "artifact-verify-session"
        session_state_path = verify_root / "session-pools" / f"{session_key}.json"
        session_state_path.parent.mkdir(parents=True, exist_ok=True)
        output_path = verify_root / f"claude_{run_id}.md"
        prompt_path = verify_root / f"prompt_{run_id}.md"
        stream_path = verify_root / f"stream_{run_id}.jsonl"
        trace_path = verify_root / f"trace_{run_id}.log"
        workflow_id = "artifact-verify-workflow"
        task_id = "artifact-verify-task"
        role = "researcher"
        write_text(
            output_path,
            "Status\nDONE\n\nRole\nresearcher\n\nSummary\nok\n\nChanged Files\nNone\n\nVerification\n- fake\n\nFindings\nNone\n\nFinal Result\nDONE\n\nRisks Or Follow-ups\nNone\n",
        )
        write_text(prompt_path, "# prompt")
        write_text(stream_path, '{"type":"result","subtype":"success"}')
        write_text(trace_path, "[ok]")
        write_json(
            session_state_path,
            {
                "version": 1,
                "sessionKey": session_key,
                "createdAt": now_iso(),
                "updatedAt": now_iso(),
                "primary": {"sessionId": "fresh", "status": "available", "leaseRunId": None},
                "parallelPool": [],
            },
        )
        write_json(
            verify_root / f"config_{run_id}.json",
            {
                "artifactSchema": ARTIFACT_SCHEMA_VERSION,
                "invocationContract": INVOCATION_CONTRACT,
                "childThreadMarkerName": CHILD_MARKER_NAME,
                "childThreadMarkerValidated": True,
                "runId": run_id,
                "workflowId": workflow_id,
                "taskId": task_id,
                "role": role,
                "outputPath": str(output_path),
                "statusPath": str(verify_root / f"status_{run_id}.json"),
                "promptPath": str(prompt_path),
                "sessionKey": session_key,
                "sessionStatePath": str(session_state_path),
                "sessionMode": "PrimaryReuse",
                "rawStreamPath": str(stream_path),
                "tracePath": str(trace_path),
                "initialSessionId": "fresh",
                "initialResume": False,
                "sessionId": "fresh",
                "resume": False,
                "attemptCount": 1,
                "retryCount": 0,
            },
        )
        write_json(
            verify_root / f"status_{run_id}.json",
            {
                "artifactSchema": ARTIFACT_SCHEMA_VERSION,
                "invocationContract": INVOCATION_CONTRACT,
                "childThreadMarkerName": CHILD_MARKER_NAME,
                "childThreadMarkerValidated": True,
                "runId": run_id,
                "workflowId": workflow_id,
                "taskId": task_id,
                "role": role,
                "status": "completed",
                "outputPath": str(output_path),
                "promptPath": str(prompt_path),
                "rawStreamPath": str(stream_path),
                "tracePath": str(trace_path),
                "exitCode": 0,
                "attemptCount": 1,
                "retryCount": 0,
                "attempts": [
                    {
                        "attempt": 1,
                        "sessionId": "fresh",
                        "resume": False,
                        "retryReason": None,
                        "exitCode": 0,
                        "sawAssistantText": True,
                        "sawResultSuccess": True,
                        "capturedFinalResult": True,
                    }
                ],
            },
        )
        write_json(
            workflow_path(verify_root, workflow_id),
            {
                "artifactSchema": ARTIFACT_SCHEMA_VERSION,
                "invocationContract": INVOCATION_CONTRACT,
                "workflowId": workflow_id,
                "createdAt": now_iso(),
                "updatedAt": now_iso(),
                "tasks": {
                    task_id: {
                        "taskId": task_id,
                        "role": role,
                        "scope": [],
                        "verification": [],
                        "runs": [run_id],
                        "status": "completed",
                    }
                },
                "runs": {
                    run_id: {
                        "runId": run_id,
                        "taskId": task_id,
                        "role": role,
                        "status": "completed",
                        "configPath": str(verify_root / f"config_{run_id}.json"),
                        "statusPath": str(verify_root / f"status_{run_id}.json"),
                        "outputPath": str(output_path),
                        "promptPath": str(prompt_path),
                        "rawStreamPath": str(stream_path),
                        "tracePath": str(trace_path),
                    }
                },
            },
        )
        verify_artifacts(run_id, str(verify_root))

        validation_root = temp_root / "real-chain-validation"
        run_real_chain_validation(argparse.Namespace(validation_root=str(validation_root), name="sample-real-chain", session_key="sample-session"))
        tasks = list((validation_root / "sample-real-chain" / "tasks").glob("*/*.md"))
        assert_equal(len(tasks), 5, "real-chain-validation-creates-five-tasks")
        print("delegate runtime tests passed")
        return 0



def run_test_session_pool(_: argparse.Namespace) -> int:
    with tempfile.TemporaryDirectory(prefix="codex_with_cc_delegate_session_pool_") as tmp:
        temp_root = Path(tmp)
        session_key = "session-pool-test"
        first = run_delegate_subprocess(
            [
                *delegate_task_args(temp_root, "serial-a", "serial A"),
                "-ArtifactRoot",
                str(temp_root),
                "-SessionKey",
                session_key,
                "-SessionMode",
                "PrimaryReuse",
                "-DryRun",
            ],
            env={CHILD_MARKER_NAME: "1"},
        )
        assert_equal(first.returncode, 0, "first-primary-dryrun-succeeds")
        state = load_json(temp_root / "session-pools" / f"{session_key}.json")
        primary_id = str(state["primary"]["sessionId"])
        assert_true("--session-id " + primary_id in first.stdout, "first-primary-uses-session-id")
        assert_equal(state["primary"]["status"], "available", "primary-released-after-dry-run")
        anchor = run_delegate_subprocess(
            [
                *delegate_task_args(temp_root, "parallel-anchor", "parallel anchor"),
                "-ArtifactRoot",
                str(temp_root),
                "-SessionKey",
                session_key,
                "-SessionMode",
                "PrimaryAnchor",
                "-AllowParallel",
                "-DryRun",
            ],
            env={CHILD_MARKER_NAME: "1"},
        )
        assert_equal(anchor.returncode, 0, "anchor-dryrun-succeeds")
        state = load_json(temp_root / "session-pools" / f"{session_key}.json")
        assert_equal(state["primary"]["sessionId"], primary_id, "anchor-keeps-primary-id")
        assert_true("--resume " + primary_id in anchor.stdout, "anchor-resumes-primary")
        parallel_a = run_delegate_subprocess(
            [
                *delegate_task_args(temp_root, "parallel-sidecar-a", "parallel sidecar A"),
                "-ArtifactRoot",
                str(temp_root),
                "-SessionKey",
                session_key,
                "-SessionMode",
                "ParallelPool",
                "-AllowParallel",
                "-DryRun",
            ],
            env={CHILD_MARKER_NAME: "1"},
        )
        assert_equal(parallel_a.returncode, 0, "parallel-a-dryrun-succeeds")
        state = load_json(temp_root / "session-pools" / f"{session_key}.json")
        pool_id = str(state["parallelPool"][0]["sessionId"])
        assert_true("--session-id " + pool_id in parallel_a.stdout, "first-parallel-uses-session-id")
        parallel_b = run_delegate_subprocess(
            [
                *delegate_task_args(temp_root, "parallel-sidecar-a-repeat", "parallel sidecar A"),
                "-ArtifactRoot",
                str(temp_root),
                "-SessionKey",
                session_key,
                "-SessionMode",
                "ParallelPool",
                "-AllowParallel",
                "-DryRun",
            ],
            env={CHILD_MARKER_NAME: "1"},
        )
        assert_equal(parallel_b.returncode, 0, "parallel-b-dryrun-succeeds")
        state = load_json(temp_root / "session-pools" / f"{session_key}.json")
        assert_equal(len(state["parallelPool"]), 1, "parallel-pool-reuses-available-id")
        assert_true("--resume " + pool_id in parallel_b.stdout, "second-parallel-resumes-pool-id")
        lease = acquire_session_lease(
            temp_root / "session-pools" / f"{session_key}.json",
            temp_root / "session-pools" / f"{session_key}.lock",
            session_key,
            "PrimaryReuse",
            "fresh-reset-run",
            "fresh-reset-fingerprint",
            60,
            0,
            False,
            False,
        )
        reset = reset_session_lease_for_fresh_session(
            temp_root / "session-pools" / f"{session_key}.json",
            temp_root / "session-pools" / f"{session_key}.lock",
            session_key,
            lease,
            "fresh-reset-run",
            "fresh-reset-fingerprint",
            "stale_claude_session",
        )
        assert_true(not reset.resume, "fresh-reset-returns-non-resume-lease")
        assert_true(reset.session_id != lease.session_id, "fresh-reset-changes-session-id")
        release_session_lease(
            temp_root / "session-pools" / f"{session_key}.json",
            temp_root / "session-pools" / f"{session_key}.lock",
            session_key,
            reset,
            "fresh-reset-run",
            "fresh-reset-fingerprint",
        )
        split = run_delegate_subprocess(
            [
                *delegate_task_args(temp_root, "split-scope", "split scope explicit dry run"),
                "-ArtifactRoot",
                str(temp_root),
                "-SessionKey",
                "split-scope",
                "-Scope",
                "docs/codex_with_cc/windows_scripts;docs/codex_with_cc",
                "-Tests",
                "pytest;git diff --check",
                "-DryRun",
            ],
            env={CHILD_MARKER_NAME: "1"},
        )
        assert_equal(split.returncode, 0, "split-scope-dryrun-succeeds")
        prompt_path_line = [line for line in split.stdout.splitlines() if line.startswith("Prompt:")][-1]
        prompt = read_text(Path(prompt_path_line.split(":", 1)[1].strip()))
        assert_true("- docs/codex_with_cc/windows_scripts\n- docs/codex_with_cc" in prompt, "semicolon-scope-splits")
        assert_true("- git diff --check" in prompt, "semicolon-tests-splits")
        print("delegate session pool tests passed")
        return 0
