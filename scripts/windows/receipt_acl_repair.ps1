# Phase 24A: pure planning + narrow Windows adapters. No application imports/task API.
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$script:AclParent='C:\PixiePinks'
$script:AclInstall='C:\PixiePinks\ReceiptConnector-190'
$script:AclDevice='f18b74ce-cf1f-440f-973f-579640ff46e6'
$script:AclComputer='DESKTOP-ADTR67Q'
$script:AclWriteMask=[long]852310 # Write, Delete, DeleteChild, WriteDac, WriteOwner.

function Expand-ReceiptMask([long]$Mask) {
    $v=$Mask -band [long]4294967295
    foreach ($mapping in @(@([long]268435456,[long]2032127),@([long]1073741824,[long]1179926),@([long]2147483648,[long]1179785),@([long]536870912,[long]1179808))) {
        if ($v -band $mapping[0]) { $v=($v -band (-bnot $mapping[0])) -bor $mapping[1] }
    }
    return $v
}
function Get-ReceiptTime($Value) {
    if($Value -is [DateTime] -or $Value -is [DateTimeOffset]){return ([DateTimeOffset]$Value).ToUniversalTime()}
    return [DateTimeOffset]::Parse([string]$Value,[Globalization.CultureInfo]::InvariantCulture).ToUniversalTime()
}
function Get-ReceiptDigest($Value) {
    $canonical=$Value
    if($Value -is [pscustomobject] -and $Value.PSObject.Properties['created_at']){
        $canonical=[ordered]@{}
        foreach($property in $Value.PSObject.Properties){$canonical[$property.Name]=if($property.Name -eq 'created_at'){(Get-ReceiptTime $property.Value).ToString('o')}else{$property.Value}}
    }
    $json=ConvertTo-Json -InputObject $canonical -Depth 100 -Compress
    $hash=[Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($json)))).Replace('-','').ToLowerInvariant() } finally { $hash.Dispose() }
}
function Test-ReceiptWithin([string]$Path,[string]$Root) { return $Path.Equals($Root,[StringComparison]::OrdinalIgnoreCase) -or $Path.StartsWith($Root.TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase) }
function Get-ReceiptAclIdentity {
    if ([Environment]::OSVersion.Platform -ne 'Win32NT' -or [Environment]::OSVersion.Version.Build -lt 22000 -or [Environment]::MachineName -ine $script:AclComputer) { throw 'P24A_WRONG_WINDOWS_TARGET' }
    $current=[Security.Principal.WindowsIdentity]::GetCurrent();$user=Get-LocalUser -Name User
    if ($current.User.Value -ne $user.SID.Value -or $current.Name -ine 'DESKTOP-ADTR67Q\User') { throw 'P24A_WRONG_USER' }
    $principal=New-Object Security.Principal.WindowsPrincipal($current)
    $osOwner=(New-Object Security.Principal.NTAccount('NT SERVICE','TrustedInstaller')).Translate([Security.Principal.SecurityIdentifier]).Value
    [pscustomobject][ordered]@{computer=[Environment]::MachineName;account=$current.Name;sid=$current.User.Value;elevated=$principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator);os_owner=$osOwner}
}
function Get-ReceiptAncestors([string]$Path) {
    $item=New-Object IO.DirectoryInfo($Path)
    while ($null -ne $item) { $item.FullName; $item=$item.Parent }
}
function Get-ReceiptSafeTree([string]$Root) {
    $stack=New-Object 'Collections.Generic.Stack[string]';$stack.Push($Root)
    while ($stack.Count) {
        $path=$stack.Pop();$item=Get-Item -LiteralPath $path -Force
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'P24A_REPARSE_POINT_REQUIRES_REVIEW' }
        $path=$item.FullName
        $path
        if ($item.PSIsContainer) { foreach ($child in Get-ChildItem -LiteralPath $path -Force) { $stack.Push($child.FullName) } }
    }
}
function Read-ReceiptAcl([string]$Path,[bool]$Repairable,[bool]$Anchor,[bool]$StrictChecker=$false) {
    $item=Get-Item -LiteralPath $Path -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'P24A_REPARSE_POINT_REQUIRES_REVIEW' }
    # A hard-linked file shares its DACL with links potentially outside our scope.
    if($Repairable -and !$item.PSIsContainer){
        if(!$item.PSObject.Properties['LinkType']){throw 'P24A_LINK_INFORMATION_UNAVAILABLE'}
        if($item.LinkType){throw 'P24A_HARDLINK_REQUIRES_REVIEW'}
    }
    $acl=Get-Acl -LiteralPath $Path
    $raw=New-Object Security.AccessControl.RawSecurityDescriptor($acl.GetSecurityDescriptorBinaryForm(),0)
    if ($null -eq $raw.DiscretionaryAcl) { throw 'P24A_NULL_DACL_REQUIRES_REVIEW' }
    foreach ($ace in $raw.DiscretionaryAcl) { if ($ace.AceType -notin @('AccessAllowed','AccessDenied')) { throw 'P24A_NONSTANDARD_ACE_REQUIRES_REVIEW' } }
    $rules=@(foreach ($rule in $acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])) {
        [pscustomobject][ordered]@{sid=$rule.IdentityReference.Value;type=[string]$rule.AccessControlType;mask=(Expand-ReceiptMask ([long]$rule.FileSystemRights));inherit=[int]$rule.InheritanceFlags;propagate=[int]$rule.PropagationFlags;inherited=[bool]$rule.IsInherited}
    })
    $section=[Security.AccessControl.AccessControlSections]::Access -bor [Security.AccessControl.AccessControlSections]::Owner -bor [Security.AccessControl.AccessControlSections]::Group
    [pscustomobject][ordered]@{path=$Path;repairable=$Repairable;anchor=$Anchor;strict_checker=$StrictChecker;directory=[bool]$item.PSIsContainer;owner=$acl.GetOwner([Security.Principal.SecurityIdentifier]).Value;protected=[bool]$acl.AreAccessRulesProtected;sddl=$acl.GetSecurityDescriptorSddlForm($section);rules=$rules;content_hash=if($item.PSIsContainer){$null}else{(Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()}}
}
function Test-ReceiptDeviceBinding([string]$Root) {
    # Keep decrypted JSON entirely inside .NET: never bind token-bearing plaintext
    # to ConvertFrom-Json/cmdlet parameters that module logging could capture.
    Add-Type -AssemblyName System.Security
    Add-Type -AssemblyName System.Runtime.Serialization
    if (!('PixiePinksAcl.DeviceBindingReader' -as [type])) {
        $references=@([Security.Cryptography.ProtectedData].Assembly.Location,[Runtime.Serialization.DataContractAttribute].Assembly.Location,[Runtime.Serialization.Json.DataContractJsonSerializer].Assembly.Location,[Xml.XmlReader].Assembly.Location,[Xml.XmlDictionaryReader].Assembly.Location) | Select-Object -Unique
        if($PSVersionTable.PSEdition -eq 'Core'){$references+=@([Reflection.Assembly]::Load('System.Xml.ReaderWriter').Location)}
        Add-Type -ReferencedAssemblies $references -TypeDefinition @'
using System;
using System.IO;
using System.Security.Cryptography;
using System.Runtime.Serialization;
using System.Runtime.Serialization.Json;
namespace PixiePinksAcl {
    [DataContract] public class DeviceOnly {
        [DataMember(Name="device_id", IsRequired=true)] public string Device;
    }
    public static class DeviceBindingReader {
        public static bool Matches(string path, string expected) {
            byte[] clear = null;
            try {
                clear = ProtectedData.Unprotect(File.ReadAllBytes(path), null, DataProtectionScope.CurrentUser);
                using (var input = new MemoryStream(clear, false)) {
                    var value = (DeviceOnly)new DataContractJsonSerializer(typeof(DeviceOnly)).ReadObject(input);
                    return value != null && String.Equals(value.Device, expected, StringComparison.Ordinal);
                }
            } catch { return false; }
            finally { if (clear != null) Array.Clear(clear, 0, clear.Length); }
        }
    }
}
'@
    }
    foreach ($name in @('credentials.dpapi','attestation.dpapi')) {
        if (![PixiePinksAcl.DeviceBindingReader]::Matches((Join-Path $Root $name),$script:AclDevice)) { throw 'P24A_DEVICE_BINDING_MISMATCH' }
    }
    return $true
}
function Assert-ReceiptPrivateStaging($Identity) {
    if(Test-ReceiptWithin $PSScriptRoot $script:AclParent){throw 'P24A_PRIVATE_REVIEWED_STAGING_REQUIRED'}
    $trusted=@($Identity.sid,'S-1-5-18','S-1-5-32-544',$Identity.os_owner)
    foreach($path in @($PSCommandPath,(Join-Path $PSScriptRoot 'repair_receipt_startup_acl.ps1'))+@(Get-ReceiptAncestors $PSScriptRoot)){
        $entry=Read-ReceiptAcl $path $false $false
        if($entry.owner -notin $trusted){throw 'P24A_UNTRUSTED_REPAIR_TOOL_OWNER'}
        foreach($rule in $entry.rules){
            if($rule.type -eq 'Deny'){throw 'P24A_REPAIR_TOOL_ACL_REQUIRES_REVIEW'}
            $danger=$rule.mask -band $script:AclWriteMask
            $createOnly=$entry.directory -and (($danger -band (-bnot [long]6)) -eq 0)
            if($rule.type -eq 'Allow' -and $rule.sid -notin $trusted -and !($rule.propagate -band 2) -and $danger -and !$createOnly){throw 'P24A_UNSAFE_REPAIR_TOOL_PATH'}
        }
    }
}
function Get-ReceiptInventory {
    $identity=Get-ReceiptAclIdentity
    Assert-ReceiptPrivateStaging $identity
    foreach($relative in @('.venv\Scripts\python.exe','.venv\Scripts\pythonw.exe','.venv\pyvenv.cfg','windows_print_agent\__main__.py','windows_print_agent\runtime.py','scripts\windows\receipt_autostart.ps1','scripts\windows\install_receipt_autostart.ps1','scripts\windows\start_receipt_connector.ps1')){if(!(Test-Path -LiteralPath (Join-Path $script:AclInstall $relative) -PathType Leaf)){throw 'P24A_REQUIRED_STARTUP_FILE_MISSING'}}
    $task=@(Get-ScheduledTask -TaskPath '\' -ErrorAction Stop | Where-Object {$_.TaskName -eq 'PixiePinks Receipt Connector - Register 1'})
    if($task.Count -and $task[0].State -ne 'Disabled'){throw 'P24A_ENABLED_TASK_REQUIRES_SEPARATE_STOP_APPROVAL'}
    $state=Join-Path $env:LOCALAPPDATA 'PixiePinks\PrintAgent'
    $cfg=Join-Path $script:AclInstall '.venv\pyvenv.cfg'
    $home=@(Get-Content -LiteralPath $cfg -Encoding UTF8 | Where-Object {$_ -match '^home\s*='} | ForEach-Object {($_ -split '=',2)[1].Trim()})
    if ($home.Count -ne 1 -or $home[0] -notmatch '^[A-Za-z]:\\' -or !(Test-Path -LiteralPath $home[0] -PathType Container)) { throw 'P24A_BASE_PYTHON_REQUIRES_REVIEW' }
    $home[0]=[IO.Path]::GetFullPath($home[0])
    if (@(Get-Content -LiteralPath $cfg -Encoding UTF8 | Where-Object {$_ -match '^include-system-site-packages\s*=\s*true'}).Count) { throw 'P24A_SYSTEM_SITE_PACKAGES_REQUIRES_REVIEW' }
    foreach ($name in @('PYTHONPATH','PYTHONHOME')) { foreach ($source in @('Process','User','Machine')) { if ([Environment]::GetEnvironmentVariable($name,$source)) { throw 'P24A_PYTHON_ENV_OVERRIDE_REQUIRES_REVIEW' } } }
    foreach ($process in Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'") {
        if ($process.ExecutablePath -and (Test-ReceiptWithin $process.ExecutablePath $script:AclInstall) -and $process.CommandLine -match '(?i)-m\s+windows_print_agent\s+run(?:\s|$)') { throw 'P24A_CONNECTOR_RUNNING_STOP_REQUIRES_SEPARATE_APPROVAL' }
    }
    $bound=Test-ReceiptDeviceBinding $state
    $paths=@{}; $selected=@(Get-ReceiptSafeTree $script:AclInstall)
    foreach ($p in $selected) {
        if ([IO.Path]::GetFileName($p) -match '^(credentials\.dpapi|attestation\.dpapi|actions\.sqlite(?:-wal|-shm)?)$') { throw 'P24A_PROTECTED_STATE_INSIDE_CODE_TREE' }
        if($paths.ContainsKey($p)){throw 'P24A_CASE_COLLISION_REQUIRES_REVIEW'}
        $paths[$p]=@($true,($p -ieq $script:AclInstall))
    }
    $paths[$script:AclParent]=@($true,$true)
    foreach ($p in @(Get-ReceiptSafeTree $home[0])+@(Get-ReceiptAncestors $home[0])+@(Get-ReceiptAncestors $state)+@(Get-ReceiptAncestors $PSScriptRoot)+@(Get-ReceiptAncestors $script:AclParent)) { if (!$paths.ContainsKey($p)) { $paths[$p]=@($false,$false) } }
    foreach ($p in @((Join-Path $state 'credentials.dpapi'),(Join-Path $state 'attestation.dpapi'),(Join-Path $state 'actions.sqlite'),$PSCommandPath,(Join-Path $PSScriptRoot 'repair_receipt_startup_acl.ps1'))) { if (!$paths.ContainsKey($p)) { $paths[$p]=@($false,$false) } }
    foreach ($suffix in @('-wal','-shm')) { $p=Join-Path $state ('actions.sqlite'+$suffix);if(Test-Path -LiteralPath $p){$paths[$p]=@($false,$false)} }
    $ordered=[string[]]@($paths.Keys);[Array]::Sort($ordered,[StringComparer]::OrdinalIgnoreCase)
    $entries=@(foreach ($p in $ordered) { Read-ReceiptAcl $p $paths[$p][0] $paths[$p][1] ($p -ieq $state) })
    $siblings=@(Get-ChildItem -LiteralPath $script:AclParent -Force | Where-Object {$_.FullName -ine $script:AclInstall} | ForEach-Object {$_.FullName})
    [pscustomobject][ordered]@{identity=[pscustomobject][ordered]@{computer=$identity.computer;account=$identity.account;sid=$identity.sid;os_owner=$identity.os_owner};device_id=$script:AclDevice;device_binding_verified=$bound;install=$script:AclInstall;parent=$script:AclParent;state_root=$state;base_python_home=$home[0];tool_outside_scope=(!(Test-ReceiptWithin $PSScriptRoot $script:AclParent));siblings=$siblings;entries=$entries}
}
function New-ReceiptRepairPlan($Inventory,[string]$Now=([DateTimeOffset]::UtcNow.ToString('o'))) {
    $trusted=@($Inventory.identity.sid,'S-1-5-18','S-1-5-32-544');$issues=New-Object Collections.Generic.List[object];$changes=New-Object Collections.Generic.List[object];$blocks=New-Object Collections.Generic.List[string]
    if (!$Inventory.device_binding_verified) {$blocks.Add('DEVICE_BINDING_NOT_VERIFIED')}
    if (!$Inventory.tool_outside_scope) {$blocks.Add('RUN_REVIEWED_TOOL_FROM_PRIVATE_STAGING_OUTSIDE_PIXIEPINKS')}
    foreach ($entry in $Inventory.entries) {
        $owners=@($trusted);if(!$entry.repairable -and !$entry.strict_checker){$owners+=@($Inventory.identity.os_owner)}
        if ($entry.owner -notin $owners) {$blocks.Add('UNTRUSTED_OWNER_REQUIRES_MANUAL_REVIEW: '+$entry.path)}
        $unsafe=@();$explicit=@()
        foreach ($rule in $entry.rules) {
            if ($rule.type -eq 'Deny') {$blocks.Add('DENY_ACE_REQUIRES_MANUAL_REVIEW: '+$entry.path);continue}
            if ($rule.mask -band (-bnot [long]2032127)) {$blocks.Add('UNSUPPORTED_RIGHTS: '+$entry.path);continue}
            if ($rule.sid -notin $owners -and ($rule.mask -band $script:AclWriteMask)) {
                $after=[long]$rule.mask -band (-bnot $script:AclWriteMask)
                $issues.Add([pscustomobject][ordered]@{path=$entry.path;sid=$rule.sid;inherited=$rule.inherited;inherit=$rule.inherit;propagate=$rule.propagate;before_mask=$rule.mask;after_mask=$after;repairable=$entry.repairable})
                $unsafe+=@($rule);if(!$rule.inherited){$explicit+=@($rule)}
                # Existing external ancestors may allow creation of unrelated siblings;
                # that cannot replace the already-existing protected child. Never repair them.
                $effective=!(($rule.propagate -band 2) -ne 0)
                $createOnly=$entry.directory -and (($rule.mask -band $script:AclWriteMask -band (-bnot [long]6)) -eq 0)
                if (!$entry.repairable -and ($entry.strict_checker -or ($effective -and !$createOnly))) {$blocks.Add('UNSAFE_EXTERNAL_PATH_NO_AUTOMATIC_REPAIR: '+$entry.path)}
            }
        }
        if ($entry.anchor -and $entry.path -ieq $Inventory.parent -and $unsafe.Count -and $Inventory.siblings.Count) {$blocks.Add('SHARED_PARENT_PROPAGATION_REQUIRES_SEPARATE_REVIEW')}
        if ($entry.repairable -and ($unsafe.Count -or ($entry.anchor -and $entry.path -ine $Inventory.parent -and !$entry.protected))) {
            $changes.Add([pscustomobject][ordered]@{path=$entry.path;operation=if($entry.anchor){'PROTECT_ANCHOR_COPY_INHERITANCE_AND_REMOVE_BROAD_WRITE'}elseif($explicit.Count){'REMOVE_EXPLICIT_BROAD_WRITE_PRESERVE_INHERITANCE'}else{'INHERITED_REPAIR_FROM_APPROVED_PARENT'};preserve_read_execute=$true;owner_preserved=$true;dacl_only=$true;trusted_full_control_for=@($trusted);unsafe_ace_count=$unsafe.Count})
        }
    }
    $body=[pscustomobject][ordered]@{format=1;created_at=$Now;inventory_hash=(Get-ReceiptDigest $Inventory);inventory=$Inventory;issues=@($issues.ToArray());changes=@($changes.ToArray());blockers=@($blocks.ToArray() | Select-Object -Unique)}
    [pscustomobject][ordered]@{mode='READ_ONLY_PLAN';write_authorized=$false;body=$body;approval_hash=(Get-ReceiptDigest $body)}
}
function Assert-ReceiptApproval($Plan,[string]$Hash,[string]$Reference,[string]$Confirmation) {
    if ($Hash -cne $Plan.approval_hash -or (Get-ReceiptDigest $Plan.body) -cne $Hash) {throw 'P24A_APPROVAL_HASH_MISMATCH'}
    $age=([DateTimeOffset]::UtcNow-(Get-ReceiptTime $Plan.body.created_at)).TotalSeconds
    if ($age -lt 0 -or $age -gt 900) {throw 'P24A_APPROVAL_EXPIRED'}
    if ($Reference -notmatch '^[A-Za-z0-9][A-Za-z0-9._:-]{7,119}$' -or $Reference -match '(?i)example|replace|sample') {throw 'P24A_GENUINE_ADMIN_APPROVAL_REFERENCE_REQUIRED'}
    if ($Confirmation -cne 'DESKTOP-ADTR67Q\User|REGISTER1|ACL ONLY') {throw 'P24A_EXACT_TARGET_CONFIRMATION_REQUIRED'}
    if((Get-ReceiptDigest $Plan.body.inventory) -cne $Plan.body.inventory_hash){throw 'P24A_INVENTORY_HASH_MISMATCH'}
    if (@($Plan.body.blockers).Count) {throw 'P24A_PLAN_BLOCKED'}
    $id=Get-ReceiptAclIdentity
    if (!$id.elevated -or $id.sid -ne $Plan.body.inventory.identity.sid) {throw 'P24A_ELEVATED_SAME_USER_REQUIRED'}
}
function Write-ReceiptNewJson([string]$Path,$Value) {
    $bytes=[Text.Encoding]::UTF8.GetBytes((ConvertTo-Json -InputObject $Value -Depth 100))
    $file=[IO.File]::Open($Path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read)
    try {$file.Write($bytes,0,$bytes.Length);$file.Flush($true)}finally{$file.Dispose()}
}
function Export-ReceiptAclBackup($Inventory,[string]$Directory,[string]$ApprovalHash,[string]$Reference) {
    $Directory=[IO.Path]::GetFullPath($Directory)
    $profile=[Environment]::GetFolderPath('UserProfile')
    if (!(Test-ReceiptWithin $Directory $profile) -or (Test-ReceiptWithin $Directory $script:AclParent) -or (Test-ReceiptWithin $Directory $Inventory.state_root) -or (Test-ReceiptWithin $Directory $Inventory.base_python_home) -or (Test-Path -LiteralPath $Directory)) {throw 'P24A_FRESH_PRIVATE_PROFILE_BACKUP_DIRECTORY_REQUIRED'}
    # Require an existing trusted private parent. Never alter an existing directory ACL.
    $parent=[IO.Directory]::GetParent($Directory).FullName
    foreach($p in Get-ReceiptAncestors $parent){$e=Read-ReceiptAcl $p $false $false;if($e.owner -notin @($Inventory.identity.sid,'S-1-5-18','S-1-5-32-544',$Inventory.identity.os_owner) -or @($e.rules | Where-Object {$_.type -eq 'Deny'}).Count){throw 'P24A_UNSAFE_BACKUP_PARENT'};foreach($rule in $e.rules){if($rule.type -eq 'Allow' -and $rule.sid -notin @($Inventory.identity.sid,'S-1-5-18','S-1-5-32-544',$Inventory.identity.os_owner) -and !($rule.propagate -band 2) -and ($rule.mask -band $script:AclWriteMask) -and ((Test-ReceiptWithin $p $profile) -or ($rule.mask -band $script:AclWriteMask -band (-bnot [long]6)))){throw 'P24A_UNSAFE_BACKUP_PARENT'}}}
    New-Item -ItemType Directory -Path $Directory -ErrorAction Stop | Out-Null
    $acl=New-Object Security.AccessControl.DirectorySecurity;$acl.SetAccessRuleProtection($true,$false)
    foreach($sid in @($Inventory.identity.sid,'S-1-5-18','S-1-5-32-544')){$acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule((New-Object Security.Principal.SecurityIdentifier($sid)),'FullControl','ContainerInherit,ObjectInherit','None','Allow')))}
    $created=Get-Item -LiteralPath $Directory -Force
    $createdAcl=Get-Acl -LiteralPath $Directory
    if(($created.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $createdAcl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin @($Inventory.identity.sid,'S-1-5-18','S-1-5-32-544')){throw 'P24A_UNSAFE_NEW_BACKUP_DIRECTORY'}
    Set-Acl -LiteralPath $Directory -AclObject $acl
    $backup=[pscustomobject][ordered]@{format=1;approval_hash=$ApprovalHash;approval_reference=$Reference;inventory=$Inventory;created_at=[DateTimeOffset]::UtcNow.ToString('o')}
    $path=Join-Path $Directory 'before-acls.json';Write-ReceiptNewJson $path $backup
    return [pscustomobject]@{file=$path;sha256=(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()}
}
function Set-ReceiptScopedAcl($Entry,[string[]]$Trusted) {
    if (!(Test-ReceiptWithin $Entry.path $script:AclInstall) -and $Entry.path -ine $script:AclParent) {throw 'P24A_WRITE_OUTSIDE_ALLOWED_SCOPE'}
    $acl=Get-Acl -LiteralPath $Entry.path
    if($Entry.anchor){$acl.SetAccessRuleProtection($true,$true)}
    foreach($rule in @($acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]))){
        $mask=Expand-ReceiptMask ([long]$rule.FileSystemRights)
        if($rule.AccessControlType -eq 'Allow' -and $rule.IdentityReference.Value -notin $Trusted -and ($mask -band $script:AclWriteMask)){
            if($rule.IsInherited){throw 'P24A_INHERITANCE_NOT_REFRESHED_REQUIRES_REVIEW'}
            $acl.RemoveAccessRuleSpecific($rule)
            $safe=$mask -band (-bnot $script:AclWriteMask)
            if($safe){$acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($rule.IdentityReference,[Security.AccessControl.FileSystemRights]$safe,$rule.InheritanceFlags,$rule.PropagationFlags,'Allow')))}
        }
    }
    $inherit=if($Entry.directory){[Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [Security.AccessControl.InheritanceFlags]::ObjectInherit}else{[Security.AccessControl.InheritanceFlags]::None}
    foreach($sid in $Trusted){$acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule((New-Object Security.Principal.SecurityIdentifier($sid)),'FullControl',$inherit,'None','Allow')))}
    Set-Acl -LiteralPath $Entry.path -AclObject $acl
}
function Get-ReceiptExplicitSignature($Entry) { Get-ReceiptDigest ([pscustomobject][ordered]@{owner=$Entry.owner;protected=$Entry.protected;rules=@($Entry.rules | Where-Object {!$_.inherited});content_hash=$Entry.content_hash}) }
function Invoke-ReceiptAclRepair($Plan,[string]$Hash,[string]$Reference,[string]$Confirmation,[string]$BackupDirectory) {
    Assert-ReceiptApproval $Plan $Hash $Reference $Confirmation
    if($Plan.mode -ne 'READ_ONLY_PLAN'){throw 'P24A_REPAIR_PLAN_REQUIRED'}
    $fresh=Get-ReceiptInventory
    $validated=New-ReceiptRepairPlan $fresh
    if(@($validated.body.blockers).Count){throw 'P24A_FRESH_PREFLIGHT_BLOCKED'}
    if((Get-ReceiptDigest $fresh) -cne $Plan.body.inventory_hash){throw 'P24A_INVENTORY_CHANGED_REPLAN_REQUIRED'}
    Assert-ReceiptApproval $Plan $Hash $Reference $Confirmation
    $backup=Export-ReceiptAclBackup $fresh $BackupDirectory $Hash $Reference
    Assert-ReceiptApproval $Plan $Hash $Reference $Confirmation
    $written=0
    $trusted=@($fresh.identity.sid,'S-1-5-18','S-1-5-32-544')
    # Parent first; all inherited effects are limited to the approved connector tree.
    $targets=@($fresh.entries | Where-Object {$_.repairable} | Sort-Object {$_.path.Length},path)
    foreach($entry in $targets){
        $now=Read-ReceiptAcl $entry.path $true $entry.anchor
        if((Get-ReceiptExplicitSignature $now) -cne (Get-ReceiptExplicitSignature $entry)){throw 'P24A_PARTIAL_REPAIR_ACL_DRIFT_REVIEW_BACKUP'}
        $needs=@($now.rules | Where-Object {$_.type -eq 'Allow' -and $_.sid -notin $trusted -and ($_.mask -band $script:AclWriteMask)}).Count
        if($needs -or ($entry.anchor -and $entry.path -ine $fresh.parent -and !$now.protected)){Set-ReceiptScopedAcl $now $trusted;$written++}
    }
    $after=Get-ReceiptInventory;$check=New-ReceiptRepairPlan $after
    if((Get-ReceiptDigest @($fresh.entries.path)) -cne (Get-ReceiptDigest @($after.entries.path)) -or (Get-ReceiptDigest @($fresh.siblings)) -cne (Get-ReceiptDigest @($after.siblings))){throw 'P24A_PARTIAL_REPAIR_PATH_SET_CHANGED_REVIEW_BACKUP'}
    if(@($check.body.blockers).Count -or @($check.body.changes).Count){throw 'P24A_PARTIAL_REPAIR_VALIDATION_FAILED_REVIEW_BACKUP'}
    foreach($change in $validated.body.changes){
        $entry=@($after.entries | Where-Object {$_.path -ieq $change.path})[0]
        foreach($sid in $trusted){$mask=[long]0;foreach($rule in $entry.rules){if($rule.type -eq 'Allow' -and $rule.sid -eq $sid -and !($rule.propagate -band 2)){$mask=$mask -bor $rule.mask}};if(($mask -band 2032127) -ne 2032127){throw 'P24A_PARTIAL_REPAIR_TRUSTED_ACCESS_VALIDATION_FAILED'}}
    }
    foreach($before in $fresh.entries){$current=@($after.entries | Where-Object {$_.path -ieq $before.path});if($current.Count -ne 1 -or $current[0].content_hash -cne $before.content_hash -or $current[0].owner -cne $before.owner -or (!$before.repairable -and $current[0].sddl -cne $before.sddl)){throw 'P24A_PARTIAL_REPAIR_UNEXPECTED_CHANGE_REVIEW_BACKUP'}}
    Write-ReceiptNewJson (Join-Path $BackupDirectory 'after-inventory.json') $after
    [pscustomobject]@{status='ACL_REPAIR_VALIDATED_NO_TASK_STARTED';direct_dacl_writes=$written;backup_file=$backup.file;backup_sha256=$backup.sha256;approval_reference=$Reference;task_created=$false;device_id=$script:AclDevice}
}
function Read-ReceiptAclBackup([string]$Path,[string]$Hash) {
    # Hash and parse the same read-only byte snapshot, not two path reads.
    $bytes=[IO.File]::ReadAllBytes($Path);$sha=[Security.Cryptography.SHA256]::Create()
    try{$actual=([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
    if($actual -cne $Hash){throw 'P24A_BACKUP_HASH_MISMATCH'}
    $backup=[Text.Encoding]::UTF8.GetString($bytes) | ConvertFrom-Json
    if($backup.format -ne 1 -or $backup.inventory.install -ine $script:AclInstall -or $backup.inventory.device_id -ne $script:AclDevice){throw 'P24A_BACKUP_TARGET_MISMATCH'}
    return $backup
}
function New-ReceiptRollbackPlan([string]$BackupFile,[string]$BackupHash) {
    $backup=Read-ReceiptAclBackup $BackupFile $BackupHash;$fresh=Get-ReceiptInventory
    $blocks=New-Object Collections.Generic.List[string]
    if($fresh.identity.sid -ne $backup.inventory.identity.sid){$blocks.Add('BACKUP_OWNER_MISMATCH')}
    if((Get-ReceiptDigest @($fresh.entries.path)) -cne (Get-ReceiptDigest @($backup.inventory.entries.path))){$blocks.Add('PATH_SET_CHANGED_REQUIRES_MANUAL_REVIEW')}
    if((Get-ReceiptDigest @($fresh.siblings)) -cne (Get-ReceiptDigest @($backup.inventory.siblings))){$blocks.Add('SHARED_PARENT_MEMBERSHIP_CHANGED')}
    foreach($old in $backup.inventory.entries){
        $now=@($fresh.entries | Where-Object {$_.path -ieq $old.path})
        if($now.Count -ne 1 -or $now[0].owner -cne $old.owner -or $now[0].content_hash -cne $old.content_hash){$blocks.Add('OWNER_OR_CONTENT_CHANGED_REQUIRES_REVIEW: '+$old.path)}
        if(!$old.repairable -and $now.Count -eq 1 -and $now[0].sddl -cne $old.sddl){$blocks.Add('EXTERNAL_ACL_CHANGED_REQUIRES_REVIEW: '+$old.path)}
    }
    $restoreChanges=@(foreach($old in $backup.inventory.entries){
        $now=@($fresh.entries | Where-Object {$_.path -ieq $old.path})
        if($old.repairable -and $now.Count -eq 1 -and $now[0].sddl -cne $old.sddl){[pscustomobject]@{path=$old.path;operation='RESTORE_EXPORTED_DACL_ONLY'}}
    })
    $body=[pscustomobject][ordered]@{format=1;created_at=[DateTimeOffset]::UtcNow.ToString('o');inventory_hash=(Get-ReceiptDigest $fresh);inventory=$fresh;backup_file=$BackupFile;backup_hash=$BackupHash;blockers=@($blocks.ToArray() | Select-Object -Unique);changes=$restoreChanges}
    [pscustomobject][ordered]@{mode='READ_ONLY_ROLLBACK_PLAN';write_authorized=$false;body=$body;approval_hash=(Get-ReceiptDigest $body)}
}
function Restore-ReceiptScopedAcl($Entry) {
    if(!(Test-ReceiptWithin $Entry.path $script:AclInstall) -and $Entry.path -ine $script:AclParent){throw 'P24A_WRITE_OUTSIDE_ALLOWED_SCOPE'}
    $acl=Get-Acl -LiteralPath $Entry.path
    $acl.SetSecurityDescriptorSddlForm($Entry.sddl,[Security.AccessControl.AccessControlSections]::Access)
    Set-Acl -LiteralPath $Entry.path -AclObject $acl
}
function Invoke-ReceiptAclRollback($Plan,[string]$Hash,[string]$Reference,[string]$Confirmation,[string]$BackupDirectory) {
    Assert-ReceiptApproval $Plan $Hash $Reference $Confirmation
    if($Plan.mode -ne 'READ_ONLY_ROLLBACK_PLAN'){throw 'P24A_ROLLBACK_PLAN_REQUIRED'}
    $backup=Read-ReceiptAclBackup $Plan.body.backup_file $Plan.body.backup_hash
    $fresh=Get-ReceiptInventory
    if((Get-ReceiptDigest $fresh) -cne $Plan.body.inventory_hash){throw 'P24A_INVENTORY_CHANGED_REPLAN_REQUIRED'}
    Assert-ReceiptApproval $Plan $Hash $Reference $Confirmation
    $undo=Export-ReceiptAclBackup $fresh $BackupDirectory $Hash $Reference
    Assert-ReceiptApproval $Plan $Hash $Reference $Confirmation
    foreach($entry in @($backup.inventory.entries | Where-Object {$_.repairable} | Sort-Object {$_.path.Length},path)){
        $now=Read-ReceiptAcl $entry.path $true $entry.anchor
        if($now.sddl -cne $entry.sddl){Restore-ReceiptScopedAcl $entry}
    }
    $after=Get-ReceiptInventory
    if((Get-ReceiptDigest $after) -cne (Get-ReceiptDigest $backup.inventory)){throw 'P24A_ROLLBACK_VALIDATION_FAILED_NO_TASK_ACTION'}
    [pscustomobject]@{status='ACL_ROLLBACK_VERIFIED_NO_TASK_ACTION';undo_backup_file=$undo.file;undo_backup_sha256=$undo.sha256;task_created=$false;device_id=$script:AclDevice}
}
