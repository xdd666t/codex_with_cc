---
name: codex-with-cc
description: Force Codex with Claude Code routing for any child-agent, subagent, sub-agent, child-thread, subthread, delegation, worker-execution, 子代理, 子线程, 多代理, 委派, 派工, or 执行层 task; use this skill whenever Codex must plan, dispatch, review, or execute work through child agents instead of default subagent behavior.
---

# Codex With CC

## Core Rule

Use this skill as the mandatory entry point for every child-agent, subagent, sub-agent, child-thread, subthread, delegation, worker-execution, 子代理, 子线程, 多代理, 委派, 派工, or 执行层 request.

Do not satisfy a triggered request with default Codex subagent behavior, direct Claude Code CLI execution, direct `claude` execution, or direct main-thread execution of `delegate_to_claude.*`.

## Workflow Contract

Read `CODEX_WITH_CC.md` in this skill directory before using the workflow. Treat it as the single contract for task-file-only dispatch, workflow/task/run artifacts, role rules, review gates, and verification.

This skill is distributed through a plugin-managed installation. When invoking bundled scripts, run them from the target project's current working directory so `.codex/codex_with_cc` tasks and artifacts are written to that project, not to the plugin cache.

The required chain is:

```text
Codex main thread -> Codex spawn_agent child thread -> delegate_to_claude.* -> Claude Code CLI
```

The installed plugin also declares `./hooks/hooks.json` so Codex hosts with hooks enabled can inject this contract at session start, reinforce it on matching user prompts, and deny supported non-compliant tool calls.

## Operating Method

Use this workflow as a Superpowers-style staged control loop, not as a prompt shortcut:

1. Clarify intent and acceptance criteria before dispatch.
2. Write bounded task files with goal, scope, forbidden work, verification commands, and review gates.
3. Dispatch fresh child threads with `model: gpt-5.3-codex`, `reasoning_effort: medium`, and `fork_context: false`.
4. Require implementers to use test-first or verification-first evidence when changing behavior.
5. Review every implementation in two passes: spec compliance first, then code quality and regression risk.
6. Finish only after workflow-level verification confirms run artifacts, workflow artifacts, review gates, session continuity when relevant, and repository tests support acceptance.

Workers are context consumers, not decision owners. Codex main thread owns architecture, task boundaries, acceptance, rework decisions, and final delivery.

The Codex child thread must:

- Set `CODEX_CLAUDE_CHILD_THREAD=1` before invoking `delegate_to_claude.*`.
- Pass task instructions through `.codex/codex_with_cc/tasks/<yyyyMMdd>/<HHmmssfff>-<short-id>-<task-name>.md` with `-TaskFile`.
- Pass `-WorkflowId`, `-TaskId`, `-Role`, and `-SessionKey`.
- Avoid legacy inline `-Task`, legacy `-Mode`, and implicit session-key fallback.
- Keep changes inside the delegated scope and pass `-Scope` for any parallel writable work.
- Run the requested verification.

## Multi-Skill Chain

Use these sibling skills when the work has enough surface area to need staged control:

- `$codex-with-cc-planning`: turn the request into task files, acceptance criteria, dependencies, and review gates.
- `$codex-with-cc-dispatching`: choose serial or parallel delegation and assign WorkflowId/TaskId/Role/SessionKey values.
- `$codex-with-cc-worker`: define the worker task file and scope for Claude Code execution.
- `$codex-with-cc-reviewing`: review worker reports, findings, verification, changed files, and review gate state.
- `$codex-with-cc-finishing`: verify the whole workflow and prepare final delivery.

## Main Thread Duties

In the main Codex thread:

- Understand the user request, define scope, choose serial or parallel delegation, and review all worker results.
- Prefer serial execution when write scopes overlap or acceptance criteria are still unstable.
- Use parallel execution only for independent read-only tasks or writable tasks with explicit non-overlapping `-Scope` values.
- Do not run `claude` directly.
- Do not run `delegate_to_claude.*` directly except when `CODEX_WITH_CC.md` explicitly allows the trusted local terminal fallback.
- Verify each run with `verify_delegate_run.*` or `verify_delegate_artifacts.*`.
- Verify the whole workflow with `verify_delegate_workflow.*`; use `verify_delegate_chain.*` when validating primary/parallel session continuity.
- Reject implementer work until both `spec` and `quality` reviewer runs are accepted.
- Do not summarize a worker as successful until the artifacts and the worker's verification evidence both support that claim.

## Worker Report Contract

Every Claude worker must finish with these exact headings:

```text
Status
Role
Summary
Changed Files
Verification
Findings
Final Result
Risks Or Follow-ups
```

`Status` and `Final Result` must use the same token:

```text
DONE
DONE_WITH_CONCERNS
NEEDS_CONTEXT
BLOCKED
FAIL
```

`Role` must use one of:

```text
planner
implementer
researcher
reviewer
final-verifier
```

Verification must list commands actually run and their outcomes. A `DONE` report without concrete verification evidence is invalid. If verification is blocked, the worker must explain the blocker and whether it is unrelated to the delegated change.
