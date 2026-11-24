using System;
using System.IO;
using System.IO.Ports;
using System.Text;
using System.Text.RegularExpressions;
using System.Diagnostics;
using System.Threading;
using System.Threading.Tasks;
using System.Security.Cryptography;
using System.Collections.Generic;
using System.Web.Script.Serialization;

namespace PhoenixSetup {
 public static class Json {
  public static readonly JavaScriptSerializer Codec=new JavaScriptSerializer{MaxJsonLength=16*1024*1024};
  public static T Read<T>(string text){return Codec.Deserialize<T>(text);}
  public static string Write(object value){return Codec.Serialize(value);}
  public static string Sha(string path){using(var h=SHA256.Create())using(var f=File.OpenRead(path))return BitConverter.ToString(h.ComputeHash(f)).Replace("-","").ToLowerInvariant();}
 }
 public sealed class Capture {
  public string Status="waiting"; public string Buffer=""; public int Attempts; double nextCtrl, nextUnlock=double.MaxValue, firstBoot=-1;
  static readonly Regex Ansi=new Regex("\x1b\\[[0-?]*[ -/]*[@-~]");
  public static bool Prompt(string text){return Regex.IsMatch(Ansi.Replace(text,""),@"(?:^|[\r\n])\s*BOLT>\s*$");}
  public byte[] Step(double now,string data){
   if(Status=="prompt"||Status=="os-started"||Status=="timeout")return null;
   Buffer+=data;if(Buffer.Length>32768)Buffer=Buffer.Substring(Buffer.Length-32768);
   string clean=Ansi.Replace(Buffer,"");
   if(Prompt(clean)){Status="prompt";return null;}
   if(Regex.IsMatch(clean,@"Linux version |Booting Linux|Starting kernel|Starting program at|login:|Kernel panic",RegexOptions.IgnoreCase)){Status="os-started";return null;}
   if(now>=90){Status="timeout";return null;}
   if(firstBoot<0&&Regex.IsMatch(clean,@"HYBROAD|\bBOLT\b",RegexOptions.IgnoreCase)){firstBoot=now;nextUnlock=now+0.2;Status="boot-detected";}
   if(now>=nextUnlock&&Attempts<8&&now-firstBoot<=12){Attempts++;nextUnlock=now+1.5;nextCtrl=now+0.35;return Encoding.ASCII.GetBytes("28392221221\r");}
   if(now>=nextCtrl){nextCtrl=now+0.05;return new byte[]{3};}return null;
  }
 }
 public sealed class DeviceReview {
  public const string MemoryLayout="vmalloc=380m bmem=768m@1279m bmem=970m@2099m";
  public string Identity, Startup, Kernel, Rootfs,Memory=MemoryLayout,Runlevel="1";
  public bool StartupAbsent;
  public bool Compatible {get{return Identity!=null&&Identity.Contains("BCM97252SSV")&&BootSlots.Valid(Kernel)&&BootSlots.Valid(Rootfs)&&Memory==MemoryLayout&&(Runlevel=="1"||Runlevel=="4");}}
  public static string Value(string reply,string key){
   var m=Regex.Match(reply,@"(?:^|[\r\n])\s*"+Regex.Escape(key)+@"[ \t]*=[ \t]*([^\r\n]*)");
   if(m.Success)return m.Groups[1].Value.Trim();
   if(Regex.IsMatch(reply,@"(?:^|[\r\n])\s*"+Regex.Escape(key)+@"[ \t]*:[ \t]*([^\r\n]*)")){m=Regex.Match(reply,Regex.Escape(key)+@"[ \t]*:[ \t]*([^\r\n]*)");return m.Groups[1].Value.Trim();}
   if(Regex.IsMatch(reply,@"(?i)("+Regex.Escape(key)+@"[^\r\n]*(?:not (?:found|defined|set)|does not exist|undefined)|(?:not found|not defined|undefined|no such variable)[^\r\n]*"+Regex.Escape(key)+@")"))return null;
   // This BOLT build reports an absent optional variable as a successful,
   // otherwise empty command. Require its echo, status and final prompt;
   // an incomplete or failed response must still be rejected.
   if(key=="STARTUP"&&Capture.Prompt(reply)){
    bool echoed=false,success=false,onlyProtocol=true;
    foreach(string raw in reply.Split('\n')){
     string line=raw.Trim();
     if(line=="printenv STARTUP"||line=="BOLT> printenv STARTUP")echoed=true;
     else if(Regex.IsMatch(line,@"^\*\*\* command status\s*=\s*0$"))success=true;
     else if(line!=""&&line!="BOLT>")onlyProtocol=false;
    }
    if(echoed&&success&&onlyProtocol)return null;
   }
   throw new InvalidDataException("Cannot interpret printenv "+key+"; inspect the session log.");
  }
  public static string ListingValue(string reply,string key){
   if(!Regex.IsMatch(reply,@"(?m)^\s*KERNEL\s+[=:]?\s*\S+")||!Regex.IsMatch(reply,@"(?m)^\s*ROOTFS\s+[=:]?\s*\S+"))throw new InvalidDataException("Incomplete BOLT environment listing");
   var match=Regex.Match(reply,@"(?m)^[ \t]*"+Regex.Escape(key)+@"(?:[ \t]+([^\r\n]*))?[ \t]*$");
   return match.Success?match.Groups[1].Value.Trim():null;
  }
 }
 public static class RetryProtocol {
  public static string Reply(Dictionary<string,object> evt,bool retry){
   if(!evt.ContainsKey("protocol_version")||Convert.ToInt32(evt["protocol_version"])!=1||!evt.ContainsKey("retry_id")||!Regex.IsMatch(Convert.ToString(evt["retry_id"]),@"^[0-9a-f]{16}$"))throw new IOException("Invalid repair checkpoint; no retry command sent");
   return "PHOENIX_RETRY "+Convert.ToBase64String(Encoding.UTF8.GetBytes(Json.Write(new{retry_id=Convert.ToString(evt["retry_id"]),retry=retry})));
  }
 }
 public static class BootSlots {
  public static bool Valid(string value){return value=="0"||value=="1";}
  public static string Entry(string kind,string kernel,string root){if((kind!="i"&&kind!="b"&&kind!="d"&&kind!="u")||!Valid(kernel)||!Valid(root))throw new InvalidDataException("Unsupported firmware slot selection");return "phoenix/"+kind+kernel+root+".txt";}
  public static string Startup(string kernel,string root){return "waitusb -t=10 && batch -fatfs usbdisk0:"+Entry("b",kernel,root);}
  public static bool VerifyReported(Dictionary<string,object> evt,DeviceReview expected){if(!evt.ContainsKey("kernel_slot")&&!evt.ContainsKey("root_slot"))return false;if(!evt.ContainsKey("kernel_slot")||!evt.ContainsKey("root_slot")||Convert.ToString(evt["kernel_slot"])!=expected.Kernel||Convert.ToString(evt["root_slot"])!=expected.Rootfs)throw new IOException("Running firmware slots differ from the reviewed selection. Installation stopped.");return true;}
 }
 public interface ISerialWire:IDisposable {
  string ReadExisting();void Write(byte[] data,int offset,int count);void DiscardInBuffer();void Close();
 }
 public sealed class ComWire:ISerialWire {
  readonly SerialPort port;
  public ComWire(string name){port=new SerialPort(name,115200,Parity.None,8,StopBits.One){Handshake=Handshake.None,DtrEnable=false,RtsEnable=false,ReadTimeout=40,WriteTimeout=1000};try{port.Open();}catch{port.Dispose();throw;}}
  public string ReadExisting(){return port.ReadExisting();}
  public void Write(byte[] bytes,int offset,int count){port.Write(bytes,offset,count);}
  public void DiscardInBuffer(){port.DiscardInBuffer();}
  public void Close(){port.Close();}
  public void Dispose(){port.Dispose();}
 }
 public sealed class SerialLink:IDisposable {
  readonly ISerialWire port; readonly Action<string> log; readonly StreamWriter raw; CancellationToken cancel;
  public bool PrivateInput; public string Captured="";
  public SerialLink(string name,Action<string> output,string folder,CancellationToken token):this(new ComWire(name),output,folder,token){}
  public SerialLink(ISerialWire wire,Action<string> output,string folder,CancellationToken token){
   port=wire;log=output;cancel=token;try{Directory.CreateDirectory(folder);raw=new StreamWriter(Path.Combine(folder,"serial.log"),false,new UTF8Encoding(false)){AutoFlush=true};}catch{wire.Dispose();throw;}
  }
  public void SetCancellation(CancellationToken token){cancel=token;}
  public string Read(){cancel.ThrowIfCancellationRequested();string s=port.ReadExisting();if(s.Length>0&&!PrivateInput){raw.Write(s);log(s);}return s;}
  void Write(byte[] bytes){cancel.ThrowIfCancellationRequested();port.Write(bytes,0,bytes.Length);}
  public void Line(string text){Write(Encoding.UTF8.GetBytes(text+"\r"));}
  public DeviceReview CaptureAndInspect(){
   CapturePrompt();return Inspect();
  }
  public void CapturePrompt(){
   var watch=Stopwatch.StartNew();var capture=new Capture();
   while(true){string s=Read();var tx=capture.Step(watch.Elapsed.TotalSeconds,s);if(tx!=null)Write(tx);
    if(capture.Status=="prompt")break;
    if(capture.Status=="timeout"||capture.Status=="os-started")throw new IOException("BOLT capture stopped: "+capture.Status+". Power off before retrying.");Thread.Sleep(10);
   }
   Captured=capture.Buffer;
  }
  public string Command(string command,int seconds){
   if(command.IndexOfAny(new[]{'\r','\n'})>=0)throw new ArgumentException("Command must be one line");
   port.DiscardInBuffer();Line(command);var watch=Stopwatch.StartNew();var text=new StringBuilder();
   while(watch.Elapsed.TotalSeconds<seconds){text.Append(Read());if(Capture.Prompt(text.ToString()))return text.ToString();Thread.Sleep(10);}
   throw new TimeoutException("No BOLT response to "+command.Split(' ')[0]);
  }
  public DeviceReview Inspect(){
   string devices=Command("show devices",10), startup=Command("printenv STARTUP",10),kernel=Command("printenv KERNEL",10),rootfs=Command("printenv ROOTFS",10),memory=Command("printenv MEMCONFIG",10),level=Command("printenv RUNLEVEL",10);
   string previous;try{previous=DeviceReview.Value(startup,"STARTUP");}catch(InvalidDataException){previous=DeviceReview.ListingValue(Command("printenv",10),"STARTUP");}return new DeviceReview{Identity=Captured+devices,Startup=previous,StartupAbsent=previous==null,Kernel=DeviceReview.Value(kernel,"KERNEL"),Rootfs=DeviceReview.Value(rootfs,"ROOTFS"),Memory=DeviceReview.Value(memory,"MEMCONFIG"),Runlevel=DeviceReview.Value(level,"RUNLEVEL")};
  }
  void NormalFallback(){Command("setenv -p RUNLEVEL 1",10);if(DeviceReview.Value(Command("printenv RUNLEVEL",10),"RUNLEVEL")!="1")throw new IOException("RUNLEVEL fallback readback failed; boot stopped");}
  public void BootInstaller(DeviceReview expected){var current=Inspect();if(!current.Compatible||current.Kernel!=expected.Kernel||current.Rootfs!=expected.Rootfs)throw new IOException("Device firmware selection changed; review again");NormalFallback();Command("waitusb -t=10",20);Line("batch -fatfs usbdisk0:"+BootSlots.Entry("u",current.Kernel,current.Rootfs));}
  public void BootDiagnostics(DeviceReview device){Command("waitusb -t=10",20);Line("batch -fatfs usbdisk0:"+BootSlots.Entry("d",device.Kernel,device.Rootfs));
   var watch=Stopwatch.StartNew();string output="";
   while(watch.Elapsed.TotalSeconds<180){output+=Read();if(output.Contains("PHOENIX_DIAGNOSTICS_READY"))return;if(output.Contains("PHOENIX_STAGE1_STOPPED")||output.Contains("Kernel panic")||Capture.Prompt(output))throw new IOException("Diagnostic Linux boot stopped. Check serial.log and prepare the latest USB payload.");if(output.Length>4*1024*1024)throw new IOException("Oversized diagnostic boot log");Thread.Sleep(20);}
   throw new TimeoutException("Diagnostic Linux did not report readiness. COM port released; inspect the logs or connect a terminal.");
  }
  public void MonitorInstaller(Func<Dictionary<string,object>,string> credentials,Action<Dictionary<string,object>> progress,bool ignoreVendorHashes=false,Func<string> recoveryKey=null,DeviceReview expectedSlots=null,Func<Dictionary<string,object>,bool> retry=null){
   string pending="";bool sent=false,policySent=false,slotsVerified=expectedSlots==null;var quiet=Stopwatch.StartNew();
   while(quiet.Elapsed.TotalMinutes<15){string s=Read();if(s.Length>0)quiet.Restart();pending+=s;if(pending.Length>262144)throw new IOException("Oversized serial line");int at;
    while((at=pending.IndexOf('\n'))>=0){string line=pending.Substring(0,at).TrimEnd('\r');pending=pending.Substring(at+1);
     if(line.Contains("PHOENIX_STAGE1_STOPPED:")||line.Contains("PHOENIX_BOOTSTRAP_STOPPED:")||line.Contains("Kernel panic"))throw new IOException("Early Linux bootstrap stopped: "+line.Trim()+"; review serial.log");
     int marker=line.IndexOf("PHOENIX_EVENT ",StringComparison.Ordinal);if(marker<0)continue;
     var evt=Json.Read<Dictionary<string,object>>(line.Substring(marker+14));string kind=Convert.ToString(evt["event"]);
     if(expectedSlots!=null&&BootSlots.VerifyReported(evt,expectedSlots))slotsVerified=true;
     if(kind=="need-retry"){if(!slotsVerified||retry==null)throw new IOException("Retry requires the current app and verified firmware slots");raw.WriteLine("PHOENIX_EVENT "+Json.Write(evt));Line(RetryProtocol.Reply(evt,retry(evt)));if(evt.ContainsKey("code")&&Convert.ToString(evt["code"])=="configuration")sent=false;quiet.Restart();continue;}
     if(kind=="need-recovery-key"){if(!slotsVerified||recoveryKey==null||Convert.ToInt32(evt["protocol_version"])!=2)throw new IOException("Recovery access requires the updated app and verified running firmware slots");Line("PHOENIX_RECOVERY_KEY "+Convert.ToBase64String(Encoding.ASCII.GetBytes(recoveryKey())));continue;}
     if(kind=="need-policy"){
      if(policySent||sent||!slotsVerified)throw new IOException("Unexpected policy request or firmware slots not verified");
      if(!evt.ContainsKey("protocol_version")||Convert.ToInt32(evt["protocol_version"])!=2)throw new IOException("Incompatible USB installer protocol; prepare the updated USB payload");
      Line("PHOENIX_POLICY "+Convert.ToBase64String(Encoding.UTF8.GetBytes(Json.Write(new{ignore_sha_mismatches=ignoreVendorHashes}))));policySent=true;
     }
     else if(kind=="need-config"){
      if(!policySent)throw new IOException("USB installer is out of date; prepare the updated payload before sending credentials");
      if(sent)throw new IOException("Repeated credentials request; stopped");if(!evt.ContainsKey("echo_disabled")||!Convert.ToBoolean(evt["echo_disabled"]))throw new IOException("Device did not disable serial echo; refusing secrets");PrivateInput=true;
      string config=credentials(evt);if(config==null)throw new OperationCanceledException();
      Line("PHOENIX_CONFIG "+Convert.ToBase64String(Encoding.UTF8.GetBytes(config)));config=null;sent=true;
     }
     else {progress(evt);raw.WriteLine("PHOENIX_EVENT "+Json.Write(evt));}
     if(kind=="failed"){string message=Convert.ToString(evt["message"]);if(evt.ContainsKey("code")&&Convert.ToString(evt["code"])=="existing-installation")throw ExistingInstallationException.FromEvent(evt);throw new IOException(message);}
     if(kind=="complete"){if(!policySent||!sent)throw new IOException("Installer completion arrived before configuration was accepted");PrivateInput=false;return;}
    }
    if(pending.Contains("PHOENIX_STAGE1_STOPPED")||pending.Contains("PHOENIX_BOOTSTRAP_STOPPED")||pending.Contains("Kernel panic"))throw new IOException("Early Linux bootstrap stopped; review serial.log");
    if(Capture.Prompt(pending))throw new IOException("Returned to BOLT before installation completed");Thread.Sleep(20);
   }throw new TimeoutException("No installer activity for 15 minutes; log saved");
  }
  public void CommitStartup(DeviceReview expected){
   var current=Inspect();if(!current.Compatible||current.Startup!=expected.Startup||current.StartupAbsent!=expected.StartupAbsent||current.Kernel!=expected.Kernel||current.Rootfs!=expected.Rootfs)throw new IOException("Device, firmware slots or STARTUP changed; review again");
   string value=BootSlots.Startup(current.Kernel,current.Rootfs);NormalFallback();
   Command("setenv -p STARTUP \""+value+"\"",10);
   var verified=Inspect();if(verified.Startup!=value||verified.Kernel!=expected.Kernel||verified.Rootfs!=expected.Rootfs||verified.Runlevel!="1"||!verified.Compatible)throw new IOException("Boot settings readback differs. Do not power off until recovery is reviewed.");
  }
  public void Dispose(){try{port.Close();}finally{port.Dispose();raw.Dispose();}}
 }
 public static class TerminalUtilities {
  public static void Serial(ISerialWire wire,bool linux,string folder,CancellationToken token,Action<string> output){
   using(var connection=new SerialLink(wire,output,folder,token)){
    connection.CapturePrompt();
    if(linux){var device=connection.Inspect();if(!device.Compatible)throw new IOException("Unsupported board, memory or active slots; diagnostic Linux was not booted.");connection.BootDiagnostics(device);}
   }
  }
 }
 public sealed class UsbDisk {
  public const long MaximumSize=64L*1024*1024*1024;
  public int Number; public string UniqueId, FriendlyName, Drive, FileSystem, PartitionStyle;public long Size;public int PartitionNumber;public bool IsBoot,IsSystem;public string BusType;
  public override string ToString(){return String.Format("{0}:  {1}  ({2:0.0} GB, disk {3}, {4})",Drive,FriendlyName,Size/1000000000.0,Number,FileSystem);}
  public void Guard(bool allowUnformatted=false){if(Size<=0||Size>MaximumSize||BusType!="USB"||IsBoot||IsSystem||String.IsNullOrWhiteSpace(UniqueId)||(!Regex.IsMatch(Drive??"",@"^[D-Z]$")&&!(allowUnformatted&&String.IsNullOrEmpty(Drive))))throw new IOException("Disk is not an eligible USB data volume");}
 }
 public sealed class PayloadFile {public string Path,Sha256;public long Bytes;}
 public sealed class PayloadManifest {public int Format;public int ProtocolVersion;public string Release;public bool HardwareValidated;public List<PayloadFile> Files;}
 public static class ProcessRunner {
  static void Stop(Process process){try{if(!process.HasExited)process.Kill();}catch(InvalidOperationException){}catch(System.ComponentModel.Win32Exception){}}
  public static string Execute(string executable,string arguments,int timeoutMs,CancellationToken token){
   token.ThrowIfCancellationRequested();
   using(var process=new Process{StartInfo=new ProcessStartInfo(executable,arguments){UseShellExecute=false,CreateNoWindow=true,RedirectStandardOutput=true,RedirectStandardError=true}}){
    process.Start();var output=process.StandardOutput.ReadToEndAsync();var errors=process.StandardError.ReadToEndAsync();var watch=Stopwatch.StartNew();
    using(token.Register(()=>Stop(process))){
     while(!process.WaitForExit(50)){
      token.ThrowIfCancellationRequested();
      if(watch.ElapsedMilliseconds>=timeoutMs){Stop(process);throw new TimeoutException("Windows helper did not respond in "+(timeoutMs/1000)+" seconds. The helper was stopped; review the log before retrying.");}
     }
     token.ThrowIfCancellationRequested();
     if(!Task.WaitAll(new Task[]{output,errors},2000))throw new IOException("Windows helper exited but its output streams did not close.");
     if(process.ExitCode!=0)throw new IOException(String.IsNullOrWhiteSpace(errors.Result)?"Windows helper stopped (exit "+process.ExitCode+").":errors.Result.Trim());
     return output.Result;
    }
   }
  }
 }
 public static class SshHostKey {
  static void Address(string address){System.Net.IPAddress ip;if(!System.Net.IPAddress.TryParse(address,out ip)||ip.AddressFamily!=System.Net.Sockets.AddressFamily.InterNetwork||ip.ToString()!=address)throw new IOException("Expected an IPv4 SSH host address");}
  static string Quote(string value){var result=new StringBuilder("\"");int slashes=0;foreach(char c in value){if(c=='\\'){slashes++;continue;}if(c=='\"'){result.Append('\\',slashes*2+1).Append(c);slashes=0;continue;}result.Append('\\',slashes).Append(c);slashes=0;}return result.Append('\\',slashes*2).Append('"').ToString();}
  public static string Arguments(string address,string knownHosts){
   Address(address);
   return "-F NUL -N -T -p 22 -o KexAlgorithms=curve25519-sha256 -o HostKeyAlgorithms=ssh-ed25519 -o StrictHostKeyChecking=accept-new -o GlobalKnownHostsFile=NUL -o "+Quote("UserKnownHostsFile=\""+knownHosts.Replace('\\','/')+"\"")+" -o HashKnownHosts=no -o UpdateHostKeys=no -o BatchMode=yes -o PreferredAuthentications=none -o PubkeyAuthentication=no -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no -o GSSAPIAuthentication=no -o HostbasedAuthentication=no -o IdentityAgent=none -o ClearAllForwardings=yes -o PermitLocalCommand=no -o ConnectTimeout=8 -o ConnectionAttempts=1 -o NumberOfPasswordPrompts=0 root@"+address;
  }
  public static string Parse(string address,string text){
   Address(address);string result=null;
   foreach(string line in text.Split('\n')){
    string[] fields=line.Trim().Split(new[]{' ','\t'},StringSplitOptions.RemoveEmptyEntries);
    if(fields.Length!=3||fields[0]!=address||fields[1]!="ssh-ed25519")continue;
    byte[] key;try{key=Convert.FromBase64String(fields[2]);}catch(FormatException){throw new IOException("Invalid SSH host key encoding");}
    if(key.Length!=51||key[0]!=0||key[1]!=0||key[2]!=0||key[3]!=11||Encoding.ASCII.GetString(key,4,11)!="ssh-ed25519"||key[15]!=0||key[16]!=0||key[17]!=0||key[18]!=32)throw new IOException("Invalid Ed25519 SSH host key");
    string candidate=address+" ssh-ed25519 "+Convert.ToBase64String(key)+"\n";
    if(result!=null&&result!=candidate)throw new IOException("Multiple SSH host keys received");result=candidate;
   }
   if(result==null)throw new IOException("No Ed25519 SSH host key received at this address");return result;
  }
  public static string Probe(string address,CancellationToken token,Action<string,string,CancellationToken> runner=null){
   Address(address);token.ThrowIfCancellationRequested();
   string folder=Path.Combine(Path.GetTempPath(),"phoenix-host-key-"+Guid.NewGuid().ToString("N")),cache=Path.Combine(folder,"known_hosts");
   Directory.CreateDirectory(folder);
   try{
    // This disposable file discovers an untrusted key. The caller must compare
    // its fingerprint with the saved device identity or ask the user to trust it.
    try{
     if(runner!=null)runner(address,cache,token);
     else{string ssh=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System),"OpenSSH","ssh.exe");if(!File.Exists(ssh))throw new IOException("Windows OpenSSH Client is required to discover the SSH host identity");ProcessRunner.Execute(ssh,Arguments(address,cache),12000,token);}
    }catch(IOException){if(!File.Exists(cache))throw;}catch(TimeoutException){if(!File.Exists(cache))throw;}
    // Authentication failure is expected: no credentials are offered and no
    // remote command/session is requested. The host key arrives before login.
    token.ThrowIfCancellationRequested();return Parse(address,File.ReadAllText(cache));
   }finally{if(File.Exists(cache))File.Delete(cache);Directory.Delete(folder);}
  }
 }
 public static class MaintenanceKeys {
  public static string ReuseOrGenerate(string session,CancellationToken token){
   string path=Path.Combine(session,"keys","maintenance_ed25519");
   if(!File.Exists(path)&&!File.Exists(path+".pub"))return Generate(session,token);
   token.ThrowIfCancellationRequested();
   string generator=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System),"OpenSSH","ssh-keygen.exe");
   string key=ProcessRunner.Execute(generator,"-y -P \"\" -f \""+path+"\"",10000,token).Trim();
   if(!Regex.IsMatch(key,@"^ssh-ed25519 [A-Za-z0-9+/=]+$"))throw new IOException("The saved maintenance key is invalid");return key;
  }
  public static string Generate(string session,CancellationToken token=default(CancellationToken)){
   string generator=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System),"OpenSSH","ssh-keygen.exe");
   if(!File.Exists(generator))throw new IOException("Windows OpenSSH Client is required to create the maintenance key. Enable OpenSSH Client in Windows Optional features and retry.");
   token.ThrowIfCancellationRequested();string folder=Path.Combine(session,"keys");Directory.CreateDirectory(folder);string path=Path.Combine(folder,"maintenance_ed25519");
   if(File.Exists(path)||File.Exists(path+".pub"))throw new IOException("A maintenance key already exists in this session folder; it will not be overwritten.");
   ProcessRunner.Execute(generator,"-q -t ed25519 -N \"\" -C phoenix-setup -f \""+path+"\"",15000,token);
   string publicKey=File.ReadAllText(path+".pub").Trim();
   if(!File.Exists(path)||!Regex.IsMatch(publicKey,@"^ssh-ed25519 [A-Za-z0-9+/=]+(?: .*)?$"))throw new IOException("Maintenance key generation did not produce a valid key pair.");
   return publicKey;
  }
 }
 public sealed class RecoveryAccess {
  public bool ready;public string address,host_fingerprint,error;public int port;
  public bool Valid{get{System.Net.IPAddress parsed;return ready&&port==22&&System.Net.IPAddress.TryParse(address,out parsed)&&parsed.AddressFamily==System.Net.Sockets.AddressFamily.InterNetwork&&Regex.IsMatch(host_fingerprint??"",@"^SHA256:[A-Za-z0-9+/]+={0,2}$");}}
  public string Instructions(string keyPath){if(!Valid)return "SSH is unavailable in the temporary environment. "+error;return "SSH to the temporary recovery environment (not the installed system):\r\nHost: "+address+"    Port: 22    User: root\r\nPrivate key: "+keyPath+"\r\nHost fingerprint: "+host_fingerprint+"\r\nVerify this fingerprint when SSH asks to trust the host.\r\nssh -i \""+keyPath+"\" -o KexAlgorithms=curve25519-sha256 -p 22 root@"+address+"\r\n\r\nHDD: /mnt/recording-disk (read-only). No existing files were replaced.\r\nKeep this app open if using the direct Ethernet cable. Close SSH before releasing access.";}
 }
 public sealed class ExistingInstallationException:IOException {
  public RecoveryAccess Access;public ExistingInstallationException(string message):base(message){}
  public static ExistingInstallationException FromEvent(Dictionary<string,object> evt){return new ExistingInstallationException(Convert.ToString(evt["message"])){Access=evt.ContainsKey("access")?Json.Read<RecoveryAccess>(Json.Write(evt["access"])):null};}
 }
 // Helper scripts ship as plain files in scripts\ next to PhoenixSetup.exe. Each is checked against the
 // SHA-256 compiled into the EXE (build\ScriptHashes.cs, generated by tools\build.ps1) before it is used.
 public static class SetupScripts {
  public static string Folder{get{return System.IO.Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"scripts");}}
  public static byte[] Load(string name,string expectedSha256){
   string path=System.IO.Path.Combine(Folder,name);
   if(!File.Exists(path))throw new IOException("Phoenix Setup is missing scripts\\"+name+". Extract the whole Phoenix Setup zip into one folder and run PhoenixSetup.exe from there.");
   byte[] data=File.ReadAllBytes(path);string actual;
   using(var hash=SHA256.Create())actual=BitConverter.ToString(hash.ComputeHash(data)).Replace("-","").ToLowerInvariant();
   if(actual!=expectedSha256)throw new InvalidDataException("scripts\\"+name+" has been changed or damaged. Extract the Phoenix Setup zip again.");
   return data;
  }
 }
 public static class Usb {
  public static string PowerShell(string code,int timeoutMs=20000,CancellationToken token=default(CancellationToken)){
   string executable=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System),"WindowsPowerShell","v1.0","powershell.exe");
   string arguments="-NoLogo -NoProfile -NonInteractive -OutputFormat Text -EncodedCommand "+Convert.ToBase64String(Encoding.Unicode.GetBytes("$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';try{& {"+code+"}}catch{[Console]::Error.WriteLine($_.Exception.Message);exit 1}"));
   return ProcessRunner.Execute(executable,arguments,timeoutMs,token).Trim('\uFEFF','\r','\n',' ');
  }
  public static List<UsbDisk> List(CancellationToken token=default(CancellationToken)){
   string script=@"$items=@(Get-CimInstance Win32_DiskDrive -Filter ""InterfaceType='USB' AND Size <= 68719476736 AND Size > 0"" -OperationTimeoutSec 5|ForEach-Object {$d=$_;$parts=@(Get-CimAssociatedInstance -InputObject $d -Association Win32_DiskDriveToDiskPartition -OperationTimeoutSec 5);$style=if($parts.Count -eq 0){'RAW'}elseif(@($parts|Where-Object {$_.Type -like 'GPT:*'}).Count){'GPT'}else{'MBR'};$boot=[bool]@($parts|Where-Object BootPartition).Count;$found=$false;foreach($p in $parts){foreach($v in @(Get-CimAssociatedInstance -InputObject $p -Association Win32_LogicalDiskToPartition -OperationTimeoutSec 5)){$found=$true;[pscustomobject]@{Number=[int]$d.Index;UniqueId=[string]$d.PNPDeviceID;FriendlyName=[string]$d.Model;Drive=([string]$v.DeviceID).Substring(0,1);FileSystem=[string]$v.FileSystem;PartitionStyle=$style;Size=[long]$d.Size;PartitionNumber=([int]$p.Index+1);IsBoot=$boot;IsSystem=([string]$v.DeviceID -eq $env:SystemDrive);BusType='USB'}}};if(-not $found){[pscustomobject]@{Number=[int]$d.Index;UniqueId=[string]$d.PNPDeviceID;FriendlyName=[string]$d.Model;Drive='';FileSystem='';PartitionStyle=$style;Size=[long]$d.Size;PartitionNumber=0;IsBoot=$boot;IsSystem=$false;BusType='USB'}}});ConvertTo-Json -InputObject $items -Compress";
   var found=Json.Read<List<UsbDisk>>(PowerShell(script,20000,token))??new List<UsbDisk>();return found.FindAll(d=>d.Size>0&&d.Size<=UsbDisk.MaximumSize);
  }
  public static string Resolve(string root,string relative){
   if(String.IsNullOrWhiteSpace(relative)||System.IO.Path.IsPathRooted(relative)||relative.IndexOf(':')>=0)throw new InvalidDataException("Unsafe payload path");
   string prefix=System.IO.Path.GetFullPath(root).TrimEnd('\\')+"\\",full=System.IO.Path.GetFullPath(System.IO.Path.Combine(prefix,relative.Replace('/','\\')));
   if(!full.StartsWith(prefix,StringComparison.OrdinalIgnoreCase))throw new InvalidDataException("Payload traversal");return full;
  }
  public static PayloadManifest Validate(string bundle){
   var m=Json.Read<PayloadManifest>(File.ReadAllText(System.IO.Path.Combine(bundle,"payload.json")));if(m.ProtocolVersion!=5)throw new InvalidDataException("USB payload is out of date. Prepare the complete current retry-capable USB payload.");if(m.Format!=1||m.Files==null||m.Files.Count<5)throw new InvalidDataException("Incomplete USB payload");
   var seen=new HashSet<string>(StringComparer.OrdinalIgnoreCase);
   foreach(var f in m.Files){if(!seen.Add(f.Path))throw new InvalidDataException("Duplicate payload path");string path=Resolve(bundle,f.Path);if(!File.Exists(path)||new FileInfo(path).Length!=f.Bytes||Json.Sha(path)!=f.Sha256)throw new InvalidDataException("Payload checksum mismatch: "+f.Path);if(f.Bytes>=4294967295L)throw new InvalidDataException("File exceeds FAT32 limit");if(System.IO.Path.GetFileName(path)=="SHA256SUMS")RequireLfChecksums(File.ReadAllBytes(path));}
   return m;
  }
  public static void RequireLfChecksums(byte[] data){if(data.Length==0||data[data.Length-1]!=10||Array.IndexOf(data,(byte)13)>=0||(data.Length>=3&&data[0]==239&&data[1]==187&&data[2]==191))throw new InvalidDataException("SHA256SUMS must use LF line endings with no byte-order mark. Use the updated USB payload and prepare the stick again.");}
  public static string FormattingScript(UsbDisk selected){
   string data=Convert.ToBase64String(Encoding.UTF8.GetBytes(Json.Write(selected)));
   using(var reader=new StreamReader(new MemoryStream(SetupScripts.Load("format-usb.ps1",ScriptHashes.FormatUsb))))return "$s=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('"+data+"'))|ConvertFrom-Json;"+reader.ReadToEnd();
  }
  public static void Prepare(UsbDisk selected,string bundle,bool format,CancellationToken token,Action<string> progress){
   selected.Guard(format);var m=Validate(bundle);token.ThrowIfCancellationRequested();
   var now=List(token).Find(d=>d.Number==selected.Number&&d.Drive==selected.Drive);if(now==null||now.UniqueId!=selected.UniqueId||now.Size!=selected.Size)throw new IOException("USB device changed; refresh and select again");now.Guard(format);
   if(format){
    if(!new System.Security.Principal.WindowsPrincipal(System.Security.Principal.WindowsIdentity.GetCurrent()).IsInRole(System.Security.Principal.WindowsBuiltInRole.Administrator))throw new IOException("Run PhoenixSetup as administrator to format this USB stick.");
    string script=FormattingScript(now);
    progress("Formatting the selected USB; wait for this operation to finish.");string drive=PowerShell(script,180000);token.ThrowIfCancellationRequested();if(!Regex.IsMatch(drive,@"^[D-Z]$"))throw new IOException("Could not identify formatted USB volume");now=List().Find(d=>d.Number==selected.Number&&d.UniqueId==selected.UniqueId&&d.Drive==drive);if(now==null)throw new IOException("USB identity changed after formatting");
   }
   if(now.FileSystem!="FAT32"||now.PartitionStyle!="MBR"||now.PartitionNumber!=1)throw new IOException("BOLT requires the first partition on an MBR/FAT32 USB. Select format, or prepare that layout first.");
   string target=now.Drive+":\\";if(Directory.Exists(System.IO.Path.Combine(target,"phoenix")))throw new IOException("This stick already contains phoenix. Use another stick or archive it yourself.");
   long bytes=0;foreach(var f in m.Files)bytes+=f.Bytes;if(new DriveInfo(target).AvailableFreeSpace<bytes+32L*1024*1024)throw new IOException("Insufficient USB free space");
   foreach(var f in m.Files){token.ThrowIfCancellationRequested();string dest=Resolve(target,f.Path);Directory.CreateDirectory(System.IO.Path.GetDirectoryName(dest));progress("Copying "+f.Path);File.Copy(Resolve(bundle,f.Path),dest,false);using(var flush=new FileStream(dest,FileMode.Open,FileAccess.ReadWrite,FileShare.Read)){flush.Flush(true);}if(Json.Sha(dest)!=f.Sha256)throw new IOException("USB readback failed: "+f.Path);}
   File.Copy(System.IO.Path.Combine(bundle,"payload.json"),System.IO.Path.Combine(target,"payload.json"),false);progress("USB prepared and readback verified. Safely eject it in Windows.");
  }
 }
}
