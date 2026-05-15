---
name: codex-with-cc-dispatching
description: Dispatch codex-with-cc tasks through the required Codex child thread -> delegate_to_claude.* -> Claude Code CLI chain with WorkflowId, TaskId, Role, and Scope metadata.
---

# Codex With CC Dispatching

Read `../codex-with-cc/CODEX_WITH_CC.md` before dispatching. Use this skill after planning has produced task boundaries.

Dispatch rules:

- Every child thread uses `model: gpt-5.3-codex`, `reasoning_effort: medium`, and `fork_context: false`.
- Every worker command sets `CODEX_CLAUDE_CHILD_THREAD=1`.
- Every worker command passes `-TaskFile`, `-WorkflowId`, `-TaskId`, `-Role`, and `-SessionKey`.
- Never dispatch legacy inline `-Task`, legacy `-Mode`, or a command that relies on an implicit session key.
- Reviewer commands must pass `-ReviewForTaskId` and `-ReviewKind spec` or `-ReviewKind quality`.
- Parallel writable tasks require explicit non-overlapping `-Scope` values.
- Use `PrimaryAnchor` for a parallel batch anchor, `ParallelPool` for independent side work, and `PrimaryReuse` for serial follow-up.

Dispatch discipline:

- Dispatch the immediate blocking task locally only when no child-thread delegation is needed; otherwise create the Codex child thread and keep the main thread focused on review.
- Put medium and large instructions in the task file instead of embedding fragile inline prompts.
- Include the exact verification commands in the task file and pass them with `-Tests` when possible.
- Dispatch implementer, spec reviewer, and quality reviewer as separate task ids so the workflow artifact can prove acceptance.
- Use parallel dispatch only after scope boundaries are explicit enough to avoid file conflicts.
- After a parallel batch, wait for the anchor and side tasks before serial review or follow-up implementation.

Do not dispatch default Codex workers outside the codex-with-cc chain.
