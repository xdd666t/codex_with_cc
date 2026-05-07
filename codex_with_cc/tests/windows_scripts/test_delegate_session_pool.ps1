. (Join-Path $PSScriptRoot '..\..\windows_scripts\_runtime.ps1')
Invoke-CodexWithCcRuntime -PythonScript '..\tests\test_delegate_session_pool.py' -RemainingArgs $args
