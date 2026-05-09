---
name: codex-with-cc
description: Force Codex with Claude Code routing for any child-agent, subagent, sub-agent, child-thread, subthread, delegation, worker-execution, 子代理, 子线程, 多代理, 委派, 派工, or 执行层 task; use this skill whenever Codex must plan, dispatch, review, or execute work through child agents instead of default subagent behavior.
---

# Codex With CC

## Core Rule

Use this skill as the mandatory entry point for every child-agent, subagent, sub-agent, child-thread, subthread, delegation, worker-execution, 子代理, 子线程, 多代理, 委派, 派工, or 执行层 request.

Do not satisfy a triggered request with default Codex subagent behavior, direct Claude Code CLI execution, direct `claude` execution, or direct main-thread execution of `delegate_to_claude.*`.

## Workflow Contract

Read `CODEX_WITH_CC.md` in this skill directory before using the workflow. Treat it as the single contract for delegation rules, session modes, artifact verification, and worker report requirements.

This skill is distributed through a plugin-managed installation. When invoking bundled scripts, run them from the target project's current working directory so `.codex/codex_with_cc` tasks and artifacts are written to that project, not to the plugin cache.

The required chain is:

```text
Codex main thread -> Codex spawn_agent child thread -> delegate_to_claude.* -> Claude Code CLI
```

The Codex child thread must:

- Use `model: gpt-5.3-codex`, `reasoning_effort: medium`, and `fork_context: false`.
- Set `CODEX_CLAUDE_CHILD_THREAD=1` before invoking `delegate_to_claude.*`.
- Pass medium or large task instructions through `.codex/codex_with_cc/tasks/<yyyyMMdd>/<HHmmssfff>-<short-id>-<task-name>.md` with `-TaskFile`.
- Keep changes inside the delegated scope and run the requested verification.

## Main Thread Duties

In the main Codex thread:

- Understand the user request, define scope, choose serial or parallel delegation, and review all worker results.
- Do not run `claude` directly.
- Do not run `delegate_to_claude.*` directly except when `CODEX_WITH_CC.md` explicitly allows the trusted local terminal fallback.
- Verify delegate artifacts with `verify_delegate_artifacts.*` for each run and `verify_delegate_chain.*` for multi-run continuity when applicable.
- Reject or return work that does not satisfy the requested scope, tests, or report contract.

## Worker Report Contract

Every Claude worker must finish with these exact headings:

```text
Process Log
Summary
Changed Files
Verification
Final Result
Risks Or Follow-ups
```

Verification must list commands actually run and their outcomes. If verification is blocked, the worker must explain the blocker and whether it is unrelated to the delegated change.
