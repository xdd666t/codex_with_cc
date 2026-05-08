# Codex With CC (Claude Code / OpenCode)

This document is the portable entry point for the Codex -> Codex child agent -> executor CLI workflow, supporting both **Claude Code** and **OpenCode** as delegated workers.

## Required Reading
1. Read this file before using the workflow in this repository.

## Executor Selection

The Codex main thread selects the executor per delegation based on user intent:

- **Claude Code** is the default executor. Use when the user does not specify a preference or explicitly requests Claude.
- **OpenCode** is used whenever the user mentions "opencode" in any context (e.g. "创建一个使用opencode的子代理", "opencode来执行", "用opencode"). The word "opencode" alone is sufficient.

## Model Selection

The Codex main thread resolves the worker model from user language and passes it via `-Model`. Rules:

1. **If the user does not mention any model**: omit `-Model` entirely. The executor uses its own configured default.
2. **If the user mentions a model by name**: resolve it from the table below and pass `-Model <exact-identifier>`.
3. **OpenCode models use `provider/model` format** (e.g. `opencode-go/deepseek-v4-flash`). Claude models use short names (e.g. `sonnet`, `opus`).
4. **When in doubt, run `opencode models` to list available OpenCode models** before constructing the child thread command.

### OpenCode Model Mapping

User language → exact model identifier to pass to `-Model`:

| User says | `-Model` value |
|-----------|---------------|
| DeepSeek / deepseek / ds / v4 flash | `opencode-go/deepseek-v4-flash` |
| DeepSeek pro / ds pro / v4 pro | `opencode-go/deepseek-v4-pro` |
| GLM / glm / 智谱 | `opencode-go/glm-5` |
| GLM 5.1 / glm 5.1 | `opencode-go/glm-5.1` |
| Kimi / kimi / k2.5 | `opencode-go/kimi-k2.5` |
| Kimi k2.6 / k2.6 | `opencode-go/kimi-k2.6` |
| Qwen / qwen / 通义千问 | `opencode-go/qwen3.6-plus` |
| Qwen 3.5 | `opencode-go/qwen3.5-plus` |
| Mimo / mimo | `opencode-go/mimo-v2.5` |
| Mimo pro | `opencode-go/mimo-v2.5-pro` |
| MiniMax / minimax | `opencode-go/minimax-m2.7` |
| MiniMax 2.5 | `opencode-go/minimax-m2.5` |
| Hy / hy3 | `opencode/hy3-preview-free` |
| Big pickle | `opencode/big-pickle` |
| Nemotron / nemotron | `opencode/nemotron-3-super-free` |

### Examples

```
"创建一个使用opencode的子代理"                          → OpenCode, no -Model (use OpenCode default)
"创建一个使用opencode的子代理，用DeepSeek模型"           → OpenCode, -Model opencode-go/deepseek-v4-flash
"用opencode + qwen来执行这个任务"                       → OpenCode, -Model opencode-go/qwen3.6-plus
"创建子代理"                                            → Claude Code, no -Model (use Claude default)
"创建子代理，用sonnet模型"                               → Claude Code, -Model sonnet
```

## Core Contract — Both Executors
1. The Codex main thread must not run `claude` or `opencode` directly.
2. The Codex main thread must not run delegate entrypoints directly, except for the trusted local terminal fallback below.
3. Every delegation must be carried by a Codex `spawn_agent` child thread.
4. The child thread must set the correct environment marker before invoking the entry script.
5. The child thread should use `model: gpt-5.3-codex`, `reasoning_effort: medium`, and `fork_context: false`.
6. Medium and large tasks should be written to a dated, uniquely named task file under `.codex/codex_with_cc/tasks/<yyyyMMdd>/<HHmmssfff>-<short-id>-<task-name>.md` and passed with `-TaskFile`.
7. Workers must keep changes inside the delegated scope, run the required verification, and finish with the exact report headings defined in this document.
8. If the Codex sandbox or delegated runner cannot execute the same worker command, run that exact command in a trusted local terminal instead.
9. Workers must read and follow all applicable Codex project skills under `.codex` before implementing or changing behavior.

## Core Contract — Claude Code
1. Set `CODEX_CLAUDE_CHILD_THREAD=1` before invoking `delegate_to_claude.*`.
2. `delegate_to_claude.*` must not pass `--effort`; Claude Code should use its configured default effort.
3. When user specifies a Claude model, pass it with `-Model` (e.g. `-Model sonnet`). When not specified, omit `-Model` to use Claude's default.

## Core Contract — OpenCode
1. Set `CODEX_OPENCODE_CHILD_THREAD=1` before invoking `delegate_to_opencode.*`.
2. Resolve model from user language using the Model Mapping table above. When user doesn't specify a model, omit `-Model` to use OpenCode's default.
3. Optionally use `-Variant high|max|minimal` to control model reasoning effort.

## Trigger Rule
Any user mention of child-agent, subagent, sub-agent, child-thread, subthread, delegation, worker-execution, or Chinese equivalents such as 子代理、子线程、多代理、委派、派工、执行层 is a workflow trigger. When triggered, the main Codex thread must use this custom delegation workflow and must not satisfy the request with the default Codex subagent flow, a host-provided agent shortcut, direct executor CLI execution, or direct main-thread execution of delegate entrypoints.

If the user mentions "opencode" in the same request (e.g. "创建一个使用opencode的子代理", "opencode执行", "用 opencode"), use the OpenCode chain with `CODEX_OPENCODE_CHILD_THREAD=1` and `delegate_to_opencode.*`. Otherwise default to Claude Code.

## Trusted Local Terminal Fallback
This fallback is an execution-location fallback only. Preserve the same child-thread marker (`CODEX_CLAUDE_CHILD_THREAD=1` or `CODEX_OPENCODE_CHILD_THREAD=1`), task file, session mode, session key, artifact root, and permission flags that the child thread would have used.

Do not replace this with the default Codex subagent flow, a direct `claude`/`opencode` command, or a modified worker command. Report that the trusted terminal fallback was used and include the command outcome in verification.

## Roles
- Codex main thread: understand the request, define scope, select executor and model, create child threads, review results, and decide final acceptance.
- Codex child thread: provide a visible conversation-tree node and invoke the worker script (Claude or OpenCode).
- Executor CLI (Claude Code or OpenCode): execute the delegated task, run verification, and produce a structured report.

## Session Modes
- `PrimaryReuse`: default serial mode. Reuses the main executor session for continuity.
- `PrimaryAnchor`: parallel-batch anchor. Its result becomes the main reusable context for later serial work.
- `ParallelPool`: independent parallel side work. Uses reusable pool sessions without writing to the main session.

Only use `-AllowParallel` when task scopes are independent.

Session pools are shared across executors — Claude and OpenCode sessions coexist in the same pool directory.

## Worker Output
Every worker must finish with these exact headings:

```text
Process Log
Summary
Changed Files
Verification
Final Result
Risks Or Follow-ups
```

Verification must list the commands actually run and their outcomes. If verification is blocked, the report must explain the blocker and whether it is unrelated to the delegated change.

## Artifacts
Delegation artifacts are written under `.codex/codex_with_cc/` with separate directories per executor:

### Claude Code Artifacts (`.codex/codex_with_cc/claude-delegate/`)
- `claude_<RunId>.md`
- `status_<RunId>.json`
- `config_<RunId>.json`
- `prompt_<RunId>.md`
- `stream_<RunId>.jsonl`
- `trace_<RunId>.log`
- `session-pools/<SessionKey>.json`

### OpenCode Artifacts (`.codex/codex_with_cc/opencode-delegate/`)
- `opencode_<RunId>.md`
- `status_<RunId>.json`
- `config_<RunId>.json`
- `prompt_<RunId>.md`
- `stream_<RunId>.jsonl`
- `trace_<RunId>.log`
- `session-pools/<SessionKey>.json` (shared with Claude)

Use `verify_delegate_artifacts.*` for each run and `verify_delegate_chain.*` for multi-run continuity checks. The shared implementation lives under `scripts/*.py`; platform wrappers should stay thin. macOS entrypoints are native shell wrappers around the same Python runtime, preserve the same Codex main thread -> child thread -> delegate entrypoint boundary as Windows, and only check for Python at runtime. Installers bootstrap Python when needed.

## Standard Worker Commands

### Claude Code — Windows

```powershell
$codexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $HOME '.codex' }
$env:CODEX_CLAUDE_CHILD_THREAD = '1'
pwsh -NoProfile -File (Join-Path $codexHome 'skills\codex-with-cc\windows_scripts\delegate_to_claude.ps1') `
  -TaskFile .\.codex\codex_with_cc\tasks\<yyyyMMdd>\<HHmmssfff>-<short-id>-<task-file>.md `
  -SessionMode PrimaryReuse `
  -SessionKey <stable-session-key> `
  -BypassPermissions
```

### Claude Code — macOS

```bash
CODEX_HOME_DIR="${CODEX_HOME:-$HOME/.codex}"
export CODEX_CLAUDE_CHILD_THREAD=1
"$CODEX_HOME_DIR/skills/codex-with-cc/macos_scripts/delegate_to_claude.sh" \
  -TaskFile ./.codex/codex_with_cc/tasks/<yyyyMMdd>/<HHmmssfff>-<short-id>-<task-file>.md \
  -SessionMode PrimaryReuse \
  -SessionKey <stable-session-key> \
  -BypassPermissions
```

### OpenCode — Windows

```powershell
$codexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $HOME '.codex' }
$env:CODEX_OPENCODE_CHILD_THREAD = '1'
pwsh -NoProfile -File (Join-Path $codexHome 'skills\codex-with-cc\windows_scripts\delegate_to_opencode.ps1') `
  -TaskFile .\.codex\codex_with_cc\tasks\<yyyyMMdd>\<HHmmssfff>-<short-id>-<task-file>.md `
  -SessionMode PrimaryReuse `
  -SessionKey <stable-session-key> `
  -BypassPermissions
```

### OpenCode — macOS

```bash
CODEX_HOME_DIR="${CODEX_HOME:-$HOME/.codex}"
export CODEX_OPENCODE_CHILD_THREAD=1
"$CODEX_HOME_DIR/skills/codex-with-cc/macos_scripts/delegate_to_opencode.sh" \
  -TaskFile ./.codex/codex_with_cc/tasks/<yyyyMMdd>/<HHmmssfff>-<short-id>-<task-file>.md \
  -SessionMode PrimaryReuse \
  -SessionKey <stable-session-key> \
  -BypassPermissions
```

Use `PrimaryAnchor -AllowParallel` for the main branch of a parallel batch and `ParallelPool -AllowParallel` for independent side work.

## Verification
Run the local regression tests after installing or changing this workflow.

Windows:

```powershell
$codexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $HOME '.codex' }
pwsh -NoProfile -File (Join-Path $codexHome 'skills\codex-with-cc\windows_scripts\test_delegate_runtime.ps1')
pwsh -NoProfile -File (Join-Path $codexHome 'skills\codex-with-cc\windows_scripts\test_delegate_session_pool.ps1')
```

macOS:

```bash
CODEX_HOME_DIR="${CODEX_HOME:-$HOME/.codex}"
"$CODEX_HOME_DIR/skills/codex-with-cc/macos_scripts/test_delegate_runtime.sh"
"$CODEX_HOME_DIR/skills/codex-with-cc/macos_scripts/test_delegate_session_pool.sh"
```

Generate a real chain validation scaffold with:

```powershell
$codexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $HOME '.codex' }
pwsh -NoProfile -File (Join-Path $codexHome 'skills\codex-with-cc\windows_scripts\run_real_delegate_chain_validation.ps1')
```

or on macOS:

```bash
CODEX_HOME_DIR="${CODEX_HOME:-$HOME/.codex}"
"$CODEX_HOME_DIR/skills/codex-with-cc/macos_scripts/run_real_delegate_chain_validation.sh"
```
