param([Parameter(Mandatory=$true)][string]$FactoryIp,[Parameter(Mandatory=$true)][string]$AdapterId,[string]$SetupExe='build/PhoenixSetup.exe')
$ErrorActionPreference='Stop'
$principal=New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if(-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'Run in Administrator PowerShell'}
Add-Type -AssemblyName System.Web.Extensions
$root=Split-Path $PSScriptRoot -Parent
$assembly=if([IO.Path]::IsPathRooted($SetupExe)){$SetupExe}else{Join-Path $root $SetupExe}
[void][Reflection.Assembly]::LoadFrom($assembly)
$folder=Join-Path $root ('build/diagnostics/result-'+(Get-Date -Format 'yyyyMMdd-HHmmss'))
[void][IO.Directory]::CreateDirectory($folder)
$adapter=[PhoenixSetup.EthernetAdapter]::List()|Where-Object {$_.Id -eq $AdapterId}
if(@($adapter).Count -ne 1){throw 'Expected direct Ethernet adapter not found'}
$bytes=([Net.IPAddress]::Parse($FactoryIp)).GetAddressBytes()
if($bytes[0] -ne 10){throw 'Expected discovered factory IPv4'}
$bytes[3]=if($bytes[3] -eq 1){2}elseif($bytes[3] -eq 254){253}else{$bytes[3] -bxor 1}
$local=([Net.IPAddress]::new($bytes)).ToString()
$state=@{factory_ip=$FactoryIp;local_ip=$local;adapter=$AdapterId;steps=@();device_commands_sent=$false}
$alias=$null
function Save-State {[IO.File]::WriteAllText((Join-Path $folder 'diagnostic.json'),($state|ConvertTo-Json -Depth 10),[Text.UTF8Encoding]::new($false))}
function Snapshot {
 $n=[Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces()|Where-Object {$_.Id -eq $AdapterId}
 @($n.GetIPProperties().UnicastAddresses|Where-Object {$_.Address.AddressFamily -eq 'InterNetwork'}|ForEach-Object {@{address=$_.Address.ToString();state=$_.DuplicateAddressDetectionState.ToString()}})
}
try{
 $state.before=Snapshot;Save-State
 $alias=New-Object PhoenixSetup.TemporaryIPv4 -ArgumentList @($adapter,$local,'255.255.255.0',$folder)
 for($attempt=0;$attempt -lt 8;$attempt++){
  $step=@{attempt=$attempt;addresses=(Snapshot);bind='pending';connection='not-tested'}
  $client=New-Object Net.Sockets.TcpClient([Net.Sockets.AddressFamily]::InterNetwork)
  try{
   $client.Client.Bind([Net.IPEndPoint]::new([Net.IPAddress]::Parse($local),0));$step.bind='OK'
  }catch{$errorBase=$_.Exception.GetBaseException();$step.bind=$errorBase.Message;if($errorBase -is [Net.Sockets.SocketException]){$step.bind_error_code=$errorBase.NativeErrorCode}}
  finally{$client.Dispose()}
  if($step.bind -eq 'OK'){
   $probe=$null
   try{$probe=[PhoenixSetup.DirectSocket]::Connect($adapter,$local,$FactoryIp,23,3000,[Threading.CancellationToken]::None);$step.connection='OK'}
   catch{$baseError=$_.Exception.GetBaseException();$step.connection=$baseError.Message;if($baseError -is [Net.Sockets.SocketException]){$step.connection_error_code=$baseError.NativeErrorCode}}
   finally{if($probe){$probe.Dispose()}}
  }
  $state.steps+=,$step;Save-State
  if($step.connection -eq 'OK'){break};Start-Sleep -Seconds 2
 }
}catch{$state.error=$_.Exception.GetBaseException().Message;Save-State}
finally{if($alias){$alias.Dispose()};$state.after=Snapshot;Save-State}
Write-Host ('DIAGNOSTIC COMPLETE: '+$folder)
