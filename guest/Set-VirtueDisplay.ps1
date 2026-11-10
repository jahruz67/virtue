# Run by VirtueSetDisplay in the signed-in user's interactive desktop.
[CmdletBinding()]
param([switch]$Register)
$ErrorActionPreference = 'Stop'
$work = Join-Path ([Environment]::GetFolderPath('CommonApplicationData')) 'Virtue'
if ($Register) {
    $userId = [Security.Principal.NTAccount]::new($env:COMPUTERNAME, 'Virtue').Translate([Security.Principal.SecurityIdentifier]).Value
    $principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Highest
    $script = Join-Path $work 'Set-VirtueDisplay.ps1'
    $action = New-ScheduledTaskAction -Execute (Join-Path $PSHOME 'powershell.exe') -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`""
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName 'VirtueSetDisplay' -Action $action -Principal $principal -Settings $settings -Force | Out-Null
    return
}
$request = $null
$result = @{ state = 'error'; message = 'The display request could not be read.' }
try {
    $path = Join-Path $work 'display-request.json'
    $entry = Get-Item -LiteralPath $path
    if ($entry.Length -gt 16384 -or ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'Invalid display request file.'
    }
    $request = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
    if (($request.width -isnot [int] -and $request.width -isnot [long]) -or
        ($request.height -isnot [int] -and $request.height -isnot [long]) -or
        $request.width -lt 320 -or $request.width -gt 16384 -or
        $request.height -lt 320 -or $request.height -gt 16384 -or
        [double]::IsNaN([double]$request.refresh_hz) -or
        [double]::IsInfinity([double]$request.refresh_hz) -or
        $request.refresh_hz -lt 1 -or $request.refresh_hz -gt 1000 -or
        $request.request_id -isnot [string] -or -not $request.request_id -or $request.request_id.Length -gt 100) {
        throw 'Invalid display mode request.'
    }
    $result.request_id = $request.request_id
    $result.monitor = $request.monitor
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public static class VirtueDisplay {
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct Mode {
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string DeviceName;
        public ushort SpecVersion, DriverVersion, Size, DriverExtra;
        public uint Fields;
        public int PositionX, PositionY;
        public uint Orientation, FixedOutput;
        public short Color, Duplex, YResolution, TTOption, Collate;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string FormName;
        public ushort LogPixels;
        public uint BitsPerPel, Width, Height, DisplayFlags, Frequency;
        public uint ICMMethod, ICMIntent, MediaType, DitherType, Reserved1, Reserved2;
        public uint PanningWidth, PanningHeight;
    }

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    [return: MarshalAs(UnmanagedType.Bool)]
    static extern bool EnumDisplaySettings(string device, int index, ref Mode mode);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    static extern int ChangeDisplaySettings(ref Mode mode, uint flags);

    public static bool Read(int index, out Mode mode) {
        mode = new Mode();
        mode.Size = (ushort)Marshal.SizeOf(typeof(Mode));
        return EnumDisplaySettings(null, index, ref mode);
    }

    public static bool Select(int width, int height, double refresh, out Mode best) {
        best = new Mode();
        double distance = double.MaxValue;
        Mode mode;
        for (int index = 0; Read(index, out mode); index++) {
            if (mode.Width != width || mode.Height != height || mode.BitsPerPel < 24 ||
                mode.Frequency < 2 || (mode.DisplayFlags & 2) != 0) continue;
            double candidate = Math.Abs(mode.Frequency - refresh);
            if (candidate < distance || (candidate == distance && mode.Frequency < best.Frequency)) {
                best = mode;
                distance = candidate;
            }
        }
        return distance != double.MaxValue;
    }

    public static int Apply(ref Mode mode) {
        // Request only dimensions and frequency, preserving orientation, position and DPI.
        mode.Fields = 0x00080000 | 0x00100000 | 0x00400000;
        int result = ChangeDisplaySettings(ref mode, 2); // CDS_TEST
        return result == 0 ? ChangeDisplaySettings(ref mode, 0) : result;
    }
}
'@
    $mode = New-Object VirtueDisplay+Mode
    if (-not [VirtueDisplay]::Select($request.width, $request.height, $request.refresh_hz, [ref]$mode)) {
        $result.state = 'unsupported'
        $result.message = 'The Windows display driver does not offer this resolution. The current display is unchanged.'
    } else {
        $current = New-Object VirtueDisplay+Mode
        if (-not [VirtueDisplay]::Read(-1, [ref]$current)) { throw 'Cannot read the active Windows display.' }
        if ($current.Width -ne $mode.Width -or $current.Height -ne $mode.Height -or $current.Frequency -ne $mode.Frequency) {
            $code = [VirtueDisplay]::Apply([ref]$mode)
            if ($code -ne 0) { throw "Windows rejected the display mode (code $code)." }
        }
        if (-not [VirtueDisplay]::Read(-1, [ref]$current)) { throw 'Cannot confirm the active Windows display.' }
        if ($current.Width -ne $mode.Width -or $current.Height -ne $mode.Height -or $current.Frequency -ne $mode.Frequency) {
            throw 'Windows did not confirm the requested display mode.'
        }
        $result.state = 'applied'
        $result.width = [int]$current.Width
        $result.height = [int]$current.Height
        $result.refresh_hz = [int]$current.Frequency
        $result.message = 'Windows is using the closest supported monitor refresh rate at the requested resolution.'
    }
} catch {
    $result.state = 'error'
    $result.message = $_.Exception.Message
}
$result.updated_at = [DateTime]::UtcNow.ToString('o')
$temporary = Join-Path $work 'display-status.json.tmp'
[IO.File]::WriteAllText($temporary, ($result | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
Move-Item -LiteralPath $temporary -Destination (Join-Path $work 'display-status.json') -Force
