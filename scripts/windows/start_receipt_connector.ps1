[CmdletBinding(SupportsShouldProcess)]param([ValidateSet('Start','Stop','Restart')][string]$Action='Start')
. "$PSScriptRoot\receipt_autostart.ps1"
try {
    if ($PSCmdlet.ShouldProcess('Register 1 / Windows User',"$Action connector; authorized pending work can dispatch on start")) {
        if ($Action -in @('Stop','Restart')) { Stop-ReceiptConnector | ConvertTo-Json }
        if ($Action -in @('Start','Restart')) { Start-ReceiptConnector | ConvertTo-Json }
    }
} catch { Write-Error (Get-ReceiptFailure $_); exit 1 }
