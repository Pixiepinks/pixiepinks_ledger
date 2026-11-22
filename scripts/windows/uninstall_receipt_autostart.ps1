[CmdletBinding(SupportsShouldProcess)]param()
. "$PSScriptRoot\receipt_autostart.ps1"
try { if ($PSCmdlet.ShouldProcess('Register 1 / Windows User','Remove startup task only')) { Uninstall-ReceiptTask | ConvertTo-Json -Depth 4 } }
catch { Write-Error (Get-ReceiptFailure $_); exit 1 }
