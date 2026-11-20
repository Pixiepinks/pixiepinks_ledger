# Run reviewed/signed setup as a Windows administrator. No password is stored by this script.
param(
 [Parameter(Mandatory=$true)][string]$AgentExe,
 [Parameter(Mandatory=$true)][string]$AgentAccount,
 [Parameter(Mandatory=$true)][string]$ExpectedSha256,
 [Parameter(Mandatory=$true)][string]$ExpectedSignerThumbprint
)
$ErrorActionPreference = 'Stop'
$ResolvedAgent = (Resolve-Path -LiteralPath $AgentExe).Path
if ([IO.Path]::GetExtension($ResolvedAgent) -ne '.exe') { throw 'Supply the reviewed signed agent executable.' }
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $ResolvedAgent).Hash -ne $ExpectedSha256) { throw 'Release SHA-256 mismatch.' }
$Signature = Get-AuthenticodeSignature -FilePath $ResolvedAgent
if ($Signature.Status -ne 'Valid' -or $Signature.SignerCertificate.Thumbprint -ne $ExpectedSignerThumbprint) { throw 'Reviewed publisher signature required.' }
$AgentSid = ([Security.Principal.NTAccount]::new($AgentAccount)).Translate([Security.Principal.SecurityIdentifier]).Value
if ($AgentSid -eq [Security.Principal.WindowsIdentity]::GetCurrent().User.Value) { throw 'Use a dedicated non-admin agent account distinct from cashier/admin browser accounts.' }
$Credential = Get-Credential -UserName $AgentAccount -Message 'Windows Task Scheduler credentials for the dedicated printer agent account'
$EnteredSid = ([Security.Principal.NTAccount]::new($Credential.UserName)).Translate([Security.Principal.SecurityIdentifier]).Value
if ($EnteredSid -ne $AgentSid) { throw 'Credential account must match the paired dedicated agent account.' }
$Action = New-ScheduledTaskAction -Execute $ResolvedAgent -Argument 'run'
$Trigger = New-ScheduledTaskTrigger -AtStartup
$Settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
# Task Scheduler protects the account password in Windows; it is not embedded in a package/config/log.
Register-ScheduledTask -TaskName 'PixiePinks Print Agent' -Action $Action -Trigger $Trigger -Settings $Settings -User $Credential.UserName -Password ($Credential.GetNetworkCredential().Password) -Description 'Outbound-only printer agent; dedicated queue ACL and DPAPI user required.' | Out-Null
Remove-Variable Credential
Write-Output 'Startup task installed. Pair and attest under the SAME dedicated agent account; start only after acceptance.'
