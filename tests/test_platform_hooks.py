#!/usr/bin/env python3
from pathlib import Path
import json
import subprocess


REPO = Path(__file__).resolve().parents[1]
HOOK_SCRIPT = REPO / "hooks" / "subagent-gate-hook.mjs"


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


def hook_specific(output: dict) -> dict:
    return output["hookSpecificOutput"]


def test_hooks_config_declares_platform_gate_events() -> None:
    hooks_config = json.loads((REPO / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    hooks = hooks_config["hooks"]

    assert set(hooks) == {"SessionStart", "UserPromptSubmit", "PreToolUse"}
    for event_name in hooks:
        command = hooks[event_name][0]["hooks"][0]["command"]
        assert "subagent-gate-hook.mjs" in command


def test_session_start_injects_codex_with_cc_contract() -> None:
    output = run_hook({"hook_event_name": "SessionStart", "source": "startup"})
    specific = hook_specific(output)
    context = specific["additionalContext"]

    assert specific["hookEventName"] == "SessionStart"
    assert "<EXTREMELY_IMPORTANT>" in context
    assert "Below is the full content of your 'codex-with-cc' skill" in context
    assert "# Codex With CC" in context
    assert "## Core Contract" in context
    assert "codex-with-cc" in context
    assert "spawn_agent" in context
    assert "delegate_to_claude" in context
    assert "Claude Code CLI" in context
    assert "Workflow Method" in context


def test_user_prompt_submit_reinforces_contract_for_subagent_requests() -> None:
    output = run_hook(
        {
            "hook_event_name": "UserPromptSubmit",
            "prompt": "请开启子代理并行委派两个 worker 处理",
        }
    )
    context = hook_specific(output)["additionalContext"]

    assert "<EXTREMELY_IMPORTANT>" in context
    assert "Below is the full content of your 'codex-with-cc' skill" in context
    assert "codex-with-cc" in context
    assert "default Codex subagent" in context


def test_user_prompt_submit_ignores_unrelated_prompts() -> None:
    output = run_hook({"hook_event_name": "UserPromptSubmit", "prompt": "解释一下 README"})

    assert output == {}


def test_pre_tool_use_denies_non_compliant_spawn_agent_payload() -> None:
    output = run_hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "spawn_agent",
            "tool_input": {
                "message": "Use a normal worker to implement this.",
                "model": "gpt-5.4",
                "reasoning_effort": "high",
                "fork_context": True,
            },
        }
    )
    specific = hook_specific(output)
    reason = specific["permissionDecisionReason"]

    assert specific["hookEventName"] == "PreToolUse"
    assert specific["permissionDecision"] == "deny"
    assert "gpt-5.3-codex" in reason
    assert "delegate_to_claude" in reason
    assert "fork_context: false" in reason


def test_pre_tool_use_allows_compliant_spawn_agent_payload() -> None:
    output = run_hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "spawn_agent",
            "tool_input": {
                "message": (
                    "Set CODEX_CLAUDE_CHILD_THREAD=1, then run "
                    "windows_scripts/delegate_to_claude.ps1 -TaskFile "
                    ".codex/codex_with_cc/tasks/20260514/120000000-task.md "
                    "-WorkflowId wf-a -TaskId task-a -Role researcher -SessionKey wf-a "
                    "-Scope skills/codex-with-cc"
                ),
                "model": "gpt-5.3-codex",
                "reasoning_effort": "medium",
                "fork_context": False,
            },
        }
    )

    assert output == {}


def test_pre_tool_use_denies_direct_claude_shell_command() -> None:
    output = run_hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "claude -p \"do delegated work\""},
        }
    )
    reason = hook_specific(output)["permissionDecisionReason"]

    assert "direct Claude CLI" in reason


def test_pre_tool_use_denies_delegate_shell_without_child_marker() -> None:
    output = run_hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {
                "command": (
                    "pwsh -NoProfile -File windows_scripts/delegate_to_claude.ps1 "
                    "-Prompt \"do delegated work\""
                )
            },
        }
    )
    reason = hook_specific(output)["permissionDecisionReason"]

    assert "CODEX_CLAUDE_CHILD_THREAD=1" in reason
    assert "-TaskFile" in reason


def test_pre_tool_use_denies_legacy_delegate_args_and_incomplete_reviewer() -> None:
    legacy = run_hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {
                "command": (
                    "$env:CODEX_CLAUDE_CHILD_THREAD='1'; "
                    "pwsh -NoProfile -File windows_scripts/delegate_to_claude.ps1 "
                    "-Task \"old inline\" -WorkflowId wf-a -TaskId task-a "
                    "-Role researcher -SessionKey wf-a -Mode Review"
                )
            },
        }
    )
    legacy_reason = hook_specific(legacy)["permissionDecisionReason"]

    assert "inline -Task" in legacy_reason
    assert "-Mode" in legacy_reason

    reviewer = run_hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {
                "command": (
                    "$env:CODEX_CLAUDE_CHILD_THREAD='1'; "
                    "pwsh -NoProfile -File windows_scripts/delegate_to_claude.ps1 "
                    "-TaskFile .codex/codex_with_cc/tasks/20260514/review.md "
                    "-WorkflowId wf-a -TaskId review-a -Role reviewer -SessionKey wf-a"
                )
            },
        }
    )
    reviewer_reason = hook_specific(reviewer)["permissionDecisionReason"]

    assert "ReviewForTaskId" in reviewer_reason
    assert "ReviewKind" in reviewer_reason
