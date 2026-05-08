from __future__ import annotations

import re
from typing import Any, Iterable

from .reports import text_has_required_report_headings


def new_opencode_cli_args(
    model: str,
    title: str,
    session_id: str | None,
    bypass_permissions: bool,
    variant: str | None,
) -> list[str]:
    args: list[str] = [
        "--format",
        "json",
    ]
    if bypass_permissions:
        args.append("--dangerously-skip-permissions")
    if model:
        args.extend(["--model", model])
    if title:
        args.extend(["--title", title])
    if session_id:
        args.extend(["--session", session_id])
    if variant:
        args.extend(["--variant", variant])
    return args


def update_opencode_stream_capture(record: dict[str, Any], state: dict[str, Any]) -> list[str]:
    state.setdefault("assistantTexts", [])
    state.setdefault("traceLines", [])
    state.setdefault("finalText", "")
    state.setdefault("sawAssistantText", False)
    state.setdefault("sawStepFinish", False)
    state.setdefault("capturedFinalResultHeading", False)
    state.setdefault("sessionId", None)

    trace_lines: list[str] = []
    record_type = str(record.get("type", ""))

    if record_type == "step_start":
        session_id = str(record.get("sessionID", ""))
        state["sessionId"] = session_id
        trace_lines.append(f"[step_start] session={session_id}")

    elif record_type == "step_finish":
        state["sawStepFinish"] = True
        part = record.get("part") if isinstance(record.get("part"), dict) else {}
        reason = str(part.get("reason", ""))
        tokens = part.get("tokens", {})
        cost = part.get("cost")
        parts = [f"[step_finish] reason={reason}"]
        if tokens:
            parts.append(f"tokens={tokens}")
        if cost is not None:
            parts.append(f"cost={cost}")
        trace_lines.append(" ".join(parts))

    elif record_type == "text":
        part = record.get("part") if isinstance(record.get("part"), dict) else {}
        text = str(part.get("text", "")).strip()
        if text:
            state["sawAssistantText"] = True
            if text_has_required_report_headings(text):
                state["capturedFinalResultHeading"] = True
            state["assistantTexts"].append(text)
            state["finalText"] = text
        trace_lines.append("[text]")

    elif record_type == "tool_use":
        part = record.get("part") if isinstance(record.get("part"), dict) else {}
        tool_name = str(part.get("tool", ""))
        tool_state = part.get("state") if isinstance(part.get("state"), dict) else {}
        status = str(tool_state.get("status", ""))
        trace_lines.append(f"[tool_use] tool={tool_name} status={status}")

    elif record_type:
        trace_lines.append(f"[{record_type}]")
    else:
        trace_lines.append("[unknown-record]")

    state["traceLines"].extend(trace_lines)
    return trace_lines


def non_json_raw_lines_opencode(raw_lines: Iterable[str]) -> list[str]:
    import json

    out: list[str] = []
    for line in raw_lines:
        if not str(line).strip():
            continue
        try:
            json.loads(str(line))
        except json.JSONDecodeError:
            out.append(str(line).strip())
    return out


def retry_decision_opencode(
    raw_lines: Iterable[str],
    saw_assistant_text: bool,
    saw_step_finish: bool,
    captured_final_result_heading: bool,
    exit_code: int,
) -> dict[str, Any]:
    has_structured_success = (
        saw_step_finish
        and captured_final_result_heading
        and exit_code == 0
    )
    decision: dict[str, Any] = {
        "shouldRetry": False,
        "retryReason": "",
        "retryWithFreshSession": False,
        "hasStructuredSuccess": has_structured_success,
        "exitCode": exit_code,
        "sawAssistantText": saw_assistant_text,
        "sawStepFinish": saw_step_finish,
        "capturedFinalResultHeading": captured_final_result_heading,
        "retryWithReportRepair": False,
    }
    if exit_code == 0 and saw_step_finish and saw_assistant_text and not has_structured_success:
        decision.update(
            {
                "shouldRetry": True,
                "retryReason": "unstructured_success_report",
                "retryWithFreshSession": False,
                "retryWithReportRepair": True,
            }
        )
    return decision
