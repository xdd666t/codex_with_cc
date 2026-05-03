# AI 安装说明

本文件写给 Codex。用户如果让你“集成 https://github.com/xdd666t/codex_with_cc 调度子线程工作流到本项目中”，请按本文执行。

README 是给人看的，不要把下面这些安装命令、验证命令、平台细节塞回 README。

## 目标

把本仓库的 `Codex -> Codex 子代理 -> Claude Code CLI` 委派工作流集成到目标项目，使目标项目获得：

- `docs/codex_with_cc` 工作流文档和脚本。
- `AGENTS.md` 的入口提示。
- 主 Codex 线程负责规划、调度、审核。
- Codex 子代理负责调用 Claude Code 委派脚本。
- Claude Code 后端可通过 CC Switch 切到 DeepSeek。

## 硬性规则

1. 保留目标项目原有内容，不要覆盖用户已有规则。
2. 主 Codex 线程不要直接运行 `claude`。
3. 主 Codex 线程不要直接运行委派脚本。
4. 真正的 Claude Code 委派必须由 Codex 子代理执行。
5. 子代理调用委派脚本前必须设置 `CODEX_CLAUDE_CHILD_THREAD=1`。
6. 能验证就验证，不能验证要说明阻塞原因。

## 推荐安装流程

1. 确认当前工作目录是目标项目根目录。
2. 拉取或读取源仓库：`https://github.com/xdd666t/codex_with_cc`。
   - 如果你已经在本仓库本地工作区内，可以直接使用当前工作区作为源仓库。
   - 如果只能通过 GitHub URL 拉取，先确认远程仓库不是空仓库，并且能读到下面列出的必需文件。
   - 如果远程仓库没有分支引用、克隆后为空，或缺少必需文件，停止安装并告诉用户“源仓库尚未发布或内容不完整”，不要编造安装成功。
3. 检查源仓库里是否存在：

```text
install_codex_with_cc.ps1
docs/codex_with_cc/CODEX_WITH_CC.md
docs/codex_with_cc/scripts/delegate_to_claude.ps1
```

4. 将 `docs/codex_with_cc` 安装到目标项目的：

```text
docs/codex_with_cc
```

安装脚本还应确保 `docs/codex_with_cc/tasks` 存在，用于放委派任务文件；不要再依赖 `.gitkeep` 之类的占位文件。

5. 更新目标项目根目录的 `AGENTS.md`。没有就创建，有就追加托管块，不要删旧内容。

推荐托管块：

```markdown
<!-- BEGIN CODEX_WITH_CC -->
Codex with Claude Code workflow: before using this workflow, read `docs/codex_with_cc/CODEX_WITH_CC.md`.
<!-- END CODEX_WITH_CC -->
```

6. 检查目标项目自己的规则文件。如果存在强约束，以目标项目规则为准；不要再为本工作流额外生成独立的 host-rules 或 project-memory 文档。
7. 运行当前平台可用的验证。
8. 向用户报告改动文件、验证结果、使用方式和剩余限制。

## Windows 安装

如果当前平台是 Windows，并且可以运行 PowerShell Core，优先使用源仓库的安装脚本。

在源仓库根目录执行：

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\install_codex_with_cc.ps1 -TargetRoot <target-project>
```

如果目标项目已经装过，直接再次执行同一条命令即可。安装脚本默认会先删除旧的 `docs/codex_with_cc` 再重装，避免已经废弃的文档、脚本或占位文件残留在目标项目里。

如果用户明确要求不修改 `AGENTS.md`：

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\install_codex_with_cc.ps1 -TargetRoot <target-project> -SkipAgentEntrypoints
```

Windows 验证命令，在目标项目根目录执行：

```powershell
pwsh -NoProfile -File .\docs\codex_with_cc\scripts\test_delegate_runtime.ps1
pwsh -NoProfile -File .\docs\codex_with_cc\scripts\test_delegate_session_pool.ps1
pwsh -NoProfile -File .\docs\codex_with_cc\scripts\run_real_delegate_chain_validation.ps1
```

## macOS 安装

macOS 不要照抄 Windows PowerShell 命令给用户。应该把工作流迁移成 macOS 原生命令。

执行原则：

1. 使用 `bash` 或 `zsh`。
2. 使用 Unix 路径。
3. 需要脚本入口时生成 `.sh` 或等价可执行脚本。
4. 需要执行权限时使用 `chmod +x`。
5. 保留原工作流语义，不要为了迁移改掉主线程/子代理边界。

建议动作：

1. 复制 `docs/codex_with_cc` 到目标项目的 `docs/codex_with_cc`。
2. 将需要在 macOS 运行的 `.ps1` 脚本迁移为等价 `.sh` 脚本。
3. 更新文档里的命令示例，让 macOS 项目引用 `.sh` 入口。
4. 确认委派脚本仍然只能由 Codex 子代理调用。
5. 运行 macOS 下可运行的验证脚本。
6. 如果暂时不能完整迁移某个验证脚本，明确说明缺口。

macOS 子代理设置环境变量示例：

```bash
export CODEX_CLAUDE_CHILD_THREAD=1
```

macOS 委派入口可以命名为 `delegate_to_claude.sh` 或项目内更合适的名字。名字不重要，语义重要：主 Codex 线程不能直接运行它。

## 委派规则

Windows 模板中的子代理标准调用形态：

```powershell
$env:CODEX_CLAUDE_CHILD_THREAD = '1'
pwsh -NoProfile -File .\docs\codex_with_cc\scripts\delegate_to_claude.ps1 `
  -TaskFile .\docs\codex_with_cc\tasks\<task-file>.md `
  -SessionMode PrimaryReuse `
  -SessionKey <stable-session-key> `
  -BypassPermissions
```

并行任务按场景使用：

- `PrimaryAnchor -AllowParallel`：并行批次的主线锚点。
- `ParallelPool -AllowParallel`：独立支线任务池。

只有任务范围互不冲突时才允许并行。多个子代理同时修改同一批文件时，必须拆分写入边界或改为串行。

## 产物位置

委派过程的产物默认写在目标项目：

```text
.codex/claude-delegate
```

常见文件包括：

- `claude_<RunId>.md`
- `status_<RunId>.json`
- `config_<RunId>.json`
- `prompt_<RunId>.md`
- `stream_<RunId>.jsonl`
- `trace_<RunId>.log`
- `session-pools/<SessionKey>.json`

Windows 模板里，检查单次委派产物：

```powershell
pwsh -NoProfile -File .\docs\codex_with_cc\scripts\verify_delegate_artifacts.ps1
```

Windows 模板里，检查多轮链路连续性：

```powershell
pwsh -NoProfile -File .\docs\codex_with_cc\scripts\verify_delegate_chain.ps1
```

## 安装完成后回复用户

最终回复必须包含：

- 安装到了哪里。
- 修改了哪些入口文件。
- 跑了哪些验证命令及结果。
- 当前平台是否完整支持。
- 后续用户应该如何派活。

不要只说“已完成”。如果某项验证没有运行，说明原因。
