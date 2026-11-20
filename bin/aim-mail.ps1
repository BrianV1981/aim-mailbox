$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ($env:PYTHONPATH) {
    $env:PYTHONPATH = "$RepoRoot\src;$env:PYTHONPATH"
} else {
    $env:PYTHONPATH = "$RepoRoot\src"
}

if (Get-Command python -ErrorAction SilentlyContinue) {
    & python -m aim_mailbox @args
    exit $LASTEXITCODE
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 -m aim_mailbox @args
    exit $LASTEXITCODE
} else {
    [Console]::Error.WriteLine("[ERROR] Python 3.10+ is required on PATH.")
    exit 1
}
