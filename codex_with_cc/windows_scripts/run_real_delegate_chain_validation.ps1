param(
  [string]$ValidationRoot,
  [string]$Name,
  [string]$SessionKey
)

. (Join-Path $PSScriptRoot '_runtime.ps1')

$remainingArgs = @()
if (-not [string]::IsNullOrWhiteSpace($ValidationRoot)) {
  $remainingArgs += @('-ValidationRoot', $ValidationRoot)
}
if (-not [string]::IsNullOrWhiteSpace($Name)) {
  $remainingArgs += @('-Name', $Name)
}
if (-not [string]::IsNullOrWhiteSpace($SessionKey)) {
  $remainingArgs += @('-SessionKey', $SessionKey)
}

Invoke-CodexWithCcRuntime -PythonScript 'run_real_delegate_chain_validation.py' -RemainingArgs $remainingArgs
