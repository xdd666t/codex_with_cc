---
name: codex-with-cc-worker
description: Prepare codex-with-cc worker task files and report requirements for Claude Code execution roles.
---

# Codex With CC Worker

Read `../codex-with-cc/CODEX_WITH_CC.md` before preparing a worker task.

Worker task files must state:

- The exact assignment and intended role.
- The WorkflowId, TaskId, SessionKey, and any review target metadata.
- Allowed scope and files that may be changed or inspected.
- Explicit out-of-scope files, behaviors, and follow-up work the worker must not execute.
- Required verification commands.
- Acceptance criteria the worker should self-check before reporting.
- The report headings: Status, Role, Summary, Changed Files, Verification, Findings, Final Result, Risks Or Follow-ups.

Worker behavior:

- Execute the assigned task directly; do not create nested delegate runs.
- Implementers must use test-first or the smallest equivalent verification-first evidence before changing behavior when the repository has a practical test surface.
- Reviewers must perform exactly one review kind: `spec` or `quality`, for the provided `ReviewForTaskId`.
- Keep noisy command output in artifacts and summarize only the evidence needed by the main thread.
- Before reporting, check scope compliance, changed files, verification results, and residual risks.
- Use `DONE_WITH_CONCERNS` only when required verification passed but meaningful risk remains.
- Use `NEEDS_CONTEXT` when the task cannot be completed without a main-thread decision.
- Use `BLOCKED` for external blockers and `FAIL` for failed work or invalid verification.
