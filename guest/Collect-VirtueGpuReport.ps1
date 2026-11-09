# Run in the Windows guest. This collects evidence and changes no security settings.
[CmdletBinding()]
param([string]$OutputPath = "$PWD\virtue-gpu-report.json")
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'Run this collector inside the Windows guest.' }

Add-Type -ReferencedAssemblies System.Windows.Forms,System.Drawing -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Windows.Forms;
public static class VirtueGlProbe {
  sealed class ProbeWindow : Form {
    protected override CreateParams CreateParams {
      get {
        CreateParams parameters = base.CreateParams;
        parameters.ClassStyle |= 0x20; // CS_OWNDC: keep the WGL window's device context stable.
        return parameters;
      }
    }
  }
  [StructLayout(LayoutKind.Sequential)] struct PFD {
    public ushort size,version; public uint flags; public byte pixelType,colorBits,redBits,redShift,greenBits,greenShift,blueBits,blueShift,alphaBits,alphaShift,accumBits,accumRedBits,accumGreenBits,accumBlueBits,accumAlphaBits,depthBits,stencilBits,auxBuffers,layerType,reserved; public uint layerMask,visibleMask,damageMask;
  }
  [DllImport("user32.dll", SetLastError=true)] static extern IntPtr GetDC(IntPtr h);
  [DllImport("user32.dll")] static extern int ReleaseDC(IntPtr h,IntPtr dc);
  [DllImport("gdi32.dll", SetLastError=true)] static extern int ChoosePixelFormat(IntPtr dc,ref PFD pfd);
  [DllImport("gdi32.dll", SetLastError=true)] static extern int DescribePixelFormat(IntPtr dc,int pf,uint size,ref PFD pfd);
  [DllImport("gdi32.dll", SetLastError=true)] static extern bool SetPixelFormat(IntPtr dc,int pf,ref PFD pfd);
  [DllImport("opengl32.dll", SetLastError=true)] static extern IntPtr wglCreateContext(IntPtr dc);
  [DllImport("opengl32.dll", SetLastError=true)] static extern bool wglMakeCurrent(IntPtr dc,IntPtr rc);
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
  static Exception Failure(string operation) {
    int code = Marshal.GetLastWin32Error();
    string detail = code == 0 ? "Windows supplied no error code." : new Win32Exception(code).Message;
    return new InvalidOperationException(operation + " failed (Win32 " + code + "): " + detail);
  }
  static List<int> PixelFormats() {
    using (Form form = new ProbeWindow()) {
      IntPtr handle = form.Handle, dc = GetDC(handle);
      if(dc==IntPtr.Zero) throw Failure("GetDC");
      try {
        PFD pfd = new PFD(); pfd.size=(ushort)Marshal.SizeOf(typeof(PFD)); pfd.version=1;
        // Prefer the requested format, then try the driver's other hardware RGBA formats.
        pfd.flags=0x25; pfd.colorBits=32; pfd.depthBits=24;
        int preferred=ChoosePixelFormat(dc,ref pfd);
        int count=DescribePixelFormat(dc,1,pfd.size,ref pfd);
        if(count==0) throw Failure("DescribePixelFormat");
        List<int> formats = new List<int>();
        for(int index=0;index<=count;index++) {
          int pf=index==0 ? preferred : index;
          if(pf==0 || (index>0 && pf==preferred)) continue;
          if(DescribePixelFormat(dc,pf,pfd.size,ref pfd)==0) continue;
          // Require window/OpenGL support; exclude generic software formats.
          if((pfd.flags & 0x24)!=0x24 || (pfd.flags & 0x40)!=0 ||
             pfd.pixelType!=0 || pfd.layerType!=0 || pfd.colorBits<24) continue;
          formats.Add(pf);
        }
        return formats;
      } finally { ReleaseDC(handle,dc); }
    }
  }
  static object RunFormat(int pf) {
    using (Form form = new ProbeWindow()) {
      form.ClientSize = new System.Drawing.Size(64,64);
      form.Text = "Virtue OpenGL probe";
      form.Show(); Application.DoEvents();
      IntPtr handle = form.Handle, dc = GetDC(handle), rc = IntPtr.Zero;
      if(dc==IntPtr.Zero) throw Failure("GetDC");
      try {
        PFD pfd = new PFD(); pfd.size=(ushort)Marshal.SizeOf(typeof(PFD)); pfd.version=1;
        if(DescribePixelFormat(dc,pf,pfd.size,ref pfd)==0) throw Failure("DescribePixelFormat");
        if(!SetPixelFormat(dc,pf,ref pfd)) throw Failure("SetPixelFormat");
        string format = " (pixel format " + pf + ", flags 0x" + pfd.flags.ToString("X") + ")";
        rc=wglCreateContext(dc);
        if(rc==IntPtr.Zero) throw Failure("wglCreateContext" + format);
        if(!wglMakeCurrent(dc,rc)) throw Failure("wglMakeCurrent" + format);
        string vendor=Marshal.PtrToStringAnsi(glGetString(0x1F00));
        string renderer=Marshal.PtrToStringAnsi(glGetString(0x1F01));
        string version=Marshal.PtrToStringAnsi(glGetString(0x1F02));
        glViewport(0,0,64,64); glClearColor(0,0,0,1); glClear(0x4000);
        glColor3f(0,1,0); glBegin(4); glVertex2f(-1,-1); glVertex2f(1,-1); glVertex2f(0,1); glEnd();
        glFinish(); byte[] pixel=new byte[4]; glReadPixels(32,24,1,1,0x1908,0x1401,pixel);
        uint error=glGetError(); bool pass=error==0 && pixel[0]<5 && pixel[1]>250 && pixel[2]<5;
        return new { vendor=vendor,renderer=renderer,version=version,draw_test_passed=pass,gl_error=error,pixel=pixel,pixel_format=new { index=pf,flags=pfd.flags,color_bits=pfd.colorBits,depth_bits=pfd.depthBits },procedure="64x64 OpenGL triangle draw, finish and pixel readback" };
      } finally { wglMakeCurrent(IntPtr.Zero,IntPtr.Zero); if(rc!=IntPtr.Zero) wglDeleteContext(rc); ReleaseDC(handle,dc); }
    }
  }
  public static object Run() {
    List<int> formats=PixelFormats();
    if(formats.Count==0) throw new InvalidOperationException("The display driver exposes no hardware RGBA pixel formats for windowed OpenGL.");
    string firstFailure=null, lastFailure=null;
    foreach(int pf in formats) {
      // Windows permits SetPixelFormat only once per window, including failed WGL attempts.
      try { return RunFormat(pf); }
      catch(Exception error) {
        if(firstFailure==null) firstFailure=error.Message;
        lastFailure=error.Message;
      }
    }
    throw new InvalidOperationException("OpenGL initialization failed for all " + formats.Count +
      " hardware pixel formats. First failure: " + firstFailure +
      (lastFailure==firstFailure ? "" : " Last failure: " + lastFailure));
  }
}
'@

$opengl = try { [VirtueGlProbe]::Run() } catch { @{ error=$_.Exception.GetBaseException().Message; draw_test_passed=$false } }
$vulkan = @{ output=''; devices=@(); error='vulkaninfo is not installed; install the Vulkan Runtime/SDK tools in the guest.' }
$vk = Get-Command vulkaninfo -ErrorAction SilentlyContinue
if ($vk) {
    $process = $null
    $started = $false
    try {
        $start = New-Object System.Diagnostics.ProcessStartInfo
        $start.FileName=$vk.Source; $start.Arguments='--summary'
        $start.UseShellExecute=$false; $start.CreateNoWindow=$true
        $start.RedirectStandardOutput=$true; $start.RedirectStandardError=$true
        $process = New-Object System.Diagnostics.Process
        $process.StartInfo=$start
        $started=$process.Start()
        if (-not $started) { throw 'vulkaninfo could not be started.' }
        $stdout=$process.StandardOutput.ReadToEndAsync(); $stderr=$process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(30000)) {
            $vulkan.error='vulkaninfo timed out after 30 seconds.'
        } elseif (-not [System.Threading.Tasks.Task]::WaitAll([System.Threading.Tasks.Task[]]@($stdout, $stderr), 5000)) {
            $vulkan.error='vulkaninfo did not finish writing its output.'
        } else {
            $text=$stdout.Result + $stderr.Result
            $vulkan.output=$text; $vulkan.error=if($process.ExitCode -eq 0){''}else{"Exit code $($process.ExitCode)"}
            $vulkan.devices=@([regex]::Matches($text,'deviceName\s*=\s*([^\r\n]+)') | ForEach-Object { @{ name=$_.Groups[1].Value.Trim() } })
        }
    } catch {
        $vulkan.error=$_.Exception.Message
    } finally {
        if ($process) {
            try {
                if ($started -and -not $process.HasExited) {
                    $process.Kill()
                    if (-not $process.WaitForExit(5000)) { $vulkan.error='vulkaninfo could not be stopped after its timeout.' }
                }
            } catch { $vulkan.error += " Cleanup failed: $($_.Exception.Message)" }
            finally { $process.Dispose() }
        }
    }
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
