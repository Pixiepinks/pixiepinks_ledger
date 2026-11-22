# Execute the actual management functions with isolated Scheduler/OS mocks.
. "$PSScriptRoot/../../scripts/windows/receipt_autostart.ps1"
$script:NativePreflight=${function:Assert-ReceiptInstallation}
function Assert($Condition,$Message) { if (!$Condition) { throw $Message } }
$script:stored=$null; $script:running=@(); $script:registerCount=0; $script:starts=0; $script:stops=0
$script:identity=[pscustomobject]@{Name='PC\User';Sid='S-1-5-21-1'}
$script:paths=[pscustomobject]@{Install='C:\PixiePinks\ReceiptConnector-190';Python='C:\PixiePinks\ReceiptConnector-190\.venv\Scripts\python.exe';PythonW='C:\PixiePinks\ReceiptConnector-190\.venv\Scripts\pythonw.exe';Root=$PSScriptRoot;Control=$PSScriptRoot}
function Get-ReceiptIdentity { if ($script:identity.Name -ne 'PC\User') {throw 'Wrong user'}; $script:identity }
function Get-ReceiptPaths { $script:paths }
function Assert-ReceiptInstallation($Paths) { if ($Paths.Install -ne 'C:\PixiePinks\ReceiptConnector-190' -or !$Paths.Python -or !$Paths.PythonW) {throw 'Missing installation/venv'} }
function Assert-ReceiptAcl($Paths,$Identity) {}
function Protect-ReceiptControl($Paths,$Identity) {}
function Find-ReceiptProcesses($Paths,$Identity) { $script:running }
function Get-ReceiptTask { $script:stored }
function New-ScheduledTaskAction($Execute,$Argument,$WorkingDirectory) { [pscustomobject]@{Execute=$Execute;Arguments=$Argument;WorkingDirectory=$WorkingDirectory} }
function New-ScheduledTaskTrigger([switch]$AtLogOn,$User) { Assert $AtLogOn 'Must be login only'; [pscustomobject]@{User=$User;Delay=''} }
function New-ScheduledTaskPrincipal($UserId,$LogonType,$RunLevel) { [pscustomobject]@{UserId=$UserId;LogonType=$LogonType;RunLevel=$RunLevel} }
function New-ScheduledTaskSettingsSet($MultipleInstances,$ExecutionTimeLimit,$RestartCount,$RestartInterval,[switch]$StartWhenAvailable,[switch]$AllowStartIfOnBatteries,[switch]$DontStopIfGoingOnBatteries) { [pscustomobject]@{MultipleInstances=$MultipleInstances;ExecutionTimeLimit=$ExecutionTimeLimit;RestartCount=$RestartCount;RestartInterval=$RestartInterval} }
function Register-ScheduledTask($TaskName,$TaskPath,$Action,$Trigger,$Principal,$Settings,$Description,[switch]$Force) { $script:registerCount++;$script:stored=[pscustomobject]@{TaskName=$TaskName;Actions=@($Action);Triggers=@($Trigger);Principal=$Principal;Settings=$Settings;Description=$Description;State='Ready'} }
function Unregister-ScheduledTask($TaskName,$TaskPath,[switch]$Confirm) { $script:stored=$null }
function Get-ScheduledTaskInfo($TaskName,$TaskPath) { [pscustomobject]@{LastTaskResult=0} }
function Enable-ScheduledTask($TaskName,$TaskPath) { $script:stored.State='Ready' }
function Disable-ScheduledTask($TaskName,$TaskPath) { $script:stored.State='Disabled' }
function Start-ScheduledTask($TaskName,$TaskPath) { $script:starts++ }
function Throws($Block) { $caught=$false;try {& $Block | Out-Null}catch {$caught=$true};Assert $caught 'Expected fail-closed rejection' }

$result=Install-ReceiptTask
Assert ($result.status -eq 'INSTALLED_NOT_STARTED' -and $script:starts -eq 0) 'Install must never launch'
Assert ($script:stored.Principal.LogonType -eq 'Interactive' -and $script:stored.Principal.RunLevel -eq 'Limited') 'Least privilege'
Assert ($script:stored.Triggers[0].User -eq 'PC\User' -and $script:stored.Triggers[0].Delay -eq 'PT15S') 'User logon trigger'
Assert ($script:stored.Settings.MultipleInstances -eq 'IgnoreNew' -and $script:stored.Settings.RestartCount -eq 3) 'Bounded restart/single instance'
Assert ($script:stored.Actions[0].Execute -eq $script:paths.PythonW -and $script:stored.Actions[0].WorkingDirectory -eq $script:paths.Install) 'Correct executable/cwd'
Assert ($script:stored.Actions[0].Arguments -eq '-B -m windows_print_agent run --autostart-register1') 'No credentials in task'
Install-ReceiptTask | Out-Null;Assert ($script:registerCount -eq 2) 'Idempotent update same task'
Start-ReceiptConnector | Out-Null;Assert ($script:starts -eq 1) 'Explicit start'
$script:running=@([pscustomobject]@{Pid=123;CorrectUser=$true});Start-ReceiptConnector | Out-Null;Assert ($script:starts -eq 1) 'No second launch'
Throws {Install-ReceiptTask};Throws {Uninstall-ReceiptTask};$script:running=@()
Stop-ReceiptConnector | Out-Null;Assert ($script:stored.State -eq 'Disabled') 'Stop disables task without kill'
Uninstall-ReceiptTask | Out-Null;Assert ($null -eq $script:stored) 'Uninstall only task'
Install-ReceiptTask | Out-Null;Assert ($null -ne $script:stored) 'Reinstall'
$script:identity.Name='PC\Administrator';Throws {Install-ReceiptTask};$script:identity.Name='PC\User'
$old=$script:paths.Python;$script:paths.Python='';Throws {Install-ReceiptTask};$script:paths.Python=$old
$old=$script:paths.Install;$script:paths.Install='C:\Wrong';Throws {Install-ReceiptTask};$script:paths.Install=$old
$old=$script:stored.Principal.UserId;$script:stored.Principal.UserId='SYSTEM';Throws {Install-ReceiptTask};$script:stored.Principal.UserId=$old
$old=$script:stored.Actions[0].Arguments;$script:stored.Actions[0].Arguments='-m other run';Throws {Install-ReceiptTask};$script:stored.Actions[0].Arguments=$old
$status=Get-ReceiptStatus;Assert ($status.task_installed -and !$status.authenticated -and !$status.printer_ready) 'Process/task is not print readiness'
try { throw 'PRIVATE_TOKEN_PDF_CUSTOMER_DATA' } catch { Assert ((Get-ReceiptFailure $_) -notmatch 'PRIVATE_TOKEN_PDF_CUSTOMER_DATA') 'No raw exception disclosure' }
# Parse all entrypoint scripts without executing an installer on this host.
foreach ($file in Get-ChildItem "$PSScriptRoot/../../scripts/windows/*.ps1") {
    $tokens=$null;$parseErrors=$null
    [Management.Automation.Language.Parser]::ParseFile($file.FullName,[ref]$tokens,[ref]$parseErrors) | Out-Null
    Assert ($parseErrors.Count -eq 0) 'PowerShell entrypoint parser error'
}
# Exercise actual sanitized status/freshness logic with isolated status files.
$scratch=Join-Path ([IO.Path]::GetTempPath()) ('pixie-startup-test-'+[Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $scratch | Out-Null
$script:paths.Root=$scratch
try {
    $script:running=@([pscustomobject]@{Pid=123;CorrectUser=$true;StartedAt=[DateTimeOffset]::UtcNow.AddMinutes(-1)})
    $state=[pscustomobject]@{pid=123;device_id=$script:Device;process='RUNNING';updated_at=[DateTimeOffset]::UtcNow.ToString('o');connection='AUTHENTICATED';printer_ready=$true;error_category='NONE';last_successful_connection=[DateTimeOffset]::UtcNow.ToString('o')}
    $state | ConvertTo-Json | Set-Content (Join-Path $scratch 'runtime-status.json')
    $status=Get-ReceiptStatus;Assert ($status.runtime_fresh -and $status.authenticated -and $status.printer_ready) 'Current identity/freshness status'
    $state.pid=999;$state | ConvertTo-Json | Set-Content (Join-Path $scratch 'runtime-status.json')
    $status=Get-ReceiptStatus;Assert (!$status.runtime_fresh -and !$status.authenticated -and !$status.printer_ready) 'Stale PID cannot claim readiness'
    $state.pid=123;$state.error_category='PRIVATE_SECRET';$state.last_successful_connection='PRIVATE_SECRET';$state | ConvertTo-Json | Set-Content (Join-Path $scratch 'runtime-status.json')
    Assert ((Get-ReceiptStatus | ConvertTo-Json) -notmatch 'PRIVATE_SECRET') 'Corrupt status must not disclose content'
    Throws {Stop-ReceiptConnector -TimeoutSeconds 0}
    Assert ($script:stored.State -eq 'Disabled' -and (Test-Path (Join-Path $scratch 'stop.request'))) 'Timeout disables future starts and retains stop fence'
    $script:running=@();Start-ReceiptConnector | Out-Null
    Assert (!(Test-Path (Join-Path $scratch 'stop.request'))) 'Only explicit start clears stop fence'
    Uninstall-ReceiptTask | Out-Null
} finally { Remove-Item -LiteralPath $scratch -Recurse -Force }

# Exercise real preflight missing-file/reparse branches before any Python invocation.
function Native-Missing-Preflight($Missing,$Reparse) {
    $drive=Join-Path ([IO.Path]::GetTempPath()) ('pixie-drive-'+[Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $drive | Out-Null
    $createdDrive=$false
    if (!(Get-PSDrive -Name C -ErrorAction SilentlyContinue)) { New-PSDrive -Name C -PSProvider FileSystem -Root $drive | Out-Null; $createdDrive=$true }
    try {
        function Test-Path($LiteralPath) { return $LiteralPath -ne $Missing }
        function Get-Item($LiteralPath,[switch]$Force) { [pscustomobject]@{Attributes=if($LiteralPath -eq $Reparse){[IO.FileAttributes]::ReparsePoint}else{[IO.FileAttributes]::Normal}} }
        $caught=$false
        try { & $script:NativePreflight $script:paths } catch {
            $caught=$true
            Assert ($_.Exception.Message -in @('Existing installation, virtual environment, credentials, attestation and journal are all required. No files were initialized.','Reparse-point startup paths are not supported. Administrator review required.')) 'Real preflight rejected wrong stage'
        }
        Assert $caught 'Native preflight must fail before starting Python'
    } finally { if ($createdDrive) { Remove-PSDrive -Name C };Remove-Item -LiteralPath $drive -Recurse -Force }
}
Native-Missing-Preflight $script:paths.Python ''
Native-Missing-Preflight (Join-Path $script:paths.Root 'actions.sqlite') ''
Native-Missing-Preflight '' $script:paths.PythonW

Write-Output 'MOCK_SCHEDULER_TESTS_PASSED'
