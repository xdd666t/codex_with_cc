[CmdletBinding(DefaultParameterSetName = 'Inline')]
param(
  [Parameter(Mandatory = $true, ParameterSetName = 'Inline')]
  [string]$Task,

  [Parameter(Mandatory = $true, ParameterSetName = 'File')]
  [string]$TaskFile,

  [string[]]$Scope = @(),
  [string[]]$Tests = @(),
  [ValidateSet('Implement', 'Fix', 'Review')]
  [string]$Mode = 'Implement',
  [string]$Model = 'sonnet',
  [string]$Name,
  [string]$NamePrefix = 'codex-delegate',
  [Nullable[decimal]]$MaxBudgetUsd,
  [string]$ArtifactRoot,
  [string]$OutputPath,
  [switch]$AllowParallel,
  [ValidateSet('PrimaryReuse', 'PrimaryAnchor', 'ParallelPool')]
  [string]$SessionMode = 'PrimaryReuse',
  [string]$SessionKey,
  [int]$SessionLeaseTimeoutSeconds = 21600,
  [int]$SessionLeaseWaitSeconds = 120,
  [switch]$ResetPrimarySession,
  [switch]$ResetParallelPool,
  [int]$LockTimeoutSeconds = 120,
  [int]$LockPollMilliseconds = 500,
  [ValidateRange(0, 100)]
  [int]$MaxRetryCount = 5,
  [switch]$BypassPermissions,
  [switch]$DryRun
)

function Resolve-CodexWithCcChoice {
  param(
    [Parameter(Mandatory = $true)][string]$Value,
    [Parameter(Mandatory = $true)][string[]]$Choices
  )

  foreach ($choice in $Choices) {
    if ([string]::Equals($Value, $choice, [System.StringComparison]::OrdinalIgnoreCase)) {
      return $choice
    }
  }

  return $Value
}

. (Join-Path $PSScriptRoot '_runtime.ps1')

$remainingArgs = @()
if ($PSCmdlet.ParameterSetName -eq 'File') {
  $remainingArgs += @('-TaskFile', $TaskFile)
} else {
  $remainingArgs += @('-Task', $Task)
}
foreach ($item in @($Scope)) {
  $remainingArgs += @('-Scope', $item)
}
foreach ($item in @($Tests)) {
  $remainingArgs += @('-Tests', $item)
}
$remainingArgs += @('-Mode', (Resolve-CodexWithCcChoice -Value $Mode -Choices @('Implement', 'Fix', 'Review')))
$remainingArgs += @('-Model', $Model)
if (-not [string]::IsNullOrWhiteSpace($Name)) {
  $remainingArgs += @('-Name', $Name)
}
$remainingArgs += @('-NamePrefix', $NamePrefix)
if ($null -ne $MaxBudgetUsd) {
  $remainingArgs += @('-MaxBudgetUsd', $MaxBudgetUsd.ToString([Globalization.CultureInfo]::InvariantCulture))
}
if (-not [string]::IsNullOrWhiteSpace($ArtifactRoot)) {
  $remainingArgs += @('-ArtifactRoot', $ArtifactRoot)
}
if (-not [string]::IsNullOrWhiteSpace($OutputPath)) {
  $remainingArgs += @('-OutputPath', $OutputPath)
}
if ($AllowParallel.IsPresent) {
  $remainingArgs += '-AllowParallel'
}
$remainingArgs += @('-SessionMode', (Resolve-CodexWithCcChoice -Value $SessionMode -Choices @('PrimaryReuse', 'PrimaryAnchor', 'ParallelPool')))
if (-not [string]::IsNullOrWhiteSpace($SessionKey)) {
  $remainingArgs += @('-SessionKey', $SessionKey)
}
$remainingArgs += @('-SessionLeaseTimeoutSeconds', ([string]$SessionLeaseTimeoutSeconds))
$remainingArgs += @('-SessionLeaseWaitSeconds', ([string]$SessionLeaseWaitSeconds))
if ($ResetPrimarySession.IsPresent) {
  $remainingArgs += '-ResetPrimarySession'
}
if ($ResetParallelPool.IsPresent) {
  $remainingArgs += '-ResetParallelPool'
}
$remainingArgs += @('-LockTimeoutSeconds', ([string]$LockTimeoutSeconds))
$remainingArgs += @('-LockPollMilliseconds', ([string]$LockPollMilliseconds))
$remainingArgs += @('-MaxRetryCount', ([string]$MaxRetryCount))
if ($BypassPermissions.IsPresent) {
  $remainingArgs += '-BypassPermissions'
}
if ($DryRun.IsPresent) {
  $remainingArgs += '-DryRun'
}

Invoke-CodexWithCcRuntime -PythonScript 'delegate_to_claude.py' -RemainingArgs $remainingArgs
