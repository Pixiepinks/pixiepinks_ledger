# Approved administrator installation only. No pairing, printer/driver changes or
# immediate start. Startup activation requires a separately approved pilot.
param(
  [Parameter(Mandatory=$true)][string]$PythonPath,
  [Parameter(Mandatory=$true)][string]$ProjectRoot,
  [Parameter(Mandatory=$true)][PSCredential]$ServiceCredential
)
$ErrorActionPreference = 'Stop'
$PythonPath = [IO.Path]::GetFullPath($PythonPath)
$ProjectRoot = [IO.Path]::GetFullPath($ProjectRoot)
if (!(Test-Path -LiteralPath $PythonPath -PathType Leaf)) { throw 'Reviewed Python executable required.' }
if (!(Test-Path -LiteralPath (Join-Path $ProjectRoot 'windows_print_agent\__main__.py') -PathType Leaf)) { throw 'Reviewed connector package required.' }
# Directory ACLs and account printer visibility must be inspected before approval.
$action = New-ScheduledTaskAction -Execute $PythonPath -Argument '-m windows_print_agent run' -WorkingDirectory $ProjectRoot
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
$taskName = 'PixiePinks Receipt Connector'
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) { throw 'Existing startup task found; review it instead of overwriting.' }
try {
  $password = $ServiceCredential.GetNetworkCredential().Password
  Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -User $ServiceCredential.UserName -Password $password -RunLevel Limited | Out-Null
} finally { $password = $null }
Write-Output 'Startup task registered, not started. Review/pilot approval is required before starting or reboot activation.'
