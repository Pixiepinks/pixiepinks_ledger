# Shared management functions. No passwords, tokens, pairing or printer commands.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$script:TaskName = 'PixiePinks Receipt Connector - Register 1'
$script:Install = 'C:\PixiePinks\ReceiptConnector-190'
$script:Device = 'f18b74ce-cf1f-440f-973f-579640ff46e6'
$script:Marker = 'PixiePinks receipt startup v1; Register1; User; no credentials'

function Get-ReceiptIdentity {
    if ([Environment]::OSVersion.Platform -ne 'Win32NT' -or [Environment]::OSVersion.Version.Build -lt 22000) { throw 'Windows 11 is required.' }
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $local = Get-LocalUser -Name 'User' -ErrorAction Stop
    if ($id.IsSystem -or $id.User.Value -ne $local.SID.Value) { throw 'Sign in as the existing local Windows account User. Do not run as SYSTEM or another administrator.' }
    return [pscustomobject]@{Name=$id.Name; Sid=$id.User.Value}
}

function Get-ReceiptPaths {
    $root = Join-Path $env:LOCALAPPDATA 'PixiePinks\PrintAgent'
    return [pscustomobject]@{Install=$script:Install; Python=(Join-Path $script:Install '.venv\Scripts\python.exe'); PythonW=(Join-Path $script:Install '.venv\Scripts\pythonw.exe'); Root=$root; Control=(Join-Path $root 'autostart')}
}

function Assert-ReceiptInstallation($Paths) {
    foreach ($p in @($Paths.Install,$Paths.Python,$Paths.PythonW,(Join-Path $Paths.Install 'windows_print_agent\runtime.py'),(Join-Path $Paths.Root 'credentials.dpapi'),(Join-Path $Paths.Root 'attestation.dpapi'),(Join-Path $Paths.Root 'actions.sqlite'))) {
        if (!(Test-Path -LiteralPath $p)) { throw 'Existing installation, virtual environment, credentials, attestation and journal are all required. No files were initialized.' }
        if ((Get-Item -LiteralPath $p -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Reparse-point startup paths are not supported. Administrator review required.' }
    }
    # Check parent directories too: a writable junction would redirect an unattended executable.
    foreach ($p in @('C:\PixiePinks',(Join-Path $Paths.Install '.venv'),(Join-Path $Paths.Install '.venv\Scripts'),(Join-Path $Paths.Install 'windows_print_agent'),$Paths.Root)) {
        if ((Get-Item -LiteralPath $p -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Reparse-point parent path rejected.' }
    }
    Push-Location -LiteralPath $Paths.Install
    try { $check = & $Paths.Python -B -m windows_print_agent startup-check 2>$null; $checkExit = $LASTEXITCODE } finally { Pop-Location }
    if ($checkExit -ne 0) { throw 'Local startup check failed. Use the existing User account and approved updated Register 1 installation; never re-pair or reset the journal.' }
    try { $value = $check | ConvertFrom-Json } catch { throw 'Invalid sanitized local startup-check response.' }
    if ($value.status -ne 'LOCAL_STARTUP_CHECK_PASSED' -or $value.device_id -ne $script:Device -or $value.windows_user -ne 'User' -or $value.startup_version -ne 1 -or $value.journal_readonly_verified -ne $true -or $value.dependencies_verified -ne $true) { throw 'Register 1 identity/startup support mismatch. Register 2 must remain untouched.' }
}

function Assert-ReceiptAcl($Paths,$Identity) {
    $trusted = @($Identity.Sid,'S-1-5-18','S-1-5-32-544')
    # Refuse dangerous permissions; do not rewrite application/DPAPI/journal ACLs.
    $codePaths = @(Get-ChildItem -LiteralPath (Join-Path $Paths.Install '.venv'),(Join-Path $Paths.Install 'windows_print_agent') -Recurse -Force | ForEach-Object {$_.FullName})
    foreach ($p in @('C:\PixiePinks',$Paths.Install,(Join-Path $Paths.Install '.venv'),(Join-Path $Paths.Install '.venv\Scripts'),$Paths.Python,$Paths.PythonW,(Join-Path $Paths.Install 'windows_print_agent'),(Join-Path $Paths.Install 'windows_print_agent\runtime.py'),$Paths.Root) + $codePaths) {
        if ((Get-Item -LiteralPath $p -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Code/venv reparse point rejected.' }
        $acl = Get-Acl -LiteralPath $p
        if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin $trusted) { throw 'Untrusted startup-path owner; administrator ACL review required.' }
        foreach ($rule in $acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])) {
            $write = [Security.AccessControl.FileSystemRights]::Write -bor [Security.AccessControl.FileSystemRights]::Delete -bor [Security.AccessControl.FileSystemRights]::ChangePermissions -bor [Security.AccessControl.FileSystemRights]::TakeOwnership
            if ($rule.AccessControlType -eq 'Allow' -and $rule.IdentityReference.Value -notin $trusted -and ($rule.FileSystemRights -band $write)) { throw 'Another principal can modify a startup path; administrator ACL review required.' }
        }
    }
}

function Protect-ReceiptControl($Paths,$Identity) {
    foreach ($directory in @((Join-Path $Paths.Root 'logs'))) {
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
        if ((Get-Item -LiteralPath $directory -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Control/log reparse point rejected.' }
        $acl = New-Object Security.AccessControl.DirectorySecurity
        $acl.SetOwner((New-Object Security.Principal.SecurityIdentifier($Identity.Sid)))
        $acl.SetAccessRuleProtection($true,$false)
        foreach ($sid in @($Identity.Sid,'S-1-5-18','S-1-5-32-544')) {
            $rule = New-Object Security.AccessControl.FileSystemAccessRule((New-Object Security.Principal.SecurityIdentifier($sid)),'FullControl','ContainerInherit,ObjectInherit','None','Allow')
            $acl.AddAccessRule($rule)
        }
        Set-Acl -LiteralPath $directory -AclObject $acl
    }
}

function Find-ReceiptProcesses($Paths,$Identity) {
    $executables = @($Paths.Python,$Paths.PythonW)
    foreach ($process in Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'") {
        if ($process.ExecutablePath -in $executables -and $process.CommandLine -match '(?i)(?:^|\s)-m\s+windows_print_agent\s+run(?:\s|$)') {
            $owner = Invoke-CimMethod -InputObject $process -MethodName GetOwnerSid
            if ($owner.ReturnValue -ne 0 -or $owner.Sid -ne $Identity.Sid) { throw 'Connector process owner could not be verified. Stop and investigate; no process was terminated.' }
            [pscustomobject]@{Pid=$process.ProcessId; CorrectUser=$true;StartedAt=$process.CreationDate}
        }
    }
}

function Get-ReceiptTask { Get-ScheduledTask -TaskPath '\' -ErrorAction Stop | Where-Object {$_.TaskName -eq $script:TaskName} }
function Assert-ReceiptTask($Task,$Identity,$Paths) {
    if (!$Task) { return }
    if ($Task.Description -ne $script:Marker -or $Task.Principal.UserId -notin @($Identity.Sid,$Identity.Name)) { throw 'Existing same-name task is not owned by this approved installer/user; do not overwrite it.' }
    if (@($Task.Actions).Count -ne 1 -or $Task.Actions[0].Execute -ne $Paths.PythonW -or $Task.Actions[0].Arguments -ne '-B -m windows_print_agent run --autostart-register1' -or $Task.Actions[0].WorkingDirectory -ne $Paths.Install) { throw 'Existing task action drift detected. Review before any update/removal.' }
}

function Install-ReceiptTask {
    $identity=Get-ReceiptIdentity; $paths=Get-ReceiptPaths
    Assert-ReceiptInstallation $paths; Assert-ReceiptAcl $paths $identity
    $task=Get-ReceiptTask; Assert-ReceiptTask $task $identity $paths
    if (@(Find-ReceiptProcesses $paths $identity).Count -gt 0) { throw 'Stop the manual/scheduled connector cleanly before installing or updating startup. No process was stopped.' }
    Protect-ReceiptControl $paths $identity
    $action=New-ScheduledTaskAction -Execute $paths.PythonW -Argument '-B -m windows_print_agent run --autostart-register1' -WorkingDirectory $paths.Install
    $trigger=New-ScheduledTaskTrigger -AtLogOn -User $identity.Name; $trigger.Delay='PT15S'
    $principal=New-ScheduledTaskPrincipal -UserId $identity.Sid -LogonType Interactive -RunLevel Limited
    $settings=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    # No Start-ScheduledTask: installation does not launch or submit any print.
    Register-ScheduledTask -TaskName $script:TaskName -TaskPath '\' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description $script:Marker -Force | Out-Null
    [pscustomobject]@{status='INSTALLED_NOT_STARTED';task=$script:TaskName;device_id=$script:Device;windows_user=$identity.Name;next_run='NEXT_USER_LOGON_OR_SEPARATELY_APPROVED_START';routine_elevation=$false}
}

function Uninstall-ReceiptTask {
    $identity=Get-ReceiptIdentity; $paths=Get-ReceiptPaths
    Assert-ReceiptInstallation $paths; Assert-ReceiptAcl $paths $identity
    $task=Get-ReceiptTask
    Assert-ReceiptTask $task $identity $paths
    if (@(Find-ReceiptProcesses $paths $identity).Count -gt 0) { throw 'Stop the connector cleanly before uninstalling startup; no process was terminated.' }
    if ($task) { Unregister-ScheduledTask -TaskName $script:TaskName -TaskPath '\' -Confirm:$false }
    # Only the stop fence belongs to startup management, never DPAPI/journal/attestation.
    $stopFile=Join-Path $paths.Root 'stop.request'
    if (Test-Path -LiteralPath $stopFile) { Remove-Item -LiteralPath $stopFile -Force }
    [pscustomobject]@{status='UNINSTALLED';credentials_preserved=$true;journal_preserved=$true;device_id=$script:Device}
}

function Get-ReceiptStatus {
    $identity=Get-ReceiptIdentity; $paths=Get-ReceiptPaths; $task=Get-ReceiptTask
    Assert-ReceiptTask $task $identity $paths
    $processes=@(Find-ReceiptProcesses $paths $identity); $info=$null; $runtime=$null
    if ($task) { $info=Get-ScheduledTaskInfo -TaskName $script:TaskName -TaskPath '\' }
    $state=Join-Path $paths.Root 'runtime-status.json'
    if (Test-Path -LiteralPath $state) { try { $runtime=Get-Content -LiteralPath $state -Raw | ConvertFrom-Json } catch { $runtime=$null } }
    $fresh=$false; $lastConnection=$null; $lastCategory=$null; $category='STATUS_UNAVAILABLE_OR_STALE'
    if ($runtime) {
        try {
            $matched=@($processes | Where-Object {$_.Pid -eq $runtime.pid})
            $updated=[DateTimeOffset]::Parse($runtime.updated_at)
            $age=([DateTimeOffset]::UtcNow - $updated).TotalSeconds
            $fresh=$runtime.device_id -eq $script:Device -and $matched.Count -eq 1 -and $age -ge 0 -and $age -lt 150 -and $runtime.process -eq 'RUNNING' -and $updated -ge ([DateTimeOffset]$matched[0].StartedAt)
            if ($runtime.PSObject.Properties['last_successful_connection']) { $lastConnection=([DateTimeOffset]::Parse($runtime.last_successful_connection)).ToString('o') }
            if ($runtime.error_category -in @('NONE','NETWORK','HTTP_TRANSIENT','SECURITY_OR_CONFIGURATION','LOCAL_REVIEW','STOP_REQUESTED','UNEXPECTED_EXIT','INSTANCE_BUSY','LOGGING_UNAVAILABLE','READINESS_HOLD')) { $lastCategory=$runtime.error_category; if ($fresh) { $category=$runtime.error_category } }
        } catch { $fresh=$false }
    }
    [pscustomobject]@{task_installed=!!$task;task_enabled=if($task){$task.State -ne 'Disabled'}else{$false};last_task_result=if($info){$info.LastTaskResult}else{$null};process_count=$processes.Count;expected_user_verified=($processes.Count -gt 0);device_id=$script:Device;runtime_fresh=!!$fresh;last_successful_connection=$lastConnection;authenticated=!!($fresh -and $runtime.connection -eq 'AUTHENTICATED');printer_ready=!!($fresh -and $runtime.printer_ready);error_category=$category;last_error_category=$lastCategory}
}

function Stop-ReceiptConnector([int]$TimeoutSeconds=120) {
    $identity=Get-ReceiptIdentity; $paths=Get-ReceiptPaths
    Assert-ReceiptInstallation $paths; Assert-ReceiptAcl $paths $identity
    $task=Get-ReceiptTask
    Assert-ReceiptTask $task $identity $paths
    if ($task) { Disable-ScheduledTask -TaskName $script:TaskName -TaskPath '\' | Out-Null }
    $processes=@(Find-ReceiptProcesses $paths $identity)
    if ($processes.Count -gt 0) {
        $stopFile=Join-Path $paths.Root 'stop.request'
        if (!(Test-Path -LiteralPath $stopFile)) {
            $stream=[IO.File]::Open($stopFile,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read)
            try { $bytes=[Text.Encoding]::ASCII.GetBytes('STOP'); $stream.Write($bytes,0,$bytes.Length); $stream.Flush($true) } finally { $stream.Dispose() }
        } elseif ((Get-Item -LiteralPath $stopFile -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Unsafe stop-request path; no file was overwritten.' }
        $deadline=[DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
        while (@(Find-ReceiptProcesses $paths $identity).Count -gt 0) {
            if ([DateTime]::UtcNow -ge $deadline) { throw 'Graceful stop timed out. Task remains disabled. Do not force-kill, replay jobs or reset the journal; review the in-flight action.' }
            Start-Sleep -Milliseconds 500
        }
    }
    [pscustomobject]@{status='STOPPED_TASK_DISABLED';journal_preserved=$true;device_id=$script:Device}
}

function Start-ReceiptConnector {
    $identity=Get-ReceiptIdentity; $paths=Get-ReceiptPaths
    Assert-ReceiptInstallation $paths; Assert-ReceiptAcl $paths $identity
    $task=Get-ReceiptTask; if (!$task) { throw 'Install the reviewed task first.' }; Assert-ReceiptTask $task $identity $paths
    if (@(Find-ReceiptProcesses $paths $identity).Count -gt 0) { return [pscustomobject]@{status='ALREADY_RUNNING_NO_SECOND_START'} }
    $stopFile=Join-Path $paths.Root 'stop.request'
    if (Test-Path -LiteralPath $stopFile) { Remove-Item -LiteralPath $stopFile -Force }
    Enable-ScheduledTask -TaskName $script:TaskName -TaskPath '\' | Out-Null
    Start-ScheduledTask -TaskName $script:TaskName -TaskPath '\'
    [pscustomobject]@{status='START_REQUESTED_NOT_PRINT_VERIFIED';device_id=$script:Device}
}

# Only installer-authored fixed guidance may leave the exception boundary.
function Get-ReceiptFailure($Record) {
    $known=@(
        'Windows 11 is required.',
        'Sign in as the existing local Windows account User. Do not run as SYSTEM or another administrator.',
        'Existing installation, virtual environment, credentials, attestation and journal are all required. No files were initialized.',
        'Reparse-point startup paths are not supported. Administrator review required.',
        'Reparse-point parent path rejected.',
        'Local startup check failed. Use the existing User account and approved updated Register 1 installation; never re-pair or reset the journal.',
        'Invalid sanitized local startup-check response.',
        'Register 1 identity/startup support mismatch. Register 2 must remain untouched.',
        'Code/venv reparse point rejected.',
        'Untrusted startup-path owner; administrator ACL review required.',
        'Another principal can modify a startup path; administrator ACL review required.',
        'Control/log reparse point rejected.',
        'Connector process owner could not be verified. Stop and investigate; no process was terminated.',
        'Existing same-name task is not owned by this approved installer/user; do not overwrite it.',
        'Existing task action drift detected. Review before any update/removal.',
        'Stop the manual/scheduled connector cleanly before installing or updating startup. No process was stopped.',
        'Stop the connector cleanly before uninstalling startup; no process was terminated.',
        'Unsafe stop-request path; no file was overwritten.',
        'Graceful stop timed out. Task remains disabled. Do not force-kill, replay jobs or reset the journal; review the in-flight action.',
        'Install the reviewed task first.'
    )
    foreach ($fixed in $known) { if ($Record.Exception.Message -ceq $fixed) { return $fixed } }
    return 'Windows operation unavailable. Review User account, local preflight, task permissions and status. No sensitive error details are emitted; do not reset protected state.'
}
