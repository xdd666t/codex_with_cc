param(
  [Parameter(Mandatory = $true)]
  [string]$RunId,
  [string]$ArtifactRoot
)

. (Join-Path $PSScriptRoot '_runtime.ps1')

$remainingArgs = @('-RunId', $RunId)
if (-not [string]::IsNullOrWhiteSpace($ArtifactRoot)) {
  $remainingArgs += @('-ArtifactRoot', $ArtifactRoot)
}

Invoke-CodexWithCcRuntime -PythonScript 'verify_delegate_artifacts.py' -RemainingArgs $remainingArgs
