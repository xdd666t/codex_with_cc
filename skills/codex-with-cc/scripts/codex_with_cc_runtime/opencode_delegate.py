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

from .common import (
    ARTIFACT_SCHEMA_VERSION,
    DEFAULT_OPENCODE_ARTIFACT_DIR,
    INVOCATION_CONTRACT,
    OPENCODE_CHILD_MARKER_NAME,
    OPENCODE_CHILD_MARKER_VALUE,
    DelegateError,
    now_iso,
)
from .io_utils import read_text, test_path_writable, write_json, write_text
from .locks import FileLock, acquire_file_lock
from .opencode_cli import new_opencode_cli_args, retry_decision_opencode, update_opencode_stream_capture, non_json_raw_lines_opencode
from .opencode_prompts import build_opencode_prompt
from .paths import repo_root, workflow_relative_path, workflow_root
from .reports import build_report_repair_prompt, get_output_resolution, path_has_required_report_headings
from .sessions import (
    SessionLease,
    acquire_session_lease,
    effective_session_key,
    normalize_delegate_list,
    release_session_lease,
    reset_session_lease_for_fresh_session,
    safe_session_key,
    task_fingerprint,
)


def opencode_startup_failure_report(message: str) -> str:
    summary = f"STARTUP_FAILURE: {message}"
    return f"""Process Log
- Delegate worker failed before OpenCode execution started.
- Startup failure: {message}

Summary
The delegate run did not reach OpenCode execution.

Changed Files
None

Verification
- not run; delegate startup failed before worker execution

Final Result
FAIL / NEED_HUMAN_INTERVENTION
{summary}

Risks Or Follow-ups
- Retry only after the startup blocker is resolved.
"""


def complete_open_startup_failure(
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
    write_text(output_path, opencode_startup_failure_report(failure_message))
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
                    "sawStepFinish": False,
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


def run_opencode_delegate(ns: argparse.Namespace) -> int:
    if os.environ.get(OPENCODE_CHILD_MARKER_NAME) != OPENCODE_CHILD_MARKER_VALUE:
        raise DelegateError(
            f"delegate_to_opencode may only run inside a Codex spawn_agent child thread. Missing required child-thread marker '{OPENCODE_CHILD_MARKER_NAME}={OPENCODE_CHILD_MARKER_VALUE}'. Main-thread/direct invocation is forbidden."
        )
    if ns.task_file and str(ns.task_file).strip():
        task_file = Path(ns.task_file)
        if not task_file.exists():
            raise DelegateError(f"Task file was not found: {task_file}")
        task_text = read_text(task_file)
    else:
        task_text = ns.task or ""
    if not task_text.strip():
        raise DelegateError("Task text cannot be empty.")

    root = repo_root()
    rel = workflow_relative_path()
    entry_path = workflow_root() / "CODEX_WITH_CC.md"
    if not entry_path.exists():
        raise DelegateError(f"Missing workflow entry document: {entry_path}")

    artifact_root = Path(ns.artifact_root).resolve() if ns.artifact_root else (root / ".codex" / "codex_with_cc" / DEFAULT_OPENCODE_ARTIFACT_DIR).resolve()
    artifact_root.mkdir(parents=True, exist_ok=True)
    output_path = Path(ns.output_path).resolve() if ns.output_path else (artifact_root / f"opencode_PLACEHOLDER.md").resolve()

    scope = normalize_delegate_list(ns.scope)
    tests = normalize_delegate_list(ns.tests)
    key = effective_session_key(ns.session_key)
    safe_key = safe_session_key(key)
    session_pools_root = artifact_root / "session-pools"
    session_state_path = session_pools_root / f"{safe_key}.json"
    session_state_lock_path = session_pools_root / f"{safe_key}.lock"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    run_id = f"{timestamp}_{uuid.uuid4().hex[:8]}"
    effective_name = ns.name if ns.name else f"{ns.name_prefix}-{run_id}"
    if output_path.name == "opencode_PLACEHOLDER.md":
        output_path = artifact_root / f"opencode_{run_id}.md"
    status_path = artifact_root / f"status_{run_id}.json"
    config_path = artifact_root / f"config_{run_id}.json"
    prompt_path = artifact_root / f"prompt_{run_id}.md"
    raw_stream_path = artifact_root / f"stream_{run_id}.jsonl"
    trace_path = artifact_root / f"trace_{run_id}.log"
    lock_path = artifact_root / "delegate.lock"
    fingerprint = task_fingerprint(task_text, scope, tests, ns.mode)

    for path in (output_path, status_path, config_path, raw_stream_path, trace_path):
        test_path_writable(path)

    config: dict[str, Any] = {
        "artifactSchema": ARTIFACT_SCHEMA_VERSION,
        "invocationContract": INVOCATION_CONTRACT,
        "childThreadMarkerName": OPENCODE_CHILD_MARKER_NAME,
        "childThreadMarkerValidated": True,
        "executor": "opencode",
        "runId": run_id,
        "repoRoot": str(root),
        "workflowRoot": str(workflow_root()),
        "workflowRelativePath": rel,
        "mode": ns.mode,
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
        "lockPath": str(lock_path),
        "taskFile": str(Path(ns.task_file).resolve()) if ns.task_file else None,
        "variant": ns.variant if hasattr(ns, "variant") and ns.variant else None,
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
        "childThreadMarkerName": OPENCODE_CHILD_MARKER_NAME,
        "childThreadMarkerValidated": True,
        "executor": "opencode",
        "runId": run_id,
        "status": "starting",
        "pid": os.getpid(),
        "outputPath": str(output_path),
        "promptPath": str(prompt_path),
        "rawStreamPath": str(raw_stream_path),
        "tracePath": str(trace_path),
        "linesWritten": 0,
        "outputBytes": 0,
        "exitCode": None,
        "attemptCount": 0,
        "retryCount": 0,
        "maxRetryCount": int(ns.max_retry_count),
        "attempts": [],
    }
    prompt = build_opencode_prompt(root, output_path, run_id, ns.mode, scope, tests, task_text)
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
                return f"Another delegate_to_opencode run is still active. Use -AllowParallel to bypass, or wait. Lock: {lock_path}. Holder: {holder}"

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
                        "mode": ns.mode,
                    },
                    ensure_ascii=False,
                ).encode("utf-8")
                + b"\n"
            )
            delegate_lock.handle.flush()
    except Exception as exc:
        complete_open_startup_failure(str(exc), config_path, status_path, output_path, raw_stream_path, trace_path, config, status)
        raise

    old_cwd = Path.cwd()
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

        print(f"Delegating to OpenCode: {shutil.which('opencode') or '<dry-run>'}")
        print(f"RunId: {run_id}")
        print(f"Session Name: {effective_name}")
        print(f"Session Mode: {ns.session_mode}")
        print(f"Session Key: {key}")
        print(f"OpenCode Session Id: {lease.session_id}")
        print(f"Prompt: {prompt_path}")
        print(f"Output: {output_path}")
        print(f"Status: {status_path}")
        print(f"Trace: {trace_path}")
        print(f"Raw Stream: {raw_stream_path}")

        if ns.dry_run:
            print("Dry run enabled; OpenCode was not invoked.")
            status["status"] = "completed"
            status["exitCode"] = 0
            write_json(status_path, status)
            return 0

        opencode_bin = shutil.which("opencode")
        if not opencode_bin:
            raise DelegateError("OpenCode CLI was not found. Install or expose the 'opencode' command first.")

        variant = str(ns.variant) if hasattr(ns, "variant") and ns.variant else None

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

        discovered_session_id: str | None = lease.session_id if lease.session_id and lease.resume else None

        with raw_stream_path.open("w", encoding="utf-8") as raw_handle, trace_path.open("w", encoding="utf-8") as trace_handle:
            while attempt < max_attempts:
                attempt += 1
                capture_state: dict[str, Any] = {
                    "assistantTexts": [],
                    "traceLines": [],
                    "finalText": "",
                    "sawAssistantText": False,
                    "sawStepFinish": False,
                    "capturedFinalResultHeading": False,
                    "sessionId": None,
                }
                attempt_raw_lines: list[str] = []
                attempt_record: dict[str, Any] = {
                    "attempt": attempt,
                    "sessionId": discovered_session_id or "(new)",
                    "resume": bool(discovered_session_id),
                    "retryReason": None,
                    "exitCode": None,
                    "sawAssistantText": False,
                    "sawStepFinish": False,
                    "capturedFinalResult": False,
                    "outputWasNormalized": False,
                }
                opencode_args = new_opencode_cli_args(
                    ns.model,
                    effective_name,
                    discovered_session_id,
                    bool(ns.bypass_permissions),
                    variant,
                )
                if attempt == 1:
                    config["initialSessionId"] = discovered_session_id
                    config["initialResume"] = bool(discovered_session_id)
                config["sessionId"] = discovered_session_id
                config["resume"] = bool(discovered_session_id)
                config["attemptCount"] = attempt
                config["retryCount"] = retry_count
                status["attemptCount"] = attempt
                status["retryCount"] = retry_count
                status["attempts"].append(attempt_record)
                write_json(config_path, config)
                write_json(status_path, status)
                print(f"Attempt {attempt}/{max_attempts}")
                print(f"OpenCode Session Id: {discovered_session_id or '(new)'}")
                arg_text = f"--session {discovered_session_id}" if discovered_session_id else "(new session, auto-assigned)"
                print(f"OpenCode Session Argument: {arg_text}")
                trace_handle.write(f"[attempt] {attempt} session={discovered_session_id} resume={bool(discovered_session_id)}\n")
                trace_handle.flush()

                process = subprocess.Popen(
                    [opencode_bin, "run", *opencode_args, prompt_text],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    cwd=str(root),
                )
                assert process.stdout is not None
                for line in process.stdout:
                    line_text = line.rstrip("\r\n")
                    if not line_text.strip():
                        continue
                    attempt_raw_lines.append(line_text)
                    raw_handle.write(line_text + "\n")
                    raw_handle.flush()
                    try:
                        record = json.loads(line_text)
                        trace_lines = update_opencode_stream_capture(record, capture_state)
                    except json.JSONDecodeError:
                        trace_lines = ["[raw] non-json output line"]
                    for trace_line in trace_lines:
                        trace_handle.write(trace_line + "\n")
                    trace_handle.flush()
                    status["linesWritten"] = int(status.get("linesWritten", 0)) + 1
                    status["lastOutputAt"] = now_iso()
                    write_json(status_path, status)
                    if not discovered_session_id and capture_state.get("sessionId"):
                        discovered_session_id = str(capture_state["sessionId"])
                        trace_handle.write(f"[session-discovered] {discovered_session_id}\n")
                        trace_handle.flush()
                exit_code = process.wait()

                final_text = str(capture_state.get("finalText") or "")
                if not final_text.strip() and capture_state["assistantTexts"]:
                    final_text = str(capture_state["assistantTexts"][-1]).strip()
                decision = retry_decision_opencode(
                    attempt_raw_lines,
                    bool(capture_state["sawAssistantText"]),
                    bool(capture_state["sawStepFinish"]),
                    bool(capture_state["capturedFinalResultHeading"]),
                    exit_code,
                )
                attempt_record.update(
                    {
                        "exitCode": exit_code,
                        "sawAssistantText": bool(capture_state["sawAssistantText"]),
                        "sawStepFinish": bool(capture_state["sawStepFinish"]),
                        "capturedFinalResult": bool(capture_state["capturedFinalResultHeading"]),
                    }
                )
                if decision["shouldRetry"] and attempt < max_attempts:
                    retry_count += 1
                    attempt_record["retryReason"] = decision["retryReason"]
                    status["retryCount"] = retry_count
                    status["lastRetryReason"] = decision["retryReason"]
                    write_json(status_path, status)
                    if decision["retryWithFreshSession"]:
                        print(f"WARNING: OpenCode session '{discovered_session_id}' failed. Retrying with a fresh session.", file=sys.stderr)
                        trace_handle.write("[retry] session failed; resetting for a fresh OpenCode session\n")
                        trace_handle.flush()
                        discovered_session_id = None
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
                        print("WARNING: OpenCode completed without the required report headings. Retrying once for structured report repair.", file=sys.stderr)
                        trace_handle.write("[retry] unstructured success; asking the same OpenCode session to emit the required report headings\n")
                        trace_handle.flush()
                        prompt_text = build_report_repair_prompt(output_path, final_text)
                    else:
                        print("WARNING: OpenCode startup failed before structured output was produced. Retrying once with the current session arguments.", file=sys.stderr)
                        trace_handle.write("[retry] startup failed before structured output; retrying with current session\n")
                        trace_handle.flush()
                    continue
                if decision["shouldRetry"]:
                    opencode_exit_code = exit_code
                    exit_code = 1
                    attempt_record["opencodeExitCode"] = opencode_exit_code
                    attempt_record["exitCode"] = exit_code
                    failure_disposition = "NEED_HUMAN_INTERVENTION"
                    failure_summary_text = f"NEED_HUMAN_INTERVENTION after exhausting retry budget. retryReason={decision.get('retryReason')}. attempt {attempt}/{max_attempts}. exitCode={exit_code}."
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
                    final_text = f"""Process Log
- Delegate worker detected a retryable OpenCode failure and exhausted the configured retry budget.
- Automatic recovery stopped to avoid unbounded compute burn.

Summary
Automatic retry recovery hit the configured ceiling and requires human or Codex intervention.

Changed Files
None

Verification
- not run; the worker never reached a trustworthy execution state

Final Result
FAIL / NEED_HUMAN_INTERVENTION
{failure_summary_text}

Risks Or Follow-ups
- Inspect OpenCode CLI startup/session health before retrying this delegated task again.
"""
                output_resolution = get_output_resolution(
                    final_text,
                    output_path,
                    exit_code,
                    bool(capture_state.get("sawStepFinish", False)),
                    bool(capture_state["capturedFinalResultHeading"]),
                )
                if output_resolution["existingStructuredOutput"] or output_resolution["finalTextHasFinalResult"]:
                    attempt_record["capturedFinalResult"] = True
                if output_resolution["outputWasNormalized"]:
                    attempt_record["capturedFinalResult"] = True
                    attempt_record["outputWasNormalized"] = True
                    attempt_record["opencodeExitCode"] = exit_code
                    exit_code = 1
                    attempt_record["exitCode"] = exit_code
                    failure_disposition = "NEED_HUMAN_INTERVENTION"
                    failure_summary_text = (
                        "UNSTRUCTURED_SUCCESS_REJECTED: OpenCode exited with code 0 but did not produce the required "
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
            write_text(output_path, "OpenCode delegate finished without a structured text result.")
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
                raise DelegateError(f"OpenCode delegate retry ceiling reached: {failure_summary_text}")
            raise DelegateError(f"OpenCode exited with code {exit_code}")
        if status["status"] != "completed":
            if failure_disposition == "NEED_HUMAN_INTERVENTION":
                raise DelegateError(f"OpenCode delegate retry ceiling reached: {failure_summary_text}")
            raise DelegateError(f"OpenCode finished without the required structured delegate report headings. Output: {output_path}")
        return 0
    finally:
        release_session_lease(session_state_path, session_state_lock_path, key, lease, run_id, fingerprint)
        if delegate_lock is not None:
            delegate_lock.release(remove=True)
        os.chdir(old_cwd)
