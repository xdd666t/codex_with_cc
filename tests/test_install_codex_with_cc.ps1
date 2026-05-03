$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Assert-True {
  param(
    [Parameter(Mandatory = $true)]
    [bool]$Condition,
    [Parameter(Mandatory = $true)]
    [string]$Name
  )

  if (-not $Condition) {
    throw "[$Name] assertion failed"
  }
}

function Assert-Contains {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Text,
    [Parameter(Mandatory = $true)]
    [string]$Needle,
    [Parameter(Mandatory = $true)]
    [string]$Name
  )

  Assert-True -Condition ($Text.Contains($Needle)) -Name $Name
}

function Assert-NotContains {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Text,
    [Parameter(Mandatory = $true)]
    [string]$Needle,
    [Parameter(Mandatory = $true)]
    [string]$Name
  )

  Assert-True -Condition (-not $Text.Contains($Needle)) -Name $Name
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$installerPath = Join-Path $repoRoot 'install_codex_with_cc.ps1'
$sourceWorkflowRoot = Join-Path $repoRoot 'docs\codex_with_cc'
$legacyTemplatesRoot = Join-Path $repoRoot 'templates'
$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) "codex_with_cc_install_$([guid]::NewGuid().ToString('N'))"
$targetRoot = Join-Path $tempRoot 'host-project'

try {
  New-Item -ItemType Directory -Path $targetRoot -Force | Out-Null
  Set-Content -LiteralPath (Join-Path $targetRoot 'README.md') -Value '# Host Project' -Encoding UTF8
  Set-Content -LiteralPath (Join-Path $targetRoot 'AGENTS.md') -Value @"
# Existing Host Instructions

Keep this project-specific rule.
"@ -Encoding UTF8

  Assert-True -Condition (Test-Path -LiteralPath $installerPath) -Name 'installer-exists'
  Assert-True -Condition (Test-Path -LiteralPath $sourceWorkflowRoot) -Name 'source-workflow-root-exists-in-docs'
  Assert-True -Condition (-not (Test-Path -LiteralPath $legacyTemplatesRoot)) -Name 'legacy-templates-root-removed'

  $installOutput = & pwsh -NoProfile -ExecutionPolicy Bypass -File $installerPath -TargetRoot $targetRoot 2>&1
  if ($LASTEXITCODE -ne 0) {
    throw "installer failed unexpectedly.`n$($installOutput -join [Environment]::NewLine)"
  }

  $workflowRoot = Join-Path $targetRoot 'docs\codex_with_cc'
  Assert-True -Condition (Test-Path -LiteralPath $workflowRoot) -Name 'workflow-root-created'
  Assert-True -Condition (Test-Path -LiteralPath (Join-Path $workflowRoot 'CODEX_WITH_CC.md')) -Name 'codex-with-cc-entry-created'
  Assert-True -Condition (-not (Test-Path -LiteralPath (Join-Path $workflowRoot 'CLAUDE_CODE_DELEGATION.md'))) -Name 'delegation-doc-not-created'
  Assert-True -Condition (-not (Test-Path -LiteralPath (Join-Path $workflowRoot 'HOST_PROJECT_RULES.md'))) -Name 'host-rules-not-created'
  Assert-True -Condition (-not (Test-Path -LiteralPath (Join-Path $workflowRoot 'PROJECT_MEMORY.md'))) -Name 'project-memory-not-created'
  Assert-True -Condition (Test-Path -LiteralPath (Join-Path $workflowRoot 'scripts\delegate_to_claude.ps1')) -Name 'delegate-script-created'
  Assert-True -Condition (Test-Path -LiteralPath (Join-Path $workflowRoot 'scripts\verify_delegate_chain.ps1')) -Name 'chain-verifier-created'
  Assert-True -Condition (Test-Path -LiteralPath (Join-Path $workflowRoot 'tasks')) -Name 'tasks-dir-created'
  Assert-True -Condition (-not (Test-Path -LiteralPath (Join-Path $workflowRoot 'tasks\.gitkeep'))) -Name 'tasks-gitkeep-not-created'
  Assert-True -Condition (-not (Test-Path -LiteralPath (Join-Path $targetRoot 'docs\ai'))) -Name 'legacy-docs-ai-not-created'
  Assert-True -Condition (-not (Test-Path -LiteralPath (Join-Path $targetRoot 'docs\scripts\ai'))) -Name 'legacy-docs-scripts-ai-not-created'

  $agentsText = Get-Content -LiteralPath (Join-Path $targetRoot 'AGENTS.md') -Raw
  Assert-Contains -Text $agentsText -Needle 'Keep this project-specific rule.' -Name 'existing-agents-content-preserved'
  Assert-Contains -Text $agentsText -Needle '<!-- BEGIN CODEX_WITH_CC -->' -Name 'agents-managed-block-added'
  Assert-Contains -Text $agentsText -Needle 'docs/codex_with_cc/CODEX_WITH_CC.md' -Name 'agents-managed-block-points-to-central-entry'
  Assert-Contains -Text $agentsText -Needle '`docs/codex_with_cc/CODEX_WITH_CC.md`' -Name 'agents-managed-block-keeps-markdown-code-format'
  Assert-True -Condition (-not (Test-Path -LiteralPath (Join-Path $targetRoot 'CLAUDE.md'))) -Name 'claude-entrypoint-not-created'
  Assert-True -Condition (-not (Test-Path -LiteralPath (Join-Path $targetRoot 'GEMINI.md'))) -Name 'gemini-entrypoint-not-created'
  Assert-Contains -Text ($installOutput -join [Environment]::NewLine) -Needle 'Agent entrypoints updated: AGENTS.md' -Name 'install-output-lists-only-agents'

  $delegateText = Get-Content -LiteralPath (Join-Path $workflowRoot 'scripts\delegate_to_claude.ps1') -Raw
  Assert-Contains -Text $delegateText -Needle 'docs/codex_with_cc/CODEX_WITH_CC.md' -Name 'delegate-uses-central-workflow-entry'
  Assert-Contains -Text $delegateText -Needle 'docs/codex_with_cc/scripts/delegate_to_claude.ps1' -Name 'delegate-prompt-uses-central-script-path'
  Assert-NotContains -Text $delegateText -Needle 'docs/codex_with_cc/CLAUDE_CODE_DELEGATION.md' -Name 'delegate-does-not-use-delegation-sidecar-doc'
  Assert-NotContains -Text $delegateText -Needle 'docs/codex_with_cc/PROJECT_MEMORY.md' -Name 'delegate-does-not-use-central-project-memory'
  Assert-NotContains -Text $delegateText -Needle 'docs/codex_with_cc/HOST_PROJECT_RULES.md' -Name 'delegate-does-not-use-host-project-rules'
  Assert-NotContains -Text $delegateText -Needle 'SmartDialog' -Name 'delegate-does-not-include-easy-kit-ui-rule'
  Assert-NotContains -Text $delegateText -Needle 'pubspec.yaml' -Name 'delegate-does-not-include-easy-kit-pubspec-rule'
  Assert-NotContains -Text $delegateText -Needle 'docs/ai/PROJECT_MEMORY.md' -Name 'delegate-does-not-use-legacy-project-memory-path'
  Assert-NotContains -Text $delegateText -Needle 'D:\Develop\GitHub\easy_kit' -Name 'delegate-does-not-hardcode-easy-kit-path'

  Set-Content -LiteralPath (Join-Path $workflowRoot 'obsolete.txt') -Value 'stale' -Encoding UTF8
  Set-Content -LiteralPath (Join-Path $workflowRoot 'HOST_PROJECT_RULES.md') -Value 'stale host rules' -Encoding UTF8
  Set-Content -LiteralPath (Join-Path $workflowRoot 'PROJECT_MEMORY.md') -Value 'stale project memory' -Encoding UTF8
  New-Item -ItemType Directory -Path (Join-Path $workflowRoot 'tasks') -Force | Out-Null
  Set-Content -LiteralPath (Join-Path $workflowRoot 'tasks\.gitkeep') -Value '' -Encoding UTF8

  $reinstallOutput = & pwsh -NoProfile -ExecutionPolicy Bypass -File $installerPath -TargetRoot $targetRoot 2>&1
  if ($LASTEXITCODE -ne 0) {
    throw "reinstall failed unexpectedly.`n$($reinstallOutput -join [Environment]::NewLine)"
  }

  $agentsTextAfterReinstall = Get-Content -LiteralPath (Join-Path $targetRoot 'AGENTS.md') -Raw
  $managedBlockCount = ([regex]::Matches($agentsTextAfterReinstall, '<!-- BEGIN CODEX_WITH_CC -->')).Count
  Assert-True -Condition ($managedBlockCount -eq 1) -Name 'reinstall-keeps-one-managed-block'
  Assert-True -Condition (-not (Test-Path -LiteralPath (Join-Path $workflowRoot 'obsolete.txt'))) -Name 'reinstall-removes-obsolete-file'
  Assert-True -Condition (-not (Test-Path -LiteralPath (Join-Path $workflowRoot 'HOST_PROJECT_RULES.md'))) -Name 'reinstall-removes-stale-host-rules'
  Assert-True -Condition (-not (Test-Path -LiteralPath (Join-Path $workflowRoot 'PROJECT_MEMORY.md'))) -Name 'reinstall-removes-stale-project-memory'
  Assert-True -Condition (-not (Test-Path -LiteralPath (Join-Path $workflowRoot 'tasks\.gitkeep'))) -Name 'reinstall-removes-stale-gitkeep'
  Assert-True -Condition (Test-Path -LiteralPath (Join-Path $workflowRoot 'tasks')) -Name 'reinstall-recreates-tasks-dir'

  Write-Host 'install tests passed' -ForegroundColor Green
} finally {
  if (Test-Path -LiteralPath $tempRoot) {
    Remove-Item -LiteralPath $tempRoot -Recurse -Force
  }
}
