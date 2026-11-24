using System;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;
using System.Net;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Collections.Generic;
using System.Threading;
using System.Diagnostics;
using System.Security.Cryptography;

namespace PhoenixSetup {
 public static class AddressReadiness {
  public static void Wait(Func<string> state,Func<bool> bind,CancellationToken token,int timeoutMs=30000,Action pause=null,Func<long> milliseconds=null){
   var watch=Stopwatch.StartNew();Func<long> clock=milliseconds??(()=>watch.ElapsedMilliseconds);long deadline=clock()+timeoutMs;string last="Missing";
   while(clock()<=deadline){
    token.ThrowIfCancellationRequested();last=state();
    if(last=="Duplicate")throw new IOException("Windows detected a duplicate temporary Ethernet address. No device settings were changed.");
    if(last=="Preferred"&&bind())return;
    if(pause!=null)pause();else if(token.WaitHandle.WaitOne(100))token.ThrowIfCancellationRequested();
   }
   throw new TimeoutException("Windows did not make the temporary Ethernet address usable within "+(timeoutMs/1000)+" seconds (state: "+last+"). Check the selected port and direct cable.");
  }
 }
 public sealed class TemporaryIPv4:IDisposable {
  [DllImport("iphlpapi.dll",ExactSpelling=true)]static extern uint AddIPAddress(uint address,uint mask,uint index,out uint context,out uint instance);
  [DllImport("iphlpapi.dll",ExactSpelling=true)]static extern uint DeleteIPAddress(uint context);
  uint context;bool owned;readonly string receipt;readonly EthernetAdapter adapter;public readonly string Address;
  public TemporaryIPv4(EthernetAdapter selected,string address,string mask,string folder):this(selected,address,mask,folder,CancellationToken.None){}
  public TemporaryIPv4(EthernetAdapter selected,string address,string mask,string folder,CancellationToken token,bool reuseExisting=false){
   adapter=selected;
   adapter.Verify();Address=IPAddress.Parse(address).ToString();receipt=Path.Combine(folder,"pc-address-"+address+".json");
   foreach(var n in System.Net.NetworkInformation.NetworkInterface.GetAllNetworkInterfaces())foreach(var a in n.GetIPProperties().UnicastAddresses)if(a.Address.ToString()==Address){
    if(!reuseExisting||!String.Equals(n.Id,adapter.Id,StringComparison.OrdinalIgnoreCase)||a.IPv4Mask.ToString()!=mask)throw new IOException("The temporary setup address already exists on an incompatible connection. Check the selected port and close other setup sessions.");
    AddressReadiness.Wait(State,Bindable,token);Save("reused-existing-address",0);return;
   }
   uint instance;uint code=AddIPAddress(BitConverter.ToUInt32(IPAddress.Parse(Address).GetAddressBytes(),0),BitConverter.ToUInt32(IPAddress.Parse(mask).GetAddressBytes(),0),(uint)adapter.Index,out context,out instance);
   if(code!=0)throw new IOException("Cannot add temporary Ethernet address (Windows "+code+"). Run Phoenix Setup as administrator.");owned=true;
   try{
    Save("waiting-for-Windows",0);
    AddressReadiness.Wait(State,Bindable,token);
    Save("ready",0);
   }catch{
    uint cleanup=DeleteIPAddress(context);owned=cleanup!=0;
    try{Save(cleanup==0?"failed-removed":"cleanup-failed",cleanup);}catch(IOException){}
    throw;
   }
  }
  string State(){
   foreach(var n in System.Net.NetworkInformation.NetworkInterface.GetAllNetworkInterfaces())if(String.Equals(n.Id,adapter.Id,StringComparison.OrdinalIgnoreCase))foreach(var a in n.GetIPProperties().UnicastAddresses)if(a.Address.ToString()==Address)return a.DuplicateAddressDetectionState.ToString();
   return "Missing";
  }
  bool Bindable(){
   using(var socket=new Socket(AddressFamily.InterNetwork,SocketType.Stream,ProtocolType.Tcp)){
    try{socket.Bind(new IPEndPoint(IPAddress.Parse(Address),0));return true;}
    catch(SocketException e){if(e.SocketErrorCode==SocketError.AddressNotAvailable)return false;throw;}
   }
  }
  void Save(string state,uint code){
   string data=Json.Write(new{address=Address,adapter=adapter.Id,interface_index=adapter.Index,context=context,state=state,windows_code=code,persistent=false});
   for(int attempt=0;;attempt++){try{File.WriteAllText(receipt,data);return;}catch(IOException){if(attempt==4)throw;Thread.Sleep(50);}}
  }
  public void Dispose(){if(!owned)return;uint code=DeleteIPAddress(context);if(code==0)owned=false;Save(code==0?"removed":"cleanup-failed",code);}
 }
 public static class DirectSocket {
  public static TcpClient Connect(EthernetAdapter adapter,string local,string remote,int port,int timeout,CancellationToken token){
   adapter.Verify();var client=new TcpClient(AddressFamily.InterNetwork);
   try{
    // IP_UNICAST_IF is a network-byte-order interface index on Windows.
    client.Client.SetSocketOption(SocketOptionLevel.IP,(SocketOptionName)31,IPAddress.HostToNetworkOrder(adapter.Index));
    client.Client.Bind(new IPEndPoint(IPAddress.Parse(local),0));var task=client.ConnectAsync(IPAddress.Parse(remote),port);
    var watch=Stopwatch.StartNew();while(!task.IsCompleted){token.ThrowIfCancellationRequested();if(watch.ElapsedMilliseconds>=timeout)throw new TimeoutException("Direct Ethernet connection timed out");Thread.Sleep(20);}task.GetAwaiter().GetResult();return client;
   }catch{client.Close();throw;}
  }
 }
 public sealed class FactoryTerminal:IDisposable {
  readonly TcpClient client;readonly NetworkStream stream;CancellationToken token;readonly StreamWriter transcript;int telnet,option;
  public FactoryTerminal(TcpClient connection,string folder,CancellationToken cancellation){client=connection;stream=client.GetStream();token=cancellation;Directory.CreateDirectory(folder);transcript=new StreamWriter(Path.Combine(folder,"factory-terminal.log"),false,new UTF8Encoding(false)){AutoFlush=true};}
  public void SetCancellation(CancellationToken value){token=value;}
  string Read(bool privateOutput){
   token.ThrowIfCancellationRequested();if(!stream.DataAvailable){if(client.Client.Poll(0,SelectMode.SelectRead))throw new EndOfStreamException("Factory terminal disconnected");return "";}
   var bytes=new byte[8192];int count=stream.Read(bytes,0,bytes.Length);if(count==0)throw new EndOfStreamException();var result=new List<byte>();
   for(int i=0;i<count;i++){
    int b=bytes[i];if(telnet==0){if(b==255)telnet=1;else result.Add((byte)b);}
    else if(telnet==1){if(b==255){result.Add(255);telnet=0;}else if(b>=251&&b<=254){option=b;telnet=2;}else telnet=b==250?3:0;}
    else if(telnet==2){if(option==251||option==253){byte[] reply={255,(byte)(option==251?254:252),(byte)b};stream.Write(reply,0,3);}telnet=0;}
    else if(telnet==3){if(b==255)telnet=4;}else telnet=b==240?0:3;
   }
   string text=Encoding.UTF8.GetString(result.ToArray()).Replace("\r","");if(!privateOutput)transcript.Write(text);return text;
  }
  string Wait(string pattern,int seconds,bool privateOutput){var watch=Stopwatch.StartNew();var output=new StringBuilder();while(watch.Elapsed.TotalSeconds<seconds){output.Append(Read(privateOutput));if(output.Length>2*1024*1024)throw new IOException("Oversized factory response");if(Regex.IsMatch(output.ToString(),pattern,RegexOptions.Multiline|RegexOptions.IgnoreCase))return output.ToString();Thread.Sleep(10);}throw new TimeoutException("Factory terminal response timed out");}
  void Line(string text){byte[] bytes=Encoding.UTF8.GetBytes(text+"\r\n");stream.Write(bytes,0,bytes.Length);}
  public void Login(){string reply=Wait(@"login:\s*$|password:\s*$|^[^\n]*#\s*$",20,false);if(Regex.IsMatch(reply,@"login:\s*$",RegexOptions.IgnoreCase)){Line("root");reply=Wait(@"password:\s*$|^[^\n]*#\s*$",10,false);}if(Regex.IsMatch(reply,@"password:\s*$",RegexOptions.IgnoreCase)){Line("");Wait(@"^[^\n]*#\s*$",10,false);}if(Run("id -u")!="0")throw new IOException("The factory terminal is not root");Run("stty -echo");}
  public string Run(string command,int seconds=30,bool privateOutput=false){
   if(command.IndexOfAny(new[]{'\r','\n'})>=0)throw new ArgumentException("Factory command must be one line");string tag="PHX_"+Guid.NewGuid().ToString("N");
   Line("printf '\\n%s\\n' '"+tag+"_BEGIN'; "+command+"; code=$?; printf '\\n%s:%s\\n' '"+tag+"_END' \"$code\"");
   string result=Wait("^"+tag+"_END:[0-9]+\\s*$",seconds,privateOutput);var start=Regex.Match(result,"^"+tag+"_BEGIN\\s*$",RegexOptions.Multiline);var end=Regex.Match(result,"^"+tag+"_END:([0-9]+)\\s*$",RegexOptions.Multiline);
   if(!start.Success||!end.Success||end.Groups[1].Value!="0")throw new IOException("Factory command failed; see the session log");return result.Substring(start.Index+start.Length,end.Index-start.Index-start.Length).Trim();
  }
  public static string Quote(string text){return "'"+text.Replace("'","'\"'\"'")+"'";}
  public byte[] ReadFile(string path){return NvramMap.Octal(Run("od -v -b "+Quote(path),60));}
  public void Dispose(){try{client.Close();}finally{transcript.Dispose();}}
 }
 public sealed class NvramRecord {public byte Flags;public string Value;}
 public sealed class NvramMap {
  public readonly Dictionary<string,NvramRecord> Records=new Dictionary<string,NvramRecord>(StringComparer.Ordinal);
  public string Get(string key){NvramRecord r;return Records.TryGetValue(key,out r)?r.Value:null;}
  public void Set(string key,string value){NvramRecord old;Records.TryGetValue(key,out old);Records[key]=new NvramRecord{Flags=old==null?(byte)0:old.Flags,Value=value};}
  public NvramMap Copy(){var copy=new NvramMap();foreach(var p in Records)copy.Records[p.Key]=new NvramRecord{Flags=p.Value.Flags,Value=p.Value.Value};return copy;}
  public bool Same(NvramMap other){if(Records.Count!=other.Records.Count)return false;foreach(var p in Records){NvramRecord r;if(!other.Records.TryGetValue(p.Key,out r)||r.Flags!=p.Value.Flags||r.Value!=p.Value.Value)return false;}return true;}
  public static NvramMap Parse(byte[] data){
   if(data.Length<2208)throw new InvalidDataException("Short device environment");uint sum=BitConverter.ToUInt32(data,0),inverse=BitConverter.ToUInt32(data,4),size=BitConverter.ToUInt32(data,8),actual=0;
   if(size==0||size>2048||unchecked(sum+inverse)!=0xffffffff)throw new InvalidDataException("Invalid environment header");for(int i=160;i<160+size;i++)actual+=data[i];if(actual!=sum)throw new InvalidDataException("Invalid environment checksum");
   int at=160,end=160+(int)size;var map=new NvramMap();
   while(at<end&&data[at]!=0){if(data[at]!=1||at+3>=end)throw new InvalidDataException("Unknown environment record");int length=data[at+1];if(length<2||at+2+length>end)throw new InvalidDataException("Invalid environment record length");string text=Encoding.GetEncoding(28591).GetString(data,at+3,length-1);int equals=text.IndexOf('=');if(equals<=0)throw new InvalidDataException("Invalid environment record name");string key=text.Substring(0,equals);if(map.Records.ContainsKey(key))throw new InvalidDataException("Duplicate environment record");map.Records[key]=new NvramRecord{Flags=data[at+2],Value=text.Substring(equals+1)};at+=length+2;}
   if(at!=end-1||data[at]!=0)throw new InvalidDataException("Invalid environment terminator");return map;
  }
  public static byte[] Octal(string text){var result=new List<byte>();foreach(string line in text.Split('\n')){string[] parts=Regex.Split(line.Trim(),@"\s+");if(parts.Length<=1)continue;if(!Regex.IsMatch(parts[0],@"^[0-7]+$"))throw new InvalidDataException("Invalid octal file transport");for(int i=1;i<parts.Length;i++){if(!Regex.IsMatch(parts[i],@"^[0-7]{3}$"))throw new InvalidDataException("Invalid octal byte");result.Add(Convert.ToByte(parts[i],8));}}return result.ToArray();}
 }
 public sealed class RecoverySession {
  public string Directory,HostAddress,AdapterId;public ExistingInstallationException Failure;
  public void Save(string directory){File.WriteAllText(Path.Combine(directory,"recovery-session.json"),Json.Write(new{version=1,key_directory=Directory,host_address=HostAddress,adapter_id=AdapterId,access=Failure.Access,message=Failure.Message}));}
  public static RecoverySession Load(string directory){
   string saved=Path.Combine(directory,"recovery-session.json");
   if(File.Exists(saved)){
    var data=Json.Read<Dictionary<string,object>>(File.ReadAllText(saved));
    if(Convert.ToInt32(data["version"])!=1)throw new IOException("Unsupported recovery session format");
    var record=new RecoverySession{Directory=Convert.ToString(data["key_directory"]),HostAddress=Convert.ToString(data["host_address"]),AdapterId=Convert.ToString(data["adapter_id"]),Failure=new ExistingInstallationException(Convert.ToString(data["message"])){Access=Json.Read<RecoveryAccess>(Json.Write(data["access"]))}};
    record.Validate();return record;
   }
   string transport=Path.Combine(directory,"ethernet-device"),events=Path.Combine(transport,"installer-events.log");
   if(!File.Exists(events))throw new IOException("This session has no saved temporary SSH connection");
   ExistingInstallationException failure=null;
   foreach(string line in File.ReadAllLines(events)){var evt=Json.Read<Dictionary<string,object>>(line);if(evt.ContainsKey("code")&&Convert.ToString(evt["code"])=="existing-installation")failure=ExistingInstallationException.FromEvent(evt);}
   if(failure==null||failure.Access==null||!failure.Access.Valid)throw new IOException("This session has no verified recovery SSH service");
   var device=IPAddress.Parse(failure.Access.address).GetAddressBytes();if(device[0]!=169||device[1]!=254||device[3]!=1)throw new IOException("Saved recovery address is not a direct-link installer address");device[3]=2;string host=new IPAddress(device).ToString();
   var address=Json.Read<Dictionary<string,object>>(File.ReadAllText(Path.Combine(transport,"pc-address-"+host+".json")));
   if(Convert.ToString(address["address"])!=host||String.IsNullOrEmpty(Convert.ToString(address["adapter"])))throw new IOException("Saved PC address is invalid");
   if(!File.Exists(Path.Combine(directory,"keys","maintenance_ed25519")))throw new IOException("The saved maintenance SSH key is missing. Restore the original session folder.");
   var result=new RecoverySession{Directory=directory,HostAddress=host,AdapterId=Convert.ToString(address["adapter"]),Failure=failure};result.Validate();return result;
  }
  public void Validate(){
   if(Failure==null||Failure.Access==null||!Failure.Access.Valid||String.IsNullOrEmpty(AdapterId))throw new IOException("Invalid saved SSH recovery identity");
   var d=IPAddress.Parse(Failure.Access.address).GetAddressBytes();IPAddress host;
   if(!IPAddress.TryParse(HostAddress,out host)||host.AddressFamily!=AddressFamily.InterNetwork||d[0]!=169||d[1]!=254||d[3]!=1||HostAddress!="169.254."+d[2]+".2")throw new IOException("Invalid saved SSH direct-link addresses");
   if(!File.Exists(Path.Combine(Directory??"","keys","maintenance_ed25519")))throw new IOException("The original maintenance SSH key is missing. Restore the original setup log folder.");
  }
  public static void VerifyHostKey(string output,string expected){
   foreach(string line in output.Split('\n')){
    var match=Regex.Match(line.Trim(),@"^\S+ ssh-ed25519 ([A-Za-z0-9+/=]+)$");if(!match.Success)continue;
    byte[] raw=Convert.FromBase64String(match.Groups[1].Value);string actual;using(var sha=SHA256.Create())actual="SHA256:"+Convert.ToBase64String(sha.ComputeHash(raw)).TrimEnd('=');
    if(actual==expected.TrimEnd('='))return;
   }
   throw new IOException("The temporary SSH host fingerprint differs from the saved installation. Do not connect until the device identity is checked.");
  }
 }
 public sealed class ResumeRecord {
  public int Version=1;public string DeviceAddress,HostAddress,Secret,CidSha,AdapterId,SessionDirectory,State;
  public byte[] AdapterMac;public DeviceReview Review;public bool IgnoreHashes;
  public void Validate(){
   IPAddress device,host;
   if(Version!=1||!IPAddress.TryParse(DeviceAddress,out device)||!IPAddress.TryParse(HostAddress,out host)||device.AddressFamily!=AddressFamily.InterNetwork||host.AddressFamily!=AddressFamily.InterNetwork)throw new InvalidDataException("Invalid saved installer connection");
   var d=device.GetAddressBytes();var h=host.GetAddressBytes();
   if(d[0]!=169||d[1]!=254||d[3]!=1||h[0]!=169||h[1]!=254||h[2]!=d[2]||h[3]!=2||!Regex.IsMatch(Secret??"",@"^[0-9a-f]{64}$")||!Regex.IsMatch(CidSha??"",@"^[0-9a-f]{64}$")||String.IsNullOrEmpty(AdapterId)||AdapterMac==null||AdapterMac.Length!=6||Review==null||!Review.Compatible||String.IsNullOrEmpty(SessionDirectory))throw new InvalidDataException("Saved installer identity or direct-link addresses are invalid");
  }
  public void VerifyAdapter(EthernetAdapter adapter){Validate();if(!String.Equals(adapter.Id,AdapterId,StringComparison.OrdinalIgnoreCase)||BitConverter.ToString(adapter.Mac)!=BitConverter.ToString(AdapterMac))throw new IOException("Select the same PC Ethernet port used for this installation.");}
  public void Save(string path){Validate();string temporary=path+".new";File.WriteAllText(temporary,Json.Write(this));if(File.Exists(path))File.Replace(temporary,path,null);else File.Move(temporary,path);}
  public static ResumeRecord Load(string path){var record=Json.Read<ResumeRecord>(File.ReadAllText(path));if(record==null)throw new InvalidDataException("Empty saved installer connection");record.Validate();return record;}
 }
 public sealed class EthernetInstallLink:IDisposable {
  const string Utility="/home/hybroad/bin/yuxing-utility.elf",UtilitySha="ea990fceb3b6d41412fda8e739bf0ec39bbab05ce9158e41668864a859b318b4";
  readonly EthernetAdapter adapter;readonly string folder;readonly Action<string> progress;CancellationToken token;TemporaryIPv4 factoryAlias,bridgeAlias;FactoryTerminal terminal;TcpClient installer;NvramMap original;string cidSha,usbMount;public DeviceReview Review;public DiscoveredBox Box;
  public EthernetInstallLink(EthernetAdapter selected,DiscoveredBox box,string session,Action<string> status,CancellationToken cancellation){adapter=selected;Box=box;folder=session;progress=status;token=cancellation;Directory.CreateDirectory(folder);}
  public void ReleaseTerminal(){if(terminal!=null){terminal.Dispose();terminal=null;}}
  public void RetainRecoveryAddress(){ReleaseTerminal();if(installer!=null){installer.Close();installer=null;}if(factoryAlias!=null){factoryAlias.Dispose();factoryAlias=null;}}
  public void ExitFactoryMode(bool reboot){
   if(factoryAlias==null||original==null)throw new IOException("Find and verify the factory terminal first");
   terminal=new FactoryTerminal(DirectSocket.Connect(adapter,factoryAlias.Address,Box.Address,23,7000,token),Path.Combine(folder,"exit-factory-"+DateTime.Now.ToString("HHmmss-fff")),token);
   try{
    terminal.Login();string cid=Hash(Encoding.ASCII.GetBytes(terminal.Run("cat /sys/class/block/mmcblk0/device/cid").Trim()));if(cid!=cidSha)throw new IOException("Device identity changed; RUNLEVEL was not changed");
    Require(original,"factory-exit-before");
    if(Hash(terminal.ReadFile(Utility))!=UtilitySha)throw new IOException("Factory utility SHA differs; RUNLEVEL was not changed");
    var wanted=FactoryExitTarget(original);
    terminal.Run("LD_PRELOAD= LD_LIBRARY_PATH=/lib:/usr/lib:/home/hybroad/lib "+Utility+" -env RUNLEVEL 1");
    Require(wanted,"factory-exit-verified");original=wanted;
    File.WriteAllText(Path.Combine(folder,"factory-exit.json"),Json.Write(new{RUNLEVEL="1",STARTUP_unchanged=true,both_copies_verified=true,reboot_requested=reboot}));
    if(reboot){try{terminal.Run("cd /; sync; /sbin/reboot",8,true);}catch(EndOfStreamException){}catch(TimeoutException){}}
   }finally{ReleaseTerminal();}
  }
  public static NvramMap FactoryExitTarget(NvramMap before){if(!BootSlots.Valid(before.Get("KERNEL"))||!BootSlots.Valid(before.Get("ROOTFS"))||(before.Get("RUNLEVEL")!="4"&&before.Get("RUNLEVEL")!="1"))throw new IOException("Unsupported environment for leaving factory mode");var wanted=before.Copy();wanted.Set("RUNLEVEL","1");return wanted;}
  public void SetCancellation(CancellationToken value){token=value;if(terminal!=null)terminal.SetCancellation(value);}
  public static string Hash(byte[] bytes){using(var sha=SHA256.Create())return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-","").ToLowerInvariant();}
  NvramMap[] ReadMaps(string label){var result=new NvramMap[2];for(int i=0;i<2;i++){byte[] bytes=NvramMap.Octal(terminal.Run("dd if=/dev/mmcblk0p"+(i+1)+" bs=512 count=5 2>/dev/null | od -v -b"));File.WriteAllBytes(Path.Combine(folder,label+"-p"+(i+1)+".bin"),bytes);result[i]=NvramMap.Parse(bytes);}return result;}
  void Require(NvramMap expected,string label){foreach(var actual in ReadMaps(label))if(!actual.Same(expected))throw new IOException("Device environment readback differs. Keep the DVR powered on; review recovery logs.");}
  public DeviceReview Inspect(){
   byte[] address=IPAddress.Parse(Box.Address).GetAddressBytes();if(address[0]!=10)throw new IOException("Factory discovery returned an unexpected address");address[3]=(byte)(address[3]==1?2:address[3]==254?253:address[3]^1);if(address[3]==0||address[3]==255)throw new IOException("Cannot choose a temporary factory address");
   progress("Waiting for Windows to activate the temporary Ethernet address");factoryAlias=new TemporaryIPv4(adapter,new IPAddress(address).ToString(),"255.255.255.0",folder,token);terminal=new FactoryTerminal(DirectSocket.Connect(adapter,factoryAlias.Address,Box.Address,23,7000,token),folder,token);terminal.Login();
   string command=terminal.Run("cat /proc/cmdline"),model=terminal.Run("tr '\\000' ' ' < /proc/device-tree/model");if(!Regex.IsMatch(command,@"\brunlevel=4\b")||command.Contains("HYBROAD_PARTITION")||command.Contains("HYBROAD_WAYLAND_PARTITION")||!model.Contains("BCM97252SSV"))throw new IOException("This is not the supported factory mode/board. No device settings were changed.");
   string cid=terminal.Run("cat /sys/class/block/mmcblk0/device/cid").Trim();if(!Regex.IsMatch(cid,@"^[0-9a-fA-F]{32}$"))throw new IOException("Cannot verify device identity");cidSha=Hash(Encoding.ASCII.GetBytes(cid));
   var maps=ReadMaps("original");original=maps[0];if(!maps[0].Same(maps[1]))throw new IOException("The two device environment copies differ. Use Advanced recovery to review them before installation.");
   if(original.Get("RUNLEVEL")!="4"||!BootSlots.Valid(original.Get("KERNEL"))||!BootSlots.Valid(original.Get("ROOTFS"))||original.Get("MEMCONFIG")!=DeviceReview.MemoryLayout)throw new IOException("Unsupported active firmware or memory layout");
   Review=new DeviceReview{Identity=model.Trim()+" / M616T factory",Kernel=original.Get("KERNEL"),Rootfs=original.Get("ROOTFS"),Memory=original.Get("MEMCONFIG"),Runlevel=original.Get("RUNLEVEL"),Startup=original.Get("STARTUP"),StartupAbsent=original.Get("STARTUP")==null};File.WriteAllText(Path.Combine(folder,"original-device.json"),Json.Write(new{review=Review,cid_sha256=cidSha,MAC=BitConverter.ToString(Box.Mac),factory_ip=Box.Address}));return Review;
  }
  string FindUsbMount(){
   string found=null;
   foreach(string mount in terminal.Run("awk '$3==\"vfat\" {print $2}' /proc/mounts").Split('\n')){
    if(!Regex.IsMatch(mount,@"^/mnt/[A-Za-z0-9/_-]+$"))continue;
    string marker=terminal.Run("if [ -f "+FactoryTerminal.Quote(mount+"/phoenix/usb-id.txt")+" ]; then cat "+FactoryTerminal.Quote(mount+"/phoenix/usb-id.txt")+"; fi");
    if(marker=="PHOENIX_INSTALLER_USB_V2"){if(found!=null)throw new IOException("More than one Phoenix USB is connected. Leave only the prepared stick in the DVR.");found=mount;}
   }
   return found;
  }
  public void Install(string payload,Func<Dictionary<string,object>,string> configuration,Action<Dictionary<string,object>> events,bool ignoreHashes,Func<string> recoveryKey=null,Func<Dictionary<string,object>,bool> retry=null){
   Usb.Validate(payload);if(Review==null||!Review.Compatible)throw new IOException("Device review required");Require(original,"before-handoff");
   string currentCid=Hash(Encoding.ASCII.GetBytes(terminal.Run("cat /sys/class/block/mmcblk0/device/cid").Trim()));if(currentCid!=cidSha)throw new IOException("Device identity changed");
   // Nothing has been written yet, so a missing stick can be fixed in place: ask, rescan, carry on.
   usbMount=null;
   while((usbMount=FindUsbMount())==null){
    string help="We can't find the Phoenix USB stick in the DVR.\n\nPlug the prepared USB stick into the rear USB port, on the back of the DVR (not the front). Wait about 15 seconds, then press Try again.\n\nNothing on the DVR has been changed.";
    if(retry==null||!retry(new Dictionary<string,object>{{"event","paused"},{"code","usb-missing"},{"message",help}}))throw new IOException("The Phoenix USB stick wasn't found in the DVR's rear USB port. Nothing was changed. Turn the DVR off, put the stick in the rear port, then start again from Connect the DVR.");
    token.ThrowIfCancellationRequested();progress("Looking for the USB stick again…");
   }
   string route=BootSlots.Entry("b",Review.Kernel,Review.Rootfs),entry=BootSlots.Entry("i",Review.Kernel,Review.Rootfs),startup=BootSlots.Startup(Review.Kernel,Review.Rootfs);
   foreach(string file in new[]{"stage1.sh","stage2.py","harvest.py","network-bridge.py","recovery.py","slots.py",Path.GetFileName(route),Path.GetFileName(entry)})if(Hash(terminal.ReadFile(usbMount+"/phoenix/"+file))!=Json.Sha(Path.Combine(payload,"phoenix",file)))throw new IOException("USB readback mismatch: "+file+". Prepare the current slot-aware USB payload.");
   if(Json.Sha(Path.Combine(payload,route))!=Json.Sha(Path.Combine(payload,entry)))throw new IOException("This payload is not a slot-aware Ethernet bootstrap USB");
   if(!ignoreHashes&&Hash(terminal.ReadFile(Utility))!=UtilitySha)throw new IOException("Factory utility SHA differs. Review the supported-firmware policy before proceeding.");
   byte[] random=new byte[32];using(var rng=RandomNumberGenerator.Create())rng.GetBytes(random);string secret=BitConverter.ToString(random).Replace("-","").ToLowerInvariant();int subnet=1+random[0]%254;string deviceAddress="169.254."+subnet+".1",hostAddress="169.254."+subnet+".2";
   progress("Waiting for Windows to activate the RAM installer connection");bridgeAlias=new TemporaryIPv4(adapter,hostAddress,"255.255.255.252",folder,token);
   var network=new{version=1,device_ip=deviceAddress,host_ip=hostAddress,port=18991,token=secret,device_cid_sha256=cidSha};
   terminal.Run("printf '%s\\n' "+FactoryTerminal.Quote(Json.Write(network))+" > "+FactoryTerminal.Quote(usbMount+"/phoenix/setup-network.json")+" && sync",30,true);
   var saved=new ResumeRecord{DeviceAddress=deviceAddress,HostAddress=hostAddress,Secret=secret,CidSha=cidSha,AdapterId=adapter.Id,AdapterMac=adapter.Mac,Review=Review,IgnoreHashes=ignoreHashes,SessionDirectory=Directory.GetParent(folder).FullName,State="handoff"};
   string resumePath=Path.Combine(folder,"resume.json");saved.Save(resumePath);
   progress("Reconnect information saved. If the app closes, use Reconnect to temporary Alpine on tab 2.");
   string writer="LD_PRELOAD= LD_LIBRARY_PATH=/lib:/usr/lib:/home/hybroad/lib "+Utility+" -env ";
   progress("Selecting the verified USB installer boot hook for kernel "+Review.Kernel+", root "+Review.Rootfs);var wanted=original.Copy();terminal.Run(writer+"RUNLEVEL 1");wanted.Set("RUNLEVEL","1");Require(wanted,"normal-fallback-selected");terminal.Run(writer+"STARTUP "+FactoryTerminal.Quote(startup));wanted.Set("STARTUP",startup);Require(wanted,"installer-ready");
   File.WriteAllText(Path.Combine(folder,"handoff.json"),Json.Write(new{STARTUP=startup,RUNLEVEL="1",KERNEL=Review.Kernel,ROOTFS=Review.Rootfs,cid_sha256=cidSha,device_ip=deviceAddress,host_ip=hostAddress,boot_hook_changed=true}));
   progress("Booting the RAM installer; waiting for direct Ethernet");try{terminal.Run("cd /; sync; /sbin/reboot",8,true);}catch(IOException){}catch(TimeoutException){}terminal.Dispose();terminal=null;
   ConnectInstaller(saved,300);
   MonitorInstaller(saved,resumePath,configuration,events,recoveryKey,false,retry);
  }
  public void Reconnect(ResumeRecord saved,string resumePath,Func<Dictionary<string,object>,string> configuration,Action<Dictionary<string,object>> events,Func<string> recoveryKey,Func<Dictionary<string,object>,bool> retry=null){
   saved.VerifyAdapter(adapter);Review=saved.Review;cidSha=saved.CidSha;
   progress("Restoring the saved PC address for temporary Alpine");
   bridgeAlias=new TemporaryIPv4(adapter,saved.HostAddress,"255.255.255.252",folder,token,true);
   ConnectInstaller(saved,30);
   MonitorInstaller(saved,resumePath,configuration,events,recoveryKey,true,retry);
  }
  public void RestoreRecovery(RecoverySession saved){
   saved.Validate();
   if(!String.Equals(adapter.Id,saved.AdapterId,StringComparison.OrdinalIgnoreCase))throw new IOException("Select the original PC Ethernet port for this recovery session");
   bridgeAlias=new TemporaryIPv4(adapter,saved.HostAddress,"255.255.255.252",folder,token,true);
   progress("Verifying the temporary Alpine SSH host fingerprint");
   string output=SshHostKey.Probe(saved.Failure.Access.address,token);
   RecoverySession.VerifyHostKey(output,saved.Failure.Access.host_fingerprint);
  }
  public void SaveRecoverySession(ExistingInstallationException error,string keyDirectory,string sessionDirectory){
   if(bridgeAlias!=null)new RecoverySession{Directory=keyDirectory,HostAddress=bridgeAlias.Address,AdapterId=adapter.Id,Failure=error}.Save(sessionDirectory);
  }
  void ConnectInstaller(ResumeRecord saved,int seconds){
   var watch=Stopwatch.StartNew();Exception last=null;
   while(watch.Elapsed.TotalSeconds<seconds){token.ThrowIfCancellationRequested();try{installer=DirectSocket.Connect(adapter,saved.HostAddress,saved.DeviceAddress,18991,2000,token);return;}catch(SocketException e){last=e;}catch(TimeoutException e){last=e;}if(token.WaitHandle.WaitOne(500))token.ThrowIfCancellationRequested();}
   throw new IOException("Temporary Alpine is not answering. Keep the DVR powered on and the same direct cable connected; retry Reconnect on tab 2. Older USB environments may not support reconnect after their bridge stopped. "+(last==null?"":last.Message));
  }
  public static void VerifySessionState(Dictionary<string,object> evt,ResumeRecord saved){
   if(!evt.ContainsKey("device_cid_sha256")||Convert.ToString(evt["device_cid_sha256"])!=saved.CidSha||!evt.ContainsKey("protocol_version")||Convert.ToInt32(evt["protocol_version"])!=2)throw new IOException("The RAM installer session identity differs. No configuration was sent.");
   BootSlots.VerifyReported(evt,saved.Review);
  }
  void MonitorInstaller(ResumeRecord saved,string resumePath,Func<Dictionary<string,object>,string> configuration,Action<Dictionary<string,object>> events,Func<string> recoveryKey,bool reconnect,Func<Dictionary<string,object>,bool> retry){
   using(var wire=installer.GetStream()){
    Send(wire,"PHOENIX_AUTH "+saved.Secret);string ready=ReadLine(wire,20);
    bool resumable=ready=="PHOENIX_NET_READY 2";
    if(!resumable&&(ready!="PHOENIX_NET_READY 1"||reconnect))throw new IOException("This USB environment cannot resume an installer session. Use saved SSH recovery access when available.");
    bool policy=false,configured=false,slotsVerified=false,stateVerified=!resumable;
    while(true){string line=ReadLine(wire,900);if(!line.StartsWith("PHOENIX_EVENT "))continue;var evt=Json.Read<Dictionary<string,object>>(line.Substring(14));string kind=Convert.ToString(evt["event"]);
     if(kind=="connection-alive")continue;
     if(kind=="session-state"){
      if(!resumable||stateVerified)throw new IOException("Unexpected installer session state");
      VerifySessionState(evt,saved);stateVerified=true;policy=Convert.ToBoolean(evt["policy_received"]);configured=Convert.ToBoolean(evt["configuration_received"]);
      if(configured&&!policy)throw new IOException("Invalid saved installer policy state");
      if(BootSlots.VerifyReported(evt,Review))slotsVerified=true;
      continue;
     }
     if(!stateVerified)throw new IOException("Installer identity was not verified");
     if(BootSlots.VerifyReported(evt,Review))slotsVerified=true;
     if(kind=="need-retry"){if(!slotsVerified||retry==null)throw new IOException("Repair checkpoint requires the current app and verified slots");File.AppendAllText(Path.Combine(folder,"installer-events.log"),Json.Write(evt)+Environment.NewLine);Send(wire,RetryProtocol.Reply(evt,retry(evt)));if(evt.ContainsKey("code")&&Convert.ToString(evt["code"])=="configuration")configured=false;continue;}
     if(kind=="need-policy"){if(!slotsVerified||policy||configured||Convert.ToInt32(evt["protocol_version"])!=2)throw new IOException("Incompatible installer policy request or running slots not verified");Send(wire,"PHOENIX_POLICY "+Convert.ToBase64String(Encoding.UTF8.GetBytes(Json.Write(new{ignore_sha_mismatches=saved.IgnoreHashes}))));policy=true;}
     else if(kind=="need-recovery-key"){if(!slotsVerified||recoveryKey==null||Convert.ToInt32(evt["protocol_version"])!=2)throw new IOException("Recovery access requires verified running firmware slots");Send(wire,"PHOENIX_RECOVERY_KEY "+Convert.ToBase64String(Encoding.ASCII.GetBytes(recoveryKey())));}
     else if(kind=="need-config"){if(!policy||configured||!evt.ContainsKey("echo_disabled")||!Convert.ToBoolean(evt["echo_disabled"]))throw new IOException("Untrusted configuration request");string value=configuration(evt);if(value==null)throw new OperationCanceledException();Send(wire,"PHOENIX_CONFIG "+Convert.ToBase64String(Encoding.UTF8.GetBytes(value)));value=null;configured=true;}
     else{File.AppendAllText(Path.Combine(folder,"installer-events.log"),Json.Write(evt)+Environment.NewLine);events(evt);}
     if(kind=="failed"){saved.State="stopped";saved.Save(resumePath);string message=Convert.ToString(evt["message"]);if(evt.ContainsKey("code")&&Convert.ToString(evt["code"])=="existing-installation")throw ExistingInstallationException.FromEvent(evt);throw new IOException(message);}
     if(kind=="complete"){if(!slotsVerified||!policy||!configured)throw new IOException("Unexpected installer completion");saved.State="complete";saved.Save(resumePath);return;}
    }
   }
  }
  void Send(NetworkStream stream,string text){token.ThrowIfCancellationRequested();byte[] data=Encoding.UTF8.GetBytes(text+"\n");stream.Write(data,0,data.Length);}
  string ReadLine(NetworkStream stream,int seconds){var result=new List<byte>();var watch=Stopwatch.StartNew();while(watch.Elapsed.TotalSeconds<seconds){token.ThrowIfCancellationRequested();if(!stream.DataAvailable){if(installer.Client.Poll(0,SelectMode.SelectRead))throw new EndOfStreamException("RAM installer disconnected");Thread.Sleep(10);continue;}int b=stream.ReadByte();if(b<0)throw new EndOfStreamException();if(b==10)return Encoding.UTF8.GetString(result.ToArray()).TrimEnd('\r');result.Add((byte)b);if(result.Count>262144)throw new IOException("Oversized installer message");}throw new TimeoutException("No RAM installer activity; logs saved");}
  public void Dispose(){if(terminal!=null){terminal.Dispose();terminal=null;}if(installer!=null){installer.Close();installer=null;}if(bridgeAlias!=null){bridgeAlias.Dispose();bridgeAlias=null;}if(factoryAlias!=null){factoryAlias.Dispose();factoryAlias=null;}}
 }
}
