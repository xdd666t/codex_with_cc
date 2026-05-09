# AI 安装说明

本文件是 `codex_with_cc` 的 **marketplace-only 安装契约**。仓库不再支持脚本式安装，也不再复制任何本地 skill 目录；安装必须先检查 `aiskyhub/aiskyhub` marketplace，再从 marketplace 安装 `codex-with-cc@aiskyhub`。

## 默认交互策略

只要用户是在让你“安装 / 集成 / 更新这套工作流”，默认进入零打扰安装模式：

1. 直接执行，不要先把安装过程变成问答游戏。
2. 默认执行用户级安装，不要切成项目级，除非用户明确要求。
3. 默认先检查 `aiskyhub` marketplace 是否已存在，再检查 `codex-with-cc@aiskyhub` 是否已启用。
4. marketplace 添加或插件安装失败时直接停止并报告，不要回退到复制文件、写入旧目录或执行本地安装脚本。
5. 必要时提醒用户重载插件列表或重启宿主。
6. 只有 marketplace 仓库不可达、宿主没有可用插件命令、或读取配置失败时，才停下来询问用户。

## 目标

把本仓库的 `Codex -> Codex 子代理 -> Claude Code CLI` 委派工作流，安装成 `aiskyhub/aiskyhub` marketplace 下的用户级插件，使后续凡是涉及子代理、子线程、多代理、委派、派工或执行层的任务，都强制触发 `$codex-with-cc`。

Any user mention of child-agent, subagent, sub-agent, child-thread, subthread, delegation, worker-execution, or Chinese equivalents such as 子代理、子线程、多代理、委派、派工、执行层 is a workflow trigger.

触发后必须走：

```text
Codex 主线程 -> Codex spawn_agent 子线程 -> 已安装插件中的 delegate_to_claude.* -> scripts/*.py -> Claude Code CLI
```

不要把 Codex 默认子代理工作流、宿主环境内置代理流程、直接运行 `claude`、或主线程直接执行 `delegate_to_claude.*` 当作等价替代。

## 分发源

唯一分发入口：

```text
Marketplace repo: https://github.com/aiskyhub/aiskyhub
Codex marketplace source: .agents/plugins/marketplace.json
Claude marketplace source: .claude-plugin/marketplace.json
Plugin name: codex-with-cc@aiskyhub
```

## Codex 安装协议

Codex 当前 CLI 没有 `marketplace list` 子命令，因此“是否已添加 marketplace”必须通过读取 `~/.codex/config.toml` 判定。

### 1. 检查 marketplace

检查：

```toml
[marketplaces.aiskyhub]
```

如果不存在，执行：

```bash
codex plugin marketplace add aiskyhub/aiskyhub
```

### 2. 检查插件

检查：

```toml
[plugins."codex-with-cc@aiskyhub"]
```

如果不存在或未启用，在 Codex 中执行：

```text
/plugin install codex-with-cc@aiskyhub --scope user
```

安装后如未即时生效，可提示用户重载插件或重启 Codex。

## Claude Code 安装协议

### 1. 检查 marketplace

执行：

```text
/plugin marketplace list
```

或：

```bash
claude plugin marketplace list
```

如果没有 `aiskyhub`，执行：

```text
/plugin marketplace add aiskyhub/aiskyhub
```

### 2. 安装插件

执行：

```text
/plugin install codex-with-cc@aiskyhub --scope user
```

如果当前会话没有刷新插件状态，补：

```text
/reload-plugins
```

## 失败处理

- marketplace 添加失败：直接报告失败并停止
- 插件安装失败：直接报告失败并停止
- 不要复制仓库文件到任何本地 skill 目录
- 不要创建或恢复旧的安装脚本路径
- 不要把失败处理成“先手动复制再说”

## 委派规则

```powershell
$env:CODEX_CLAUDE_CHILD_THREAD = '1'
pwsh -NoProfile -File "<installed-plugin-root>\windows_scripts\delegate_to_claude.ps1" `
  -TaskFile .\.codex\codex_with_cc\tasks\<yyyyMMdd>\<HHmmssfff>-<short-id>-<task-file>.md `
  -SessionMode PrimaryReuse `
  -SessionKey <stable-session-key> `
  -BypassPermissions
```

macOS 子线程标准调用形态：

```bash
export CODEX_CLAUDE_CHILD_THREAD=1
"<installed-plugin-root>/macos_scripts/delegate_to_claude.sh" \
  -TaskFile ./.codex/codex_with_cc/tasks/<yyyyMMdd>/<HHmmssfff>-<short-id>-<task-file>.md \
  -SessionMode PrimaryReuse \
  -SessionKey <stable-session-key> \
  -BypassPermissions
```

并行任务按场景使用：

- `PrimaryAnchor -AllowParallel`：并行批次的主线锚点。
- `ParallelPool -AllowParallel`：独立支线任务池。

只有任务范围互不冲突时才允许并行。多个子代理同时修改同一批文件时，必须拆分写入边界或改为串行。

## 验证与产物

委派运行产物默认写在当前项目：

```text
.codex/codex_with_cc/claude-delegate
```

常见文件包括：

- `claude_<RunId>.md`
- `status_<RunId>.json`
- `config_<RunId>.json`
- `prompt_<RunId>.md`
- `stream_<RunId>.jsonl`
- `trace_<RunId>.log`
- `session-pools/<SessionKey>.json`

检查单次委派产物：

```powershell
pwsh -NoProfile -File "<installed-plugin-root>\windows_scripts\verify_delegate_artifacts.ps1"
```

```bash
"<installed-plugin-root>/macos_scripts/verify_delegate_artifacts.sh"
```

检查多轮链路连续性：

```powershell
pwsh -NoProfile -File "<installed-plugin-root>\windows_scripts\verify_delegate_chain.ps1"
```

```bash
"<installed-plugin-root>/macos_scripts/verify_delegate_chain.sh"
```
