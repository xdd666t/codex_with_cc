---
name: codex-with-cc-reviewing
description: Review codex-with-cc worker reports, verification evidence, changed files, and findings before accepting or returning delegated work.
---

# Codex With CC Reviewing

Read `../codex-with-cc/CODEX_WITH_CC.md` before reviewing delegated results.

Review duties:

- Verify the run artifact with `verify_delegate_run.*` or `verify_delegate_artifacts.*`.
- Check that Status and Final Result use the same valid status token.
- Check that Role matches the delegated role.
- Check that reviewer runs include `ReviewForTaskId` and `ReviewKind`.
- Inspect Changed Files and Verification for concrete evidence.
- Reject `DONE` reports that do not name commands actually run and their outcomes.
- Treat `DONE_WITH_CONCERNS`, `NEEDS_CONTEXT`, `BLOCKED`, and `FAIL` as requiring main-thread judgment before acceptance.
- Return the task for rework when findings are valid and actionable.

Use two passes:

1. Spec compliance review: did the worker do exactly the assigned task, stay inside scope, and meet acceptance criteria?
2. Quality review: are the changes maintainable, minimal, tested, and unlikely to regress adjacent behavior?

Do not accept an implementer task at workflow level until both review passes are accepted in the workflow artifact.
