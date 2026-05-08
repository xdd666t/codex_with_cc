<!-- BEGIN CODEX_WITH_CC -->
Codex with Claude Code / OpenCode workflow: before using this workflow, read `skills/codex-with-cc/CODEX_WITH_CC.md`.
Any user mention of child-agent, subagent, sub-agent, child-thread, subthread, delegation, worker-execution, or Chinese equivalents such as 子代理、子线程、多代理、委派、派工、执行层 is a workflow trigger.
If the task involves child agents, subagents, delegation, or any worker-execution step, you must read that file first and follow the custom `Codex main thread -> Codex child agent -> delegate_to_claude.* -> Claude Code CLI` or `delegate_to_opencode.* -> OpenCode CLI` workflow defined there.
When the user mentions "opencode" (e.g. "创建一个使用opencode的子代理"), use the `delegate_to_opencode.* -> OpenCode CLI` chain. Otherwise default to Claude Code (`delegate_to_claude.*`).
<!-- END CODEX_WITH_CC -->
