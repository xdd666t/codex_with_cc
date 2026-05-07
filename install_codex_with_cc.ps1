param(
  [string]$TargetRoot = (Get-Location).Path,
  [ValidateSet('Auto', 'Windows', 'macOS')]
  [string]$Platform = 'Auto',
  [ValidateSet('Auto', 'Never')]
  [string]$BootstrapPython = 'Auto',
  [switch]$Force,
  [switch]$SkipAgentEntrypoints
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Resolve-CodexWithCcPython {
  $candidates = @(
    @{ Exe = 'py'; Prefix = @('-3') },
    @{ Exe = 'python'; Prefix = @() },
    @{ Exe = 'python3'; Prefix = @() },
    @{ Exe = '/opt/homebrew/bin/python3'; Prefix = @() },
    @{ Exe = '/usr/local/bin/python3'; Prefix = @() }
  )

  foreach ($candidate in $candidates) {
    $command = Get-Command $candidate.Exe -ErrorAction SilentlyContinue
    if ($null -eq $command -and -not (Test-Path -LiteralPath $candidate.Exe)) {
      continue
    }
    & $candidate.Exe @($candidate.Prefix + @('-c', 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)')) *> $null
    if ($LASTEXITCODE -eq 0) {
      return [pscustomobject]$candidate
    }
  }
  return $null
}

function Resolve-InstallPlatform {
  param([ValidateSet('Auto', 'Windows', 'macOS')][string]$Value)
  if ($Value -ne 'Auto') {
    return $Value
  }
  if ([System.Runtime.InteropServices.RuntimeInformation]::IsOSPlatform([System.Runtime.InteropServices.OSPlatform]::Windows)) {
    return 'Windows'
  }
  if ([System.Runtime.InteropServices.RuntimeInformation]::IsOSPlatform([System.Runtime.InteropServices.OSPlatform]::OSX)) {
    return 'macOS'
  }
  throw 'Unsupported install platform. Pass -Platform Windows or -Platform macOS explicitly.'
}

function Add-HomebrewToPath {
  foreach ($brewPath in @('/opt/homebrew/bin/brew', '/usr/local/bin/brew')) {
    if (Test-Path -LiteralPath $brewPath) {
      $brewBin = Split-Path -Parent $brewPath
      $currentPath = [Environment]::GetEnvironmentVariable('PATH', 'Process')
      if (-not (($currentPath -split [regex]::Escape([IO.Path]::PathSeparator)) -contains $brewBin)) {
        [Environment]::SetEnvironmentVariable('PATH', "$brewBin$([IO.Path]::PathSeparator)$currentPath", 'Process')
      }
    }
  }
}

function Ensure-CodexWithCcPython {
  param(
    [Parameter(Mandatory = $true)][string]$InstallPlatform,
    [Parameter(Mandatory = $true)][string]$Policy
  )
  $python = Resolve-CodexWithCcPython
  if ($null -ne $python) {
    return $python
  }
  if ($Policy -eq 'Never') {
    throw 'Python 3.9+ is required but was not found, and -BootstrapPython Never was requested.'
  }

  Write-Host 'Python 3.9+ was not found; bootstrapping Python runtime for codex_with_cc.'
  if ($InstallPlatform -eq 'Windows') {
    if ($null -eq (Get-Command winget -ErrorAction SilentlyContinue)) {
      throw 'Python bootstrap failed: winget was not found. Install Python 3.9+ manually or expose winget on PATH.'
    }
    & winget install --id Python.Python.3.14 --exact --silent --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
      throw "Python bootstrap failed: winget install Python.Python.3.14 exited with code $LASTEXITCODE."
    }
  } elseif ($InstallPlatform -eq 'macOS') {
    Add-HomebrewToPath
    if ($null -eq (Get-Command brew -ErrorAction SilentlyContinue)) {
      Write-Host 'Homebrew was not found; installing Homebrew using the official non-interactive installer.'
      $oldNonInteractive = [Environment]::GetEnvironmentVariable('NONINTERACTIVE', 'Process')
      try {
        [Environment]::SetEnvironmentVariable('NONINTERACTIVE', '1', 'Process')
        & /bin/bash -c '$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)'
      } finally {
        [Environment]::SetEnvironmentVariable('NONINTERACTIVE', $oldNonInteractive, 'Process')
      }
      if ($LASTEXITCODE -ne 0) {
        throw "Homebrew bootstrap failed with exit code $LASTEXITCODE."
      }
      Add-HomebrewToPath
    }
    & brew install python
    if ($LASTEXITCODE -ne 0) {
      throw "Python bootstrap failed: brew install python exited with code $LASTEXITCODE."
    }
    Add-HomebrewToPath
  }

  $python = Resolve-CodexWithCcPython
  if ($null -eq $python) {
    throw 'Python bootstrap completed but Python 3.9+ is still not available on PATH.'
  }
  return $python
}

$installPlatform = Resolve-InstallPlatform -Value $Platform
$runtimeScript = Join-Path $PSScriptRoot 'codex_with_cc\scripts\install_codex_with_cc.py'
if (-not (Test-Path -LiteralPath $runtimeScript)) {
  throw "Missing shared Python runtime: $runtimeScript"
}
$python = Ensure-CodexWithCcPython -InstallPlatform $installPlatform -Policy $BootstrapPython

$runtimeArgs = @($python.Prefix + @(
  $runtimeScript,
  '--target-root', $TargetRoot,
  '--platform', $installPlatform
))
if ($SkipAgentEntrypoints) {
  $runtimeArgs += '--skip-agent-entrypoints'
}

& $python.Exe @runtimeArgs
exit $LASTEXITCODE
