[CmdletBinding()]param()
. "$PSScriptRoot\receipt_autostart.ps1"
try { Get-ReceiptStatus | ConvertTo-Json -Depth 4 }
catch { Write-Error (Get-ReceiptFailure $_); exit 1 }
