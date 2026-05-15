from __future__ import annotations

import argparse
import os
import uuid
from datetime import datetime
from pathlib import Path

from .io_utils import write_text
from .paths import repo_root, script_ext, script_family, workflow_relative_path



def script_command(script_path: str) -> str:
    if os.name == "nt":
        file_arg = script_path if os.path.isabs(script_path) else f".\\{script_path}"
        return f"pwsh -NoProfile -File {file_arg}"
    return script_path if os.path.isabs(script_path) else f"./{script_path}"



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
    workflow_id = f"wf-{name}"
    dated_task_root = task_root / task_date
    artifact_root.mkdir(parents=True, exist_ok=True)
    dated_task_root.mkdir(parents=True, exist_ok=True)
    rel = workflow_relative_path()
    scripts = script_family()
    ext = script_ext()
    delegate_entry = f"{rel}/{scripts}/delegate_to_claude{ext}"
    verify_entry = f"{rel}/{scripts}/verify_delegate_artifacts{ext}"
    chain_entry = f"{rel}/{scripts}/verify_delegate_chain{ext}"
    slash_delegate = delegate_entry.replace("/", "\\") if os.name == "nt" else delegate_entry
    slash_verify = verify_entry.replace("/", "\\") if os.name == "nt" else verify_entry
    slash_chain = chain_entry.replace("/", "\\") if os.name == "nt" else chain_entry
    task_specs = [
        (
            "anchor-read-protocol.md",
            "PrimaryAnchor",
            "-SessionMode PrimaryAnchor -AllowParallel",
            [delegate_entry, f"{rel}/CODEX_WITH_CC.md"],
            "只读验证任务：通过 Codex spawn_agent 子线程承载 Claude worker，审查 delegate entrypoint 与 session pool 的主线锚点行为。",
        ),
        (
            "parallel-artifact-audit.md",
            "ParallelPool",
            "-SessionMode ParallelPool -AllowParallel",
            [verify_entry, chain_entry, ".codex/codex_with_cc/claude-delegate"],
            "只读验证任务：审查新 schema delegate artifacts 与 verify_delegate_artifacts 的契约要求。",
        ),
        (
            "parallel-stream-audit.md",
            "ParallelPool",
            "-SessionMode ParallelPool -AllowParallel",
            [delegate_entry, ".codex/codex_with_cc/claude-delegate"],
            "只读验证任务：审查 stream capture、retry decision 与 trace/rawStream 行为。",
        ),
        (
            "reuse-cross-check-1.md",
            "PrimaryReuse",
            "-SessionMode PrimaryReuse",
            [delegate_entry, verify_entry, chain_entry, f"{rel}/CODEX_WITH_CC.md"],
            "真实复核/返工任务：在锚点与并发旁路完成后，使用同一 SessionKey 续接主线，对前三份结果做交叉复核。",
        ),
        (
            "reuse-cross-check-2.md",
            "PrimaryReuse",
            "-SessionMode PrimaryReuse",
            [delegate_entry, verify_entry, chain_entry, f"{rel}/CODEX_WITH_CC.md"],
            "只读验证任务：再次在同一 SessionKey 下顺序续接主线，验证缓存命中不是偶发成功。",
        ),
    ]
    task_files: list[Path] = []
    for file_name, mode, flags, scope_items, task_body in task_specs:
        task_path = dated_task_root / f"{batch_id}-{file_name}"
        task_id = file_name.replace(".md", "")
        task_files.append(task_path)
        verify_command = f"{script_command(slash_verify)} -RunId <{file_name.replace('.md', '-run-id')}> -ArtifactRoot \"{artifact_root}\""
        scope = "\n".join(scope_items)
        scope_flags = " ".join(f'-Scope "{item}"' for item in scope_items)
        required_args = f'-TaskFile "{task_path}" -WorkflowId "{workflow_id}" -TaskId "{task_id}" -Role researcher -ArtifactRoot "{artifact_root}" -SessionKey "{session_key}" {flags} {scope_flags} -Tests \'{verify_command}\' -BypassPermissions'
        content = f"""# Real Delegate Chain Validation Task

- SessionKey: {session_key}
- ArtifactRoot: {artifact_root}
- SessionMode: {mode}
- Child-thread only: This task must run inside a Codex spawn_agent child thread with model 'gpt-5.3-codex', reasoning_effort 'medium', fork_context 'false'.
- Required child-thread marker: set process environment CODEX_CLAUDE_CHILD_THREAD=1 before invoking the worker entry script.
- Worker entry script: {delegate_entry}
- Required worker arguments: {required_args}

Allowed scope:
{scope}

Verification command to run after this task completes:
{verify_command}

{task_body}

要求：
- 输出必须包含 Status / Role / Summary / Changed Files / Verification / Findings / Final Result / Risks Or Follow-ups。
- Status 和 Final Result 必须使用同一个状态值：DONE、DONE_WITH_CONCERNS、NEEDS_CONTEXT、BLOCKED 或 FAIL。
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
- {script_command(slash_verify)} -RunId <anchor-run-id> -ArtifactRoot "{artifact_root}"
- {script_command(slash_verify)} -RunId <parallel-a-run-id> -ArtifactRoot "{artifact_root}"
- {script_command(slash_verify)} -RunId <parallel-b-run-id> -ArtifactRoot "{artifact_root}"
- {script_command(slash_verify)} -RunId <reuse-1-run-id> -ArtifactRoot "{artifact_root}"
- {script_command(slash_verify)} -RunId <reuse-2-run-id> -ArtifactRoot "{artifact_root}"
- {script_command(slash_chain)} -ArtifactRoot "{artifact_root}" -SessionKey "{session_key}" -AnchorRunId <anchor-run-id> -ParallelRunIds <parallel-a-run-id>,<parallel-b-run-id> -ReuseRunIds <reuse-1-run-id>,<reuse-2-run-id>
"""
    )
    return 0
