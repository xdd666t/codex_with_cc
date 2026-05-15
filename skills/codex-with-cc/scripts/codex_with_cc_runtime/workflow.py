from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .common import ARTIFACT_SCHEMA_VERSION, INVOCATION_CONTRACT, WORKER_ROLES, now_iso
from .io_utils import load_json, write_json
from .reports import parse_report_final_result, parse_report_role, parse_report_status, report_summary_line


REQUIRED_IMPLEMENTER_REVIEWS = ("spec", "quality")


def safe_workflow_id(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", value.strip())
    return safe or "workflow"


def safe_task_id(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", value.strip())
    return safe or "task"


def workflow_path(artifact_root: Path | str, workflow_id: str) -> Path:
    return Path(artifact_root).resolve() / f"workflow_{safe_workflow_id(workflow_id)}.json"


def normalize_role(role: str) -> str:
    value = role.strip().lower()
    if value not in WORKER_ROLES:
        expected = ", ".join(WORKER_ROLES)
        raise ValueError(f"invalid role: {role!r} (choose from {expected})")
    return value


def empty_workflow(workflow_id: str) -> dict[str, Any]:
    now = now_iso()
    return {
        "artifactSchema": ARTIFACT_SCHEMA_VERSION,
        "invocationContract": INVOCATION_CONTRACT,
        "workflowId": workflow_id,
        "createdAt": now,
        "updatedAt": now,
        "tasks": {},
        "runs": {},
    }


def review_decision_for(run_status: str, report_status: str, report_final_result: str) -> str:
    if run_status != "completed":
        return "failed"
    if report_status == "DONE" and report_final_result == "DONE":
        return "accepted"
    if report_status in ("DONE_WITH_CONCERNS", "NEEDS_CONTEXT", "BLOCKED"):
        return "needs-review"
    return "rejected"


def implementer_review_decision(task: dict[str, Any]) -> str:
    if task.get("status") != "completed":
        return "failed"
    if task.get("lastReportStatus") != "DONE" or task.get("lastReportFinalResult") != "DONE":
        return "needs-review"
    reviews = task.get("reviews") if isinstance(task.get("reviews"), dict) else {}
    missing = [kind for kind in REQUIRED_IMPLEMENTER_REVIEWS if not isinstance(reviews.get(kind), dict)]
    if missing:
        return "pending-review"
    if all((reviews[kind] or {}).get("reviewDecision") == "accepted" for kind in REQUIRED_IMPLEMENTER_REVIEWS):
        return "accepted"
    return "needs-review"


def update_workflow_acceptance(workflow: dict[str, Any]) -> None:
    tasks = workflow.get("tasks") if isinstance(workflow.get("tasks"), dict) else {}
    implementers = [task for task in tasks.values() if isinstance(task, dict) and task.get("role") == "implementer"]
    if not implementers:
        workflow["finalAcceptance"] = {"status": "accepted", "reason": "no implementer tasks require review"}
        return
    pending = [task.get("taskId") for task in implementers if task.get("reviewDecision") != "accepted"]
    workflow["finalAcceptance"] = {
        "status": "accepted" if not pending else "pending-review",
        "pendingTasks": pending,
    }


def update_workflow_record(
    artifact_root: Path,
    workflow_id: str,
    task_id: str,
    role: str,
    scope: list[str],
    verification: list[str],
    depends_on: list[str],
    run_id: str,
    config_path: Path,
    status_path: Path,
    output_path: Path,
    prompt_path: Path,
    raw_stream_path: Path,
    trace_path: Path,
    run_status: str,
    review_for_task_id: str | None = None,
    review_kind: str | None = None,
) -> Path:
    path = workflow_path(artifact_root, workflow_id)
    if path.exists():
        workflow = load_json(path)
    else:
        workflow = empty_workflow(workflow_id)
    report_text = output_path.read_text(encoding="utf-8") if output_path.exists() else ""
    report_status = parse_report_status(report_text)
    report_final_result = parse_report_final_result(report_text)
    report_role = parse_report_role(report_text)
    report_summary = report_summary_line(report_text)
    review_decision = review_decision_for(run_status, report_status, report_final_result)
    workflow["artifactSchema"] = ARTIFACT_SCHEMA_VERSION
    workflow["invocationContract"] = INVOCATION_CONTRACT
    workflow["workflowId"] = workflow_id
    workflow["updatedAt"] = now_iso()
    workflow.setdefault("tasks", {})
    workflow.setdefault("runs", {})
    task = workflow["tasks"].setdefault(
        task_id,
        {
            "taskId": task_id,
            "role": role,
            "scope": scope,
            "verification": verification,
            "dependsOn": depends_on,
            "runs": [],
            "status": run_status,
        },
    )
    task["role"] = role
    task["scope"] = scope
    task["verification"] = verification
    task["dependsOn"] = depends_on
    task["status"] = run_status
    task["lastReportStatus"] = report_status
    task["lastReportFinalResult"] = report_final_result
    task["lastReportRole"] = report_role
    task["reviewDecision"] = review_decision
    if role == "implementer":
        task.setdefault("reviews", {})
        task["reviewDecision"] = implementer_review_decision(task)
    if role == "reviewer":
        task["reviewForTaskId"] = review_for_task_id
        task["reviewKind"] = review_kind
    if run_id not in task["runs"]:
        task["runs"].append(run_id)
    workflow["runs"][run_id] = {
        "runId": run_id,
        "taskId": task_id,
        "role": role,
        "status": run_status,
        "reportStatus": report_status,
        "reportFinalResult": report_final_result,
        "reportRole": report_role,
        "reportSummary": report_summary,
        "reviewDecision": review_decision,
        "reviewForTaskId": review_for_task_id,
        "reviewKind": review_kind,
        "configPath": str(config_path),
        "statusPath": str(status_path),
        "outputPath": str(output_path),
        "promptPath": str(prompt_path),
        "rawStreamPath": str(raw_stream_path),
        "tracePath": str(trace_path),
    }
    if role == "reviewer" and review_for_task_id and review_kind:
        target = workflow["tasks"].setdefault(
            review_for_task_id,
            {
                "taskId": review_for_task_id,
                "role": "implementer",
                "scope": [],
                "verification": [],
                "dependsOn": [],
                "runs": [],
                "status": "unknown",
            },
        )
        reviews = target.setdefault("reviews", {})
        reviews[review_kind] = {
            "runId": run_id,
            "taskId": task_id,
            "status": run_status,
            "reportStatus": report_status,
            "reportFinalResult": report_final_result,
            "reportRole": report_role,
            "reviewDecision": review_decision,
        }
        target["reviewDecision"] = implementer_review_decision(target)
    for candidate in workflow["tasks"].values():
        if isinstance(candidate, dict) and candidate.get("role") == "implementer":
            candidate["reviewDecision"] = implementer_review_decision(candidate)
    update_workflow_acceptance(workflow)
    write_json(path, workflow)
    return path
