using System;
using System.IO;
using System.IO.Compression;
using System.IO.Pipes;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Diagnostics;
using System.Collections.Generic;
using System.Net;
using System.Net.Sockets;
using System.Drawing;
using System.Windows.Forms;

namespace PhoenixSetup {
 public sealed class UpdateRecord {
  public string Address,KeyPath,HostFingerprint,SessionId,PackageSha256,PackagePath,PublisherPath,Source;
  public bool AllowUnsigned;
 }
 public static class UpdateFiles {
  public static byte[] Resource(string name){using(var stream=typeof(UpdateFiles).Assembly.GetManifestResourceStream(name)){if(stream==null)throw new IOException("Missing updater resource");using(var output=new MemoryStream()){stream.CopyTo(output);return output.ToArray();}}}
  public static string Fingerprint(byte[] key){using(var sha=SHA256.Create())return "SHA256:"+Convert.ToBase64String(sha.ComputeHash(key)).TrimEnd('=');}
  public static string PublisherFingerprint(string json){var key=Json.Read<Dictionary<string,string>>(json);return Fingerprint(Convert.FromBase64String(key["n"]));}
  public static void ValidateAddress(string address){IPAddress parsed;if(!IPAddress.TryParse(address,out parsed)||parsed.AddressFamily!=AddressFamily.InterNetwork||parsed.ToString()!=address||parsed.Equals(IPAddress.Any)||parsed.Equals(IPAddress.Broadcast)||IPAddress.IsLoopback(parsed))throw new IOException("Enter the DVR's IPv4 address from Settings / Device info.");}
  public static UpdateRecord Infer(string root){
   if(!Directory.Exists(root))return new UpdateRecord();var directories=Directory.GetDirectories(root);Array.Sort(directories,StringComparer.Ordinal);Array.Reverse(directories);
   foreach(string folder in directories){
    string update=Path.Combine(folder,"update-session.json");
    if(File.Exists(update))try{var saved=Json.Read<UpdateRecord>(File.ReadAllText(update));ValidateAddress(saved.Address);if(saved.Address.StartsWith("169.254."))throw new IOException("Temporary link is not a saved LAN address");if(saved.KeyPath!=null&&!File.Exists(saved.KeyPath))saved.KeyPath=null;saved.Source=folder;return saved;}catch(Exception){}
    string receipt=Path.Combine(folder,"installation-receipt.json"),key=Path.Combine(folder,"keys","maintenance_ed25519");
    if(File.Exists(receipt))try{
     var value=Json.Read<Dictionary<string,object>>(File.ReadAllText(receipt));string fingerprint=value.ContainsKey("ssh_host_fingerprint")?Convert.ToString(value["ssh_host_fingerprint"]):"";var match=Regex.Match(fingerprint,@"SHA256:[A-Za-z0-9+/]+={0,2}");
     if(!File.Exists(key)){string resume=Path.Combine(folder,"ethernet-device","resume.json");if(File.Exists(resume)){var prior=Json.Read<Dictionary<string,object>>(File.ReadAllText(resume));if(prior.ContainsKey("SessionDirectory"))key=Path.Combine(Convert.ToString(prior["SessionDirectory"]),"keys","maintenance_ed25519");}}
     return new UpdateRecord{KeyPath=File.Exists(key)?key:null,HostFingerprint=match.Success?match.Value.TrimEnd('='):null,Source=folder};
    }catch(Exception){}
   }return new UpdateRecord();
  }
  public static string Quote(string value){var result=new StringBuilder("\"");int slashes=0;foreach(char c in value){if(c=='\\'){slashes++;continue;}if(c=='\"'){result.Append('\\',slashes*2+1).Append(c);slashes=0;continue;}result.Append('\\',slashes).Append(c);slashes=0;}return result.Append('\\',slashes*2).Append('"').ToString();}
  public static string KnownHostsOption(string path){return Quote("UserKnownHostsFile=\""+path.Replace('\\','/')+"\"");}
 }
 public sealed class UpdatePackage {
  public string Version,Sha256;public bool Signed;
  static byte[] Read(Stream stream,int size){var data=new byte[size];int offset=0;while(offset<size){int n=stream.Read(data,offset,size-offset);if(n==0)throw new IOException("Truncated update package");offset+=n;}return data;}
  static string Field(byte[] header,int start,int length){return Encoding.ASCII.GetString(header,start,length).TrimEnd('\0',' ');}
  public static UpdatePackage Inspect(string path,string publicKey){
   if(!File.Exists(path)||new FileInfo(path).Length>512L*1024*1024)throw new IOException("Select a Phoenix update .tar.gz package (up to 512 MB).");
   byte[] manifest=null,signature=null;var hashes=new Dictionary<string,string>();var sizes=new Dictionary<string,int>();long total=0;
   using(var file=File.OpenRead(path))using(var gzip=new GZipStream(file,CompressionMode.Decompress)){
    while(true){var header=Read(gzip,512);bool empty=true;foreach(byte b in header)if(b!=0){empty=false;break;}if(empty)break;
     string name=Field(header,0,100),prefix=Field(header,345,155);if(prefix!="")name=prefix+"/"+name;
     int checksum=0,expectedChecksum;for(int i=0;i<header.Length;i++)checksum+=(i>=148&&i<156)?32:header[i];try{expectedChecksum=Convert.ToInt32(Field(header,148,8),8);}catch(Exception){throw new IOException("Invalid archive header checksum");}if(checksum!=expectedChecksum)throw new IOException("Archive header checksum differs");
     if(header[156]!=0&&header[156]!=(byte)'0')throw new IOException("Update contains a non-file archive member");
     int size;try{size=Convert.ToInt32(Field(header,124,12),8);}catch(Exception){throw new IOException("Invalid update archive size");}
     total+=size;if(size<0||total>512L*1024*1024||hashes.Count>=500||hashes.ContainsKey(name))throw new IOException("Oversized or duplicate update members");
     var data=Read(gzip,size);using(var sha=SHA256.Create())hashes.Add(name,BitConverter.ToString(sha.ComputeHash(data)).Replace("-","").ToLowerInvariant());sizes[name]=size;
     if(name=="manifest.json"){if(size>262144)throw new IOException("Oversized manifest");manifest=data;}else if(name=="signature.bin"){if(size>1024)throw new IOException("Oversized signature");signature=data;}
     Read(gzip,(512-size%512)%512);
    }
   }
   if(manifest==null)throw new IOException("Update manifest missing");
   if(signature!=null){
    var key=Json.Read<Dictionary<string,string>>(publicKey);byte[] modulus=Convert.FromBase64String(key["n"]),exponent=Convert.FromBase64String(key["e"]);
    if(modulus.Length<384||Convert.ToBase64String(exponent)!="AQAB")throw new IOException("Invalid publisher key");
    using(var rsa=new RSACryptoServiceProvider()){rsa.PersistKeyInCsp=false;rsa.ImportParameters(new RSAParameters{Modulus=modulus,Exponent=exponent});if(!rsa.VerifyData(manifest,CryptoConfig.MapNameToOID("SHA256"),signature))throw new IOException("Package signature is invalid. It cannot be installed as unsigned.");}
   }
   var root=Json.Read<Dictionary<string,object>>(Encoding.UTF8.GetString(manifest));
   if(Convert.ToInt32(root["format"])!=1||Convert.ToString(root["kernel"])!="4.1.20-1.9"||Convert.ToString(root["alpine"])!="3.24.3"||Convert.ToString(root["settings_migration"])!="none")throw new IOException("Unsupported update format, base or settings migration");
   string version=Convert.ToString(root["version"]);if(!Regex.IsMatch(version,@"^[A-Za-z0-9._-]{1,80}$"))throw new IOException("Invalid update version");
   var files=(Dictionary<string,object>)root["files"];if(hashes.Count!=files.Count+(signature==null?1:2))throw new IOException("Update members differ from manifest");
   foreach(var entry in files){var row=(Dictionary<string,object>)entry.Value;string name="files/"+entry.Key;if(entry.Key.StartsWith("/")||entry.Key.Contains("..")||entry.Key.Contains("\\")||!hashes.ContainsKey(name)||hashes[name]!=Convert.ToString(row["sha256"])||sizes[name]!=Convert.ToInt32(row["bytes"]))throw new IOException("Update file checksum/path differs");}
   return new UpdatePackage{Version=version,Signed=signature!=null,Sha256=Json.Sha(path)};
  }
 }
 public sealed class UpdateSsh:IDisposable {
  readonly string address,key,knownHosts;string password;readonly CancellationToken token;
  static string OpenSsh(string name){string path=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System),"OpenSSH",name);if(!File.Exists(path))throw new IOException("Windows OpenSSH Client is required. Enable it in Windows Optional features.");return path;}
  public UpdateSsh(string host,string identity,string secret,string known,CancellationToken cancel){UpdateFiles.ValidateAddress(host);address=host;key=identity;password=secret;knownHosts=known;token=cancel;}
  public static string Scan(string address,CancellationToken token){UpdateFiles.ValidateAddress(address);return SshHostKey.Probe(address,token);}
  public static string HostFingerprint(string known){return UpdateFiles.Fingerprint(Convert.FromBase64String(known.Trim().Split(new[]{' '},StringSplitOptions.RemoveEmptyEntries)[2]));}
  public string Execute(string command,Stream input=null,int seconds=60){
   string arguments="-F NUL -T -o StrictHostKeyChecking=yes -o UpdateHostKeys=no -o HostKeyAlgorithms=ssh-ed25519 -o KexAlgorithms=curve25519-sha256 -o IdentityAgent=none -o ClearAllForwardings=yes -o PermitLocalCommand=no -o ConnectTimeout=8 -o ServerAliveInterval=5 -o ServerAliveCountMax=3 -o NumberOfPasswordPrompts=1 -o GlobalKnownHostsFile=NUL -o "+UpdateFiles.KnownHostsOption(knownHosts);
   if(!String.IsNullOrWhiteSpace(key)){if(!File.Exists(key))throw new IOException("The selected SSH key file is missing");arguments+=" -o IdentitiesOnly=yes -i "+UpdateFiles.Quote(key);}
   arguments+=" -o BatchMode="+(String.IsNullOrEmpty(password)?"yes":"no")+" root@"+address+" "+UpdateFiles.Quote(command);
   string pipeName="phoenix-update-"+Guid.NewGuid().ToString("N");NamedPipeServerStream pipe=null;Task credential=null;
   var start=new ProcessStartInfo(OpenSsh("ssh.exe"),arguments){UseShellExecute=false,CreateNoWindow=true,RedirectStandardInput=true,RedirectStandardOutput=true,RedirectStandardError=true};
   start.EnvironmentVariables.Remove("SSH_ASKPASS");start.EnvironmentVariables.Remove("PHOENIX_UPDATE_ASKPASS_PIPE");
   if(!String.IsNullOrEmpty(password)){
    var security=new PipeSecurity();security.SetAccessRuleProtection(true,false);security.AddAccessRule(new PipeAccessRule(WindowsIdentity.GetCurrent().User,PipeAccessRights.FullControl,AccessControlType.Allow));
    pipe=new NamedPipeServerStream(pipeName,PipeDirection.Out,1,PipeTransmissionMode.Byte,PipeOptions.Asynchronous,4096,4096,security);
    start.EnvironmentVariables["SSH_ASKPASS"]=Application.ExecutablePath;start.EnvironmentVariables["SSH_ASKPASS_REQUIRE"]="force";start.EnvironmentVariables["DISPLAY"]="phoenix";start.EnvironmentVariables["PHOENIX_UPDATE_ASKPASS_PIPE"]=pipeName;
    var server=pipe;string secret=password;credential=Task.Run(()=>{try{server.WaitForConnection();using(var writer=new StreamWriter(server,new UTF8Encoding(false),1024,true)){writer.WriteLine(secret);writer.Flush();}}catch(IOException){}catch(ObjectDisposedException){}});
   }
   try{using(var process=new Process{StartInfo=start}){
    process.Start();var output=process.StandardOutput.ReadToEndAsync();var errors=process.StandardError.ReadToEndAsync();
    var upload=Task.Run(()=>{try{if(input!=null)input.CopyTo(process.StandardInput.BaseStream);process.StandardInput.Close();}catch(IOException){}});var watch=Stopwatch.StartNew();
    try{while(!process.WaitForExit(100)){token.ThrowIfCancellationRequested();if(watch.Elapsed.TotalSeconds>seconds)throw new TimeoutException("SSH operation timed out. Reconnect to check its status; a detached update may still be running.");}}
    finally{if(!process.HasExited)try{process.Kill();}catch(InvalidOperationException){}}
    if(!Task.WaitAll(new Task[]{output,errors,upload},5000))throw new IOException("SSH streams did not close");
    token.ThrowIfCancellationRequested();if(process.ExitCode!=0)throw new IOException("SSH/update operation failed: "+errors.Result.Trim()+" "+output.Result.Trim());return output.Result;
   }}finally{if(pipe!=null)pipe.Dispose();if(credential!=null)try{credential.Wait(1000);}catch(AggregateException){}}
  }
  public static int AskPass(){string name=Environment.GetEnvironmentVariable("PHOENIX_UPDATE_ASKPASS_PIPE");if(!Regex.IsMatch(name??"",@"^phoenix-update-[0-9a-f]{32}$"))return 1;try{using(var pipe=new NamedPipeClientStream(".",name,PipeDirection.In)){pipe.Connect(10000);using(var reader=new StreamReader(pipe))using(var output=new StreamWriter(Console.OpenStandardOutput(),new UTF8Encoding(false))){output.Write(reader.ReadLine());output.Flush();}}return 0;}catch(IOException){return 1;}}
  public void Dispose(){password=null;}
 }
 public sealed class UpdatePanel:FlowLayoutPanel {
  readonly TextBox address=new TextBox{Width=420},key=new TextBox{Width=690},password=new TextBox{Width=420,UseSystemPasswordChar=true,MaxLength=256},package=new TextBox{Width=690},publisher=new TextBox{Width=690};
  readonly Label hint=new Label{AutoSize=true,MaximumSize=new Size(800,0)},summary=new Label{AutoSize=true,MaximumSize=new Size(800,0)};
  readonly Button stage=new Button{Text="Connect and stage update",AutoSize=true},apply=new Button{Text="Apply staged update…",AutoSize=true,Enabled=false},check=new Button{Text="Check update status",AutoSize=true},rollback=new Button{Text="Roll back previous core…",AutoSize=true,Enabled=false};
  readonly Action<Func<CancellationToken,Task>> run;readonly Action<string> progress;readonly string folder;readonly bool demo;
  UpdateRecord record=new UpdateRecord();string expectedFingerprint,lastPhase;
  // Simple view shows address, password and one Update button; Advanced view shows every original control.
  readonly Button easy=new Button{Text="Update my DVR",AutoSize=true,Tag=Theme.Primary};
  readonly List<Control> advancedRows=new List<Control>(),simpleRows=new List<Control>();Control packageRow,packageLabel;bool simple=true;
  public UpdatePanel(string session,bool simulated,Action<Func<CancellationToken,Task>> execute,Action<string> log){
   folder=session;demo=simulated;run=execute;progress=log;FlowDirection=FlowDirection.TopDown;WrapContents=false;AutoSize=true;
   Add(new Label{Text="Update my DVR",AutoSize=true,Font=new Font("Segoe UI Semibold",19)});
   Simple(new Label{Text="Turn the DVR on and make sure it's connected to your home network. Your recordings, schedules and settings are kept. The DVR restarts at the end, so TV stops for a couple of minutes.",AutoSize=true,MaximumSize=new Size(780,0),Font=new Font("Segoe UI",11),ForeColor=Theme.Muted});
   Advanced(new Label{Text="Boot Phoenix normally, keeping its boot USB connected. Connect the PC and DVR to the household LAN. Recordings, schedules, settings, passwords and SSH identity are retained. Activation restarts the DVR.",AutoSize=true,MaximumSize=new Size(800,0)});
   Simple(new Label{Text="DVR address",AutoSize=true,Font=new Font("Segoe UI Semibold",10),Margin=new Padding(3,12,3,0)});Advanced(new Label{Text="Device IPv4 address (Settings → Device info)",AutoSize=true});Add(address);
   Simple(new Label{Text="On the TV, open Settings, then Device info, to see the DVR's address.",AutoSize=true,ForeColor=Theme.Muted});
   var load=new Button{Text="Load previous installation details",AutoSize=true};load.Click+=(s,e)=>{Infer();SetSimple(simple);};
   var forget=new Button{Text="Use another device / clear saved identity",AutoSize=true};forget.Click+=(s,e)=>{if(MessageBox.Show(this,"Clear the saved update session and expected host fingerprint? You will need to verify the new device fingerprint.","Choose device",MessageBoxButtons.YesNo)==DialogResult.Yes){record=new UpdateRecord();expectedFingerprint=null;lastPhase=null;key.Clear();address.Clear();summary.Text="Choose the device address and credentials.";apply.Enabled=rollback.Enabled=false;}};
   var savedRow=new FlowLayoutPanel{AutoSize=true};savedRow.Controls.Add(load);savedRow.Controls.Add(forget);Advanced(savedRow);hint.Visible=!demo;Advanced(hint);
   Simple(new Label{Text="DVR password",AutoSize=true,Font=new Font("Segoe UI Semibold",10),Margin=new Padding(3,10,3,0)});Advanced(new Label{Text="SSH login: root. Password / key passphrase (optional when the saved key works)",AutoSize=true});Add(password);
   Simple(new Label{Text="The password you chose when you set the DVR up. You can leave it blank if this computer set the DVR up.",AutoSize=true,MaximumSize=new Size(780,0),ForeColor=Theme.Muted});
   Advanced(new Label{Text="SSH private key (optional)",AutoSize=true});advancedRows.Add(Browse(key,"Choose SSH key…","All files|*.*"));
   packageLabel=new Label{Text="Phoenix update file (.tar.gz)",AutoSize=true,Font=new Font("Segoe UI Semibold",10),Margin=new Padding(3,10,3,0)};Add(packageLabel);packageRow=Browse(package,"Choose update file…","Phoenix update|*.tar.gz|All files|*.*");
   Advanced(new Label{Text="Custom publisher public key (optional; leave blank for Phoenix releases)",AutoSize=true});advancedRows.Add(Browse(publisher,"Choose publisher key…","Publisher key|*.json|All files|*.*"));
   var row=new FlowLayoutPanel{AutoSize=true};foreach(var button in new[]{stage,apply,check,rollback})row.Controls.Add(button);Advanced(row);Simple(easy);Add(summary);
   stage.Click+=(s,e)=>run(Stage);apply.Click+=(s,e)=>run(t=>Activate(t,false));check.Click+=(s,e)=>run(Check);rollback.Click+=(s,e)=>run(t=>Activate(t,true));
   easy.Click+=(s,e)=>run(EasyUpdate);
   if(!demo)Infer();SetSimple(true);
  }
  void Simple(Control c){simpleRows.Add(c);Add(c);}
  // One-button update. If this computer started an update that hasn't finished (for example after Stop or closing
  // the app), pick that one up instead of starting a second one: wait for staging, apply it, or wait for the restart.
  async Task EasyUpdate(CancellationToken token){
   Dictionary<string,object> state=null;
   if(Regex.IsMatch(record.SessionId??"",@"^[0-9a-f]{32}$")){
    try{using(var ssh=await Connect(token)){await Bootstrap(ssh);state=await Command(ssh,"status");}}
    catch(IOException error){if(error.Message==BusyMessage)throw;state=null;}
   }
   string phase=state!=null&&state.ContainsKey("phase")&&!state.ContainsKey("failure")?Convert.ToString(state["phase"]):null;
   if(phase=="stage-queued"||phase=="staging"){
    ShowState(state);progress("Picking up the update this computer started earlier. Waiting for the DVR to finish preparing it…");
    var until=DateTime.UtcNow.AddMinutes(30);
    while(phase!="ready"){if(DateTime.UtcNow>until)throw new TimeoutException("The DVR is still preparing the update. Press Update my DVR again in a few minutes.");await Task.Delay(3000,token);using(var ssh=await Connect(token)){await Bootstrap(ssh);state=await Command(ssh,"status");}ShowState(state);if(state.ContainsKey("failure"))throw new IOException(Convert.ToString(state["failure"]));phase=Convert.ToString(state["phase"]);}
   }
   if(phase=="ready"){await Activate(token,false);return;}
   if(phase=="apply-queued"||phase=="stopping"||phase=="exchanging"||phase=="activated"||phase=="awaiting-reboot"){ShowState(state);progress("The update is being applied. Waiting for the DVR to restart…");await WaitForRestart(token);return;}
   await Stage(token);if(lastPhase=="ready")await Activate(token,false);
  }
  void Advanced(Control c){advancedRows.Add(c);Add(c);}
  public void SetSimple(bool value){simple=value;foreach(var c in simpleRows)c.Visible=value;foreach(var c in advancedRows)c.Visible=!value;bool needPackage=!value||String.IsNullOrWhiteSpace(package.Text)||!File.Exists(package.Text);packageRow.Visible=packageLabel.Visible=needPackage;}
  static string BundledPackage(){try{string best=null;foreach(string path in Directory.GetFiles(AppDomain.CurrentDomain.BaseDirectory,"phoenix-update-*.tar.gz"))if(best==null||String.CompareOrdinal(Path.GetFileName(path),Path.GetFileName(best))>0)best=path;return best;}catch(Exception){return null;}}
  void Add(Control control){control.Margin=new Padding(3,4,3,5);Controls.Add(control);}
  Control Browse(TextBox target,string title,string filter){var button=new Button{Text=title,AutoSize=true};button.Click+=(s,e)=>{using(var dialog=new OpenFileDialog{Filter=filter})if(dialog.ShowDialog(this)==DialogResult.OK)target.Text=dialog.FileName;};Controls.Remove(target);target.Width=570;var row=new FlowLayoutPanel{AutoSize=true,WrapContents=false};row.Controls.Add(target);row.Controls.Add(button);Add(row);return row;}
  void Infer(){record=UpdateFiles.Infer(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"Phoenix","SetupLogs"));address.Text=record.Address??"";key.Text=record.KeyPath??"";package.Text=BundledPackage()??record.PackagePath??"";publisher.Text=record.PublisherPath??"";expectedFingerprint=record.HostFingerprint;hint.Text=record.Source==null?"No saved installation found. Enter the device address and credentials.":"Loaded key/identity from "+record.Source+(String.IsNullOrEmpty(record.Address)?". The install used a temporary network; enter the current LAN IP.":". Check the current LAN IP before connecting.");}
  string PublicKey(){string json=String.IsNullOrWhiteSpace(publisher.Text)?Encoding.UTF8.GetString(UpdateFiles.Resource("PhoenixSetup.UpdatePublicKey")):File.ReadAllText(publisher.Text);var fields=Json.Read<Dictionary<string,string>>(json);if(fields.Count!=2||!fields.ContainsKey("n")||!fields.ContainsKey("e"))throw new IOException("Select a public publisher key containing only n and e. Never select the private signing key.");return Json.Write(fields);}
  void Save(){record.Address=address.Text.Trim();record.KeyPath=key.Text.Trim();record.HostFingerprint=expectedFingerprint;record.PackagePath=package.Text;record.PublisherPath=publisher.Text;File.WriteAllText(Path.Combine(folder,"update-session.json"),Json.Write(record));}
  public void SetBusy(bool busy){foreach(Control control in Controls)control.Enabled=!busy;if(!busy){apply.Enabled=lastPhase=="ready";rollback.Enabled=lastPhase=="confirmed"||lastPhase=="awaiting-reboot"||lastPhase=="activated";}}
  async Task<UpdateSsh> Connect(CancellationToken token){
   if(demo)throw new IOException("Demo mode does not connect to devices");string ip=address.Text.Trim();UpdateFiles.ValidateAddress(ip);
   string known=await Task.Run(()=>UpdateSsh.Scan(ip,token)),fingerprint=UpdateSsh.HostFingerprint(known);
   if(!String.IsNullOrEmpty(expectedFingerprint)&&expectedFingerprint.TrimEnd('=')!=fingerprint)throw new IOException("SSH host identity differs from the saved installation. No credentials or update were sent.");
   if(String.IsNullOrEmpty(expectedFingerprint)&&MessageBox.Show(this,(simple?"This computer hasn't updated this DVR before. Is this your Phoenix DVR?\n\nAddress: "+ip+"\nIdentity: "+fingerprint+"\n\nChoose Yes only if this is your DVR.":"Verify this is your Phoenix device before sending credentials:\n\n"+ip+"\n"+fingerprint+"\n\nTrust this host?"),simple?"Is this your DVR?":"SSH device identity",MessageBoxButtons.YesNo,MessageBoxIcon.Question)!=DialogResult.Yes)throw new OperationCanceledException();
   if(Regex.IsMatch(password.Text,@"[\x00-\x1f\x7f]"))throw new IOException("The password/passphrase must not contain control characters");expectedFingerprint=fingerprint;string knownPath=Path.Combine(folder,"update-known-hosts");File.WriteAllText(knownPath,known);Save();return new UpdateSsh(ip,key.Text.Trim(),password.Text,knownPath,token);
  }
  string Worker {get{return "/run/phoenix-update-"+record.SessionId;}}
  async Task Bootstrap(UpdateSsh ssh){
   if(!Regex.IsMatch(record.SessionId??"",@"^[0-9a-f]{32}$"))throw new IOException("No update session selected");string dir=Worker;
   byte[] publicKey=Encoding.UTF8.GetBytes(PublicKey());await Task.Run(()=>{ssh.Execute("umask 077; mkdir -p "+dir+" && chmod 700 "+dir);using(var input=new MemoryStream(SetupScripts.Load("update.py",ScriptHashes.UpdateWorker)))ssh.Execute("umask 077; cat > "+dir+"/update.py",input);using(var input=new MemoryStream(publicKey))ssh.Execute("umask 077; cat > "+dir+"/update-public-key.json",input);});
  }
  const string BusyMessage="The DVR is still busy with an earlier update. Leave it switched on, wait a few minutes, then press Update my DVR again.";
  async Task<Dictionary<string,object>> Command(UpdateSsh ssh,string action,string extra="",int seconds=60){try{return await RawCommand(ssh,action,extra,seconds);}catch(IOException error){if(error.Message.Contains("BlockingIOError")||error.Message.Contains("Resource temporarily unavailable"))throw new IOException(BusyMessage,error);throw;}}
  async Task<Dictionary<string,object>> RawCommand(UpdateSsh ssh,string action,string extra,int seconds){return await Task.Run(()=>{string output=ssh.Execute("cd / && python3 -I "+Worker+"/update.py "+action+" --session "+record.SessionId+extra,null,seconds);var lines=output.Trim().Split('\n');var result=Json.Read<Dictionary<string,object>>(lines[lines.Length-1]);if(result.ContainsKey("error"))throw new IOException(Convert.ToString(result["error"]));return result;});}
  static string FriendlyPhase(string phase,string version){
   switch(phase){
    case "uploading":return "Sending the update to the DVR…";case "uploaded":return "Update sent to the DVR.";
    case "stage-queued":case "staging":return "The DVR is preparing the update…";case "ready":return "The update is ready to apply.";
    case "apply-queued":case "stopping":case "exchanging":return "Applying the update…";case "activated":case "awaiting-reboot":return "The DVR is restarting…";
    case "confirmed":return "Update completed."+(String.IsNullOrEmpty(version)?"":" Phoenix "+version+" is running.");case "rolled-back":return "The previous version is back on the DVR.";
    case "rollback-queued":return "Going back to the previous version…";
   }return "Update status: "+phase;
  }
  void ShowState(Dictionary<string,object> state){string phase=state.ContainsKey("phase")?Convert.ToString(state["phase"]):"unknown";lastPhase=phase;summary.Text=simple?FriendlyPhase(phase,state.ContainsKey("version")?Convert.ToString(state["version"]):null)+(state.ContainsKey("failure")?"\nThe update stopped: "+state["failure"]:""):"Update state: "+phase+(state.ContainsKey("version")?" — "+state["version"]:"")+(state.ContainsKey("health")?"\n"+state["health"]:"")+(state.ContainsKey("failure")?"\nUpdate worker: "+state["failure"]:"");apply.Enabled=phase=="ready";rollback.Enabled=phase=="confirmed"||phase=="awaiting-reboot"||phase=="activated";progress(summary.Text);Save();}
  async Task Stage(CancellationToken token){
   string selected=package.Text,publicKey=PublicKey();var info=await Task.Run(()=>UpdatePackage.Inspect(selected,publicKey));
   if(!info.Signed){if(MessageBox.Show(this,"This package is not signed. Its publisher and authenticity cannot be verified. Install at your own risk.\n\nVersion: "+info.Version+"\nSHA256: "+info.Sha256+"\n\nContinue staging this package?","Unsigned update",MessageBoxButtons.YesNo,MessageBoxIcon.Warning)!=DialogResult.Yes)throw new OperationCanceledException();}
   else if(!String.IsNullOrWhiteSpace(publisher.Text)&&MessageBox.Show(this,"This package is signed by the selected custom publisher key:\n\n"+UpdateFiles.PublisherFingerprint(publicKey)+"\n\nTrust this publisher to update Phoenix?","Custom publisher",MessageBoxButtons.YesNo,MessageBoxIcon.Warning)!=DialogResult.Yes)throw new OperationCanceledException();
   using(var ssh=await Connect(token)){
    string installed=await Task.Run(()=>{try{return ssh.Execute("cat /data/native-frontend/version.txt 2>/dev/null || true").Trim();}catch(IOException){return "";}});
    if(installed!=""&&installed==info.Version&&MessageBox.Show(this,"Your DVR already has the latest version, Phoenix "+installed+".\n\nDo you want to apply it again anyway? Your recordings and settings are kept either way.","Already up to date",MessageBoxButtons.YesNo,MessageBoxIcon.Information,MessageBoxDefaultButton.Button2)!=DialogResult.Yes){
     lastPhase=null;summary.Text="Your DVR already has Phoenix "+installed+". Nothing was changed.";progress(summary.Text);return;
    }
    var previous=new UpdateRecord{SessionId=record.SessionId,PackageSha256=record.PackageSha256,AllowUnsigned=record.AllowUnsigned};
    record.SessionId=Guid.NewGuid().ToString("N");record.PackageSha256=info.Sha256;record.AllowUnsigned=!info.Signed;await Bootstrap(ssh);
    progress("Connected. Preparing the private update inbox.");
    try{await Command(ssh,"init"," --sha256 "+info.Sha256+(info.Signed?"":" --allow-unsigned"));}
    catch(Exception){record.SessionId=previous.SessionId;record.PackageSha256=previous.PackageSha256;record.AllowUnsigned=previous.AllowUnsigned;Save();throw;}
    Save();
    progress("Copying update package; the running installation remains active.");await Task.Run(()=>{using(var input=File.OpenRead(selected))ssh.Execute("umask 077; cat > /mnt/recording-disk/phoenix-updates/"+record.SessionId+"/package.part",input,600);});
    await Command(ssh,"uploaded");progress("Verifying and staging the new core. It continues if the PC disconnects.");ShowState(await Command(ssh,"stage"));
    var until=DateTime.UtcNow.AddMinutes(30);while(DateTime.UtcNow<until){await Task.Delay(3000,token);var state=await Command(ssh,"status");ShowState(state);if(state.ContainsKey("failure"))throw new IOException(Convert.ToString(state["failure"]));if(Convert.ToString(state["phase"])=="ready"){var recordings=await Command(ssh,"recordings");if(!Convert.ToBoolean(recordings["can_apply"]))summary.Text+="\nA recording is active or starts within five minutes. Apply later.";return;}}
    throw new TimeoutException("Staging is still running. Use Check update status to reconnect.");
   }
  }
  async Task Check(CancellationToken token){using(var ssh=await Connect(token)){await Bootstrap(ssh);ShowState(await Command(ssh,"status"));}}
  static string List(Dictionary<string,object> state,string name){return state.ContainsKey(name)?Json.Write(state[name]):"unknown";}
  async Task Activate(CancellationToken token,bool previous){
   using(var ssh=await Connect(token)){
    await Bootstrap(ssh);var status=await Command(ssh,"recordings");ShowState(status);
    if(!Convert.ToBoolean(status["can_apply"]))throw new IOException("Update deferred. Active recordings: "+List(status,"active_recordings")+". Starting within five minutes: "+List(status,"starting_soon"));
    if(MessageBox.Show(this,(previous?"Restore the retained previous Phoenix core?":"Apply the staged Phoenix update?")+"\n\nTvheadend reports no active recordings and none starting within five minutes. Recordings, settings, passwords and SSH keys will be retained. Playback will stop and the device will restart.\n\nKeep power and the boot USB connected.",previous?"Confirm rollback":"Confirm update",MessageBoxButtons.YesNo,MessageBoxIcon.Question)!=DialogResult.Yes)return;
    ShowState(await Command(ssh,previous?"rollback":"launch"));summary.Text="Update worker started. It continues if the PC disconnects. Waiting for the device to restart…";progress(summary.Text);
   }
   await WaitForRestart(token);
  }
  async Task WaitForRestart(CancellationToken token){
   var deadline=DateTime.UtcNow.AddMinutes(5);while(DateTime.UtcNow<deadline){await Task.Delay(5000,token);try{using(var ssh=await Connect(token)){await Bootstrap(ssh);var state=await Command(ssh,"status");ShowState(state);string phase=Convert.ToString(state["phase"]);if(phase=="confirmed"||phase=="rolled-back"){password.Clear();if(phase=="confirmed")using(var done=new UpdateDoneDialog(state.ContainsKey("version")?Convert.ToString(state["version"]):null))done.ShowDialog(FindForm());return;}}}catch(IOException){progress("Waiting for the same SSH identity to return after restart…");}}
   summary.Text="Startup has not been confirmed. Keep the boot USB connected. Use Check update status to reconnect; the previous core is retained. Do not run a fresh installation.";
  }
 }
 // Shown when an update has been applied and the box has restarted with the new version.
 public sealed class UpdateDoneDialog:Form {
  public UpdateDoneDialog(string version){
   Text="Update completed";ClientSize=new Size(440,300);StartPosition=FormStartPosition.CenterParent;FormBorderStyle=FormBorderStyle.FixedDialog;MinimizeBox=false;MaximizeBox=false;ShowInTaskbar=false;
   BackColor=Theme.Bg;ForeColor=Theme.Fg;Font=new Font("Segoe UI",10);DoubleBuffered=true;
   var title=new Label{Text="Update completed!",AutoSize=false,TextAlign=ContentAlignment.MiddleCenter,Font=new Font("Segoe UI Semibold",18),Bounds=new Rectangle(0,138,440,40)};
   var detail=new Label{Text=String.IsNullOrEmpty(version)?"Your DVR has restarted with the new version.":"Phoenix "+version+" is running on your DVR.",AutoSize=false,TextAlign=ContentAlignment.MiddleCenter,ForeColor=Theme.Muted,Bounds=new Rectangle(20,180,400,44)};
   var ok=new Button{Text="OK",Tag=Theme.Primary,DialogResult=DialogResult.OK,Size=new Size(120,40),Location=new Point(160,238)};
   Controls.Add(title);Controls.Add(detail);Controls.Add(ok);AcceptButton=ok;CancelButton=ok;Theme.Apply(this);
  }
  protected override void OnPaint(PaintEventArgs e){
   base.OnPaint(e);var g=e.Graphics;g.SmoothingMode=System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
   var circle=new Rectangle(170,30,100,100);
   using(var fill=new SolidBrush(Color.FromArgb(46,160,67)))g.FillEllipse(fill,circle);
   using(var pen=new Pen(Color.White,11){StartCap=System.Drawing.Drawing2D.LineCap.Round,EndCap=System.Drawing.Drawing2D.LineCap.Round,LineJoin=System.Drawing.Drawing2D.LineJoin.Round})
    g.DrawLines(pen,new[]{new Point(196,82),new Point(212,98),new Point(245,62)});
  }
 }
}
