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

$installerRoot = $PSScriptRoot
$sourceWorkflowRoot = Join-Path $installerRoot 'docs\codex_with_cc'
if (-not (Test-Path -LiteralPath $sourceWorkflowRoot)) {
  throw "Workflow source was not found: $sourceWorkflowRoot"
}

$resolvedTargetRoot = Get-FullPath -Path $TargetRoot
if (-not (Test-Path -LiteralPath $resolvedTargetRoot)) {
  New-Item -ItemType Directory -Path $resolvedTargetRoot -Force | Out-Null
}
$resolvedTargetRoot = (Resolve-Path -LiteralPath $resolvedTargetRoot).Path

$docsRoot = Join-Path $resolvedTargetRoot 'docs'
$workflowRoot = Join-Path $docsRoot 'codex_with_cc'

if (Test-Path -LiteralPath $workflowRoot) {
  if (-not (Test-PathInside -Child $workflowRoot -Parent $resolvedTargetRoot)) {
    throw "Refusing to remove workflow directory outside target root: $workflowRoot"
  }
  Remove-Item -LiteralPath $workflowRoot -Recurse -Force
}

New-Item -ItemType Directory -Path $docsRoot -Force | Out-Null
Copy-Item -LiteralPath $sourceWorkflowRoot -Destination $workflowRoot -Recurse -Force
New-Item -ItemType Directory -Path (Join-Path $workflowRoot 'tasks') -Force | Out-Null

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
