param(
    [string]$TaskName = "SteamDiscInsertWatcher"
)

$ErrorActionPreference = 'Stop'

$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$watcherExePath = Join-Path $projectDir 'SteamDiscInsertWatcher.exe'
$watcherPyPath = Join-Path $projectDir 'disc_insert_watcher.py'

if (Test-Path -LiteralPath $watcherExePath) {
    $exec = $watcherExePath
    $args = ''
}
else {
    if (-not (Test-Path -LiteralPath $watcherPyPath)) {
        throw "Watcher executable/script not found. Expected: $watcherExePath or $watcherPyPath"
    }

    $python = (Get-Command py -ErrorAction SilentlyContinue)
    if ($python) {
        $exec = 'py'
        $args = "-3 `"$watcherPyPath`""
    }
    else {
        $python = (Get-Command pythonw -ErrorAction SilentlyContinue)
        if (-not $python) {
            $python = (Get-Command python -ErrorAction SilentlyContinue)
        }

        if (-not $python) {
            throw 'Python was not found in PATH. Install Python 3.10+ and retry.'
        }

        $exec = $python.Source
        $args = "`"$watcherPyPath`""
    }
}

$action = New-ScheduledTaskAction -Execute $exec -Argument $args -WorkingDirectory $projectDir
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -Hidden

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description 'Watches optical disc inserts and launches Steam physical launcher discs.' -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

Write-Host "Installed and started scheduled startup watcher task: $TaskName"
