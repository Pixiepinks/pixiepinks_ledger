[CmdletBinding(SupportsShouldProcess)]param()
. "$PSScriptRoot\receipt_autostart.ps1"
try { if ($PSCmdlet.ShouldProcess('Register 1 / Windows User','Install logon task; does not start connector')) { Install-ReceiptTask | ConvertTo-Json -Depth 4 } }
catch { Write-Error (Get-ReceiptFailure $_); exit 1 }
