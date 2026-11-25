param([string]$Output=(Join-Path $PSScriptRoot '..\build'),[string]$Python='python')
$ErrorActionPreference='Stop'
$sourceRoot=Split-Path $PSScriptRoot -Parent
$csc=Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $csc)) {throw '.NET Framework C# compiler was not found'}
New-Item -ItemType Directory -Force -Path $Output | Out-Null
$Output=(Resolve-Path -LiteralPath $Output).Path
$exe=Join-Path $Output 'PhoenixSetup.exe'

# Helper scripts ship as plain files in scripts\ beside the EXE (not embedded resources).
# The EXE pins their SHA-256 so a changed or damaged copy is refused at run time.
function Get-Sha([string]$Path){(Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()}
$scripts=Join-Path $Output 'scripts'
New-Item -ItemType Directory -Force -Path $scripts | Out-Null
Copy-Item -LiteralPath (Join-Path $sourceRoot 'src\format-usb.ps1') -Destination (Join-Path $scripts 'format-usb.ps1') -Force
Copy-Item -LiteralPath (Join-Path $sourceRoot 'src\update.py') -Destination (Join-Path $scripts 'update.py') -Force
$hashFile=Join-Path $Output 'ScriptHashes.cs'
$hashSource='namespace PhoenixSetup{static class ScriptHashes{public const string FormatUsb="'+(Get-Sha (Join-Path $scripts 'format-usb.ps1'))+'";public const string UpdateWorker="'+(Get-Sha (Join-Path $scripts 'update.py'))+'";}}'
[IO.File]::WriteAllText($hashFile,$hashSource,(New-Object Text.UTF8Encoding($false)))

# Application manifest: runs as invoker (the app relaunches itself elevated when it needs to), declares Windows 10/11.
$manifest=Join-Path $Output 'app.manifest'
$manifestXml=@'
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
  <assemblyIdentity version="2026.11.26.30" processorArchitecture="amd64" name="Phoenix.Setup" type="win32"/>
  <description>Phoenix Setup</description>
  <trustInfo xmlns="urn:schemas-microsoft-com:asm.v3">
    <security><requestedPrivileges><requestedExecutionLevel level="asInvoker" uiAccess="false"/></requestedPrivileges></security>
  </trustInfo>
  <compatibility xmlns="urn:schemas-microsoft-com:compatibility.v1">
    <application><supportedOS Id="{8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a}"/></application>
  </compatibility>
</assembly>
'@
[IO.File]::WriteAllText($manifest,$manifestXml,(New-Object Text.UTF8Encoding($false)))

# Program icon built from the logo already in src\Logo.cs (the source export carries no image files).
Add-Type -AssemblyName System.Drawing
$icon=Join-Path $Output 'app.ico'
$logoText=[IO.File]::ReadAllText((Join-Path $sourceRoot 'src\Logo.cs'))
$match=[regex]::Match($logoText,'Png\s*=\s*((?:"[A-Za-z0-9+/=]*"\s*\+?\s*)+);')
if (-not $match.Success) {throw 'Logo data not found in src\Logo.cs'}
$logoBase64=(([regex]::Matches($match.Groups[1].Value,'"([A-Za-z0-9+/=]*)"')) | ForEach-Object {$_.Groups[1].Value}) -join ''
$logo=[Drawing.Image]::FromStream((New-Object IO.MemoryStream(,[Convert]::FromBase64String($logoBase64))))
$images=@()
foreach ($size in 16,24,32,48,64,256) {
  $bitmap=New-Object Drawing.Bitmap($size,$size,[Drawing.Imaging.PixelFormat]::Format32bppArgb)
  $g=[Drawing.Graphics]::FromImage($bitmap);$g.Clear([Drawing.Color]::Transparent)
  $g.InterpolationMode=[Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic;$g.SmoothingMode=[Drawing.Drawing2D.SmoothingMode]::HighQuality;$g.PixelOffsetMode=[Drawing.Drawing2D.PixelOffsetMode]::HighQuality
  $scale=[Math]::Min($size/$logo.Width,$size/$logo.Height);$w=[int][Math]::Round($logo.Width*$scale);$h=[int][Math]::Round($logo.Height*$scale)
  $g.DrawImage($logo,[int](($size-$w)/2),[int](($size-$h)/2),$w,$h);$g.Dispose()
  $png=New-Object IO.MemoryStream;$bitmap.Save($png,[Drawing.Imaging.ImageFormat]::Png);$bitmap.Dispose()
  $images+=,@($size,$png.ToArray())
}
$logo.Dispose()
$ico=New-Object IO.MemoryStream;$writer=New-Object IO.BinaryWriter($ico)
$writer.Write([UInt16]0);$writer.Write([UInt16]1);$writer.Write([UInt16]$images.Count)
$offset=6+16*$images.Count
foreach ($image in $images) {
  $dimension=if($image[0] -ge 256){0}else{$image[0]}
  $writer.Write([byte]$dimension);$writer.Write([byte]$dimension);$writer.Write([byte]0);$writer.Write([byte]0)
  $writer.Write([UInt16]1);$writer.Write([UInt16]32);$writer.Write([UInt32]$image[1].Length);$writer.Write([UInt32]$offset)
  $offset+=$image[1].Length
}
foreach ($image in $images) {$writer.Write([byte[]]$image[1])}
$writer.Flush();[IO.File]::WriteAllBytes($icon,$ico.ToArray());$writer.Dispose()

$keyResource='/resource:'+(Join-Path $sourceRoot 'src\update-public-key.json')+',PhoenixSetup.UpdatePublicKey'
$sources=@('src\AssemblyInfo.cs','src\Core.cs','src\EthernetDiscovery.cs','src\EthernetTransport.cs','src\Updates.cs','src\Theme.cs','src\Logo.cs','src\Program.cs','tests\SelfTests.cs','tests\UpdateTests.cs') | ForEach-Object {Join-Path $sourceRoot $_}
& $csc /nologo /codepage:65001 /target:winexe /platform:x64 /debug- /optimize+ /r:System.Windows.Forms.dll /r:System.Drawing.dll /r:System.Web.Extensions.dll "/win32icon:$icon" "/win32manifest:$manifest" /out:$exe $keyResource $hashFile $sources
if ($LASTEXITCODE -ne 0) {throw 'Compilation failed'}
& $Python (Join-Path $PSScriptRoot 'verify-release.py') $exe --report (Join-Path $Output 'release-hygiene.json')
if ($LASTEXITCODE -ne 0) {throw 'Release hygiene check failed; compiled artifact must not be packaged'}
$check=Start-Process -FilePath $exe -ArgumentList '--self-test' -WindowStyle Hidden -Wait -PassThru
if ($check.ExitCode -ne 0) {throw 'Core tests failed; see self-tests.json'}
$ui=Start-Process -FilePath $exe -ArgumentList '--ui-self-test' -WindowStyle Hidden -PassThru
if (-not $ui.WaitForExit(10000)) {Stop-Process -Id $ui.Id;throw 'UI discovery responsiveness test hung'}
if ($ui.ExitCode -ne 0) {throw 'UI responsiveness test failed; see ui-self-test.json'}
Write-Output "Built and tested: $exe (with scripts\format-usb.ps1 and scripts\update.py)"

& (Join-Path $sourceRoot 'tests\test-format-usb.ps1')
