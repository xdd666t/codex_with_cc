from __future__ import annotations

import argparse
import contextlib
import dataclasses
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable


ARTIFACT_SCHEMA_VERSION = 2
INVOCATION_CONTRACT = "spawn_agent_child_only"
CHILD_MARKER_NAME = "CODEX_CLAUDE_CHILD_THREAD"
CHILD_MARKER_VALUE = "1"
REPORT_HEADINGS = (
    "Process Log",
    "Summary",
    "Changed Files",
    "Verification",
    "Final Result",
    "Risks Or Follow-ups",
)
SKILL_NAME = "codex-with-cc"


class DelegateError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def runtime_python_root() -> Path:
    return Path(__file__).resolve().parent


def workflow_root() -> Path:
    return runtime_python_root().parent


def codex_home() -> Path:
    raw = os.environ.get("CODEX_HOME")
    return Path(raw).expanduser().resolve() if raw else (Path.home() / ".codex").resolve()


def repo_root() -> Path:
    root = workflow_root()
    container = root.parent
    if root.name == SKILL_NAME and container.name == "skills":
        if same_path(container.parent, codex_home()):
            return Path.cwd().resolve()
        if container.parent.name == ".codex":
            return container.parent.parent.resolve()
    if container.name in ("docs", "doc"):
        return container.parent.resolve()
    return container.resolve()


def workflow_relative_path() -> str:
    root = workflow_root().resolve()
    repo = repo_root().resolve()
    try:
        return root.relative_to(repo).as_posix()
    except ValueError:
        return root.as_posix()


def script_family() -> str:
    return "windows_scripts" if os.name == "nt" else "macos_scripts"


def script_ext() -> str:
    return ".ps1" if os.name == "nt" else ".sh"


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data["updatedAt"] = now_iso()
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    temp_path = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        temp_path.write_text(payload, encoding="utf-8")
        os.replace(temp_path, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temp_path.unlink()


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y")
    return bool(value)


def same_path(a: str | Path, b: str | Path) -> bool:
    left = os.path.normcase(os.path.realpath(os.path.abspath(os.fspath(a))))
    right = os.path.normcase(os.path.realpath(os.path.abspath(os.fspath(b))))
    return left == right


def test_final_result_heading(text: str | None) -> bool:
    if not text or not text.strip():
        return False
    return report_heading_match(text, "Final Result") is not None


def report_heading_match(text: str, heading: str) -> re.Match[str] | None:
    pattern = rf"(?m)^\s*(?:#+\s*)?(?:\*\*)?{re.escape(heading)}(?:\*\*)?\s*$"
    return re.search(pattern, text)


def text_has_required_report_headings(text: str | None) -> bool:
    if not text or not text.strip():
        return False
    positions: list[int] = []
    for heading in REPORT_HEADINGS:
        match = report_heading_match(text, heading)
        if match is None:
            return False
        positions.append(match.start())
    return positions == sorted(positions)


def path_has_final_result(path: Path | str | None) -> bool:
    if not path:
        return False
    path = Path(path)
    if not path.exists():
        return False
    return test_final_result_heading(read_text(path))


def path_has_required_report_headings(path: Path | str | None) -> bool:
    if not path:
        return False
    path = Path(path)
    if not path.exists():
        return False
    return text_has_required_report_headings(read_text(path))


def convert_unstructured_final_text(text: str | None) -> str:
    trimmed = (text or "").strip()
    if not trimmed:
        return ""
    if text_has_required_report_headings(trimmed):
        return trimmed
    return f"""Process Log
- Claude Code exited successfully but did not produce the required delegate report headings.
- The delegate wrapper rejected that unstructured response and preserved it below for audit.

Summary
Claude Code did not satisfy the delegate report contract. Treat this run as failed even though the Claude CLI process exited with code 0.

Changed Files
Unknown from unstructured response; inspect repository diff and raw delegate artifacts before accepting file-level conclusions.

Verification
Unknown from unstructured response; do not treat verification as proven unless the original response below lists exact commands and outcomes.

Final Result
UNSTRUCTURED_SUCCESS_REJECTED
{trimmed}

Risks Or Follow-ups
- Retry after fixing prompt/session handling, or rerun with a fresh session if the response indicates stale context.
"""


def get_output_resolution(
    final_text: str,
    output_path: Path,
    exit_code: int,
    saw_result_success: bool,
    captured_final_result_heading: bool,
) -> dict[str, Any]:
    final_has = text_has_required_report_headings(final_text)
    existing_structured = path_has_required_report_headings(output_path)
    normalized = (
        exit_code == 0
        and saw_result_success
        and not final_has
        and not existing_structured
        and bool(final_text.strip())
    )
    persisted = convert_unstructured_final_text(final_text) if normalized else final_text
    persisted_has = text_has_required_report_headings(persisted)
    should_persist = persisted_has or (not existing_structured and bool(final_text.strip()))
    delegate_succeeded = exit_code == 0 and saw_result_success and (final_has or existing_structured)
    return {
        "finalTextHasFinalResult": final_has,
        "existingStructuredOutput": existing_structured,
        "outputWasNormalized": normalized,
        "persistedFinalText": persisted,
        "shouldPersistFinalText": should_persist,
        "delegateSucceeded": delegate_succeeded,
    }


def build_report_repair_prompt(output_path: Path, previous_text: str) -> str:
    return f"""Your previous response did not satisfy the required delegate report contract.

Do not make new edits unless you discover your previous work was incomplete. Do not ask what to do next.
Use the completed work and verification from this same Claude session to write the final delegate report.

Write the report to this path if you choose to write a file, and also return the report as your final response:
{output_path}

Your final response must start with `Process Log` on the first line and must include exactly these headings in this order:

Process Log
- <what you did>

Summary
<brief result>

Changed Files
- <path or None>

Verification
- <command and outcome>

Final Result
<PASS, FAIL, or blocked result>

Risks Or Follow-ups
- <risk or None>

Previous non-compliant response:
{previous_text.strip()}
"""


def test_path_writable(path: Path) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    probe = path.parent / f".write_probe_{uuid.uuid4().hex}.tmp"
    try:
        write_text(probe, "ok")
        probe.unlink()
    except Exception as exc:  # pragma: no cover - message path
        raise DelegateError(f"Path is not writable: {path}. {exc}") from exc


class FileLock:
    def __init__(self, path: Path):
        self.path = path
        self.handle: Any | None = None

    def try_acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            self.handle.close()
            self.handle = None
            return False

    def release(self, remove: bool = False) -> None:
        if self.handle is not None:
            try:
                if os.name == "nt":
                    import msvcrt

                    self.handle.seek(0)
                    with contextlib.suppress(OSError):
                        msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    with contextlib.suppress(OSError):
                        fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            finally:
                self.handle.close()
                self.handle = None
        if remove:
            with contextlib.suppress(FileNotFoundError):
                self.path.unlink()


def acquire_file_lock(path: Path, timeout_seconds: int, poll_seconds: float, message: Callable[[], str]) -> FileLock:
    if timeout_seconds < 0:
        raise DelegateError(f"LockTimeoutSeconds must be >= 0. Current: {timeout_seconds}")
    if poll_seconds < 0.05:
        raise DelegateError(f"LockPollMilliseconds must be >= 50. Current: {int(poll_seconds * 1000)}")
    deadline = time.monotonic() + max(0, timeout_seconds)
    while True:
        lock = FileLock(path)
        if lock.try_acquire():
            return lock
        if time.monotonic() >= deadline:
            raise DelegateError(message())
        time.sleep(poll_seconds)


def pid_alive(pid: Any) -> bool:
    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return False
    if pid_int <= 0:
        return False
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid_int}", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return str(pid_int) in result.stdout
        except Exception:
            return False
    try:
        os.kill(pid_int, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def new_session_id() -> str:
    return str(uuid.uuid4())


def effective_session_key(value: str | None) -> str:
    if value and value.strip():
        return value
    for env_name in ("CODEX_THREAD_ID", "CODEX_SESSION_ID"):
        env_value = os.environ.get(env_name)
        if env_value and env_value.strip():
            return env_value
    print(
        "WARNING: Using default Claude session key fallback. Pass -SessionKey explicitly or set CODEX_THREAD_ID / CODEX_SESSION_ID to avoid unintended session sharing.",
        file=sys.stderr,
    )
    return "default"


def safe_session_key(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", value)
    return safe or "default"


def normalize_delegate_list(items: Iterable[str] | None) -> list[str]:
    values: list[str] = []
    for item in items or []:
        if item is None:
            continue
        for part in re.split(r"\s*;\s*", str(item)):
            part = part.strip()
            if part:
                values.append(part)
    return values


def task_fingerprint(text: str, scope_items: list[str], test_items: list[str], task_mode: str) -> str:
    prefix = text[:1000]
    raw = "\n".join(
        (
            f"mode={task_mode}",
            f"scope={'|'.join(sorted(scope_items))}",
            f"tests={'|'.join(sorted(test_items))}",
            f"task={prefix}",
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def lease_expired(item: dict[str, Any] | None, timeout_seconds: int) -> bool:
    if item is None or timeout_seconds < 0 or item.get("status") != "leased":
        return False
    leased_at = item.get("leasedAt")
    if not leased_at:
        return True
    try:
        dt = datetime.fromisoformat(str(leased_at).replace("Z", "+00:00"))
    except ValueError:
        return True
    return (datetime.now(dt.tzinfo or timezone.utc) - dt).total_seconds() >= timeout_seconds


def new_session_pool_state(key: str) -> dict[str, Any]:
    now = now_iso()
    return {
        "version": 1,
        "sessionKey": key,
        "createdAt": now,
        "updatedAt": now,
        "primary": {
            "sessionId": None,
            "status": "available",
            "leaseRunId": None,
            "leasePid": None,
            "leasedAt": None,
            "lastUsedAt": None,
            "lastRunId": None,
            "lastResetAt": None,
            "lastResetReason": None,
            "lastResetFromSessionId": None,
            "lastResetFromRunId": None,
        },
        "parallelPool": [],
    }


def ensure_slot_fields(slot: dict[str, Any], include_fingerprint: bool = False) -> None:
    for name in ("lastResetAt", "lastResetReason", "lastResetFromSessionId", "lastResetFromRunId", "leasePid"):
        slot.setdefault(name, None)
    if include_fingerprint:
        slot.setdefault("lastTaskFingerprint", None)


def read_session_pool_state(path: Path, key: str) -> dict[str, Any]:
    if not path.exists():
        return new_session_pool_state(key)
    state = load_json(path)
    if not isinstance(state.get("primary"), dict):
        state["primary"] = new_session_pool_state(key)["primary"]
    if not isinstance(state.get("parallelPool"), list):
        state["parallelPool"] = []
    ensure_slot_fields(state["primary"])
    for slot in state["parallelPool"]:
        if isinstance(slot, dict):
            ensure_slot_fields(slot, include_fingerprint=True)
    return state


def write_session_pool_state(path: Path, state: dict[str, Any]) -> None:
    write_json(path, state)


def update_session_state(
    state_path: Path,
    lock_path: Path,
    key: str,
    timeout_seconds: int,
    update: Callable[[dict[str, Any]], Any],
) -> Any:
    lock = acquire_file_lock(
        lock_path,
        timeout_seconds,
        0.1,
        lambda: f"Timed out waiting for Claude session pool lock: {lock_path}",
    )
    try:
        state = read_session_pool_state(state_path, key)
        result = update(state)
        write_session_pool_state(state_path, state)
        return result
    finally:
        lock.release(remove=False)


@dataclasses.dataclass
class SessionLease:
    mode: str
    session_id: str
    resume: bool
    pool_index: int | None
    leased: bool = True

    def to_config(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "sessionId": self.session_id,
            "resume": self.resume,
            "poolIndex": self.pool_index,
            "leased": self.leased,
        }


def acquire_session_lease(
    state_path: Path,
    lock_path: Path,
    key: str,
    mode: str,
    run_id: str,
    fingerprint: str,
    lease_timeout_seconds: int,
    wait_seconds: int,
    reset_primary: bool,
    reset_pool: bool,
) -> SessionLease:
    deadline = time.monotonic() + max(0, wait_seconds)

    def reclaim(state: dict[str, Any]) -> None:
        primary = state["primary"]
        if lease_expired(primary, lease_timeout_seconds) or (
            primary.get("status") == "leased" and primary.get("leasePid") and not pid_alive(primary.get("leasePid"))
        ):
            primary["status"] = "available"
            primary["leaseRunId"] = None
            primary["leasePid"] = None
            primary["leasedAt"] = None
        for slot in state["parallelPool"]:
            if lease_expired(slot, lease_timeout_seconds) or (
                slot.get("status") == "leased" and slot.get("leasePid") and not pid_alive(slot.get("leasePid"))
            ):
                slot["status"] = "available"
                slot["leaseRunId"] = None
                slot["leasePid"] = None
                slot["leasedAt"] = None

    while True:
        def updater(state: dict[str, Any]) -> SessionLease | None:
            if reset_primary:
                state["primary"].update(
                    {
                        "sessionId": None,
                        "status": "available",
                        "leaseRunId": None,
                        "leasePid": None,
                        "leasedAt": None,
                        "lastUsedAt": None,
                        "lastRunId": None,
                    }
                )
            if reset_pool:
                state["parallelPool"] = []
            reclaim(state)
            now = now_iso()
            if mode in ("PrimaryReuse", "PrimaryAnchor"):
                primary = state["primary"]
                if primary.get("status") == "leased":
                    return None
                resume = bool(primary.get("sessionId"))
                if not resume:
                    primary["sessionId"] = new_session_id()
                primary["status"] = "leased"
                primary["leaseRunId"] = run_id
                primary["leasePid"] = os.getpid()
                primary["leasedAt"] = now
                return SessionLease(mode, str(primary["sessionId"]), resume, None)

            pool = state["parallelPool"]
            available: list[tuple[int, dict[str, Any], bool]] = []
            for index, item in enumerate(pool):
                if item.get("status") != "leased":
                    available.append((index, item, str(item.get("lastTaskFingerprint")) == fingerprint))
            if not available:
                item = {
                    "sessionId": new_session_id(),
                    "status": "leased",
                    "leaseRunId": run_id,
                    "leasePid": os.getpid(),
                    "leasedAt": now,
                    "lastUsedAt": None,
                    "lastRunId": None,
                    "lastTaskFingerprint": fingerprint,
                    "lastResetAt": None,
                    "lastResetReason": None,
                    "lastResetFromSessionId": None,
                    "lastResetFromRunId": None,
                }
                pool.append(item)
                return SessionLease(mode, str(item["sessionId"]), False, len(pool) - 1)
            available.sort(
                key=lambda entry: (
                    0 if entry[2] else 1,
                    datetime.fromisoformat(str(entry[1].get("lastUsedAt")).replace("Z", "+00:00"))
                    if entry[1].get("lastUsedAt")
                    else datetime.min,
                )
            )
            selected_index, item, _ = available[0]
            resume = bool(item.get("sessionId"))
            if not resume:
                item["sessionId"] = new_session_id()
            item["status"] = "leased"
            item["leaseRunId"] = run_id
            item["leasePid"] = os.getpid()
            item["leasedAt"] = now
            item["lastTaskFingerprint"] = fingerprint
            return SessionLease(mode, str(item["sessionId"]), resume, selected_index)

        lease = update_session_state(state_path, lock_path, key, 30, updater)
        if lease:
            return lease
        if time.monotonic() >= deadline:
            raise DelegateError(
                f"Claude primary session is leased by another delegate. SessionKey: {key}. Use a longer -SessionLeaseWaitSeconds or choose ParallelPool."
            )
        time.sleep(0.25)


def release_session_lease(
    state_path: Path,
    lock_path: Path,
    key: str,
    lease: SessionLease | None,
    run_id: str,
    fingerprint: str,
) -> None:
    if lease is None or not lease.leased:
        return

    def updater(state: dict[str, Any]) -> None:
        now = now_iso()
        if lease.mode in ("PrimaryReuse", "PrimaryAnchor"):
            primary = state["primary"]
            if str(primary.get("leaseRunId")) == run_id:
                primary["status"] = "available"
                primary["leaseRunId"] = None
                primary["leasePid"] = None
                primary["leasedAt"] = None
                primary["lastUsedAt"] = now
                primary["lastRunId"] = run_id
            return None
        for slot in state["parallelPool"]:
            if str(slot.get("sessionId")) == lease.session_id and str(slot.get("leaseRunId")) == run_id:
                slot["status"] = "available"
                slot["leaseRunId"] = None
                slot["leasePid"] = None
                slot["leasedAt"] = None
                slot["lastUsedAt"] = now
                slot["lastRunId"] = run_id
                slot["lastTaskFingerprint"] = fingerprint
                break
        return None

    with contextlib.suppress(Exception):
        update_session_state(state_path, lock_path, key, 30, updater)


def reset_session_lease_for_fresh_session(
    state_path: Path,
    lock_path: Path,
    key: str,
    lease: SessionLease,
    run_id: str,
    fingerprint: str,
    reason: str,
) -> SessionLease:
    if lease is None or not lease.leased:
        raise DelegateError("Cannot reset a Claude session lease that is not currently leased.")

    def updater(state: dict[str, Any]) -> SessionLease:
        now = now_iso()
        if lease.mode in ("PrimaryReuse", "PrimaryAnchor"):
            primary = state["primary"]
            if str(primary.get("leaseRunId")) != run_id:
                raise DelegateError(
                    f"Cannot reset primary Claude session lease; expected run '{run_id}' but found '{primary.get('leaseRunId')}'."
                )
            old_session = lease.session_id
            primary["sessionId"] = new_session_id()
            primary["status"] = "leased"
            primary["leaseRunId"] = run_id
            primary["leasePid"] = os.getpid()
            primary["leasedAt"] = now
            primary["lastUsedAt"] = None
            primary["lastRunId"] = None
            primary["lastResetAt"] = now
            primary["lastResetReason"] = reason
            primary["lastResetFromSessionId"] = old_session
            primary["lastResetFromRunId"] = run_id
            return SessionLease(lease.mode, str(primary["sessionId"]), False, None)
        for index, slot in enumerate(state["parallelPool"]):
            if str(slot.get("sessionId")) == lease.session_id and str(slot.get("leaseRunId")) == run_id:
                old_session = lease.session_id
                slot["sessionId"] = new_session_id()
                slot["status"] = "leased"
                slot["leaseRunId"] = run_id
                slot["leasePid"] = os.getpid()
                slot["leasedAt"] = now
                slot["lastUsedAt"] = None
                slot["lastRunId"] = None
                slot["lastTaskFingerprint"] = fingerprint
                slot["lastResetAt"] = now
                slot["lastResetReason"] = reason
                slot["lastResetFromSessionId"] = old_session
                slot["lastResetFromRunId"] = run_id
                return SessionLease(lease.mode, str(slot["sessionId"]), False, index)
        raise DelegateError(f"Cannot reset parallel Claude session lease for run '{run_id}'; the leased session was not found.")

    return update_session_state(state_path, lock_path, key, 30, updater)


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
    prompt_text: str,
) -> list[str]:
    args = [
        "--verbose",
        "--print",
        "--output-format",
        "stream-json",
        "--model",
        model,
        "--name",
        session_name,
        "--permission-mode",
        "acceptEdits",
    ]
    args.extend(["--resume" if resume else "--session-id", session_id])
    if max_budget_usd not in (None, ""):
        args.extend(["--max-budget-usd", str(max_budget_usd)])
    if bypass_permissions:
        args.append("--dangerously-skip-permissions")
    args.append(prompt_text)
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


def build_prompt(
    repo: Path,
    output_path: Path,
    mode: str,
    scope: list[str],
    tests: list[str],
    task_text: str,
) -> str:
    rel = workflow_relative_path()
    primary_entry = f"{rel}/{script_family()}/delegate_to_claude{script_ext()}"
    windows_entry = f"{rel}/windows_scripts/delegate_to_claude.ps1"
    macos_entry = f"{rel}/macos_scripts/delegate_to_claude.sh"
    scope_text = "\n".join(f"- {item}" for item in scope) if scope else "- No explicit file scope was provided. Infer the narrowest safe scope from the task and current code."
    tests_text = "\n".join(f"- {item}" for item in tests) if tests else "- Run the smallest relevant verification you can identify from the change."
    worker_protocol_text = f"""- This prompt is already the only allowed Claude worker context for this delegated run.
- Never call `{primary_entry}`, `{windows_entry}`, `{macos_entry}`, `claude`, or `spawn_agent` recursively from inside this worker.
- Treat `{rel}/CODEX_WITH_CC.md` as the workflow contract to inspect when the task scope requires it, not as an execution recipe for this worker.
- If the task is an audit or validation, inspect the scoped files and run the listed verification commands directly instead of creating nested delegate runs.
- If you think another delegate run is required, stop and explain why in `Final Result` instead of invoking it yourself.
"""
    return f"""Execute the delegated task below now. This is not a readiness check; do not ask what to work on.

You are Claude Code acting as an implementation worker for Codex.

This worker script is reserved for Codex `spawn_agent` child threads. It is not a valid main-thread entry point.

Codex owns architecture, task boundaries, and final review. Your job is to execute the delegated task directly in this repository, keep changes narrow, verify them, and report exactly what changed.

Repository root:
{repo}

Delegated output report path:
{output_path}

Mode:
{mode}

Allowed / intended scope:
{scope_text}

Required or expected verification:
{tests_text}

Worker protocol:
{worker_protocol_text}

Task:
{task_text}

Hard requirements:
- Read {rel}/CODEX_WITH_CC.md before scanning other repository files.
- Use {rel}/CODEX_WITH_CC.md as the single workflow contract for delegation, audit flow, session mode interpretation, and worker report requirements.
- Follow all applicable project-defined skills and workflow skills before implementing or changing behavior, especially Codex project skills under `.codex`. Read the target project's agent/rule files and referenced skill documents when they apply to the delegated task.
- Keep edits inside the intended scope unless the task is impossible without a small supporting change.
- You must run necessary verification before handing work back. Run every command listed under Required or expected verification; if none is listed, infer the smallest meaningful format/analyze/test command for the changed area.
- Do not return code that you know fails to compile, analyze, or pass the required focused tests. Fix verification failures and rerun them until they pass.
- If verification is blocked by an external dependency or a clearly pre-existing unrelated failure, report the exact command, failure summary, and why it is not caused by your changes.
- Never claim verification passed unless you actually ran the command and saw it pass.
- Process and summarize your own CLI output. The Codex child thread will forward your final structured result; it should not reinterpret long logs for you.
- Treat this script as a child-thread worker entry only. Do not reinterpret it as permission for the Codex main thread to invoke Claude directly.
- Write enough detail in Process Log for the user to understand what happened, but keep raw verbose command output in the transcript/log instead of duplicating it.
- Finish with this exact report skeleton. Do not add text before `Process Log`; do not bold or decorate these headings:
Process Log
- <what you did>

Summary
<brief result>

Changed Files
- <path or None>

Verification
- <command and outcome>

Final Result
<PASS, FAIL, or blocked result>

Risks Or Follow-ups
- <risk or None>
"""


def startup_failure_report(message: str) -> str:
    summary = f"STARTUP_FAILURE: {message}"
    return f"""Process Log
- Delegate worker failed before Claude Code execution started.
- Startup failure: {message}

Summary
The delegate run did not reach Claude Code execution.

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
    write_text(output_path, startup_failure_report(failure_message))
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

    artifact_root = Path(ns.artifact_root).resolve() if ns.artifact_root else (root / ".codex" / "codex_with_cc" / "claude-delegate").resolve()
    artifact_root.mkdir(parents=True, exist_ok=True)
    output_path = Path(ns.output_path).resolve() if ns.output_path else (artifact_root / f"claude_PLACEHOLDER.md").resolve()

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
    if output_path.name == "claude_PLACEHOLDER.md":
        output_path = artifact_root / f"claude_{run_id}.md"
    status_path = artifact_root / f"status_{run_id}.json"
    config_path = artifact_root / f"config_{run_id}.json"
    prompt_path = artifact_root / f"prompt_{run_id}.md"
    raw_stream_path = artifact_root / f"stream_{run_id}.jsonl"
    trace_path = artifact_root / f"trace_{run_id}.log"
    lock_path = artifact_root / "delegate.lock"
    fingerprint = task_fingerprint(task_text, scope, tests, ns.mode)

    for path in (output_path, status_path, config_path, raw_stream_path, trace_path):
        test_path_writable(path)

    prompt = build_prompt(root, output_path, ns.mode, scope, tests, task_text)
    write_text(prompt_path, prompt)

    config: dict[str, Any] = {
        "artifactSchema": ARTIFACT_SCHEMA_VERSION,
        "invocationContract": INVOCATION_CONTRACT,
        "childThreadMarkerName": CHILD_MARKER_NAME,
        "childThreadMarkerValidated": True,
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
                        "mode": ns.mode,
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
            status["status"] = "completed"
            status["exitCode"] = 0
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
                    prompt_text,
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
                decision = retry_decision(
                    attempt_raw_lines,
                    lease.resume,
                    exit_code,
                    bool(capture_state["sawAssistantText"]),
                    bool(capture_state["sawResultSuccess"]),
                    bool(capture_state["capturedFinalResultHeading"]),
                )
                attempt_record.update(
                    {
                        "exitCode": exit_code,
                        "sawAssistantText": bool(capture_state["sawAssistantText"]),
                        "sawResultSuccess": bool(capture_state["sawResultSuccess"]),
                        "capturedFinalResult": bool(capture_state["capturedFinalResultHeading"]),
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
                    final_text = f"""Process Log
- Delegate worker detected a retryable Claude failure and exhausted the configured retry budget.
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
    finally:
        release_session_lease(session_state_path, session_state_lock_path, key, lease, run_id, fingerprint)
        if delegate_lock is not None:
            delegate_lock.release(remove=True)
        os.chdir(old_cwd)


def verify_artifacts(run_id: str, artifact_root_value: str | None) -> dict[str, Any]:
    root = Path(artifact_root_value).resolve() if artifact_root_value else (repo_root() / ".codex" / "codex_with_cc" / "claude-delegate").resolve()
    config_path = root / f"config_{run_id}.json"
    status_path = root / f"status_{run_id}.json"
    output_path = root / f"claude_{run_id}.md"
    for label, path in (("config", config_path), ("status", status_path), ("output", output_path)):
        if not path.exists():
            raise DelegateError(f"Missing delegate {label}: {path}")
    config = load_json(config_path)
    status = load_json(status_path)
    for obj in (config, status):
        if "artifactSchema" not in obj or "invocationContract" not in obj:
            raise DelegateError("Legacy delegate artifact is unsupported; rerun with current spawn_agent-based flow.")
    if int(config["artifactSchema"]) != ARTIFACT_SCHEMA_VERSION or int(status["artifactSchema"]) != ARTIFACT_SCHEMA_VERSION:
        raise DelegateError(f"Unexpected delegate artifact schema. Expected {ARTIFACT_SCHEMA_VERSION}.")
    if config.get("invocationContract") != INVOCATION_CONTRACT or status.get("invocationContract") != INVOCATION_CONTRACT:
        raise DelegateError(f"Unexpected delegate invocation contract. Expected '{INVOCATION_CONTRACT}'.")
    if config.get("childThreadMarkerName") != CHILD_MARKER_NAME or status.get("childThreadMarkerName") != CHILD_MARKER_NAME:
        raise DelegateError(f"Unexpected child-thread marker name. Expected '{CHILD_MARKER_NAME}'.")
    if not boolish(config.get("childThreadMarkerValidated")) or not boolish(status.get("childThreadMarkerValidated")):
        raise DelegateError("Delegate artifact indicates the child-thread marker was not validated.")
    if not same_path(str(config.get("outputPath")), output_path):
        raise DelegateError(f"Config outputPath mismatch. Expected: {output_path} ; Actual: {config.get('outputPath')}")
    if not same_path(str(status.get("outputPath")), output_path):
        raise DelegateError(f"Status outputPath mismatch. Expected: {output_path} ; Actual: {status.get('outputPath')}")
    status_value = str(status.get("status"))
    if status_value not in ("starting", "running", "completed", "failed"):
        raise DelegateError(f"Unexpected delegate status value: {status_value}")
    completed = status_value == "completed"
    structured_failure = status_value == "failed"
    if not completed and not structured_failure:
        raise DelegateError(f"Delegate status is neither completed nor failed: {status_value}")
    if not path_has_required_report_headings(output_path):
        raise DelegateError(f"Delegate output does not contain the required report headings in order: {output_path}")
    if completed and status.get("exitCode") is not None and int(status["exitCode"]) != 0:
        raise DelegateError(f"Delegate exitCode is not zero: {status['exitCode']}")
    if structured_failure and status.get("exitCode") is not None and int(status["exitCode"]) == 0:
        raise DelegateError("Structured failed delegate must record a non-zero exitCode.")
    if "attempts" not in status:
        raise DelegateError("Delegate status is missing attempts[] audit data.")
    if "sessionMode" not in config:
        raise DelegateError("Delegate config is missing sessionMode.")
    if "sessionKey" not in config:
        raise DelegateError("Delegate config is missing sessionKey.")
    attempts = list(status.get("attempts") or [])
    status_attempt_count = int(status.get("attemptCount", len(attempts)))
    status_retry_count = int(status.get("retryCount", 0))
    config_attempt_count = int(config.get("attemptCount", status_attempt_count))
    config_retry_count = int(config.get("retryCount", status_retry_count))
    if len(attempts) != status_attempt_count:
        raise DelegateError(f"Delegate attempts[] count mismatch. attempts={len(attempts)} status.attemptCount={status_attempt_count}")
    if status_attempt_count < 1:
        raise DelegateError("Delegate status must record at least one attempt.")
    if config_attempt_count != status_attempt_count:
        raise DelegateError(f"Config/status attemptCount mismatch. config={config_attempt_count} status={status_attempt_count}")
    if config_retry_count != status_retry_count:
        raise DelegateError(f"Config/status retryCount mismatch. config={config_retry_count} status={status_retry_count}")
    if structured_failure:
        for prop in ("failureDisposition", "failureSummary", "maxRetryCount"):
            if prop not in status:
                raise DelegateError(f"Structured failed delegate status is missing '{prop}'.")
            if prop not in config:
                raise DelegateError(f"Structured failed delegate config is missing '{prop}'.")
        if status.get("failureDisposition") != "NEED_HUMAN_INTERVENTION":
            raise DelegateError(f"Structured failed delegate must set failureDisposition to 'NEED_HUMAN_INTERVENTION'. Actual: {status.get('failureDisposition')}")
        if config.get("failureDisposition") != status.get("failureDisposition"):
            raise DelegateError("Structured failed delegate failureDisposition must match between config and status.")
        if not str(status.get("failureSummary", "")).strip():
            raise DelegateError("Structured failed delegate must record a non-empty failureSummary.")
        if config.get("failureSummary") != status.get("failureSummary"):
            raise DelegateError("Structured failed delegate failureSummary must match between config and status.")
        if int(config.get("maxRetryCount")) != int(status.get("maxRetryCount")):
            raise DelegateError("Structured failed delegate maxRetryCount must match between config and status.")
    recorded_retry_reasons = 0
    for index, attempt in enumerate(attempts):
        for prop in ("attempt", "sessionId", "resume", "retryReason", "exitCode", "sawAssistantText", "sawResultSuccess", "capturedFinalResult"):
            if prop not in attempt:
                raise DelegateError(f"Delegate attempt[{index}] is missing '{prop}'.")
        if int(attempt["attempt"]) != index + 1:
            raise DelegateError(f"Delegate attempt numbering is not sequential at index {index}. Expected {index + 1} but found {attempt['attempt']}.")
        if str(attempt.get("retryReason") or "").strip():
            recorded_retry_reasons += 1
    if recorded_retry_reasons != status_retry_count:
        raise DelegateError(f"Delegate retry count mismatch. attempts-with-retryReason={recorded_retry_reasons} status.retryCount={status_retry_count}")
    first_attempt = attempts[0]
    final_attempt = attempts[-1]
    if "initialSessionId" not in config:
        raise DelegateError("Delegate config is missing initialSessionId.")
    if "initialResume" not in config:
        raise DelegateError("Delegate config is missing initialResume.")
    if str(config.get("initialSessionId")) != str(first_attempt.get("sessionId")):
        raise DelegateError(f"Config initialSessionId mismatch. Expected first attempt session {first_attempt.get('sessionId')} but found {config.get('initialSessionId')}")
    if boolish(config.get("initialResume")) != boolish(first_attempt.get("resume")):
        raise DelegateError(f"Config initialResume mismatch. Expected first attempt resume {boolish(first_attempt.get('resume'))} but found {boolish(config.get('initialResume'))}")
    if "sessionId" in config and str(config.get("sessionId")) != str(final_attempt.get("sessionId")):
        raise DelegateError(f"Config final sessionId mismatch. Expected final attempt session {final_attempt.get('sessionId')} but found {config.get('sessionId')}")
    if "resume" in config and boolish(config.get("resume")) != boolish(final_attempt.get("resume")):
        raise DelegateError(f"Config final resume mismatch. Expected final attempt resume {boolish(final_attempt.get('resume'))} but found {boolish(config.get('resume'))}")
    if int(final_attempt.get("exitCode")) != int(status.get("exitCode")):
        raise DelegateError(f"Final attempt exitCode mismatch. Expected {status.get('exitCode')} but found {final_attempt.get('exitCode')}")
    if completed:
        if not boolish(final_attempt.get("sawResultSuccess")):
            raise DelegateError("Completed delegate must record sawResultSuccess=true on the final attempt.")
        if not boolish(final_attempt.get("capturedFinalResult")):
            raise DelegateError("Completed delegate must record capturedFinalResult=true on the final attempt.")
    if structured_failure and not boolish(final_attempt.get("capturedFinalResult")):
        raise DelegateError("Structured failed delegate must record capturedFinalResult=true on the final attempt.")
    optional_paths: set[str] = set()
    for prop in ("rawStreamPath", "tracePath", "promptPath"):
        for obj in (config, status):
            if obj.get(prop):
                optional_paths.add(str(obj[prop]))
    for path_text in optional_paths:
        if not Path(path_text).exists():
            raise DelegateError(f"Referenced artifact path is missing: {path_text}")
    state_path = config.get("sessionStatePath")
    if state_path and Path(str(state_path)).exists():
        state = load_json(Path(str(state_path)))
        primary = state.get("primary") if isinstance(state.get("primary"), dict) else {}
        if str(primary.get("leaseRunId")) == run_id:
            raise DelegateError(f"Primary session lease is still held by run {run_id}.")
        for slot in state.get("parallelPool") or []:
            if str(slot.get("leaseRunId")) == run_id:
                raise DelegateError(f"Parallel session lease is still held by run {run_id}.")
    return {"config": config, "status": status, "artifactRoot": root}


def run_verify_artifacts(ns: argparse.Namespace) -> int:
    verify_artifacts(ns.run_id, ns.artifact_root)
    print(f"Artifact verification passed for RunId: {ns.run_id}")
    return 0


def normalize_run_ids(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        for part in re.split(r"[\s,]+", str(value)):
            clean = part.strip().strip("'\"")
            if clean:
                out.append(clean)
    return out


def load_artifact_record(root: Path, run_id: str) -> dict[str, Any]:
    verify_artifacts(run_id, str(root))
    return {
        "runId": run_id,
        "config": load_json(root / f"config_{run_id}.json"),
        "status": load_json(root / f"status_{run_id}.json"),
    }


def run_verify_chain(ns: argparse.Namespace) -> int:
    root = Path(ns.artifact_root).resolve()
    parallel_ids = normalize_run_ids(ns.parallel_run_ids)
    reuse_ids = normalize_run_ids(ns.reuse_run_ids)
    anchor = load_artifact_record(root, ns.anchor_run_id)
    parallels = [load_artifact_record(root, run_id) for run_id in parallel_ids]
    reuses = [load_artifact_record(root, run_id) for run_id in reuse_ids]
    if anchor["config"].get("sessionMode") != "PrimaryAnchor":
        raise DelegateError("Anchor run must use PrimaryAnchor.")
    if anchor["config"].get("sessionKey") != ns.session_key:
        raise DelegateError("Anchor run sessionKey mismatch.")
    session_state_path = str(anchor["config"].get("sessionStatePath") or "")
    if not session_state_path:
        raise DelegateError("Anchor run is missing sessionStatePath.")
    if not Path(session_state_path).exists():
        raise DelegateError(f"Missing session state path: {session_state_path}")
    expected_main_session_id = str(anchor["config"].get("sessionId"))
    parallel_pool_reuse = False
    stale_reset_occurred = False
    for record in parallels:
        if record["config"].get("sessionMode") != "ParallelPool":
            raise DelegateError(f"Parallel run '{record['runId']}' must use ParallelPool.")
        if record["config"].get("sessionKey") != ns.session_key:
            raise DelegateError(f"Parallel run '{record['runId']}' sessionKey mismatch.")
        if boolish(record["config"].get("initialResume")):
            parallel_pool_reuse = True
    for record in reuses:
        config = record["config"]
        if config.get("sessionMode") != "PrimaryReuse":
            raise DelegateError(f"Reuse run '{record['runId']}' must use PrimaryReuse.")
        if config.get("sessionKey") != ns.session_key:
            raise DelegateError(f"Reuse run '{record['runId']}' sessionKey mismatch.")
        if not boolish(config.get("initialResume")):
            raise DelegateError(f"Reuse run '{record['runId']}' must start by attempting resume=true.")
        if str(config.get("initialSessionId")) != expected_main_session_id:
            raise DelegateError(f"Reuse run '{record['runId']}' did not start from the expected main session.")
        attempts = list(record["status"].get("attempts") or [])
        first = attempts[0]
        final = attempts[-1]
        if not boolish(first.get("resume")):
            raise DelegateError(f"Reuse run '{record['runId']}' first attempt must be resume=true.")
        if str(final.get("sessionId")) != expected_main_session_id:
            stale_reset_occurred = True
            if int(record["status"].get("retryCount", 0)) < 1:
                raise DelegateError(f"Reuse run '{record['runId']}' changed primary session without recording a retry.")
            if record["status"].get("lastRetryReason") != "stale_claude_session":
                raise DelegateError(f"Reuse run '{record['runId']}' must record stale_claude_session when changing primary session.")
            if boolish(final.get("resume")):
                raise DelegateError(f"Reuse run '{record['runId']}' fresh recovery attempt must be resume=false.")
            expected_main_session_id = str(final.get("sessionId"))
    state = load_json(Path(session_state_path))
    if state.get("sessionKey") != ns.session_key:
        raise DelegateError("Session pool sessionKey mismatch.")
    primary = state.get("primary") or {}
    if primary.get("status") != "available":
        raise DelegateError("Primary session slot must be available after chain completion.")
    if str(primary.get("sessionId")) != expected_main_session_id:
        raise DelegateError("Final primary session ID does not match the expected chain head.")
    if stale_reset_occurred:
        if not primary.get("lastResetAt"):
            raise DelegateError("Primary session reset is missing lastResetAt.")
        if primary.get("lastResetReason") != "stale_claude_session":
            raise DelegateError("Primary session reset reason must be stale_claude_session.")
        if not primary.get("lastResetFromSessionId"):
            raise DelegateError("Primary session reset is missing lastResetFromSessionId.")
        if not primary.get("lastResetFromRunId"):
            raise DelegateError("Primary session reset is missing lastResetFromRunId.")
    for record in parallels:
        session_id = str(record["config"].get("sessionId"))
        slot = next((slot for slot in state.get("parallelPool") or [] if str(slot.get("sessionId")) == session_id), None)
        if slot is None:
            raise DelegateError(f"Parallel pool slot for run '{record['runId']}' was not found.")
        if slot.get("status") != "available":
            raise DelegateError(f"Parallel pool slot for run '{record['runId']}' must be available after chain completion.")
        if not slot.get("lastTaskFingerprint"):
            raise DelegateError(f"Parallel pool slot for run '{record['runId']}' is missing lastTaskFingerprint.")
    orphan = primary.get("status") == "leased" or any(slot.get("status") == "leased" for slot in state.get("parallelPool") or [])
    summary = {
        "primaryCacheHit": True,
        "parallelPoolReuse": parallel_pool_reuse,
        "staleResetOccurred": stale_reset_occurred,
        "orphanLeaseDetected": orphan,
        "artifactContractValid": True,
        "chainPassed": not orphan,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if orphan:
        raise DelegateError("Delegate chain verification failed because a session lease is still active.")
    return 0


def run_real_chain_validation(ns: argparse.Namespace) -> int:
    root = repo_root()
    validation_root = Path(ns.validation_root).resolve() if ns.validation_root else (root / ".codex" / "codex_with_cc" / "claude-delegate-validation").resolve()
    name = ns.name or f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-real-chain"
    session_key = ns.session_key or f"delegate-real-chain-{uuid.uuid4().hex[:12]}"
    chain_root = validation_root / name
    artifact_root = chain_root / "artifacts"
    task_root = chain_root / "tasks"
    task_date = datetime.now().strftime("%Y%m%d")
    batch_id = f"{datetime.now().strftime('%H%M%S-%f')[:-3]}-{uuid.uuid4().hex[:6]}"
    dated_task_root = task_root / task_date
    artifact_root.mkdir(parents=True, exist_ok=True)
    dated_task_root.mkdir(parents=True, exist_ok=True)
    rel = workflow_relative_path()
    scripts = script_family()
    ext = script_ext()
    delegate_entry = f"{rel}/{scripts}/delegate_to_claude{ext}"
    verify_entry = f"{rel}/{scripts}/verify_delegate_artifacts{ext}"
    chain_entry = f"{rel}/{scripts}/verify_delegate_chain{ext}"
    command_prefix = "pwsh -NoProfile -File .\\" if os.name == "nt" else "./"
    slash_delegate = delegate_entry.replace("/", "\\") if os.name == "nt" else delegate_entry
    slash_verify = verify_entry.replace("/", "\\") if os.name == "nt" else verify_entry
    slash_chain = chain_entry.replace("/", "\\") if os.name == "nt" else chain_entry
    task_specs = [
        (
            "anchor-read-protocol.md",
            "PrimaryAnchor",
            "-SessionMode PrimaryAnchor -AllowParallel",
            f"{delegate_entry}\n{rel}/CODEX_WITH_CC.md",
            "只读验证任务：通过 Codex spawn_agent 子线程承载 Claude worker，审查 delegate entrypoint 与 session pool 的主线锚点行为。",
        ),
        (
            "parallel-artifact-audit.md",
            "ParallelPool",
            "-SessionMode ParallelPool -AllowParallel",
            f"{verify_entry}\n{chain_entry}\n.codex/codex_with_cc/claude-delegate",
            "只读验证任务：审查新 schema delegate artifacts 与 verify_delegate_artifacts 的契约要求。",
        ),
        (
            "parallel-stream-audit.md",
            "ParallelPool",
            "-SessionMode ParallelPool -AllowParallel",
            f"{delegate_entry}\n.codex/codex_with_cc/claude-delegate",
            "只读验证任务：审查 stream capture、retry decision 与 trace/rawStream 行为。",
        ),
        (
            "reuse-cross-check-1.md",
            "PrimaryReuse",
            "-SessionMode PrimaryReuse",
            f"{delegate_entry}\n{verify_entry}\n{chain_entry}\n{rel}/CODEX_WITH_CC.md",
            "真实复核/返工任务：在锚点与并发旁路完成后，使用同一 SessionKey 续接主线，对前三份结果做交叉复核。",
        ),
        (
            "reuse-cross-check-2.md",
            "PrimaryReuse",
            "-SessionMode PrimaryReuse",
            f"{delegate_entry}\n{verify_entry}\n{chain_entry}\n{rel}/CODEX_WITH_CC.md",
            "只读验证任务：再次在同一 SessionKey 下顺序续接主线，验证缓存命中不是偶发成功。",
        ),
    ]
    task_files: list[Path] = []
    for file_name, mode, flags, scope, task_body in task_specs:
        task_path = dated_task_root / f"{batch_id}-{file_name}"
        task_files.append(task_path)
        verify_command = f"{command_prefix}{slash_verify} -RunId <{file_name.replace('.md', '-run-id')}> -ArtifactRoot \"{artifact_root}\""
        content = f"""# Real Delegate Chain Validation Task

- SessionKey: {session_key}
- ArtifactRoot: {artifact_root}
- SessionMode: {mode}
- Child-thread only: This task must run inside a Codex spawn_agent child thread with model 'gpt-5.3-codex', reasoning_effort 'medium', fork_context 'false'.
- Required child-thread marker: set process environment CODEX_CLAUDE_CHILD_THREAD=1 before invoking the worker entry script.
- Worker entry script: {delegate_entry}
- Required worker arguments: -TaskFile "{task_path}" -ArtifactRoot "{artifact_root}" -SessionKey "{session_key}" {flags} -BypassPermissions

Allowed scope:
{scope}

Verification command to run after this task completes:
{verify_command}

{task_body}

要求：
- 输出必须包含 Process Log / Summary / Changed Files / Verification / Final Result / Risks Or Follow-ups。
"""
        write_text(task_path, content)
    print(
        f"""Real delegate chain validation scaffold created.

Validation Root: {chain_root}
Artifact Root: {artifact_root}
Task Root: {task_root}
Task Date Root: {dated_task_root}
Session Key: {session_key}

Required Codex orchestration rules:
- The Codex main thread may only create spawn_agent child threads and collect results.
- Every Claude worker must run inside a child thread with:
  - model: gpt-5.3-codex
  - reasoning_effort: medium
  - fork_context: false
- Every child thread must set CODEX_CLAUDE_CHILD_THREAD=1 and then call {delegate_entry} with -TaskFile.
- Do not run Claude CLI or delegate_to_claude directly from the main thread.

Recommended execution order:
1. Child thread: {task_files[0].name} (PrimaryAnchor)
2. Child thread: {task_files[1].name} (ParallelPool)
3. Child thread: {task_files[2].name} (ParallelPool)
4. Wait for the anchor + both parallel runs to finish
5. Child thread: {task_files[3].name} (PrimaryReuse)
6. Child thread: {task_files[4].name} (PrimaryReuse)

Post-run verification commands:
- {command_prefix}{slash_verify} -RunId <anchor-run-id> -ArtifactRoot "{artifact_root}"
- {command_prefix}{slash_verify} -RunId <parallel-a-run-id> -ArtifactRoot "{artifact_root}"
- {command_prefix}{slash_verify} -RunId <parallel-b-run-id> -ArtifactRoot "{artifact_root}"
- {command_prefix}{slash_verify} -RunId <reuse-1-run-id> -ArtifactRoot "{artifact_root}"
- {command_prefix}{slash_verify} -RunId <reuse-2-run-id> -ArtifactRoot "{artifact_root}"
- {command_prefix}{slash_chain} -ArtifactRoot "{artifact_root}" -SessionKey "{session_key}" -AnchorRunId <anchor-run-id> -ParallelRunIds <parallel-a-run-id>,<parallel-b-run-id> -ReuseRunIds <reuse-1-run-id>,<reuse-2-run-id>
"""
    )
    return 0


def resolve_install_platform(value: str) -> str:
    if value and value.lower() != "auto":
        if value.lower() in ("macos", "darwin"):
            return "macOS"
        if value.lower() == "windows":
            return "Windows"
        raise DelegateError("Unsupported install platform. Pass Windows or macOS.")
    if sys.platform == "darwin":
        return "macOS"
    if os.name == "nt":
        return "Windows"
    raise DelegateError("Unsupported install platform. Pass --platform Windows or --platform macOS explicitly.")


def remove_agent_entrypoint_block(path: Path) -> bool:
    if not path.exists():
        return False
    text = read_text(path)
    pattern = re.compile(r"(?s)<!-- BEGIN CODEX_WITH_CC -->.*?<!-- END CODEX_WITH_CC -->")
    updated = pattern.sub("", text)
    if updated == text:
        return False
    if updated.strip():
        write_text(path, updated.strip() + "\n")
    else:
        path.unlink()
    return True


def update_installed_workflow_references(workflow: Path, workflow_relative: str) -> None:
    canonical = "docs/codex_with_cc"
    canonical_win = canonical.replace("/", "\\")
    replacement = workflow_relative.replace("\\", "/")
    replacement_win = replacement.replace("/", "\\")
    if replacement == canonical:
        return
    for path in workflow.rglob("*"):
        if not path.is_file() or path.suffix not in {".md", ".ps1", ".sh"}:
            continue
        text = read_text(path)
        updated = (
            text.replace(f"./{canonical}", replacement)
            .replace(f".\\{canonical_win}", replacement_win)
            .replace(canonical, replacement)
            .replace(canonical_win, replacement_win)
        )
        if updated != text:
            write_text(path, updated)


def update_gitignore_file(path: Path) -> None:
    entry = ".codex/codex_with_cc"
    if path.exists():
        text = read_text(path)
        for line in text.splitlines():
            if line.strip() in (entry, f"{entry}/"):
                return
        updated = text.rstrip()
        if updated:
            updated += "\n"
        updated += entry + "\n"
    else:
        updated = entry + "\n"
    write_text(path, updated)


def copy_workflow_source(source: Path, destination: Path, excluded_script_root: str) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        if item.name in (excluded_script_root, "__pycache__") or item.suffix == ".pyc":
            continue
        target = destination / item.name
        if item.is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(item, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(item, target)


def remove_path_inside(target_root: Path, path: Path) -> bool:
    if not path.exists():
        return False
    try:
        path.resolve().relative_to(target_root)
    except ValueError as exc:
        raise DelegateError(f"Refusing to remove path outside target root: {path}") from exc
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    return True


def run_install(ns: argparse.Namespace) -> int:
    install_platform = resolve_install_platform(ns.platform)
    source_workflow = workflow_root()
    installer_root = source_workflow.parent.resolve()
    source_skill = source_workflow if source_workflow.name == SKILL_NAME else installer_root / "skills" / SKILL_NAME
    target_root = Path(ns.target_root).resolve()
    target_root.mkdir(parents=True, exist_ok=True)
    if same_path(installer_root, target_root):
        raise DelegateError(
            f"Refusing to install codex_with_cc into its own source repository. Choose a different target root: {installer_root}"
        )
    if not source_workflow.exists():
        raise DelegateError(f"Workflow source was not found: {source_workflow}")
    if not source_skill.exists():
        raise DelegateError(f"Skill source was not found: {source_skill}")

    target_workflow = codex_home() / "skills" / SKILL_NAME
    target_local_skill = target_root / ".codex" / "skills" / SKILL_NAME
    workflow_relative = target_workflow.resolve().as_posix()
    task_root = target_root / ".codex" / "codex_with_cc" / "tasks"
    cleanup: list[str] = []

    if same_path(source_workflow, target_workflow) and not same_path(source_skill, target_workflow):
        raise DelegateError(
            f"Refusing to install codex_with_cc into its own source repository. Choose a different target root: {source_workflow}"
        )

    for candidate in (target_root / "docs" / "codex_with_cc", target_root / "doc" / "codex_with_cc"):
        if same_path(source_workflow, candidate):
            raise DelegateError(
                f"Refusing to install codex_with_cc into its own source repository. Choose a different target root: {source_workflow}"
            )
        if remove_path_inside(target_root, candidate):
            cleanup.append(candidate.relative_to(target_root).as_posix())
    if remove_agent_entrypoint_block(target_root / "AGENTS.md"):
        cleanup.append("AGENTS.md managed block")
    if remove_path_inside(target_root, target_local_skill):
        cleanup.append(target_local_skill.relative_to(target_root).as_posix())
    if not same_path(source_skill, target_workflow) and remove_path_inside(codex_home(), target_workflow):
        cleanup.append(str(target_workflow))

    if not same_path(source_skill, target_workflow):
        excluded = "macos_scripts" if install_platform == "Windows" else "windows_scripts"
        copy_workflow_source(source_skill, target_workflow, excluded)
    update_installed_workflow_references(target_workflow, workflow_relative)
    if install_platform == "macOS":
        mac_scripts = target_workflow / "macos_scripts"
        if mac_scripts.exists():
            for script in mac_scripts.glob("*.sh"):
                script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    task_root.mkdir(parents=True, exist_ok=True)
    gitkeep = task_root / ".gitkeep"
    if gitkeep.exists():
        gitkeep.unlink()
    update_gitignore_file(target_root / ".gitignore")
    print(f"codex_with_cc global skill installed into: {target_workflow}")
    print(f"Old install artifacts cleaned: {', '.join(cleanup) if cleanup else 'none'}")
    print("Next: restart Codex, then use $codex-with-cc or the subagent/delegation trigger words.")
    return 0


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


def run_test_runtime(_: argparse.Namespace) -> int:
    with tempfile.TemporaryDirectory(prefix="codex_with_cc_delegate_runtime_") as tmp:
        temp_root = Path(tmp)
        missing = run_delegate_subprocess(
            ["-Task", "marker rejection probe", "-ArtifactRoot", str(temp_root / "marker"), "-SessionKey", "marker", "-DryRun"],
            env={CHILD_MARKER_NAME: ""},
        )
        assert_true(missing.returncode != 0, "missing-child-thread-marker-fails")
        assert_true(f"{CHILD_MARKER_NAME}=1" in (missing.stdout + missing.stderr), "missing-child-thread-marker-names-required-marker")

        dry_root = temp_root / "dry"
        dry = run_delegate_subprocess(
            ["-Task", "dry run probe", "-ArtifactRoot", str(dry_root), "-SessionKey", "dry", "-SessionMode", "PrimaryReuse", "-MaxRetryCount", "7", "-DryRun"],
            env={CHILD_MARKER_NAME: "1"},
        )
        assert_equal(dry.returncode, 0, "dry-run-succeeds")
        config = load_json(next(dry_root.glob("config_*.json")))
        status = load_json(next(dry_root.glob("status_*.json")))
        assert_true("effort" not in config, "dry-run-config-omits-effort")
        assert_equal(int(config["maxRetryCount"]), 7, "dry-run-config-records-max-retry")
        assert_equal(int(status["maxRetryCount"]), 7, "dry-run-status-records-max-retry")

        if os.name == "nt":
            fake_body = '@echo off\necho {"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"I inspected the tests."}]}}\necho {"type":"result","subtype":"success"}\nexit /b 0\n'
        else:
            fake_body = '#!/bin/sh\necho \'{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"I inspected the tests."}]}}\'\necho \'{"type":"result","subtype":"success"}\'\nexit 0\n'
        fake_bin = make_fake_claude_bin(temp_root, fake_body)
        run_root = temp_root / "unstructured"
        env = {CHILD_MARKER_NAME: "1", "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}"}
        run = run_delegate_subprocess(
            [
                "-Task",
                "unstructured success rejection probe",
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
        assert_true(text_has_required_report_headings(markdown_report), "markdown-report-headings-accepted")
        missing_summary = markdown_report.replace("**Summary**\n", "")
        assert_true(not text_has_required_report_headings(missing_summary), "missing-report-heading-rejected")

        retry_report = "\n".join(
            (
                "Process Log",
                "- repaired the report format",
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
                "Final Result",
                "PASS",
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
                "-Task",
                "unstructured success report repair probe",
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
        write_text(output_path, "Process Log\nSummary\nChanged Files\nVerification\nFinal Result\nok\nRisks Or Follow-ups\n")
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
                "artifactSchema": 2,
                "invocationContract": INVOCATION_CONTRACT,
                "childThreadMarkerName": CHILD_MARKER_NAME,
                "childThreadMarkerValidated": True,
                "runId": run_id,
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
                "artifactSchema": 2,
                "invocationContract": INVOCATION_CONTRACT,
                "childThreadMarkerName": CHILD_MARKER_NAME,
                "childThreadMarkerValidated": True,
                "runId": run_id,
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
            ["-Task", "serial A", "-ArtifactRoot", str(temp_root), "-SessionKey", session_key, "-SessionMode", "PrimaryReuse", "-DryRun"],
            env={CHILD_MARKER_NAME: "1"},
        )
        assert_equal(first.returncode, 0, "first-primary-dryrun-succeeds")
        state = load_json(temp_root / "session-pools" / f"{session_key}.json")
        primary_id = str(state["primary"]["sessionId"])
        assert_true("--session-id " + primary_id in first.stdout, "first-primary-uses-session-id")
        assert_equal(state["primary"]["status"], "available", "primary-released-after-dry-run")
        anchor = run_delegate_subprocess(
            ["-Task", "parallel anchor", "-ArtifactRoot", str(temp_root), "-SessionKey", session_key, "-SessionMode", "PrimaryAnchor", "-AllowParallel", "-DryRun"],
            env={CHILD_MARKER_NAME: "1"},
        )
        assert_equal(anchor.returncode, 0, "anchor-dryrun-succeeds")
        state = load_json(temp_root / "session-pools" / f"{session_key}.json")
        assert_equal(state["primary"]["sessionId"], primary_id, "anchor-keeps-primary-id")
        assert_true("--resume " + primary_id in anchor.stdout, "anchor-resumes-primary")
        parallel_a = run_delegate_subprocess(
            ["-Task", "parallel sidecar A", "-ArtifactRoot", str(temp_root), "-SessionKey", session_key, "-SessionMode", "ParallelPool", "-AllowParallel", "-DryRun"],
            env={CHILD_MARKER_NAME: "1"},
        )
        assert_equal(parallel_a.returncode, 0, "parallel-a-dryrun-succeeds")
        state = load_json(temp_root / "session-pools" / f"{session_key}.json")
        pool_id = str(state["parallelPool"][0]["sessionId"])
        assert_true("--session-id " + pool_id in parallel_a.stdout, "first-parallel-uses-session-id")
        parallel_b = run_delegate_subprocess(
            ["-Task", "parallel sidecar A", "-ArtifactRoot", str(temp_root), "-SessionKey", session_key, "-SessionMode", "ParallelPool", "-AllowParallel", "-DryRun"],
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
                "-Task",
                "split scope explicit dry run",
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


def choice_arg(choices: list[str]) -> Callable[[str], str]:
    lookup = {choice.lower(): choice for choice in choices}

    def parse(value: str) -> str:
        selected = lookup.get(value.lower())
        if selected is None:
            expected = ", ".join(choices)
            raise argparse.ArgumentTypeError(f"invalid choice: {value!r} (choose from {expected})")
        return selected

    return parse


def int_range_arg(name: str, minimum: int, maximum: int) -> Callable[[str], int]:
    def parse(value: str) -> int:
        try:
            parsed = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"{name} must be an integer") from exc
        if parsed < minimum or parsed > maximum:
            raise argparse.ArgumentTypeError(f"{name} must be between {minimum} and {maximum}")
        return parsed

    return parse


def add_delegate_args(parser: argparse.ArgumentParser) -> None:
    task_group = parser.add_mutually_exclusive_group()
    task_group.add_argument("-Task", dest="task")
    task_group.add_argument("-TaskFile", dest="task_file")
    parser.add_argument("-Scope", dest="scope", action="append", default=[])
    parser.add_argument("-Tests", dest="tests", action="append", default=[])
    parser.add_argument("-Mode", dest="mode", type=choice_arg(["Implement", "Fix", "Review"]), default="Implement")
    parser.add_argument("-Model", dest="model", default="sonnet")
    parser.add_argument("-Name", dest="name")
    parser.add_argument("-NamePrefix", dest="name_prefix", default="codex-delegate")
    parser.add_argument("-MaxBudgetUsd", dest="max_budget_usd")
    parser.add_argument("-ArtifactRoot", dest="artifact_root")
    parser.add_argument("-OutputPath", dest="output_path")
    parser.add_argument("-AllowParallel", dest="allow_parallel", action="store_true")
    parser.add_argument("-SessionMode", dest="session_mode", type=choice_arg(["PrimaryReuse", "PrimaryAnchor", "ParallelPool"]), default="PrimaryReuse")
    parser.add_argument("-SessionKey", dest="session_key")
    parser.add_argument("-SessionLeaseTimeoutSeconds", dest="session_lease_timeout_seconds", type=int, default=21600)
    parser.add_argument("-SessionLeaseWaitSeconds", dest="session_lease_wait_seconds", type=int, default=120)
    parser.add_argument("-ResetPrimarySession", dest="reset_primary_session", action="store_true")
    parser.add_argument("-ResetParallelPool", dest="reset_parallel_pool", action="store_true")
    parser.add_argument("-LockTimeoutSeconds", dest="lock_timeout_seconds", type=int, default=120)
    parser.add_argument("-LockPollMilliseconds", dest="lock_poll_milliseconds", type=int, default=500)
    parser.add_argument("-MaxRetryCount", dest="max_retry_count", type=int_range_arg("MaxRetryCount", 0, 100), default=5)
    parser.add_argument("-BypassPermissions", dest="bypass_permissions", action="store_true")
    parser.add_argument("-DryRun", dest="dry_run", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codex_with_cc scripts")
    sub = parser.add_subparsers(dest="command", required=True)
    delegate = sub.add_parser("delegate")
    add_delegate_args(delegate)
    delegate.set_defaults(func=run_delegate)

    verify = sub.add_parser("verify-artifacts")
    verify.add_argument("-RunId", dest="run_id", required=True)
    verify.add_argument("-ArtifactRoot", dest="artifact_root")
    verify.set_defaults(func=run_verify_artifacts)

    chain = sub.add_parser("verify-chain")
    chain.add_argument("-ArtifactRoot", dest="artifact_root", required=True)
    chain.add_argument("-SessionKey", dest="session_key", required=True)
    chain.add_argument("-AnchorRunId", dest="anchor_run_id", required=True)
    chain.add_argument("-ParallelRunIds", dest="parallel_run_ids", nargs="+", required=True)
    chain.add_argument("-ReuseRunIds", dest="reuse_run_ids", nargs="+", required=True)
    chain.set_defaults(func=run_verify_chain)

    validation = sub.add_parser("run-real-chain-validation")
    validation.add_argument("-ValidationRoot", dest="validation_root")
    validation.add_argument("-Name", dest="name")
    validation.add_argument("-SessionKey", dest="session_key")
    validation.set_defaults(func=run_real_chain_validation)

    install = sub.add_parser("install")
    install.add_argument("--target-root", "-TargetRoot", dest="target_root", default=os.getcwd())
    install.add_argument("--platform", "-Platform", dest="platform", default="Auto")
    install.add_argument("--skip-agent-entrypoints", "-SkipAgentEntrypoints", dest="skip_agent_entrypoints", action="store_true")
    install.set_defaults(func=run_install)

    sub.add_parser("test-runtime").set_defaults(func=run_test_runtime)
    sub.add_parser("test-session-pool").set_defaults(func=run_test_session_pool)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    ns = parser.parse_args(argv)
    try:
        return int(ns.func(ns))
    except DelegateError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
