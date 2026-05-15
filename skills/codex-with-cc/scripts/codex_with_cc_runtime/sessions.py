from __future__ import annotations

import dataclasses
import contextlib
import hashlib
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from .common import DelegateError, now_iso
from .io_utils import load_json, write_json
from .locks import acquire_file_lock, pid_alive



def new_session_id() -> str:
    return str(uuid.uuid4())



def effective_session_key(value: str | None) -> str:
    if value and value.strip():
        return value
    raise DelegateError("SessionKey is required. Pass -SessionKey explicitly for every delegate run.")



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
    raw = "\n".join(
        (
            f"mode={task_mode}",
            f"scope={'|'.join(sorted(scope_items))}",
            f"tests={'|'.join(sorted(test_items))}",
            f"task={text}",
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
