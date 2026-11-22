# Pure plans + mocked native filesystem/ACL adapter; never changes a Windows DACL.
. "$PSScriptRoot/../../scripts/windows/receipt_acl_repair.ps1"
$script:Assertions=0
function Assert($Condition,$Message){$script:Assertions++;if(!$Condition){throw $Message}}
function Throws($Block,[string]$Code=''){try{& $Block | Out-Null}catch{if($Code){Assert ($_.Exception.Message -ceq $Code) ('Expected '+$Code+' but got '+$_.Exception.Message)};return};throw 'Expected rejection'}
$script:UserSid='S-1-5-21-100-200-300-1001';$script:Broad='S-1-5-11'
function Rule($Sid,$Mask,[bool]$Inherited=$false,[int]$Inherit=0,[int]$Propagate=0,[string]$Type='Allow'){[pscustomobject][ordered]@{sid=$Sid;type=$Type;mask=[long]$Mask;inherit=$Inherit;propagate=$Propagate;inherited=$Inherited}}
function Entry($Path,[bool]$Repairable,[bool]$Anchor,[bool]$Protected,$Rules,[bool]$Directory=$true){[pscustomobject][ordered]@{path=$Path;repairable=$Repairable;anchor=$Anchor;strict_checker=$false;directory=$Directory;owner=$script:UserSid;protected=$Protected;sddl=('before:'+ $Path);rules=@($Rules);content_hash=if($Directory){$null}else{'unchanged-content'}}}
function Inventory {
    $parent=Entry $script:AclParent $true $true $false @((Rule $script:UserSid 2032127),(Rule 'S-1-5-18' 2032127),(Rule 'S-1-5-32-544' 2032127),(Rule $script:Broad 1245631 $true 3))
    $root=Entry $script:AclInstall $true $true $false @((Rule $script:UserSid 2032127),(Rule 'S-1-5-18' 2032127),(Rule 'S-1-5-32-544' 2032127),(Rule $script:Broad 1245631 $true 3))
    $module=Entry ($script:AclInstall+'\windows_print_agent') $true $false $false @((Rule $script:UserSid 2032127),(Rule 'S-1-5-18' 2032127),(Rule 'S-1-5-32-544' 2032127),(Rule $script:Broad 1245631 $true 3))
    $file=Entry ($script:AclInstall+'\windows_print_agent\runtime.py') $true $false $false @((Rule $script:UserSid 2032127),(Rule 'S-1-5-18' 2032127),(Rule 'S-1-5-32-544' 2032127),(Rule 'S-1-1-0' 1245631)) $false
    [pscustomobject][ordered]@{identity=[pscustomobject][ordered]@{computer='DESKTOP-ADTR67Q';account='DESKTOP-ADTR67Q\User';sid=$script:UserSid;os_owner='TrustedInstallerSid'};device_id=$script:AclDevice;device_binding_verified=$true;install=$script:AclInstall;parent=$script:AclParent;tool_outside_scope=$true;siblings=@();entries=@($parent,$root,$module,$file)}
}
$inventory=Inventory;$plan=New-ReceiptRepairPlan $inventory
Assert ($plan.mode -eq 'READ_ONLY_PLAN' -and !$plan.write_authorized -and $plan.body.blockers.Count -eq 0) 'Dry-run default'
Assert ($plan.body.issues.Count -eq 4 -and $plan.body.changes.Count -eq 4) 'Enumerate inherited/explicit unsafe principals'
Assert ($plan.body.issues[0].after_mask -eq 1179817) 'Retain RX/synchronize, remove Modify'
Assert ($plan.body.changes[0].trusted_full_control_for.Count -eq 3) 'Preserve User/SYSTEM/Administrators'
Assert ($inventory.entries[0].rules[-1].mask -eq 1245631 -and !$inventory.entries[0].protected) 'Plan is nonmutating'
Assert ((Expand-ReceiptMask 268435456) -eq 2032127) 'GenericAll mapped before stripping'
Assert ((Expand-ReceiptMask 1073741824) -band $script:AclWriteMask) 'GenericWrite captured'
foreach($sid in @('S-1-1-0','S-1-5-11','S-1-5-32-545','S-1-5-21-OTHER')){
    $i=Inventory;$i.entries[3].rules=@((Rule $sid 2032127));$p=New-ReceiptRepairPlan $i
    Assert ($p.body.issues[-1].sid -eq $sid -and $p.body.issues[-1].after_mask -eq 1179817) 'All broad/foreign write principals detected'
}
$i=Inventory;$i.siblings=@('C:\PixiePinks\Register2');$p=New-ReceiptRepairPlan $i
Assert ($p.body.blockers -contains 'SHARED_PARENT_PROPAGATION_REQUIRES_SEPARATE_REVIEW') 'No sibling propagation'
$i=Inventory;$i.entries[0].rules=@((Rule $script:UserSid 2032127));$i.entries[0].protected=$true;$i.siblings=@('C:\PixiePinks\UnrelatedApp');$p=New-ReceiptRepairPlan $i
Assert ($p.body.blockers.Count -eq 0) 'Safe parent need not change'
# Protected external/state paths are never auto-repaired.
$i=Inventory;$external=Entry 'C:\Users\User\AppData\Local\PixiePinks\PrintAgent' $false $false $true @((Rule $script:Broad 1245631));$external.strict_checker=$true;$i.entries+=@($external)
Assert (@((New-ReceiptRepairPlan $i).body.blockers).Count -gt 0) 'Unsafe private state blocks rather than mutates'
$i=Inventory;$external=Entry 'C:\' $false $false $false @((Rule $script:Broad 4));$i.entries+=@($external)
Assert ((New-ReceiptRepairPlan $i).body.blockers.Count -eq 0) 'Existing volume root create-sibling right cannot replace protected target'
$i=Inventory;$i.entries[0].rules+=@((Rule $script:Broad 1 $false 0 0 Deny));Assert ((New-ReceiptRepairPlan $i).body.blockers.Count -gt 0) 'Deny ACE review'
$i=Inventory;$i.entries[0].owner='OTHER';Assert ((New-ReceiptRepairPlan $i).body.blockers.Count -gt 0) 'No automatic takeownership'
$i=Inventory;$i.tool_outside_scope=$false;Assert ((New-ReceiptRepairPlan $i).body.blockers.Count -gt 0) 'Private reviewed staging required'
$i=Inventory;$i.device_binding_verified=$false;Assert ((New-ReceiptRepairPlan $i).body.blockers.Count -gt 0) 'No Register 2 use'
# Approval and UAC tests use the actual approval validator.
function Get-ReceiptAclIdentity{[pscustomobject]@{sid=$script:UserSid;elevated=$script:Elevated}}
$script:Elevated=$false;$plan=New-ReceiptRepairPlan (Inventory);$reference='owner-reviewed-24a-001';$confirmation='DESKTOP-ADTR67Q\User|REGISTER1|ACL ONLY'
Throws {Assert-ReceiptApproval $plan $plan.approval_hash $reference $confirmation} 'P24A_ELEVATED_SAME_USER_REQUIRED'
$script:Elevated=$true;$plan=($plan | ConvertTo-Json -Depth 100 | ConvertFrom-Json);Assert-ReceiptApproval $plan $plan.approval_hash $reference $confirmation
Throws {Assert-ReceiptApproval $plan 'bad-hash' $reference $confirmation} 'P24A_APPROVAL_HASH_MISMATCH'
Throws {Assert-ReceiptApproval $plan $plan.approval_hash $reference 'wrong-target'} 'P24A_EXACT_TARGET_CONFIRMATION_REQUIRED'
Throws {Assert-ReceiptApproval $plan $plan.approval_hash 'example-reference' $confirmation} 'P24A_GENUINE_ADMIN_APPROVAL_REFERENCE_REQUIRED'
$expired=New-ReceiptRepairPlan (Inventory) ([DateTimeOffset]::UtcNow.AddMinutes(-16).ToString('o'))
Throws {Assert-ReceiptApproval $expired $expired.approval_hash $reference $confirmation} 'P24A_APPROVAL_EXPIRED'
# Fresh-state check, backup-before-write, post-validation and rollback ordering.
$script:Current=Inventory;$script:Original=($script:Current | ConvertTo-Json -Depth 100 | ConvertFrom-Json);$script:Events=New-Object Collections.Generic.List[string];$script:FailPath=''
function Get-ReceiptInventory{return ($script:Current | ConvertTo-Json -Depth 100 | ConvertFrom-Json)}
function Read-ReceiptAcl($Path,$Repairable,$Anchor){return (@($script:Current.entries | Where-Object {$_.path -eq $Path})[0] | ConvertTo-Json -Depth 100 | ConvertFrom-Json)}
function Export-ReceiptAclBackup($Inventory,$Directory,$ApprovalHash,$Reference){$script:Events.Add('BACKUP');[pscustomobject]@{file='private-before.json';sha256='backup-hash'}}
function Write-ReceiptNewJson($Path,$Value){$script:Events.Add('MANIFEST')}
function Set-ReceiptScopedAcl($Entry,$Trusted){
    if($Entry.path -eq $script:FailPath){throw 'P24A_PARTIAL_REPAIR_ACL_DRIFT_REVIEW_BACKUP'}
    $script:Events.Add('SET:'+$Entry.path)
    $target=@($script:Current.entries | Where-Object {$_.path -eq $Entry.path})[0]
    if($Entry.anchor){$target.protected=$true}
    # Model native parent inheritance refresh only inside the selected connector.
    foreach($e in $script:Current.entries){
        foreach($rule in $e.rules){if($rule.sid -notin $Trusted -and ($rule.mask -band $script:AclWriteMask) -and ($e.path -eq $Entry.path -or ($Entry.anchor -and $rule.inherited))){$rule.mask=$rule.mask -band (-bnot $script:AclWriteMask);$e.sddl='after:'+$e.path}}
    }
    $target.sddl='after:'+$Entry.path
}
$plan=New-ReceiptRepairPlan $script:Current
$result=Invoke-ReceiptAclRepair $plan $plan.approval_hash $reference $confirmation 'private-backup'
Assert ($result.status -eq 'ACL_REPAIR_VALIDATED_NO_TASK_STARTED' -and $script:Events[0] -eq 'BACKUP') 'Backup before ACL mutations'
Assert ((New-ReceiptRepairPlan $script:Current).body.changes.Count -eq 0) 'Whole-chain post-validation'
# Mock protected backup input, while exercising fresh rollback plan and validation.
function Read-ReceiptAclBackup($Path,$Hash){if($Hash -ne 'backup-hash'){throw 'P24A_BACKUP_HASH_MISMATCH'};[pscustomobject]@{inventory=$script:Original}}
function Restore-ReceiptScopedAcl($Entry){$script:Events.Add('RESTORE:'+$Entry.path);for($j=0;$j -lt $script:Current.entries.Count;$j++){if($script:Current.entries[$j].path -eq $Entry.path){$script:Current.entries[$j]=($Entry | ConvertTo-Json -Depth 100 | ConvertFrom-Json)}}}
$rollback=New-ReceiptRollbackPlan 'private-before.json' 'backup-hash'
Assert ($rollback.body.blockers.Count -eq 0) 'Rollback dry run'
$result=Invoke-ReceiptAclRollback $rollback $rollback.approval_hash $reference $confirmation 'private-undo'
Assert ($result.status -eq 'ACL_ROLLBACK_VERIFIED_NO_TASK_ACTION') 'Exact rollback validation'
Assert ((Get-ReceiptDigest $script:Current) -ceq (Get-ReceiptDigest $script:Original)) 'Restore original ACLs/inheritance'
# A safe shared parent is never rewritten, including rollback.
$script:Current=Inventory;$script:Current.entries[0].rules=@((Rule $script:UserSid 2032127));$script:Current.siblings=@('C:\PixiePinks\OtherApp')
$script:Original=($script:Current | ConvertTo-Json -Depth 100 | ConvertFrom-Json);$script:Events.Clear()
$p=New-ReceiptRepairPlan (Get-ReceiptInventory);Invoke-ReceiptAclRepair $p $p.approval_hash $reference $confirmation 'private' | Out-Null
Assert (!($script:Events -contains ('SET:'+$script:AclParent))) 'No unrelated shared-parent write'
$p=New-ReceiptRollbackPlan 'private-before.json' 'backup-hash';Invoke-ReceiptAclRollback $p $p.approval_hash $reference $confirmation 'undo' | Out-Null
Assert (!($script:Events -contains ('RESTORE:'+$script:AclParent))) 'No unrelated shared-parent rollback write'
# Mid-apply failure keeps a prior export and never silently claims success.
$script:Current=Inventory;$script:Original=($script:Current | ConvertTo-Json -Depth 100 | ConvertFrom-Json);$script:Events.Clear();$script:FailPath=$script:AclInstall
$p=New-ReceiptRepairPlan (Get-ReceiptInventory)
Throws {Invoke-ReceiptAclRepair $p $p.approval_hash $reference $confirmation 'private'} 'P24A_PARTIAL_REPAIR_ACL_DRIFT_REVIEW_BACKUP'
Assert ($script:Events[0] -eq 'BACKUP' -and $script:Events.Count -eq 2) 'Partial apply retains original backup and stops'
$script:FailPath=''
$script:Current=Inventory;$plan=New-ReceiptRepairPlan (Get-ReceiptInventory);$script:Current.entries[0].sddl='CONCURRENT-CHANGE'
Throws {Invoke-ReceiptAclRepair $plan $plan.approval_hash $reference $confirmation 'private'} 'P24A_INVENTORY_CHANGED_REPLAN_REQUIRED'
$script:Current=Inventory;$script:Current.siblings=@('C:\PixiePinks\NewOtherApp');$rollback=New-ReceiptRollbackPlan 'private-before.json' 'backup-hash'
Assert ($rollback.body.blockers -contains 'SHARED_PARENT_MEMBERSHIP_CHANGED') 'Rollback cannot affect newly added sibling'
# No task management, ACL resets, owner takeover or protected-state writes in repair entrypoints.
foreach($path in @("$PSScriptRoot/../../scripts/windows/receipt_acl_repair.ps1","$PSScriptRoot/../../scripts/windows/repair_receipt_startup_acl.ps1")){
    $text=Get-Content -LiteralPath $path -Raw
    Assert ($text -notmatch '(?i)Register-ScheduledTask|Start-ScheduledTask|Stop-Process|takeown|icacls.+/reset') 'No prohibited operation'
    $tokens=$null;$errors=$null;[Management.Automation.Language.Parser]::ParseFile((Resolve-Path $path),[ref]$tokens,[ref]$errors)|Out-Null
    Assert ($errors.Count -eq 0) 'PowerShell parser'
}
Write-Output 'ACL_REPAIR_MOCK_TESTS_PASSED'
# Strengthened Phase24 installer guard: real validator, mocked ACL/read adapters.
. "$PSScriptRoot/../../scripts/windows/receipt_autostart.ps1"
function Native-Guard-Case([long]$Mask,[string]$UnsafePath,[bool]$Reject){
    $drive=Join-Path ([IO.Path]::GetTempPath()) ('pixie-acl-guard-'+[Guid]::NewGuid().ToString('N'));New-Item -ItemType Directory -Path $drive | Out-Null
    $created=$false;if(!(Get-PSDrive -Name C -ErrorAction SilentlyContinue)){New-PSDrive -Name C -PSProvider FileSystem -Root $drive | Out-Null;$created=$true}
    try{
        $script:GuardMask=$Mask;$script:GuardPath=$UnsafePath;$script:GuardRoots=@()
        function Get-ChildItem($LiteralPath,[switch]$Recurse,[switch]$Force){$script:GuardRoots+=@($LiteralPath);[pscustomobject]@{FullName=($script:AclInstall+'\scripts\windows\start_receipt_connector.ps1')}}
        function Get-Item($LiteralPath,[switch]$Force){[pscustomobject]@{Attributes=[IO.FileAttributes]::Normal}}
        function Get-Acl($LiteralPath){
            $rule=[pscustomobject]@{IdentityReference=[pscustomobject]@{Value=if(($LiteralPath -replace '/','\') -eq $script:GuardPath){$script:Broad}else{$script:UserSid}};FileSystemRights=[Security.AccessControl.FileSystemRights]$script:GuardMask;AccessControlType='Allow'}
            $acl=[pscustomobject]@{Rule=$rule}
            $acl | Add-Member ScriptMethod GetOwner {param($Type);[pscustomobject]@{Value=$script:UserSid}}
            $acl | Add-Member ScriptMethod GetAccessRules {param($Explicit,$Inherited,$Type);@($this.Rule)}
            $acl
        }
        $paths=[pscustomobject]@{Install=$script:AclInstall;Python=($script:AclInstall+'\.venv\Scripts\python.exe');PythonW=($script:AclInstall+'\.venv\Scripts\pythonw.exe');Root='C:\Users\User\AppData\Local\PixiePinks\PrintAgent'}
        $identity=[pscustomobject]@{Sid=$script:UserSid}
        if($Reject){Throws {Assert-ReceiptAcl $paths $identity} 'Another principal can modify a startup path; administrator ACL review required.'}
        else{Assert-ReceiptAcl $paths $identity}
        Assert (@($script:GuardRoots | ForEach-Object {$_ -replace '/','\'}) -contains ($script:AclInstall+'\scripts\windows')) 'Launcher scripts included in trust checker'
    }finally{if($created){Remove-PSDrive C};Remove-Item -LiteralPath $drive -Recurse -Force}
}
Native-Guard-Case 64 ($script:AclInstall+'\scripts') $true
Native-Guard-Case 1245631 ($script:AclInstall+'\scripts\windows\start_receipt_connector.ps1') $true
Native-Guard-Case 131241 ($script:AclInstall+'\scripts\windows\start_receipt_connector.ps1') $false
Write-Output 'ACL_INSTALLER_GUARD_TESTS_PASSED'

Write-Output ('ACL_ASSERTIONS='+$script:Assertions)
