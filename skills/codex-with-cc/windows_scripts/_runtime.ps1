Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Resolve-CodexWithCcPython {
  $candidates = @(
    @{ Exe = 'py'; Prefix = @('-3') },
    @{ Exe = 'python'; Prefix = @() },
    @{ Exe = 'python3'; Prefix = @() }
  )

  foreach ($candidate in $candidates) {
    $command = Get-Command $candidate.Exe -ErrorAction SilentlyContinue
    if ($null -eq $command) {
      continue
    }

    $probeArgs = @($candidate.Prefix + @('-c', 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)'))
    & $candidate.Exe @probeArgs *> $null
    if ($LASTEXITCODE -eq 0) {
      return [pscustomobject]$candidate
    }
  }

  throw 'Python 3.9+ was not found. Install Python and expose py/python/python3 on PATH, then retry the codex-with-cc plugin command.'
}

function Invoke-CodexWithCcRuntime {
  param(
    [Parameter(Mandatory = $true)][string]$PythonScript,
    [string[]]$RemainingArgs = @()
  )

  $workflowRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
  $runtimeRoot = Join-Path $workflowRoot 'scripts'
  $runtimeScript = Join-Path $runtimeRoot $PythonScript
  if (-not (Test-Path -LiteralPath $runtimeScript)) {
    throw "Missing shared Python runtime: $runtimeScript"
  }

  $python = Resolve-CodexWithCcPython
  $oldPythonPath = [Environment]::GetEnvironmentVariable('PYTHONPATH', 'Process')
  try {
    $newPythonPath = if ([string]::IsNullOrWhiteSpace($oldPythonPath)) {
      $runtimeRoot
    } else {
      "$runtimeRoot$([IO.Path]::PathSeparator)$oldPythonPath"
    }
    [Environment]::SetEnvironmentVariable('PYTHONPATH', $newPythonPath, 'Process')
    $runtimeArgs = @($python.Prefix + @($runtimeScript) + $RemainingArgs)
    & $python.Exe @runtimeArgs
    exit $LASTEXITCODE
  } finally {
    [Environment]::SetEnvironmentVariable('PYTHONPATH', $oldPythonPath, 'Process')
  }
}
