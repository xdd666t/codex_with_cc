# AI 安装说明

## 默认交互策略（零打扰优先）

只要用户是在让你“安装 / 集成 / 更新这套工作流”，默认进入零打扰安装模式：

1. 直接执行，不要先把安装过程变成问答游戏。
2. 默认允许你按安装目录选择规则创建或覆盖目标项目里的 `<workflow-root>`。
3. 默认允许你更新目标项目根目录的 `AGENTS.md`；只有用户明确说“不改 AGENTS.md”时才跳过。
4. 默认允许你运行当前平台可用的本地验证；不要先问“要不要跑验证”。
5. 默认当前工作目录就是目标项目根目录；如果能从上下文或工作区直接判断，就不要为此提问。
6. 默认用户要的是“直接装好并汇报结果”，不是“先出方案再等批准”。
7. 默认按上游仓库原样接入，不要擅自混入目标项目专属示例、业务规则、验证命令或定制说明。
8. 只有用户明确要求你“顺手定制”或“改成项目特化版本”时，才允许额外改工作流文档。

下面这些问题，默认不要问：

- “要不要创建 `<workflow-root>`？”
- “要不要修改 `AGENTS.md`？”
- “要不要保留旧的 `<workflow-root>` 内容？”
- “要不要顺手跑验证脚本？”
- “你是想安装还是更新？”
- “要不要我先给你一个计划？”
- “要保留上游原样接入，还是顺手按当前项目定制？”

只有遇到下面这些硬阻塞，才允许停下来问用户：

1. 目标项目根目录无法确定，而且无法从当前工作区或用户上下文推断。
2. 源仓库内容缺失、为空，或缺少安装所需关键文件。
3. 当前平台没有可用执行路径，而且你也无法完成等价迁移。
4. 写入失败、命令无法执行、权限被拒绝，且无法自行绕过。
5. 目标项目已有明确规则禁止你修改入口文件或禁止你运行验证命令。

如果不是上面这些硬阻塞，就继续安装，做完再一次性汇报。

补充约束：

- 这套工作流默认是独立层，不需要为了当前项目再发明一份特化版安装文档。
- 一旦目标项目接入这套工作流，后续凡是需要子代理/子线程继续执行的任务，都必须强制走本仓库定义的自定义委派链路：`Codex 主线程 -> Codex 子代理 -> <workflow-root>/<platform_scripts>/delegate_to_claude.* -> scripts/*.py -> Claude Code CLI`。
- 不要把 Codex 自带的默认子代理工作流、宿主环境内置代理流程，或者“直接让当前 AI 自己继续开子代理干活”当作这套链路的等价替代。
- 如果 Codex 沙箱或代跑环境无法执行同一委派命令，Codex 应改由受信任本机终端运行同一命令，并保留相同的 `CODEX_CLAUDE_CHILD_THREAD=1`、`TaskFile`、Session 参数、ArtifactRoot 和权限参数。
- 不要追问“要保留上游原样接入，还是顺手按当前项目定制”这类范围选择题。
- 如果用户没有明确提出项目定制诉求，就保持上游文件原样接入，再在最终汇报里说明哪些验证受当前平台或本机环境限制。
- Windows 目标项目不要安装 `macos_scripts`；macOS 目标项目不要安装 `windows_scripts`。两个平台都必须安装共享的 `scripts/*.py`。

## 目标

把本仓库的 `Codex -> Codex 子代理 -> Claude Code CLI` 委派工作流集成到目标项目，使目标项目获得：

- `<workflow-root>` 工作流文档和脚本。
- `AGENTS.md` 的入口提示。
- 主 Codex 线程负责规划、调度、审核。
- Codex 子代理负责调用 Claude Code 委派脚本。
- Claude Code 后端可通过 CC Switch 切到 DeepSeek。

## 硬性规则

1. 保留目标项目原有内容，不要覆盖用户已有规则。
2. 主 Codex 线程不要直接运行 `claude`。
3. 主 Codex 线程不要直接运行委派脚本。
4. 真正的 Claude Code 委派必须由 Codex 子代理执行。
5. 只要任务需要进入子代理执行层，就必须使用本工作流提供的自定义委派链路，不要退回 Codex 默认子代理工作流充当执行层。
6. 子代理调用委派脚本前必须设置 `CODEX_CLAUDE_CHILD_THREAD=1`。
7. 如果 Codex 沙箱不能承载同一委派命令，允许使用受信任本机终端执行完全相同的命令；这只是执行位置 fallback，不允许改命令、绕过委派脚本或直接调用 `claude`。
8. Claude Code worker 必须读取并遵守目标项目 `.codex` 下约定的 skills、技能文档和工作流要求。
9. 能验证就验证，不能验证要说明阻塞原因。

## 推荐安装流程

1. 优先把当前工作目录直接视为目标项目根目录；只有在根目录确实无法判断时才提问。
2. 拉取或读取源仓库：`https://github.com/xdd666t/codex_with_cc`。
   - 如果你已经在本仓库本地工作区内，可以直接使用当前工作区作为源仓库。
   - 如果只能通过 GitHub URL 拉取，先确认远程仓库不是空仓库，并且能读到下面列出的必需文件。
   - 如果远程仓库没有分支引用、克隆后为空，或缺少必需文件，停止安装并告诉用户“源仓库尚未发布或内容不完整”，不要编造安装成功。
3. 检查源仓库里是否存在：

```text
install_codex_with_cc.ps1
codex_with_cc/CODEX_WITH_CC.md
codex_with_cc/scripts/delegate_to_claude.py
codex_with_cc/windows_scripts/delegate_to_claude.ps1
codex_with_cc/macos_scripts/delegate_to_claude.sh
```

4. 按目标项目根目录直属文档目录选择工作流安装位置。

```text
安装目录选择规则：目标项目根目录同时存在 `docs` 和 `doc` 时使用 `docs/codex_with_cc`；只有 `doc` 时使用 `doc/codex_with_cc`；两者都没有时创建并使用 `docs/codex_with_cc`。
```

安装脚本还应确保 `.codex/codex_with_cc/tasks` 存在，用于放委派任务文件；实际任务文件应按 `.codex/codex_with_cc/tasks/<yyyyMMdd>/<HHmmssfff>-<short-id>-<task-file>.md` 创建，避免同一天多个会话或多个子代理任务使用固定文件名互相覆盖；不要再把任务文件放进 `<workflow-root>` 这种会进版本库的目录里，也不要再依赖 `.gitkeep` 之类的占位文件。
同时应确保目标项目的 `.gitignore` 包含 `.codex/codex_with_cc`，避免本工作流的委派任务和运行产物被误提交，同时不影响目标项目 `.codex` 下其他共用内容。

5. 更新目标项目根目录的 `AGENTS.md`。没有就创建，有就追加托管块，不要删旧内容。除非用户明确禁止，否则不要为这一步单独征求确认。

推荐托管块：

```markdown
<!-- BEGIN CODEX_WITH_CC -->
Codex with Claude Code workflow: before using this workflow, read `<workflow-root>/CODEX_WITH_CC.md`.
If the task involves child agents, subagents, delegation, or any worker-execution step, you must read that file first and follow the custom `Codex main thread -> Codex child agent -> delegate_to_claude.* -> Claude Code CLI` workflow defined there.
<!-- END CODEX_WITH_CC -->
```

6. 检查目标项目自己的规则文件。如果存在强约束，以目标项目规则为准；不要再为本工作流额外生成独立的 host-rules 或 project-memory 文档。
7. 运行当前平台可用的验证。能直接跑就直接跑，不要先问用户“是否现在验证”。
8. 向用户报告改动文件、验证结果、使用方式和剩余限制；把确认放在安装之后，而不是安装之前。

## Windows 安装

如果当前平台是 Windows，并且可以运行 PowerShell Core，优先使用源仓库的安装脚本。安装器会先检查 Python 3.9+；如果缺失，默认通过 `winget install Python.Python.3.14` 自动安装。

在源仓库根目录执行：

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\install_codex_with_cc.ps1 -TargetRoot <target-project>
```

安装器不支持把源仓库自身作为 `-TargetRoot`；请使用外部目标项目目录，避免安装时移除源工作流目录。

如果目标项目已经装过，直接再次执行同一条命令即可。安装脚本默认会先清理旧的 `docs/codex_with_cc` 和 `doc/codex_with_cc`，再按当前目录选择规则重装，避免已经废弃的文档、脚本或占位文件残留在目标项目里。

如果用户没有额外限制，Windows 下默认动作应该是：

1. 直接执行安装脚本。
2. 直接更新 `AGENTS.md`。
3. 直接运行：
   - `pwsh -NoProfile -File .\<workflow-root>\windows_scripts\test_delegate_runtime.ps1`
   - `pwsh -NoProfile -File .\<workflow-root>\tests\windows_scripts\test_delegate_runtime.ps1`
   - `pwsh -NoProfile -File .\<workflow-root>\tests\windows_scripts\test_delegate_session_pool.ps1`
4. 最后再向用户汇报结果。

不要在 Windows 安装前额外追问“是否覆盖旧工作流”“是否需要验证”“是否需要改 AGENTS.md”；这些都已经有默认答案。

如果用户明确要求不修改 `AGENTS.md`：

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\install_codex_with_cc.ps1 -TargetRoot <target-project> -SkipAgentEntrypoints
```

Windows 验证命令，在目标项目根目录执行：

```powershell
pwsh -NoProfile -File .\<workflow-root>\tests\windows_scripts\test_delegate_runtime.ps1
pwsh -NoProfile -File .\<workflow-root>\tests\windows_scripts\test_delegate_session_pool.ps1
pwsh -NoProfile -File .\<workflow-root>\windows_scripts\run_real_delegate_chain_validation.ps1
```

## macOS 安装

macOS 不要照抄 Windows PowerShell 命令给用户。优先使用源仓库的原生安装脚本。安装器会先检查 Python 3.9+；如果缺失，默认通过 Homebrew 自动安装 Python。没有 Homebrew 时，会使用官方非交互 Homebrew installer，再执行 `brew install python`。

执行原则：

1. 使用 `bash` 或 `zsh`。
2. 使用 Unix 路径。
3. 需要脚本入口时生成 `.sh` 或等价可执行脚本。
4. 需要执行权限时使用 `chmod +x`。
5. 保留原工作流语义，不要为了迁移改掉主线程/子代理边界。

默认安装命令：

```bash
./install_codex_with_cc.sh --target-root <target-project> --platform macOS
```

如果用户明确要求不修改 `AGENTS.md`：

```bash
./install_codex_with_cc.sh --target-root <target-project> --platform macOS --skip-agent-entrypoints
```

macOS 验证命令，在目标项目根目录执行：

```bash
./<workflow-root>/tests/macos_scripts/test_delegate_runtime.sh
./<workflow-root>/tests/macos_scripts/test_delegate_session_pool.sh
./<workflow-root>/macos_scripts/run_real_delegate_chain_validation.sh
```

macOS 子代理设置环境变量示例：

```bash
export CODEX_CLAUDE_CHILD_THREAD=1
```

macOS 委派入口可以命名为 `delegate_to_claude.sh` 或项目内更合适的名字。名字不重要，语义重要：主 Codex 线程不能直接运行它。

## 委派规则

Windows 模板中的子代理标准调用形态：

```powershell
$env:CODEX_CLAUDE_CHILD_THREAD = '1'
pwsh -NoProfile -File .\<workflow-root>\windows_scripts\delegate_to_claude.ps1 `
  -TaskFile .\.codex\codex_with_cc\tasks\<yyyyMMdd>\<HHmmssfff>-<short-id>-<task-file>.md `
  -SessionMode PrimaryReuse `
  -SessionKey <stable-session-key> `
  -BypassPermissions
```

macOS 模板中的子代理标准调用形态：

```bash
export CODEX_CLAUDE_CHILD_THREAD=1
./<workflow-root>/macos_scripts/delegate_to_claude.sh \
  -TaskFile ./.codex/codex_with_cc/tasks/<yyyyMMdd>/<HHmmssfff>-<short-id>-<task-file>.md \
  -SessionMode PrimaryReuse \
  -SessionKey <stable-session-key> \
  -BypassPermissions
```

并行任务按场景使用：

- `PrimaryAnchor -AllowParallel`：并行批次的主线锚点。
- `ParallelPool -AllowParallel`：独立支线任务池。

只有任务范围互不冲突时才允许并行。多个子代理同时修改同一批文件时，必须拆分写入边界或改为串行。

## 产物位置

委派过程的产物默认写在目标项目：

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

Windows 模板里，检查单次委派产物：

```powershell
pwsh -NoProfile -File .\<workflow-root>\windows_scripts\verify_delegate_artifacts.ps1
```

macOS 模板里，检查单次委派产物：

```bash
./<workflow-root>/macos_scripts/verify_delegate_artifacts.sh
```

Windows 模板里，检查多轮链路连续性：

```powershell
pwsh -NoProfile -File .\<workflow-root>\windows_scripts\verify_delegate_chain.ps1
```

macOS 模板里，检查多轮链路连续性：

```bash
./<workflow-root>/macos_scripts/verify_delegate_chain.sh
```

## 安装完成后回复用户

最终回复必须包含：

- 安装到了哪里。
- 修改了哪些入口文件。
- 跑了哪些验证命令及结果。
- 当前平台是否完整支持。
- 后续用户应该如何派活。

不要只说“已完成”。如果某项验证没有运行，说明原因。
