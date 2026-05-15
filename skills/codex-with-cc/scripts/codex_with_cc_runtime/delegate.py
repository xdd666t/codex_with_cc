from __future__ import annotations

import argparse
import contextlib
import dataclasses
import json
import os
import shutil
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from .claude_cli import new_claude_cli_args, retry_decision, update_stream_capture, failure_summary
from .common import ARTIFACT_SCHEMA_VERSION, CHILD_MARKER_NAME, CHILD_MARKER_VALUE, INVOCATION_CONTRACT, DelegateError, now_iso
from .io_utils import read_text, test_path_writable, write_json, write_text
from .locks import FileLock, acquire_file_lock
from .paths import repo_root, workflow_relative_path, workflow_root
from .prompts import build_prompt
from .reports import build_report_repair_prompt, get_output_resolution, path_has_required_report_headings
from .sessions import SessionLease, acquire_session_lease, effective_session_key, normalize_delegate_list, release_session_lease, reset_session_lease_for_fresh_session, safe_session_key, task_fingerprint
from .workflow import normalize_role, safe_task_id, update_workflow_record, workflow_path



def startup_failure_report(message: str, role: str = "reviewer") -> str:
    summary = f"STARTUP_FAILURE: {message}"
    return f"""Status
FAIL

Role
{role}

Summary
The delegate run did not reach Claude Code execution.

Changed Files
None

Verification
- not run; delegate startup failed before worker execution

Findings
- Delegate worker failed before Claude Code execution started.
- Startup failure: {message}

Final Result
FAIL
{summary}

Risks Or Follow-ups
- Retry only after the startup blocker is resolved.
"""



def dry_run_report(run_id: str, prompt_path: Path, role: str) -> str:
    return f"""Status
DONE

Role
{role}

Summary
Delegate dry run completed without executing Claude Code.

Changed Files
None

Verification
- dry-run artifact generation completed for RunId {run_id}

Findings
- Dry run enabled; Claude Code was not invoked.
- Delegate prompt and audit artifacts were generated for inspection.

Final Result
DONE

Risks Or Follow-ups
- Inspect the generated prompt before running without -DryRun if needed: {prompt_path}
"""



def complete_startup_failure(
    failure_message: str,
    config_path: Path,
    status_path: Path,
    output_path: Path,
    raw_stream_path: Path,
    trace_path: Path,
    config: dict[str, Any],
    status: dict[str, Any],
) -> None:
    failure = f"STARTUP_FAILURE: {failure_message}"
    write_text(output_path, startup_failure_report(failure_message, str(config.get("role") or "reviewer")))
    if not raw_stream_path.exists():
        write_text(raw_stream_path, "")
    if not trace_path.exists():
        write_text(trace_path, f"[startup-failure] {failure_message}")
    status.update(
        {
            "status": "failed",
            "outputBytes": output_path.stat().st_size,
            "exitCode": 1,
            "attemptCount": 1,
            "retryCount": 0,
            "failureDisposition": "NEED_HUMAN_INTERVENTION",
            "failureSummary": failure,
            "attempts": [
                {
                    "attempt": 1,
                    "sessionId": "",
                    "resume": False,
                    "retryReason": None,
                    "exitCode": 1,
                    "sawAssistantText": False,
                    "sawResultSuccess": False,
                    "capturedFinalResult": True,
                }
            ],
        }
    )
    config.update(
        {
            "initialSessionId": "",
            "initialResume": False,
            "sessionId": "",
            "resume": False,
            "attemptCount": 1,
            "retryCount": 0,
            "failureDisposition": "NEED_HUMAN_INTERVENTION",
            "failureSummary": failure,
        }
    )
    write_json(config_path, config)
    write_json(status_path, status)



def run_delegate(ns: argparse.Namespace) -> int:
    if os.environ.get(CHILD_MARKER_NAME) != CHILD_MARKER_VALUE:
        raise DelegateError(
            f"delegate_to_claude may only run inside a Codex spawn_agent child thread. Missing required child-thread marker '{CHILD_MARKER_NAME}={CHILD_MARKER_VALUE}'. Main-thread/direct invocation is forbidden."
        )
    task_file = Path(ns.task_file)
    if not task_file.exists():
        raise DelegateError(f"Task file was not found: {task_file}")
    task_text = read_text(task_file)
    if not task_text.strip():
        raise DelegateError("Task text cannot be empty.")
    if str(ns.role).lower() == "reviewer" and (not ns.review_for_task_id or not ns.review_kind):
        raise DelegateError("Reviewer runs must pass -ReviewForTaskId and -ReviewKind.")

    root = repo_root()
    rel = workflow_relative_path()
    entry_path = workflow_root() / "CODEX_WITH_CC.md"
    if not entry_path.exists():
        raise DelegateError(f"Missing workflow entry document: {entry_path}")

    artifact_root = Path(ns.artifact_root).resolve() if ns.artifact_root else (root / ".codex" / "codex_with_cc" / "claude-delegate").resolve()
    artifact_root.mkdir(parents=True, exist_ok=True)
    output_path = Path(ns.output_path).resolve() if ns.output_path else (artifact_root / f"claude_PLACEHOLDER.md").resolve()

    scope = normalize_delegate_list(ns.scope)
    tests = normalize_delegate_list(ns.tests)
    depends_on = [safe_task_id(item) for item in normalize_delegate_list(ns.depends_on)]
    key = effective_session_key(ns.session_key)
    safe_key = safe_session_key(key)
    session_pools_root = artifact_root / "session-pools"
    session_state_path = session_pools_root / f"{safe_key}.json"
    session_state_lock_path = session_pools_root / f"{safe_key}.lock"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    run_id = f"{timestamp}_{uuid.uuid4().hex[:8]}"
    workflow_id = ns.workflow_id.strip()
    task_id = safe_task_id(ns.task_id)
    role = normalize_role(ns.role)
    mode = role
    effective_name = ns.name if ns.name else f"{ns.name_prefix}-{run_id}"
    if output_path.name == "claude_PLACEHOLDER.md":
        output_path = artifact_root / f"claude_{run_id}.md"
    status_path = artifact_root / f"status_{run_id}.json"
    config_path = artifact_root / f"config_{run_id}.json"
    prompt_path = artifact_root / f"prompt_{run_id}.md"
    raw_stream_path = artifact_root / f"stream_{run_id}.jsonl"
    trace_path = artifact_root / f"trace_{run_id}.log"
    workflow_file_path = workflow_path(artifact_root, workflow_id)
    lock_path = artifact_root / "delegate.lock"
    fingerprint = task_fingerprint(task_text, scope, tests, mode)

    for path in (output_path, status_path, config_path, raw_stream_path, trace_path):
        test_path_writable(path)

    config: dict[str, Any] = {
        "artifactSchema": ARTIFACT_SCHEMA_VERSION,
        "invocationContract": INVOCATION_CONTRACT,
        "childThreadMarkerName": CHILD_MARKER_NAME,
        "childThreadMarkerValidated": True,
        "runId": run_id,
        "workflowId": workflow_id,
        "taskId": task_id,
        "role": role,
        "repoRoot": str(root),
        "workflowRoot": str(workflow_root()),
        "workflowRelativePath": rel,
        "mode": mode,
        "model": ns.model,
        "sessionName": effective_name,
        "sessionMode": ns.session_mode,
        "sessionKey": key,
        "sessionStatePath": str(session_state_path),
        "sessionStateLockPath": str(session_state_lock_path),
        "promptPath": str(prompt_path),
        "outputPath": str(output_path),
        "statusPath": str(status_path),
        "rawStreamPath": str(raw_stream_path),
        "tracePath": str(trace_path),
        "workflowPath": str(workflow_file_path),
        "lockPath": str(lock_path),
        "taskFile": str(task_file.resolve()),
        "dependsOn": depends_on,
        "reviewForTaskId": safe_task_id(ns.review_for_task_id) if ns.review_for_task_id else None,
        "reviewKind": ns.review_kind,
        "maxBudgetUsd": str(ns.max_budget_usd) if ns.max_budget_usd not in (None, "") else None,
        "bypassPermissions": bool(ns.bypass_permissions),
        "allowParallel": bool(ns.allow_parallel),
        "initialSessionId": None,
        "initialResume": None,
        "attemptCount": 0,
        "retryCount": 0,
        "maxRetryCount": int(ns.max_retry_count),
    }
    status: dict[str, Any] = {
        "artifactSchema": ARTIFACT_SCHEMA_VERSION,
        "invocationContract": INVOCATION_CONTRACT,
        "childThreadMarkerName": CHILD_MARKER_NAME,
        "childThreadMarkerValidated": True,
        "runId": run_id,
        "workflowId": workflow_id,
        "taskId": task_id,
        "role": role,
        "status": "starting",
        "pid": os.getpid(),
        "outputPath": str(output_path),
        "promptPath": str(prompt_path),
        "rawStreamPath": str(raw_stream_path),
        "tracePath": str(trace_path),
        "workflowPath": str(workflow_file_path),
        "linesWritten": 0,
        "outputBytes": 0,
        "exitCode": None,
        "attemptCount": 0,
        "retryCount": 0,
        "maxRetryCount": int(ns.max_retry_count),
        "attempts": [],
    }
    prompt = build_prompt(
        root,
        output_path,
        run_id,
        mode,
        scope,
        tests,
        task_text,
        workflow_id=workflow_id,
        task_id=task_id,
        role=role,
        review_for_task_id=safe_task_id(ns.review_for_task_id) if ns.review_for_task_id else None,
        review_kind=ns.review_kind,
    )
    write_text(prompt_path, prompt)
    write_json(config_path, config)
    write_json(status_path, status)

    delegate_lock: FileLock | None = None
    lease: SessionLease | None = None
    try:
        if not ns.allow_parallel:
            def lock_message() -> str:
                holder = ""
                if lock_path.exists():
                    with contextlib.suppress(Exception):
                        holder = read_text(lock_path)
                return f"Another delegate_to_claude run is still active. Use -AllowParallel to bypass, or wait. Lock: {lock_path}. Holder: {holder}"

            delegate_lock = acquire_file_lock(
                lock_path,
                int(ns.lock_timeout_seconds),
                int(ns.lock_poll_milliseconds) / 1000,
                lock_message,
            )
            delegate_lock.handle.seek(0)
            delegate_lock.handle.truncate(0)
            delegate_lock.handle.write(
                json.dumps(
                    {
                        "runId": run_id,
                        "sessionName": effective_name,
                        "pid": os.getpid(),
                        "startedAt": now_iso(),
                        "mode": mode,
                    },
                    ensure_ascii=False,
                ).encode("utf-8")
                + b"\n"
            )
            delegate_lock.handle.flush()
    except Exception as exc:
        complete_startup_failure(str(exc), config_path, status_path, output_path, raw_stream_path, trace_path, config, status)
        raise

    old_cwd = Path.cwd()
    execution_started = False
    try:
        os.chdir(root)
        lease = acquire_session_lease(
            session_state_path,
            session_state_lock_path,
            key,
            ns.session_mode,
            run_id,
            fingerprint,
            int(ns.session_lease_timeout_seconds),
            int(ns.session_lease_wait_seconds),
            bool(ns.reset_primary_session),
            bool(ns.reset_parallel_pool),
        )
        config["sessionId"] = lease.session_id
        config["resume"] = lease.resume
        write_json(config_path, config)

        print(f"Delegating to Claude Code: {shutil.which('claude') or '<dry-run>'}")
        print(f"RunId: {run_id}")
        print(f"Session Name: {effective_name}")
        print(f"Session Mode: {ns.session_mode}")
        print(f"Session Key: {key}")
        print(f"Claude Session Id: {lease.session_id}")
        print(f"Claude Session Argument: {'--resume' if lease.resume else '--session-id'} {lease.session_id}")
        print(f"Prompt: {prompt_path}")
        print(f"Output: {output_path}")
        print(f"Status: {status_path}")
        print(f"Trace: {trace_path}")
        print(f"Raw Stream: {raw_stream_path}")

        if ns.dry_run:
            print("Dry run enabled; Claude Code was not invoked.")
            write_text(output_path, dry_run_report(run_id, prompt_path, role))
            write_text(raw_stream_path, "")
            write_text(trace_path, f"[dry-run] Claude Code was not invoked for run {run_id}\n")
            status["attemptCount"] = 1
            status["retryCount"] = 0
            status["attempts"] = [
                {
                    "attempt": 1,
                    "sessionId": lease.session_id,
                    "resume": lease.resume,
                    "retryReason": None,
                    "exitCode": 0,
                    "sawAssistantText": False,
                    "sawResultSuccess": True,
                    "capturedFinalResult": True,
                    "dryRun": True,
                }
            ]
            status["status"] = "completed"
            status["exitCode"] = 0
            status["outputBytes"] = output_path.stat().st_size
            config["initialSessionId"] = lease.session_id
            config["initialResume"] = lease.resume
            config["attemptCount"] = 1
            config["retryCount"] = 0
            write_json(config_path, config)
            write_json(status_path, status)
            return 0

        claude = shutil.which("claude")
        if not claude:
            raise DelegateError("Claude Code CLI was not found. Install or expose the 'claude' command first.")

        status["status"] = "running"
        write_json(status_path, status)
        prompt_text = read_text(prompt_path)
        attempt = 0
        max_attempts = int(ns.max_retry_count) + 1
        retry_count = 0
        output_resolution: dict[str, Any] | None = None
        delegate_succeeded = False
        exit_code = -1
        final_text = ""
        failure_disposition = ""
        failure_summary_text = ""
        raw_stream_path.parent.mkdir(parents=True, exist_ok=True)
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        with raw_stream_path.open("w", encoding="utf-8") as raw_handle, trace_path.open("w", encoding="utf-8") as trace_handle:
            while attempt < max_attempts:
                attempt += 1
                capture_state: dict[str, Any] = {
                    "assistantTexts": [],
                    "traceLines": [],
                    "finalText": "",
                    "sawAssistantText": False,
                    "sawResultSuccess": False,
                    "capturedFinalResultHeading": False,
                }
                attempt_raw_lines: list[str] = []
                attempt_record: dict[str, Any] = {
                    "attempt": attempt,
                    "sessionId": lease.session_id,
                    "resume": lease.resume,
                    "retryReason": None,
                    "exitCode": None,
                    "sawAssistantText": False,
                    "sawResultSuccess": False,
                    "capturedFinalResult": False,
                    "outputWasNormalized": False,
                    "sawStaleSessionText": False,
                    "sawStreamJsonVerboseError": False,
                }
                claude_args = new_claude_cli_args(
                    ns.model,
                    effective_name,
                    lease.session_id,
                    lease.resume,
                    str(ns.max_budget_usd) if ns.max_budget_usd not in (None, "") else None,
                    bool(ns.bypass_permissions),
                )
                if attempt == 1:
                    config["initialSessionId"] = lease.session_id
                    config["initialResume"] = lease.resume
                config["sessionId"] = lease.session_id
                config["resume"] = lease.resume
                config["attemptCount"] = attempt
                config["retryCount"] = retry_count
                status["attemptCount"] = attempt
                status["retryCount"] = retry_count
                status["attempts"].append(attempt_record)
                write_json(config_path, config)
                write_json(status_path, status)
                print(f"Attempt {attempt}/{max_attempts}")
                print(f"Claude Session Id: {lease.session_id}")
                print(f"Claude Session Argument: {'--resume' if lease.resume else '--session-id'} {lease.session_id}")
                trace_handle.write(f"[attempt] {attempt} session={lease.session_id} resume={lease.resume}\n")
                trace_handle.flush()

                process = subprocess.Popen(
                    [claude, *claude_args],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    cwd=str(root),
                )
                execution_started = True
                assert process.stdout is not None
                assert process.stdin is not None
                process.stdin.write(prompt_text)
                if not prompt_text.endswith("\n"):
                    process.stdin.write("\n")
                process.stdin.close()
                for line in process.stdout:
                    line_text = line.rstrip("\r\n")
                    if not line_text.strip():
                        continue
                    attempt_raw_lines.append(line_text)
                    raw_handle.write(line_text + "\n")
                    raw_handle.flush()
                    try:
                        record = json.loads(line_text)
                        trace_lines = update_stream_capture(record, capture_state)
                    except json.JSONDecodeError:
                        trace_lines = ["[raw] non-json output line"]
                    for trace_line in trace_lines:
                        trace_handle.write(trace_line + "\n")
                    trace_handle.flush()
                    status["linesWritten"] = int(status.get("linesWritten", 0)) + 1
                    status["lastOutputAt"] = now_iso()
                    write_json(status_path, status)
                exit_code = process.wait()
                final_text = str(capture_state.get("finalText") or "")
                if not final_text.strip() and capture_state["assistantTexts"]:
                    final_text = str(capture_state["assistantTexts"][-1]).strip()
                output_file_has_report = path_has_required_report_headings(output_path)
                captured_report = bool(capture_state["capturedFinalResultHeading"]) or output_file_has_report
                decision = retry_decision(
                    attempt_raw_lines,
                    lease.resume,
                    exit_code,
                    bool(capture_state["sawAssistantText"]),
                    bool(capture_state["sawResultSuccess"]),
                    captured_report,
                )
                attempt_record.update(
                    {
                        "exitCode": exit_code,
                        "sawAssistantText": bool(capture_state["sawAssistantText"]),
                        "sawResultSuccess": bool(capture_state["sawResultSuccess"]),
                        "capturedFinalResult": captured_report,
                        "sawStaleSessionText": bool(decision["sawStaleSessionText"]),
                        "sawStreamJsonVerboseError": bool(decision["sawStreamJsonVerboseError"]),
                    }
                )
                if decision["shouldRetry"] and attempt < max_attempts:
                    retry_count += 1
                    attempt_record["retryReason"] = decision["retryReason"]
                    status["retryCount"] = retry_count
                    status["lastRetryReason"] = decision["retryReason"]
                    write_json(status_path, status)
                    if decision["retryWithFreshSession"]:
                        print(f"WARNING: Claude rejected resumed session '{lease.session_id}'. Retrying once with a fresh session.", file=sys.stderr)
                        trace_handle.write("[retry] stale session id rejected; resetting lease for a fresh Claude session\n")
                        trace_handle.flush()
                        lease = reset_session_lease_for_fresh_session(
                            session_state_path,
                            session_state_lock_path,
                            key,
                            lease,
                            run_id,
                            fingerprint,
                            str(decision["retryReason"]),
                        )
                    elif decision["retryWithReportRepair"]:
                        print("WARNING: Claude completed without the required report headings. Retrying once for structured report repair.", file=sys.stderr)
                        trace_handle.write("[retry] unstructured success; asking the same Claude session to emit the required report headings\n")
                        trace_handle.flush()
                        prompt_text = build_report_repair_prompt(output_path, final_text)
                        lease = dataclasses.replace(lease, resume=True)
                    else:
                        print("WARNING: Claude startup failed before structured output was produced. Retrying once with the current session arguments.", file=sys.stderr)
                        trace_handle.write("[retry] stream-json startup failed before structured output; retrying with current session\n")
                        trace_handle.flush()
                    continue
                if decision["shouldRetry"]:
                    claude_exit_code = exit_code
                    exit_code = 1
                    attempt_record["claudeExitCode"] = claude_exit_code
                    attempt_record["exitCode"] = exit_code
                    failure_disposition = "NEED_HUMAN_INTERVENTION"
                    failure_summary_text = failure_summary(
                        attempt_raw_lines,
                        str(decision["retryReason"]),
                        attempt,
                        int(ns.max_retry_count),
                        exit_code,
                    )
                    status["failureDisposition"] = failure_disposition
                    status["failureSummary"] = failure_summary_text
                    status["finalRetryReason"] = decision["retryReason"]
                    config["failureDisposition"] = failure_disposition
                    config["failureSummary"] = failure_summary_text
                    config["finalRetryReason"] = decision["retryReason"]
                    write_json(config_path, config)
                    write_json(status_path, status)
                    trace_handle.write("[failure] retry ceiling reached; forcing NEED_HUMAN_INTERVENTION\n")
                    trace_handle.write(f"[failure] {failure_summary_text}\n")
                    trace_handle.flush()
                    final_text = f"""Status
FAIL

Role
{role}

Summary
Automatic retry recovery hit the configured ceiling and requires human or Codex intervention.

Changed Files
None

Verification
- not run; the worker never reached a trustworthy execution state

Findings
- Delegate worker detected a retryable Claude failure and exhausted the configured retry budget.
- Automatic recovery stopped to avoid unbounded compute burn.

Final Result
FAIL
{failure_summary_text}

Risks Or Follow-ups
- Inspect Claude CLI startup/session health before retrying this delegated task again.
"""
                output_resolution = get_output_resolution(
                    final_text,
                    output_path,
                    exit_code,
                    bool(capture_state["sawResultSuccess"]),
                    bool(capture_state["capturedFinalResultHeading"]),
                )
                if output_resolution["existingStructuredOutput"] or output_resolution["finalTextHasFinalResult"]:
                    attempt_record["capturedFinalResult"] = True
                if output_resolution["outputWasNormalized"]:
                    attempt_record["capturedFinalResult"] = True
                    attempt_record["outputWasNormalized"] = True
                    attempt_record["claudeExitCode"] = exit_code
                    exit_code = 1
                    attempt_record["exitCode"] = exit_code
                    failure_disposition = "NEED_HUMAN_INTERVENTION"
                    failure_summary_text = (
                        "UNSTRUCTURED_SUCCESS_REJECTED: Claude Code exited with code 0 but did not produce the required "
                        "delegate report headings, so the wrapper rejected the run."
                    )
                    status["failureDisposition"] = failure_disposition
                    status["failureSummary"] = failure_summary_text
                    config["failureDisposition"] = failure_disposition
                    config["failureSummary"] = failure_summary_text
                delegate_succeeded = bool(output_resolution["delegateSucceeded"])
                break

        if output_resolution is None:
            output_resolution = get_output_resolution(final_text, output_path, exit_code, False, False)
        if output_resolution["shouldPersistFinalText"]:
            write_text(output_path, str(output_resolution["persistedFinalText"]))
        elif not output_path.exists():
            write_text(output_path, "Claude delegate finished without a structured text result.")
        output_has_report = path_has_required_report_headings(output_path)
        if status["attempts"] and output_has_report:
            status["attempts"][-1]["capturedFinalResult"] = True
        if output_resolution["outputWasNormalized"]:
            status["outputWasNormalized"] = True
            config["outputWasNormalized"] = True
            write_json(config_path, config)
        status["outputBytes"] = output_path.stat().st_size
        status["exitCode"] = exit_code
        status["retryCount"] = retry_count
        status["status"] = "completed" if delegate_succeeded and output_has_report else "failed"
        if failure_disposition:
            status["failureDisposition"] = failure_disposition
            status["failureSummary"] = failure_summary_text
            config["failureDisposition"] = failure_disposition
            config["failureSummary"] = failure_summary_text
            write_json(config_path, config)
        write_json(status_path, status)
        if exit_code != 0:
            if failure_disposition == "NEED_HUMAN_INTERVENTION":
                raise DelegateError(f"Claude delegate retry ceiling reached: {failure_summary_text}")
            raise DelegateError(f"Claude Code exited with code {exit_code}")
        if status["status"] != "completed":
            if failure_disposition == "NEED_HUMAN_INTERVENTION":
                raise DelegateError(f"Claude delegate retry ceiling reached: {failure_summary_text}")
            raise DelegateError(f"Claude Code finished without the required structured delegate report headings. Output: {output_path}")
        return 0
    except Exception as exc:
        if not execution_started:
            complete_startup_failure(str(exc), config_path, status_path, output_path, raw_stream_path, trace_path, config, status)
        raise
    finally:
        with contextlib.suppress(Exception):
            update_workflow_record(
                artifact_root,
                workflow_id,
                task_id,
                role,
                scope,
                tests,
                depends_on,
                run_id,
                config_path,
                status_path,
                output_path,
                prompt_path,
                raw_stream_path,
                trace_path,
                str(status.get("status") or "unknown"),
                safe_task_id(ns.review_for_task_id) if ns.review_for_task_id else None,
                ns.review_kind,
            )
        release_session_lease(session_state_path, session_state_lock_path, key, lease, run_id, fingerprint)
        if delegate_lock is not None:
            delegate_lock.release(remove=True)
        os.chdir(old_cwd)
