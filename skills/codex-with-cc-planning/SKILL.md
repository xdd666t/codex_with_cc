---
name: codex-with-cc-planning
description: Plan codex-with-cc workflows by turning a subagent or delegation request into scoped tasks, roles, acceptance criteria, and review gates before dispatch.
---

# Codex With CC Planning

Read `../codex-with-cc/CODEX_WITH_CC.md` before planning. Use this skill when a request needs child-agent, subagent, delegation, 子代理, 委派, or 派工 routing through codex-with-cc.

Produce a concise workflow design before any dispatch:

- Assign one `WorkflowId` for the user request.
- Split work into task-file-sized assignments with stable `TaskId` values and explicit dependencies recorded as `-DependsOn`.
- Choose one role per task: `planner`, `implementer`, `researcher`, `reviewer`, or `final-verifier`.
- Define `Scope`, allowed writes, forbidden writes, verification commands, and a stable `SessionKey` for each task.
- Mark each task as serial, parallel read-only, or parallel writable with non-overlapping scope.
- Define acceptance criteria and both required review gates for every implementer task: `spec` review first, then `quality` review.
- Include a final-verifier task when the workflow changes files or combines multiple worker results.

Planning quality checks:

- Do not dispatch a task that depends on an unresolved product decision.
- Do not parallelize tasks that may write the same file or mutate the same behavior.
- Prefer a small researcher task before implementation when the code path is unknown.
- Do not plan inline `-Task`, legacy `-Mode`, or implicit session-key fallback.
- Return `NEEDS_CONTEXT` when safe decomposition is not possible from the current request and repository context.

Do not run `claude` or `delegate_to_claude.*` from the main thread.
