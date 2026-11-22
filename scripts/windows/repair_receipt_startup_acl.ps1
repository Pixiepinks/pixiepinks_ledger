[CmdletBinding(SupportsShouldProcess)]
param(
    [ValidateSet('Plan','Apply','RollbackPlan','Rollback')][string]$Mode='Plan',
    [string]$PlanFile,[string]$ApprovalHash,[string]$ApprovalReference,[string]$ConfirmTarget,[string]$BackupDirectory,
    [string]$BackupFile,[string]$BackupHash
)
. "$PSScriptRoot\receipt_acl_repair.ps1"
try {
    switch($Mode){
        'Plan' { $report=New-ReceiptRepairPlan (Get-ReceiptInventory);$report | Add-Member NoteProperty current_token_elevated (Get-ReceiptAclIdentity).elevated;$report | ConvertTo-Json -Depth 100 }
        'RollbackPlan' { New-ReceiptRollbackPlan $BackupFile $BackupHash | ConvertTo-Json -Depth 100 }
        default {
            if(!$PlanFile){throw 'P24A_REVIEWED_PLAN_REQUIRED'}
            $plan=Get-Content -LiteralPath $PlanFile -Raw -Encoding UTF8 | ConvertFrom-Json
            Assert-ReceiptApproval $plan $ApprovalHash $ApprovalReference $ConfirmTarget
            if($Mode -eq 'Apply' -and $plan.mode -ne 'READ_ONLY_PLAN'){throw 'P24A_REPAIR_PLAN_REQUIRED'}
            if($Mode -eq 'Rollback' -and $plan.mode -ne 'READ_ONLY_ROLLBACK_PLAN'){throw 'P24A_ROLLBACK_PLAN_REQUIRED'}
            if($PSCmdlet.ShouldProcess('DESKTOP-ADTR67Q\User / Register 1 code ACLs only',$Mode)){
                if($Mode -eq 'Apply'){Invoke-ReceiptAclRepair $plan $ApprovalHash $ApprovalReference $ConfirmTarget $BackupDirectory | ConvertTo-Json -Depth 100}
                else{Invoke-ReceiptAclRollback $plan $ApprovalHash $ApprovalReference $ConfirmTarget $BackupDirectory | ConvertTo-Json -Depth 100}
            }
        }
    }
} catch {
    # No raw exceptions, DPAPI plaintext, application output or receipt data.
    $code='P24A_WINDOWS_OPERATION_UNAVAILABLE_REVIEW_BACKUP_AND_PLAN'
    $message=$_.Exception.Message
    foreach($fixed in @('P24A_LINK_INFORMATION_UNAVAILABLE','P24A_HARDLINK_REQUIRES_REVIEW','P24A_APPROVAL_EXPIRED','P24A_APPROVAL_HASH_MISMATCH','P24A_BACKUP_HASH_MISMATCH','P24A_BACKUP_TARGET_MISMATCH','P24A_BASE_PYTHON_REQUIRES_REVIEW','P24A_CASE_COLLISION_REQUIRES_REVIEW','P24A_CONNECTOR_RUNNING_STOP_REQUIRES_SEPARATE_APPROVAL','P24A_DEVICE_BINDING_MISMATCH','P24A_ELEVATED_SAME_USER_REQUIRED','P24A_ENABLED_TASK_REQUIRES_SEPARATE_STOP_APPROVAL','P24A_EXACT_TARGET_CONFIRMATION_REQUIRED','P24A_FRESH_PREFLIGHT_BLOCKED','P24A_FRESH_PRIVATE_PROFILE_BACKUP_DIRECTORY_REQUIRED','P24A_GENUINE_ADMIN_APPROVAL_REFERENCE_REQUIRED','P24A_INHERITANCE_NOT_REFRESHED_REQUIRES_REVIEW','P24A_INVENTORY_CHANGED_REPLAN_REQUIRED','P24A_INVENTORY_HASH_MISMATCH','P24A_NONSTANDARD_ACE_REQUIRES_REVIEW','P24A_NULL_DACL_REQUIRES_REVIEW','P24A_PARTIAL_REPAIR_ACL_DRIFT_REVIEW_BACKUP','P24A_PARTIAL_REPAIR_PATH_SET_CHANGED_REVIEW_BACKUP','P24A_PARTIAL_REPAIR_TRUSTED_ACCESS_VALIDATION_FAILED','P24A_PARTIAL_REPAIR_UNEXPECTED_CHANGE_REVIEW_BACKUP','P24A_PARTIAL_REPAIR_VALIDATION_FAILED_REVIEW_BACKUP','P24A_PLAN_BLOCKED','P24A_PRIVATE_REVIEWED_STAGING_REQUIRED','P24A_PROTECTED_STATE_INSIDE_CODE_TREE','P24A_PYTHON_ENV_OVERRIDE_REQUIRES_REVIEW','P24A_REPAIR_PLAN_REQUIRED','P24A_REPAIR_TOOL_ACL_REQUIRES_REVIEW','P24A_REPARSE_POINT_REQUIRES_REVIEW','P24A_REQUIRED_STARTUP_FILE_MISSING','P24A_REVIEWED_PLAN_REQUIRED','P24A_ROLLBACK_PLAN_REQUIRED','P24A_ROLLBACK_VALIDATION_FAILED_NO_TASK_ACTION','P24A_SYSTEM_SITE_PACKAGES_REQUIRES_REVIEW','P24A_UNSAFE_BACKUP_PARENT','P24A_UNSAFE_NEW_BACKUP_DIRECTORY','P24A_UNSAFE_REPAIR_TOOL_PATH','P24A_UNTRUSTED_REPAIR_TOOL_OWNER','P24A_WINDOWS_OPERATION_UNAVAILABLE_REVIEW_BACKUP_AND_PLAN','P24A_WRITE_OUTSIDE_ALLOWED_SCOPE','P24A_WRONG_USER','P24A_WRONG_WINDOWS_TARGET')){if($message -ceq $fixed){$code=$fixed;break}}
    [pscustomobject]@{status='STOPPED_NO_TASK_ACTION';error_category=$code;acl_mutation_requested=($Mode -in @('Apply','Rollback'));note='If Apply/Rollback began, ACLs may be partially changed. Preserve exported backups and use a fresh rollback plan; never reset journals.'} | ConvertTo-Json
    exit 1
}
