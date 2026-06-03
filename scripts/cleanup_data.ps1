param(
    [int]$Days = 3,
    [int]$DebugDays = 3,
    [switch]$AllMessages,
    [switch]$ClearStates,
    [switch]$SkipDebug,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$OutputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

Set-Location (Split-Path -Parent $PSScriptRoot)

$CleanupArgs = @("cleanup", "--days", "$Days", "--debug-days", "$DebugDays")
if ($AllMessages) {
    $CleanupArgs += "--all-messages"
}
if ($ClearStates) {
    $CleanupArgs += "--clear-states"
}
if ($SkipDebug) {
    $CleanupArgs += "--skip-debug"
}
if ($DryRun) {
    $CleanupArgs += "--dry-run"
}

.\.venv\Scripts\python -m app @CleanupArgs
