# Run in the Windows guest. This collects evidence and changes no security settings.
[CmdletBinding()]
param([string]$OutputPath = "$PWD\virtue-gpu-report.json")
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'Run this collector inside the Windows guest.' }

Add-Type -ReferencedAssemblies System.Windows.Forms,System.Drawing -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Windows.Forms;
public static class VirtueGlProbe {
  [StructLayout(LayoutKind.Sequential)] struct PFD {
    public ushort size,version; public uint flags; public byte pixelType,colorBits,redBits,redShift,greenBits,greenShift,blueBits,blueShift,alphaBits,alphaShift,accumBits,accumRedBits,accumGreenBits,accumBlueBits,accumAlphaBits,depthBits,stencilBits,auxBuffers,layerType,reserved; public uint layerMask,visibleMask,damageMask;
  }
  [DllImport("user32.dll")] static extern IntPtr GetDC(IntPtr h);
  [DllImport("user32.dll")] static extern int ReleaseDC(IntPtr h,IntPtr dc);
  [DllImport("gdi32.dll")] static extern int ChoosePixelFormat(IntPtr dc,ref PFD pfd);
  [DllImport("gdi32.dll")] static extern bool SetPixelFormat(IntPtr dc,int pf,ref PFD pfd);
  [DllImport("opengl32.dll")] static extern IntPtr wglCreateContext(IntPtr dc);
  [DllImport("opengl32.dll")] static extern bool wglMakeCurrent(IntPtr dc,IntPtr rc);
  [DllImport("opengl32.dll")] static extern bool wglDeleteContext(IntPtr rc);
  [DllImport("opengl32.dll")] static extern IntPtr glGetString(uint name);
  [DllImport("opengl32.dll")] static extern void glViewport(int x,int y,int width,int height);
  [DllImport("opengl32.dll")] static extern void glClearColor(float r,float g,float b,float a);
  [DllImport("opengl32.dll")] static extern void glClear(uint mask);
  [DllImport("opengl32.dll")] static extern void glBegin(uint mode);
  [DllImport("opengl32.dll")] static extern void glEnd();
  [DllImport("opengl32.dll")] static extern void glColor3f(float r,float g,float b);
  [DllImport("opengl32.dll")] static extern void glVertex2f(float x,float y);
  [DllImport("opengl32.dll")] static extern void glFinish();
  [DllImport("opengl32.dll")] static extern uint glGetError();
  [DllImport("opengl32.dll")] static extern void glReadPixels(int x,int y,int w,int h,uint format,uint type,[Out] byte[] bytes);
  public static object Run() {
    using (Form form = new Form()) {
      form.ClientSize = new System.Drawing.Size(64,64);
      form.Text = "Virtue OpenGL probe";
      form.Show(); Application.DoEvents();
      IntPtr handle = form.Handle, dc = GetDC(handle), rc = IntPtr.Zero;
      try {
        PFD pfd = new PFD(); pfd.size=(ushort)Marshal.SizeOf(typeof(PFD)); pfd.version=1;
        pfd.flags=0x24; pfd.colorBits=32; pfd.depthBits=24;
        int pf=ChoosePixelFormat(dc,ref pfd);
        if(pf==0 || !SetPixelFormat(dc,pf,ref pfd)) throw new Exception("No OpenGL pixel format.");
        rc=wglCreateContext(dc); if(rc==IntPtr.Zero || !wglMakeCurrent(dc,rc)) throw new Exception("Cannot create OpenGL context.");
        string vendor=Marshal.PtrToStringAnsi(glGetString(0x1F00));
        string renderer=Marshal.PtrToStringAnsi(glGetString(0x1F01));
        string version=Marshal.PtrToStringAnsi(glGetString(0x1F02));
        glViewport(0,0,64,64); glClearColor(0,0,0,1); glClear(0x4000);
        glColor3f(0,1,0); glBegin(4); glVertex2f(-1,-1); glVertex2f(1,-1); glVertex2f(0,1); glEnd();
        glFinish(); byte[] pixel=new byte[4]; glReadPixels(32,24,1,1,0x1908,0x1401,pixel);
        uint error=glGetError(); bool pass=error==0 && pixel[0]<5 && pixel[1]>250 && pixel[2]<5;
        return new { vendor=vendor,renderer=renderer,version=version,draw_test_passed=pass,gl_error=error,pixel=pixel,procedure="64x64 OpenGL triangle draw, finish and pixel readback" };
      } finally { wglMakeCurrent(IntPtr.Zero,IntPtr.Zero); if(rc!=IntPtr.Zero) wglDeleteContext(rc); ReleaseDC(handle,dc); }
    }
  }
}
'@

$opengl = try { [VirtueGlProbe]::Run() } catch { @{ error=$_.Exception.Message; draw_test_passed=$false } }
$vulkan = @{ output=''; devices=@(); error='vulkaninfo is not installed; install the Vulkan Runtime/SDK tools in the guest.' }
$vk = Get-Command vulkaninfo -ErrorAction SilentlyContinue
if ($vk) {
    $start = New-Object System.Diagnostics.ProcessStartInfo
    $start.FileName=$vk.Source; $start.Arguments='--summary'
    $start.UseShellExecute=$false; $start.CreateNoWindow=$true
    $start.RedirectStandardOutput=$true; $start.RedirectStandardError=$true
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo=$start
    [void]$process.Start()
    $stdout=$process.StandardOutput.ReadToEndAsync(); $stderr=$process.StandardError.ReadToEndAsync()
    if (-not $process.WaitForExit(30000)) {
        $process.Kill(); $vulkan.error='vulkaninfo timed out after 30 seconds.'
    } else {
        $text=$stdout.Result + $stderr.Result
        $vulkan.output=$text; $vulkan.error=if($process.ExitCode -eq 0){''}else{"Exit code $($process.ExitCode)"}
        $vulkan.devices=@([regex]::Matches($text,'deviceName\s*=\s*([^\r\n]+)') | ForEach-Object { @{ name=$_.Groups[1].Value.Trim() } })
    }
    $process.Dispose()
}
$adapters = @(Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion,DriverDate,Status,ConfigManagerErrorCode)
$os = Get-CimInstance Win32_OperatingSystem
$report = [ordered]@{
    schema_version=1
    collected_at=[DateTime]::UtcNow.ToString('o')
    computer=$env:COMPUTERNAME
    windows=@{caption=$os.Caption;version=$os.Version;build=$os.BuildNumber}
    driver_installed=(@($adapters | Where-Object { $_.Name -match 'Yttrium|VirtIO.*GPU' }).Count -gt 0)
    adapters=$adapters
    opengl=$opengl
    vulkan=$vulkan
    render_tests=@{vulkan=@{passed=$false;note='Enumeration only. Run a Vulkan graphical workload separately.'}}
    direct3d=@{verified=$false;note='No Direct3D rendering test was run.'}
}
$json = $report | ConvertTo-Json -Depth 12
[System.IO.File]::WriteAllText([System.IO.Path]::GetFullPath($OutputPath),$json,(New-Object System.Text.UTF8Encoding $false))
Write-Host "Saved $OutputPath"
Write-Host 'Import the report in Virtue graphics diagnostics. A passed GL probe does not certify CAD compatibility.'
