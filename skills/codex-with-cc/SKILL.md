---
name: codex-with-cc
description: Force Codex with Claude Code / OpenCode routing for any child-agent, subagent, sub-agent, child-thread, subthread, delegation, worker-execution, 子代理, 子线程, 多代理, 委派, 派工, or 执行层 task; use this skill whenever Codex must plan, dispatch, review, or execute work through child agents instead of default subagent behavior. Supports two executors — Claude Code (default) and OpenCode — selectable per delegation.
---

# Codex With CC (Claude Code / OpenCode)

## Core Rule

Use this skill as the mandatory entry point for every child-agent, subagent, sub-agent, child-thread, subthread, delegation, worker-execution, 子代理, 子线程, 多代理, 委派, 派工, or 执行层 request.

Do not satisfy a triggered request with default Codex subagent behavior, direct CLI execution, or direct main-thread execution of `delegate_to_claude.*` / `delegate_to_opencode.*`.

## Executor Selection

When a delegation task is triggered, the Codex main thread chooses the executor:

| Condition | Executor | Entry Script | Marker |
|-----------|----------|-------------|--------|
| Default / user mentions Claude / no explicit preference | **Claude Code** | `delegate_to_claude.*` | `CODEX_CLAUDE_CHILD_THREAD=1` |
| User mentions opencode in any context (e.g. "创建一个使用opencode的子代理", "opencode来执行", "用opencode") | **OpenCode** | `delegate_to_opencode.*` | `CODEX_OPENCODE_CHILD_THREAD=1` |
| User explicitly says "用 claude" | **Claude Code** | `delegate_to_claude.*` | `CODEX_CLAUDE_CHILD_THREAD=1` |

An OpenCode delegation uses `-Model provider/model` format (e.g. `openai/gpt-5.3-codex`) while a Claude delegation uses short names (e.g. `sonnet`).

## Workflow Contract

Read `CODEX_WITH_CC.md` in this skill directory before using the workflow. Treat it as the single contract for delegation rules, session modes, artifact verification, and worker report requirements — for both executors.

This skill is installed globally under `$CODEX_HOME/skills/codex-with-cc`. When invoking bundled scripts, run them from the target project's current working directory so `.codex/codex_with_cc` tasks and artifacts are written to that project, not to the global skill directory.

The supported chains are:

```text
Codex main thread -> Codex spawn_agent child thread -> delegate_to_claude.* -> Claude Code CLI
Codex main thread -> Codex spawn_agent child thread -> delegate_to_opencode.* -> OpenCode CLI
```

### Claude Code Child Thread

- Use `model: gpt-5.3-codex`, `reasoning_effort: medium`, and `fork_context: false`.
- Set `CODEX_CLAUDE_CHILD_THREAD=1` before invoking `delegate_to_claude.*`.
- Pass medium or large task instructions through `.codex/codex_with_cc/tasks/<yyyyMMdd>/<HHmmssfff>-<short-id>-<task-name>.md` with `-TaskFile`.
- `-Model` expects short names (e.g. `sonnet`).
- Keep changes inside the delegated scope and run the requested verification.

### OpenCode Child Thread

- Use `model: gpt-5.3-codex`, `reasoning_effort: medium`, and `fork_context: false`.
- Set `CODEX_OPENCODE_CHILD_THREAD=1` before invoking `delegate_to_opencode.*`.
- Pass medium or large task instructions through `.codex/codex_with_cc/tasks/<yyyyMMdd>/<HHmmssfff>-<short-id>-<task-name>.md` with `-TaskFile`.
- `-Model` expects `provider/model` format (e.g. `openai/gpt-5.3-codex`).
- Optionally use `-Variant high|max|minimal` to control reasoning effort.
- Keep changes inside the delegated scope and run the requested verification.

## Main Thread Duties

In the main Codex thread:

- Understand the user request, define scope, choose executor (Claude or OpenCode), choose serial or parallel delegation, and review all worker results.
- Do not run `claude` or `opencode` directly.
- Do not run `delegate_to_claude.*` or `delegate_to_opencode.*` directly except when `CODEX_WITH_CC.md` explicitly allows the trusted local terminal fallback.
- Verify delegate artifacts with `verify_delegate_artifacts.*` for each run and `verify_delegate_chain.*` for multi-run continuity when applicable.
- Reject or return work that does not satisfy the requested scope, tests, or report contract.

## Worker Report Contract

Every worker (Claude or OpenCode) must finish with these exact headings:

```text
Process Log
Summary
Changed Files
Verification
Final Result
Risks Or Follow-ups
```

Verification must list commands actually run and their outcomes. If verification is blocked, the worker must explain the blocker and whether it is unrelated to the delegated change.
