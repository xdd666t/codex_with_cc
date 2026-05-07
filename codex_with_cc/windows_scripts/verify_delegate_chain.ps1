param(
  [Parameter(Mandatory = $true)][string]$ArtifactRoot,
  [Parameter(Mandatory = $true)][string]$SessionKey,
  [Parameter(Mandatory = $true)][string]$AnchorRunId,
  [Parameter(Mandatory = $true)][string[]]$ParallelRunIds,
  [Parameter(Mandatory = $true)][string[]]$ReuseRunIds
)

. (Join-Path $PSScriptRoot '_runtime.ps1')

$remainingArgs = @(
  '-ArtifactRoot', $ArtifactRoot,
  '-SessionKey', $SessionKey,
  '-AnchorRunId', $AnchorRunId,
  '-ParallelRunIds'
)
$remainingArgs += @($ParallelRunIds)
$remainingArgs += '-ReuseRunIds'
$remainingArgs += @($ReuseRunIds)

Invoke-CodexWithCcRuntime -PythonScript 'verify_delegate_chain.py' -RemainingArgs $remainingArgs
