$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $repoRoot 'codex_with_cc\scripts\test_helpers.ps1')

$aiInstallPath = Join-Path $repoRoot 'AI_INSTALL.md'

Assert-True -Condition (Test-Path -LiteralPath $aiInstallPath) -Name 'ai-install-doc-exists'

$aiInstallText = Get-Content -LiteralPath $aiInstallPath -Raw

Assert-Contains -Text $aiInstallText -Needle '默认按上游仓库原样接入，不要擅自混入目标项目专属示例、业务规则、验证命令或定制说明。' -Name 'ai-install-doc-keeps-upstream-workflow-verbatim-by-default'
Assert-Contains -Text $aiInstallText -Needle '只有用户明确要求你“顺手定制”或“改成项目特化版本”时，才允许额外改工作流文档。' -Name 'ai-install-doc-allows-customization-only-on-explicit-request'
Assert-Contains -Text $aiInstallText -Needle '都必须强制走本仓库定义的自定义委派链路：`Codex 主线程 -> Codex 子代理 -> docs/codex_with_cc/scripts/delegate_to_claude.* -> Claude Code CLI`。' -Name 'ai-install-doc-forces-custom-delegate-chain'
Assert-Contains -Text $aiInstallText -Needle '不要把 Codex 自带的默认子代理工作流、宿主环境内置代理流程，或者“直接让当前 AI 自己继续开子代理干活”当作这套链路的等价替代。' -Name 'ai-install-doc-forbids-native-subagent-fallback'
Assert-Contains -Text $aiInstallText -Needle '只要任务需要进入子代理执行层，就必须使用本工作流提供的自定义委派链路，不要退回 Codex 默认子代理工作流充当执行层。' -Name 'ai-install-doc-makes-custom-chain-a-hard-rule'
Assert-Contains -Text $aiInstallText -Needle '.codex/codex_with_cc/tasks' -Name 'ai-install-doc-moves-task-files-under-codex'
Assert-Contains -Text $aiInstallText -Needle '.codex/codex_with_cc/tasks/<yyyyMMdd>/<HHmmssfff>-<short-id>-<task-file>.md' -Name 'ai-install-doc-uses-dated-unique-task-file'
Assert-Contains -Text $aiInstallText -Needle '.gitignore` 包含 `.codex/`' -Name 'ai-install-doc-ensures-codex-is-ignored'
Assert-Contains -Text $aiInstallText -Needle '安装器不支持把源仓库自身作为 `-TargetRoot`；请使用外部目标项目目录，避免安装时移除源工作流目录。' -Name 'ai-install-doc-forbids-self-target-install'
Assert-Contains -Text $aiInstallText -Needle '不要追问“要保留上游原样接入，还是顺手按当前项目定制”这类范围选择题。' -Name 'ai-install-doc-forbids-unnecessary-scope-questions'

Write-Host 'AI install doc tests passed' -ForegroundColor Green
