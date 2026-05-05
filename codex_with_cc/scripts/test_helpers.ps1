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

function Assert-Equal {
  param(
    [Parameter(Mandatory = $true)]
    [AllowNull()]
    [object]$Actual,
    [Parameter(Mandatory = $true)]
    [AllowNull()]
    [object]$Expected,
    [Parameter(Mandatory = $true)]
    [string]$Name
  )

  if ($Actual -ne $Expected) {
    throw "[$Name] expected '$Expected' but got '$Actual'"
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

function Invoke-DelegateWorkerScript {
  param(
    [Parameter(Mandatory = $true)]
    [string[]]$ArgumentList,
    [switch]$SetChildThreadMarker,
    [string]$ScriptPath = (Join-Path $PSScriptRoot 'delegate_to_claude.ps1')
  )

  $markerName = 'CODEX_CLAUDE_CHILD_THREAD'
  $originalMarker = [Environment]::GetEnvironmentVariable($markerName, 'Process')
  try {
    if ($SetChildThreadMarker) {
      [Environment]::SetEnvironmentVariable($markerName, '1', 'Process')
    } else {
      [Environment]::SetEnvironmentVariable($markerName, $null, 'Process')
    }

    $output = & pwsh -NoProfile -File $ScriptPath @ArgumentList 2>&1
    return [pscustomobject]@{
      ExitCode = $LASTEXITCODE
      Output = @($output)
    }
  } finally {
    [Environment]::SetEnvironmentVariable($markerName, $originalMarker, 'Process')
  }
}
