# Optional convenience for the documented guest-only security changes.
[CmdletBinding(SupportsShouldProcess=$true,ConfirmImpact='High')]
param([switch]$IUnderstandGuestSigningWillBeWeakened)
$ErrorActionPreference='Stop'
if (-not $IUnderstandGuestSigningWillBeWeakened) {
    throw 'Read docs/yttrium.md, make the offline recovery checkpoint, disable Secure Boot in guest UEFI, then rerun with -IUnderstandGuestSigningWillBeWeakened. Test signing accepts test-signed drivers; hibernation and Fast Startup will be disabled.'
}
$principal=New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run PowerShell as Administrator inside the guest.' }
$secureBoot=Confirm-SecureBootUEFI
if ($secureBoot) { throw 'Guest Secure Boot is still enabled. Disable it explicitly in guest UEFI first. No settings were changed.' }
if ($PSCmdlet.ShouldProcess('This Windows guest','Enable Windows test signing; disable hibernation/Fast Startup')) {
    & bcdedit.exe /set testsigning on
    if ($LASTEXITCODE -ne 0) { throw 'bcdedit failed; no driver installation attempted.' }
    & powercfg.exe /hibernate off
    if ($LASTEXITCODE -ne 0) { throw 'Disabling hibernation failed. Resolve this before installing the driver.' }
    Write-Host 'Restart Windows, then follow the certificate and driver installation steps in docs/yttrium.md.'
}
