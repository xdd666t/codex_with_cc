from __future__ import annotations

import json
import re
from typing import Any, Iterable

from .reports import text_has_required_report_headings



def text_blocks(content: Any) -> list[str]:
    if content is None:
        return []
    items = content if isinstance(content, list) else [content]
    out: list[str] = []
    for item in items:
        if isinstance(item, dict) and item.get("type") == "text" and str(item.get("text", "")).strip():
            out.append(str(item["text"]))
    return out



def update_stream_capture(record: dict[str, Any], state: dict[str, Any]) -> list[str]:
    state.setdefault("assistantTexts", [])
    state.setdefault("traceLines", [])
    state.setdefault("finalText", "")
    state.setdefault("sawAssistantText", False)
    state.setdefault("sawResultSuccess", False)
    state.setdefault("capturedFinalResultHeading", False)

    trace_lines: list[str] = []
    record_type = str(record.get("type", ""))
    if record_type == "system":
        parts = ["[system]"]
        if record.get("subtype"):
            parts.append(str(record["subtype"]))
        if record.get("status"):
            parts.append(str(record["status"]))
        trace_lines.append(" ".join(parts))
    elif record_type == "assistant":
        message = record.get("message") if isinstance(record.get("message"), dict) else {}
        message_id = message.get("id")
        trace_lines.append(f"[assistant] message={message_id}" if message_id else "[assistant]")
        texts = text_blocks(message.get("content"))
        if texts:
            text = "\n".join(texts).strip()
            if text:
                state["sawAssistantText"] = True
                if text_has_required_report_headings(text):
                    state["capturedFinalResultHeading"] = True
                state["assistantTexts"].append(text)
                state["finalText"] = text
    elif record_type == "result":
        subtype = str(record.get("subtype", ""))
        line = "[result]"
        if subtype:
            line += f" {subtype}"
        if record.get("cost_usd") is not None:
            line += f" cost={record['cost_usd']}"
        if subtype == "success":
            state["sawResultSuccess"] = True
        trace_lines.append(line)
    elif record_type == "stream_event":
        event = record.get("event") if isinstance(record.get("event"), dict) else {}
        event_type = str(event.get("type", ""))
        trace_lines.append(f"[stream] {event_type}" if event_type else "[stream]")
    elif record_type:
        trace_lines.append(f"[{record_type}]")
    else:
        trace_lines.append("[unknown-record]")
    state["traceLines"].extend(trace_lines)
    return trace_lines



def new_claude_cli_args(
    model: str,
    session_name: str,
    session_id: str,
    resume: bool,
    max_budget_usd: str | None,
    bypass_permissions: bool,
) -> list[str]:
    args: list[str] = [
        "--verbose",
        "--print",
        "--output-format",
        "stream-json",
        "--input-format",
        "text",
    ]
    if model:
        args.extend(["--model", model])
    args.extend(
        [
            "--name",
            session_name,
            "--permission-mode",
            "acceptEdits",
        ]
    )
    args.extend(["--resume" if resume else "--session-id", session_id])
    if max_budget_usd not in (None, ""):
        args.extend(["--max-budget-usd", str(max_budget_usd)])
    if bypass_permissions:
        args.append("--dangerously-skip-permissions")
    return args



def non_json_raw_lines(raw_lines: Iterable[str]) -> list[str]:
    out: list[str] = []
    for line in raw_lines:
        if not str(line).strip():
            continue
        try:
            json.loads(str(line))
        except json.JSONDecodeError:
            out.append(str(line).strip())
    return out



def retry_decision(
    raw_lines: Iterable[str],
    resume_attempt: bool,
    exit_code: int,
    saw_assistant_text: bool,
    saw_result_success: bool,
    captured_final_result_heading: bool,
) -> dict[str, Any]:
    joined = "\n".join(non_json_raw_lines(raw_lines))
    saw_stale = re.search(r"No conversation found.*session ID", joined) is not None
    saw_stream_json = re.search(r"stream-json.*requires.*--verbose", joined) is not None
    has_structured_success = saw_result_success and captured_final_result_heading
    decision = {
        "shouldRetry": False,
        "retryReason": "",
        "retryWithFreshSession": False,
        "sawStaleSessionText": saw_stale,
        "sawStreamJsonVerboseError": saw_stream_json,
        "hasStructuredSuccess": has_structured_success,
        "exitCode": exit_code,
        "sawAssistantText": saw_assistant_text,
        "sawResultSuccess": saw_result_success,
        "capturedFinalResultHeading": captured_final_result_heading,
        "retryWithReportRepair": False,
    }
    if resume_attempt and saw_stale and not has_structured_success:
        decision.update({"shouldRetry": True, "retryReason": "stale_claude_session", "retryWithFreshSession": True})
    elif saw_stream_json and not has_structured_success:
        decision.update({"shouldRetry": True, "retryReason": "stream_json_startup", "retryWithFreshSession": False})
    elif exit_code == 0 and saw_result_success and saw_assistant_text and not has_structured_success:
        decision.update(
            {
                "shouldRetry": True,
                "retryReason": "unstructured_success_report",
                "retryWithFreshSession": False,
                "retryWithReportRepair": True,
            }
        )
    return decision



def failure_summary(raw_lines: Iterable[str], retry_reason: str | None, attempt_count: int, max_retry_count: int, exit_code: int) -> str:
    seen: list[str] = []
    for line in non_json_raw_lines(raw_lines):
        if line not in seen:
            seen.append(line)
        if len(seen) >= 2:
            break
    snippet = " | ".join(seen) if seen else "No non-JSON stderr summary was captured."
    reason = retry_reason or "unknown_retry_condition"
    max_attempts = max_retry_count + 1
    return (
        f"NEED_HUMAN_INTERVENTION after exhausting retry budget. retryReason={reason}. "
        f"attempt {attempt_count}/{max_attempts}. exitCode={exit_code}. {snippet}"
    )
