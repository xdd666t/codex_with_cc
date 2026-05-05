param(
  [string]$TargetRoot = (Get-Location).Path,
  [switch]$Force,
  [switch]$SkipAgentEntrypoints
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-FullPath {
  param([Parameter(Mandatory = $true)][string]$Path)
  return [System.IO.Path]::GetFullPath($Path)
}

function Test-PathInside {
  param(
    [Parameter(Mandatory = $true)][string]$Child,
    [Parameter(Mandatory = $true)][string]$Parent
  )

  $fullChild = Get-FullPath -Path $Child
  $fullParent = (Get-FullPath -Path $Parent).TrimEnd([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar)
  return $fullChild.StartsWith($fullParent + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)
}

function Update-AgentEntrypoint {
  param(
    [Parameter(Mandatory = $true)][string]$Path
  )

  $begin = '<!-- BEGIN CODEX_WITH_CC -->'
  $end = '<!-- END CODEX_WITH_CC -->'
  $block = @(
    $begin
    'Codex with Claude Code workflow: before using this workflow, read `docs/codex_with_cc/CODEX_WITH_CC.md`.'
    'If the task involves child agents, subagents, delegation, or any worker-execution step, you must read that file first and follow the custom `Codex main thread -> Codex child agent -> delegate_to_claude.* -> Claude Code CLI` workflow defined there.'
    $end
  ) -join [Environment]::NewLine

  if (Test-Path -LiteralPath $Path) {
    $text = Get-Content -LiteralPath $Path -Raw
    $pattern = '(?s)<!-- BEGIN CODEX_WITH_CC -->.*?<!-- END CODEX_WITH_CC -->'
    if ($text -match $pattern) {
      $updated = [regex]::Replace($text, $pattern, [System.Text.RegularExpressions.MatchEvaluator]{ param($m) $block })
    } else {
      $updated = $text.TrimEnd() + [Environment]::NewLine + [Environment]::NewLine + $block + [Environment]::NewLine
    }
  } else {
    $updated = $block + [Environment]::NewLine
  }

  [System.IO.File]::WriteAllText($Path, $updated, (New-Object System.Text.UTF8Encoding($false)))
}

function Update-GitIgnore {
  param(
    [Parameter(Mandatory = $true)][string]$Path
  )

  $entry = '.codex/'
  if (Test-Path -LiteralPath $Path) {
    $text = Get-Content -LiteralPath $Path -Raw
    $lines = @($text -split "\r?\n")
    $hasCodexIgnore = $false
    foreach ($line in $lines) {
      $normalizedLine = $line.Trim()
      if ($normalizedLine -eq '.codex' -or $normalizedLine -eq $entry) {
        $hasCodexIgnore = $true
        break
      }
    }

    if ($hasCodexIgnore) {
      return
    }

    $updated = $text.TrimEnd()
    if ($updated.Length -gt 0) {
      $updated += [Environment]::NewLine
    }
    $updated += $entry + [Environment]::NewLine
  } else {
    $updated = $entry + [Environment]::NewLine
  }

  [System.IO.File]::WriteAllText($Path, $updated, (New-Object System.Text.UTF8Encoding($false)))
}

$installerRoot = $PSScriptRoot
$resolvedInstallerRoot = Get-FullPath -Path $installerRoot
$sourceWorkflowRoot = Join-Path $installerRoot 'codex_with_cc'
if (-not (Test-Path -LiteralPath $sourceWorkflowRoot)) {
  throw "Workflow source was not found: $sourceWorkflowRoot"
}
$resolvedSourceWorkflowRoot = (Resolve-Path -LiteralPath $sourceWorkflowRoot).Path

$resolvedTargetRoot = Get-FullPath -Path $TargetRoot
if (-not (Test-Path -LiteralPath $resolvedTargetRoot)) {
  New-Item -ItemType Directory -Path $resolvedTargetRoot -Force | Out-Null
}
$resolvedTargetRoot = (Resolve-Path -LiteralPath $resolvedTargetRoot).Path

if ([string]::Equals($resolvedInstallerRoot, $resolvedTargetRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
  throw "Refusing to install codex_with_cc into its own source repository. Choose a different -TargetRoot so the installer does not modify its source repository: $resolvedInstallerRoot"
}

$docsRoot = Join-Path $resolvedTargetRoot 'docs'
$workflowRoot = Join-Path $docsRoot 'codex_with_cc'
$codexRoot = Join-Path $resolvedTargetRoot '.codex'
$taskRoot = Join-Path $codexRoot 'codex_with_cc\tasks'
$resolvedWorkflowRoot = [System.IO.Path]::GetFullPath($workflowRoot)

if ([string]::Equals($resolvedSourceWorkflowRoot, $resolvedWorkflowRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
  throw "Refusing to install codex_with_cc into its own source repository. Choose a different -TargetRoot so the installer does not remove its source workflow directory: $resolvedSourceWorkflowRoot"
}

if (Test-Path -LiteralPath $workflowRoot) {
  if (-not (Test-PathInside -Child $workflowRoot -Parent $resolvedTargetRoot)) {
    throw "Refusing to remove workflow directory outside target root: $workflowRoot"
  }
  Remove-Item -LiteralPath $workflowRoot -Recurse -Force
}

New-Item -ItemType Directory -Path $docsRoot -Force | Out-Null
Copy-Item -LiteralPath $sourceWorkflowRoot -Destination $workflowRoot -Recurse -Force
New-Item -ItemType Directory -Path $taskRoot -Force | Out-Null
$taskGitkeepPath = Join-Path $taskRoot '.gitkeep'
if (Test-Path -LiteralPath $taskGitkeepPath) {
  Remove-Item -LiteralPath $taskGitkeepPath -Force
}
Update-GitIgnore -Path (Join-Path $resolvedTargetRoot '.gitignore')

if (-not $SkipAgentEntrypoints) {
  foreach ($entryName in @('AGENTS.md')) {
    Update-AgentEntrypoint -Path (Join-Path $resolvedTargetRoot $entryName)
  }
}

Write-Host "codex_with_cc installed into: $workflowRoot"
if (-not $SkipAgentEntrypoints) {
  Write-Host 'Agent entrypoints updated: AGENTS.md'
}
Write-Host 'Next: read docs/codex_with_cc/CODEX_WITH_CC.md and use it as the single workflow contract.'
