. (Join-Path $PSScriptRoot '_runtime.ps1')
Invoke-CodexWithCcRuntime -PythonScript 'delegate_to_opencode.py' -RemainingArgs $args
