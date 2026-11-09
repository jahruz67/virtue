# Invoked automatically by Windows Setup and the temporary logon task.
[CmdletBinding()]
param([switch]$Resume, [switch]$Repair)
$ErrorActionPreference = 'Stop'
$work = Join-Path ([System.Environment]::GetFolderPath('CommonApplicationData')) 'Virtue'
$statusPath = Join-Path $work 'setup-status.json'
$installedAssetsPath = Join-Path $work 'installed-assets.json'
$taskName = 'VirtueFinishSetup'

function New-WorkSecurity([bool]$directory) {
    $security = if ($directory) { [System.Security.AccessControl.DirectorySecurity]::new() } else { [System.Security.AccessControl.FileSecurity]::new() }
    $security.SetAccessRuleProtection($true, $false)
    $security.SetOwner([System.Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))
    $inheritance = if ($directory) { [System.Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit' } else { [System.Security.AccessControl.InheritanceFlags]::None }
    foreach ($sid in @('S-1-5-18', 'S-1-5-32-544')) {
        $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
            [System.Security.Principal.SecurityIdentifier]::new($sid),
            [System.Security.AccessControl.FileSystemRights]::FullControl,
            $inheritance, [System.Security.AccessControl.PropagationFlags]::None,
            [System.Security.AccessControl.AccessControlType]::Allow)
        $security.AddAccessRule($rule)
    }
    return $security
}

function Initialize-WorkDirectory {
    $entry = Get-Item -LiteralPath $work -Force -ErrorAction SilentlyContinue
    if (-not $entry) {
        $entry = [System.IO.Directory]::CreateDirectory($work, (New-WorkSecurity $true))
    } elseif ($entry -isnot [System.IO.DirectoryInfo]) {
        throw 'The Windows setup work path must be a directory.'
    }
    $pending = [System.Collections.Generic.Queue[System.IO.FileSystemInfo]]::new()
    $entries = [System.Collections.Generic.List[System.IO.FileSystemInfo]]::new()
    $pending.Enqueue($entry)
    while ($pending.Count) {
        $entry = $pending.Dequeue()
        if (($entry.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -or $entry.LinkType) {
            throw 'Windows setup files must not contain links or junctions.'
        }
        $entries.Add($entry)
        if ($entry -is [System.IO.DirectoryInfo]) {
            foreach ($child in (Get-ChildItem -LiteralPath $entry.FullName -Force)) { $pending.Enqueue($child) }
        }
    }
    foreach ($entry in $entries) {
        $entry.Refresh()
        if (($entry.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -or $entry.LinkType) {
            throw 'Windows setup files must not contain links or junctions.'
        }
        Set-Acl -LiteralPath $entry.FullName -AclObject (New-WorkSecurity ($entry -is [System.IO.DirectoryInfo]))
    }
}

function Copy-SetupFile([string]$source, [string]$destination) {
    $existing = [System.IO.FileInfo]::new($destination)
    if ($existing.Exists) { $existing.IsReadOnly = $false }
    $reader = [System.IO.File]::OpenRead($source)
    try {
        $writer = [System.IO.File]::Create($destination, 4096, [System.IO.FileOptions]::None, (New-WorkSecurity $false))
        try { $reader.CopyTo($writer) } finally { $writer.Dispose() }
    } finally { $reader.Dispose() }
}

function Write-WorkFile([string]$path, [string]$text) {
    $stream = [System.IO.File]::Create($path, 4096, [System.IO.FileOptions]::None, (New-WorkSecurity $false))
    try {
        $writer = [System.IO.StreamWriter]::new($stream, [System.Text.UTF8Encoding]::new($true))
        try { $writer.WriteLine($text) } finally { $writer.Dispose() }
    } finally { $stream.Dispose() }
}

function Publish-SetupStatus {
    $elapsed = [System.Diagnostics.Stopwatch]::StartNew()
    while ($true) {
        try {
            Move-Item -LiteralPath ($statusPath + '.tmp') -Destination $statusPath -Force
            return
        } catch {
            # Guest-agent readers can hold a handle without FILE_SHARE_DELETE.
            $exception = $_.Exception
            $retryable = $false
            while ($exception) {
                $code = if ($exception -is [System.ComponentModel.Win32Exception]) { $exception.NativeErrorCode } else { $exception.HResult -band 0xFFFF }
                if ($code -in @(5, 32, 33)) { $retryable = $true; break }
                $exception = $exception.InnerException
            }
            if (-not $retryable -or $elapsed.Elapsed.TotalSeconds -ge 5) { throw }
            Start-Sleep -Milliseconds 100
        }
    }
}

function Write-Status([string]$state, [string]$message, [int]$phase, $report = $null, [switch]$SerialOnly) {
    $value = @{ state=$state; message=$message; phase=$phase; graphics_report=$report; updated_at=[DateTime]::UtcNow.ToString('o') }
    try {
        if (-not $SerialOnly) {
            Write-WorkFile ($statusPath + '.tmp') ($value | ConvertTo-Json -Depth 20)
            Publish-SetupStatus
        }
    } finally {
        # Report over serial even if saving the status file fails.
        $port = [System.IO.Ports.SerialPort]::new('COM1', 115200)
        try {
            $port.Encoding = [System.Text.UTF8Encoding]::new($false)
            $port.WriteTimeout = 10000
            $port.Open()
            $port.WriteLine(($value | ConvertTo-Json -Depth 20 -Compress))
        } catch { } finally { $port.Dispose() }
    }
}

function Invoke-Installer([string]$file, [string[]]$arguments) {
    & $file @arguments | Out-Null
    if ($LASTEXITCODE -notin @(0, 3010, 1641)) {
        throw "Required component installation failed ($file, exit $LASTEXITCODE)."
    }
}

function Install-Driver([string]$inf, [int]$phase) {
    if (-not (Test-Path -LiteralPath $inf -PathType Leaf)) { throw "The driver INF is missing: $inf" }
    Write-Status 'installing' ("Installing driver: {0}…" -f (Split-Path -Leaf $inf)) $phase
    $token = [Guid]::NewGuid().ToString('N')
    $stdout = Join-Path $work ($token + '.stdout.txt')
    $stderr = Join-Path $work ($token + '.stderr.txt')
    $log = Join-Path $work 'pnputil.log'
    $command = '/add-driver "{0}" /install' -f $inf
    try {
        $process = Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\pnputil.exe') -ArgumentList $command -NoNewWindow -Wait -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
        $exitCode = $process.ExitCode
        # ERROR_NO_MORE_ITEMS (259) includes retries where the driver is already current.
        $driverSucceeded = $exitCode -in @(0, 259, 3010, 1641)
        $output = (@(Get-Content -LiteralPath $stdout -ErrorAction SilentlyContinue
                     Get-Content -LiteralPath $stderr -ErrorAction SilentlyContinue) -join [Environment]::NewLine).Trim()
        $logSaved = $false
        $setupLines = @()
        if (-not $driverSucceeded) {
            try {
                $setupLines = @(Get-Content -LiteralPath (Join-Path $env:SystemRoot 'inf\setupapi.dev.log') -Tail 80 -ErrorAction Stop)
            } catch { Write-Warning 'Could not read the Windows driver log.' }
        }
        try {
            Add-Content -LiteralPath $log -Encoding UTF8 -Value ("{0:o} pnputil.exe {1}`r`nExit: {2}`r`n{3}`r`n" -f [DateTime]::UtcNow, $command, $exitCode, $output)
            $logSaved = $true
            if ($setupLines.Count) {
                Add-Content -LiteralPath $log -Encoding UTF8 -Value 'Recent Windows SetupAPI driver log:'
                $setupLines | Add-Content -LiteralPath $log -Encoding UTF8
            }
        } catch { Write-Warning 'Could not save the complete driver installation log.' }
        if (-not $driverSucceeded) {
            $details = if ($output) { $output.Substring(0, [Math]::Min($output.Length, 4000)) } else { 'PnPUtil returned no explanation.' }
            $errors = @($setupLines | Where-Object { $_ -match '^\s*!!!' })
            if (-not $errors.Count) { $errors = @($setupLines | Select-Object -Last 12) }
            $setupDetails = ($errors -join [Environment]::NewLine).Trim()
            if ($setupDetails.Length -gt 1800) { $setupDetails = $setupDetails.Substring($setupDetails.Length - 1800) }
            if ($setupDetails) { $details += "`nRecent Windows driver errors:`n$setupDetails" }
            $logDetails = if ($logSaved) { "Full log inside Windows: $log" } else { 'The driver log could not be saved.' }
            throw "Driver installation failed for '$inf' (PnPUtil exit $exitCode).`n$details`n$logDetails"
        }
    } finally {
        Remove-Item -LiteralPath $stdout, $stderr -Force -ErrorAction SilentlyContinue
    }
}

function Install-Virtio {
    $media = @(Get-PSDrive -PSProvider FileSystem | Where-Object {
        Test-Path -LiteralPath (Join-Path $_.Root 'guest-agent\qemu-ga-x86_64.msi')
    })
    if ($media.Count -ne 1) { throw 'The required VirtIO installation media is missing or ambiguous.' }
    $root = $media[0].Root
    $windows = if ([Environment]::OSVersion.Version.Build -ge 22000) { 'w11' } else { 'w10' }
    foreach ($driver in @('vioserial', 'NetKVM', 'viostor', 'vioscsi', 'viorng', 'vioinput')) {
        $directory = Join-Path $root "$driver\$windows\amd64"
        if (-not (Test-Path -LiteralPath $directory)) { $directory = Join-Path $root "$driver\w10\amd64" }
        $infs = @(Get-ChildItem -LiteralPath $directory -Filter '*.inf')
        if (-not $infs.Count) { throw "The required $driver driver is missing." }
        foreach ($inf in $infs) { Install-Driver $inf.FullName 1 }
    }
    $agent = Join-Path $root 'guest-agent\qemu-ga-x86_64.msi'
    Invoke-Installer 'msiexec.exe' @('/i', $agent, '/qn', '/norestart')
    Start-Service -Name 'QEMU-GA'
}

function Import-DriverCertificate([string]$path) {
    $certificate = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new($path)
    $importTaskName = 'VirtueImportCertificate-' + [Guid]::NewGuid().ToString('N')
    $resultPath = Join-Path $work ($importTaskName + '.txt')
    $registered = $false
    try {
        $thumbprint = $certificate.Thumbprint
        if ((Test-Path -LiteralPath "Cert:\LocalMachine\Root\$thumbprint") -and
            (Test-Path -LiteralPath "Cert:\LocalMachine\TrustedPublisher\$thumbprint")) { return }
        # Some guests restrict TrustedPublisher writes even for elevated administrators.
        # Run only this import as SYSTEM; graphics probing must stay interactive.
        $command = @'
$ErrorActionPreference = 'Stop'
$message = ''
$certificate = $null
try {
    $certificate = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new([Convert]::FromBase64String('__CERTIFICATE__'))
    foreach ($name in @('Root', 'TrustedPublisher')) {
        $store = [System.Security.Cryptography.X509Certificates.X509Store]::new($name, 'LocalMachine')
        try {
            $store.Open([System.Security.Cryptography.X509Certificates.OpenFlags]::ReadWrite)
            if (-not $store.Certificates.Find('FindByThumbprint', $certificate.Thumbprint, $false).Count) {
                $store.Add($certificate)
            }
            if (-not $store.Certificates.Find('FindByThumbprint', $certificate.Thumbprint, $false).Count) {
                throw 'The certificate is missing after import.'
            }
        } finally { $store.Dispose() }
    }
} catch {
    $message = "Certificate import failed ($name): $($_.Exception.Message)"
} finally {
    if ($certificate) { $certificate.Dispose() }
}
[System.IO.File]::WriteAllText('__RESULT_PATH__.tmp', $message)
[System.IO.File]::Move('__RESULT_PATH__.tmp', '__RESULT_PATH__')
if ($message) { exit 1 }
'@
        $command = $command.Replace('__CERTIFICATE__', [Convert]::ToBase64String($certificate.RawData)).Replace('__RESULT_PATH__', $resultPath.Replace("'", "''"))
        $encoded = [Convert]::ToBase64String([System.Text.Encoding]::Unicode.GetBytes($command))
        $action = New-ScheduledTaskAction -Execute (Join-Path $PSHOME 'powershell.exe') -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -EncodedCommand $encoded"
        $principal = New-ScheduledTaskPrincipal -UserId 'S-1-5-18' -LogonType ServiceAccount -RunLevel Highest
        Register-ScheduledTask -TaskName $importTaskName -Action $action -Principal $principal | Out-Null
        $registered = $true
        Start-ScheduledTask -TaskName $importTaskName
        $deadline = [DateTime]::UtcNow.AddSeconds(60)
        do {
            if (Test-Path -LiteralPath $resultPath -PathType Leaf) {
                $message = [System.IO.File]::ReadAllText($resultPath)
                if ($message) { throw $message }
                foreach ($name in @('Root', 'TrustedPublisher')) {
                    if (-not (Test-Path -LiteralPath "Cert:\LocalMachine\$name\$thumbprint")) {
                        throw "The driver signing certificate is missing from LocalMachine\$name after import."
                    }
                }
                return
            }
            Start-Sleep -Milliseconds 250
        } while ([DateTime]::UtcNow -lt $deadline)
        throw 'Windows did not finish importing the driver signing certificate within 60 seconds.'
    } finally {
        if ($registered) {
            Stop-ScheduledTask -TaskName $importTaskName -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -TaskName $importTaskName -Confirm:$false -ErrorAction SilentlyContinue
        }
        Remove-Item -LiteralPath $resultPath, ($resultPath + '.tmp') -Force -ErrorAction SilentlyContinue
        $certificate.Dispose()
    }
}

function Install-Yttrium {
    $workRoot = [System.IO.Path]::GetFullPath($work).TrimEnd('\')
    $drivers = [System.IO.Path]::GetFullPath((Join-Path $workRoot ('Drivers-' + [Guid]::NewGuid().ToString('N'))))
    if ([System.IO.Path]::GetDirectoryName($drivers) -ne $workRoot) { throw 'The driver extraction path is invalid.' }
    try {
        Expand-Archive -LiteralPath (Join-Path $work 'YttriumDrivers.zip') -DestinationPath $drivers
        $certificate = Get-ChildItem -LiteralPath $drivers -Recurse -Filter 'VirtIOTestCert.cer' | Select-Object -First 1
        if (-not $certificate) { throw 'The verified Yttrium archive is missing its signing certificate.' }
        Write-Status 'installing' 'Importing the Yttrium driver signing certificate…' 2
        Import-DriverCertificate $certificate.FullName
        $infs = @(Get-ChildItem -LiteralPath $drivers -Recurse -Filter '*.inf' | Where-Object { $_.FullName -notmatch '\\(x86|i386|arm64)\\' })
        if (-not $infs.Count) { throw 'The verified Yttrium archive is missing the x64 graphics driver.' }
        Write-Status 'installing' 'Installing Yttrium graphics drivers…' 2
        foreach ($inf in $infs) { Install-Driver $inf.FullName 2 }
    } finally {
        if (Test-Path -LiteralPath $drivers) {
            try { Remove-Item -LiteralPath $drivers -Recurse -Force }
            catch { Write-Warning 'Could not remove the temporary driver files.' }
        }
    }
}

function Register-Resume {
    $script = Join-Path $work 'Install-VirtueGuest.ps1'
    $userId = [System.Security.Principal.NTAccount]::new($env:COMPUTERNAME, 'Virtue').Translate([System.Security.Principal.SecurityIdentifier]).Value
    $action = New-ScheduledTaskAction -Execute (Join-Path $PSHOME 'powershell.exe') -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`" -Resume"
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $userId
    $principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Highest
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Force | Out-Null
}

function Enable-AutomaticLogon {
    # Setup retries can use more logons than the answer file's initial allowance.
    $logon = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
    $account = Get-Content -LiteralPath (Join-Path $work 'windows-account.json') -Raw | ConvertFrom-Json
    Set-ItemProperty -LiteralPath $logon -Name DefaultUserName -Value $account.username
    Set-ItemProperty -LiteralPath $logon -Name DefaultPassword -Value $account.password
    Set-ItemProperty -LiteralPath $logon -Name DefaultDomainName -Value $env:COMPUTERNAME
    Remove-ItemProperty -LiteralPath $logon -Name AutoLogonCount -ErrorAction SilentlyContinue
    Set-ItemProperty -LiteralPath $logon -Name AutoAdminLogon -Value '1'
}

function Test-SetupUserSession([string]$account) {
    if (-not ('VirtueSessionQuery' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
public static class VirtueSessionQuery {
    [StructLayout(LayoutKind.Sequential)] struct Session {
        public int Id; public IntPtr Name; public int State;
    }
    [DllImport("wtsapi32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern bool WTSEnumerateSessions(IntPtr server, int reserved, int version, out IntPtr sessions, out int count);
    [DllImport("wtsapi32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern bool WTSQuerySessionInformation(IntPtr server, int id, int info, out IntPtr buffer, out int bytes);
    [DllImport("wtsapi32.dll")] static extern void WTSFreeMemory(IntPtr buffer);
    static string Read(int id, int info) {
        IntPtr buffer; int bytes;
        if (!WTSQuerySessionInformation(IntPtr.Zero, id, info, out buffer, out bytes))
            throw new Win32Exception(Marshal.GetLastWin32Error());
        try { return Marshal.PtrToStringUni(buffer) ?? ""; }
        finally { if (buffer != IntPtr.Zero) WTSFreeMemory(buffer); }
    }
    public static bool IsSignedIn(string account) {
        IntPtr sessions; int count;
        if (!WTSEnumerateSessions(IntPtr.Zero, 0, 1, out sessions, out count))
            throw new Win32Exception(Marshal.GetLastWin32Error());
        try {
            int size = Marshal.SizeOf(typeof(Session));
            for (int i = 0; i < count; i++) {
                Session session = (Session)Marshal.PtrToStructure(IntPtr.Add(sessions, i * size), typeof(Session));
                if (session.State >= 6) continue;
                string user = Read(session.Id, 5);
                if (user.Length == 0) continue;
                string domain = Read(session.Id, 7);
                if (domain.Length == 0) domain = Environment.MachineName;
                if (String.Equals(domain + "\\" + user, account, StringComparison.OrdinalIgnoreCase)) return true;
            }
            return false;
        } finally { WTSFreeMemory(sessions); }
    }
}
'@
    }
    return [VirtueSessionQuery]::IsSignedIn($account)
}

function Start-Resume {
    $localUser = "$env:COMPUTERNAME\Virtue"
    if (-not (Test-SetupUserSession $localUser)) {
        Write-Status 'installing' 'Restarting Windows to sign in and resume setup automatically…' $phase
        Restart-Computer -Force
        return
    }
    $previousStatus = Get-Content -LiteralPath $statusPath -Raw
    Start-ScheduledTask -TaskName $taskName
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    do {
        $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        if ($task -and $task.State -eq 'Running') { return }
        # A fast task can finish and unregister itself between scheduler queries.
        $status = Get-Content -LiteralPath $statusPath -Raw -ErrorAction SilentlyContinue
        if ($status -and $status -ne $previousStatus) { return }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $deadline)
    throw 'Windows did not start the setup task. Sign in to Windows as Virtue and retry automatic setup.'
}

function Test-Graphics {
    $collector = Join-Path $work 'Collect-VirtueGpuReport.ps1'
    $output = Join-Path $work 'gpu-report.json'
    $stderr = Join-Path $work 'gpu-probe.stderr.txt'
    $arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$collector`" -OutputPath `"$output`""
    $lastFailure = 'No successful graphics report was produced.'
    for ($attempt = 1; $attempt -le 12; $attempt++) {
        Write-Status 'installing' "Checking accelerated graphics ($attempt/12)…" 3
        [System.IO.File]::Delete($output)
        $probe = Start-Process -FilePath (Join-Path $PSHOME 'powershell.exe') -ArgumentList $arguments -WindowStyle Hidden -PassThru -RedirectStandardError $stderr
        try {
            if (-not $probe.WaitForExit(45000)) {
                try { $probe.Kill() } catch { if (-not $probe.HasExited) { throw } }
                if (-not $probe.WaitForExit(5000)) { throw 'The graphics verification process could not be stopped.' }
                $lastFailure = 'The graphics verification process timed out.'
            } elseif ($probe.ExitCode -ne 0) {
                $detail = (Get-Content -LiteralPath $stderr -Tail 12 -ErrorAction SilentlyContinue) -join [Environment]::NewLine
                $lastFailure = "The graphics verification process failed (exit $($probe.ExitCode)). $detail"
            } else {
                try {
                    $report = Get-Content -LiteralPath $output -Raw | ConvertFrom-Json
                    $renderer = [string]$report.opengl.renderer
                    if ($report.opengl.draw_test_passed -eq $true -and $renderer -match 'zink' -and $renderer -match 'venus' -and $renderer -notmatch 'llvmpipe|lavapipe|swiftshader|Microsoft Basic Render|GDI Generic|software rasterizer') {
                        return $report
                    }
                    $lastFailure = "The graphics report did not pass the hardware rendering check (renderer: $($report.opengl.renderer))."
                } catch {
                    $lastFailure = "The graphics report could not be read: $($_.Exception.Message)"
                }
            }
        } finally { $probe.Dispose() }
        if ($attempt -lt 12) { Start-Sleep -Seconds 5 }
    }
    throw "The graphics driver did not pass its hardware rendering check. $lastFailure"
}

$principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    if ($Resume) { $arguments += ' -Resume' }
    if ($Repair) { $arguments += ' -Repair' }
    try {
        $elevated = Start-Process -FilePath (Join-Path $PSHOME 'powershell.exe') -ArgumentList $arguments -Verb RunAs -WindowStyle Hidden -Wait -PassThru
        try { exit $elevated.ExitCode } finally { $elevated.Dispose() }
    } catch {
        $message = "Windows setup requires administrator approval: $($_.Exception.Message)"
        Write-Status 'error' $message 1 -SerialOnly
        Write-Error $message -ErrorAction Continue
        exit 1
    }
}

$phase = 1
$workReady = $false
try {
    Initialize-WorkDirectory
    $workReady = $true
    if ($Resume -or ($Repair -and (Test-Path -LiteralPath $statusPath))) {
        $phase = (Get-Content -LiteralPath $statusPath -Raw | ConvertFrom-Json).phase
    }
    if ($phase -notin @(1, 2, 3)) { throw 'The saved Windows setup phase is invalid.' }
    if (-not $Resume) {
        $installedHashes = $null
        if ($Repair -and $phase -eq 3 -and (Test-Path -LiteralPath $installedAssetsPath)) {
            try { $installedHashes = Get-Content -LiteralPath $installedAssetsPath -Raw | ConvertFrom-Json }
            catch { Write-Warning 'Could not read the installed component checksums; graphics components will be reinstalled.' }
        }
        if ($Repair) {
            Write-Status 'installing' 'Updating the setup components for the existing Windows installation…' $phase
        } else {
            Write-Status 'installing' 'Installing Windows integration drivers…' 1
        }
        foreach ($name in @('Install-VirtueGuest.ps1', 'Collect-VirtueGpuReport.ps1', 'windows-account.json', 'asset-checksums.json', 'YttriumDrivers.zip', 'VulkanRT.exe')) {
            $source = Join-Path $PSScriptRoot $name
            $destination = Join-Path $work $name
            if ([System.IO.Path]::GetFullPath($source) -ne [System.IO.Path]::GetFullPath($destination)) {
                Copy-SetupFile $source $destination
            }
            Set-Acl -LiteralPath $destination -AclObject (New-WorkSecurity $false)
        }
        $hashes = Get-Content -LiteralPath (Join-Path $work 'asset-checksums.json') -Raw | ConvertFrom-Json
        foreach ($asset in @(@('YttriumDrivers.zip', $hashes.guest), @('VulkanRT.exe', $hashes.vulkan))) {
            if ((Get-FileHash -LiteralPath (Join-Path $work $asset[0]) -Algorithm SHA256).Hash -ne $asset[1]) {
                throw 'A required driver download failed verification.'
            }
        }
        if ($Repair -and $phase -eq 3 -and ($installedHashes.guest -ne $hashes.guest -or $installedHashes.vulkan -ne $hashes.vulkan)) {
            $phase = 2
        }
        Enable-AutomaticLogon
        Register-Resume
        if ($Repair) {
            Write-Status 'installing' 'Resuming Windows setup with updated components…' $phase
            Start-Resume
            exit 0
        }
    }
    if ($phase -eq 1) {
        Write-Status 'installing' 'Installing Windows integration drivers…' 1
        Install-Virtio
        Write-Status 'installing' 'Preparing Yttrium graphics and restarting Windows…' 1
        if (Confirm-SecureBootUEFI) { throw 'The VM firmware did not apply the required Yttrium configuration.' }
        Invoke-Installer 'bcdedit.exe' @('/set', 'testsigning', 'on')
        Invoke-Installer 'powercfg.exe' @('/hibernate', 'off')
        Write-Status 'installing' 'Installing Yttrium graphics after restart…' 2
        Restart-Computer -Force
    } elseif ($phase -eq 2) {
        Write-Status 'installing' 'Installing Yttrium graphics and the Vulkan runtime…' 2
        Install-Yttrium
        Write-Status 'installing' 'Installing the Vulkan runtime…' 2
        $installer = Start-Process -FilePath (Join-Path $work 'VulkanRT.exe') -ArgumentList '/S' -WindowStyle Hidden -Wait -PassThru
        if ($installer.ExitCode -notin @(0, 3010)) { throw 'The Vulkan runtime did not install successfully.' }
        # Record installed versions only after both installers have succeeded.
        $hashes = Get-Content -LiteralPath (Join-Path $work 'asset-checksums.json') -Raw | ConvertFrom-Json
        Write-WorkFile ($installedAssetsPath + '.tmp') (@{ guest=$hashes.guest; vulkan=$hashes.vulkan } | ConvertTo-Json)
        Move-Item -LiteralPath ($installedAssetsPath + '.tmp') -Destination $installedAssetsPath -Force
        Write-Status 'installing' 'Checking accelerated graphics after restart…' 3
        Restart-Computer -Force
    } elseif ($phase -eq 3) {
        $report = Test-Graphics
        # Keep this private VM opening directly into its generated Windows account.
        Enable-AutomaticLogon
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Status 'ready' 'Windows and accelerated graphics are ready.' 3 $report
    } else { throw 'The automatic installation state is invalid.' }
} catch {
    $failure = $_
    $message = $failure.Exception.Message
    if ($failure.InvocationInfo.MyCommand.Name) {
        $message = '{0}: {1}' -f $failure.InvocationInfo.MyCommand.Name, $message
    }
    if ($failure.InvocationInfo.ScriptLineNumber -gt 0) {
        $message += ' (guest setup line {0})' -f $failure.InvocationInfo.ScriptLineNumber
    }
    try { Write-Status 'error' $message $phase -SerialOnly:(-not $workReady) }
    catch { Write-Warning "Could not save the setup failure: $($_.Exception.Message)" }
    Write-Error $message -ErrorAction Continue
    try {
        if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
            Disable-ScheduledTask -TaskName $taskName | Out-Null
        }
    } catch { Write-Warning 'Could not disable the failed setup task.' }
    exit 1
}
