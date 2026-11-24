using System;
using System.IO;
using System.IO.Compression;
using System.IO.Pipes;
using System.Diagnostics;
using System.Security.Cryptography;
using System.Text;
using System.Threading.Tasks;
using System.Collections.Generic;

namespace PhoenixSetup {
 public static class UpdateTests {
  static void Put(Stream archive,string name,byte[] data){var header=new byte[512];Encoding.ASCII.GetBytes(name).CopyTo(header,0);Encoding.ASCII.GetBytes(Convert.ToString(data.Length,8).PadLeft(11,'0')+"\0").CopyTo(header,124);header[156]=(byte)'0';for(int i=148;i<156;i++)header[i]=32;int checksum=0;foreach(byte b in header)checksum+=b;Encoding.ASCII.GetBytes(Convert.ToString(checksum,8).PadLeft(6,'0')+"\0 ").CopyTo(header,148);archive.Write(header,0,header.Length);archive.Write(data,0,data.Length);var pad=new byte[(512-data.Length%512)%512];archive.Write(pad,0,pad.Length);}
  static void Package(string path,RSACryptoServiceProvider signer,bool signed,bool damaged){
   var payload=new Dictionary<string,byte[]>{{"data/native-frontend/frontend.py",Encoding.UTF8.GetBytes("test code")},{"data/native-frontend/version.txt",Encoding.UTF8.GetBytes("test-release\n")}};
   var files=new Dictionary<string,object>();foreach(var row in payload){using(var hash=SHA256.Create())files[row.Key]=new{bytes=row.Value.Length,sha256=BitConverter.ToString(hash.ComputeHash(row.Value)).Replace("-","").ToLowerInvariant(),mode=420};}
   byte[] manifest=Encoding.UTF8.GetBytes(Json.Write(new{format=1,version="test-release",kernel="4.1.20-1.9",alpine="3.24.3",settings_migration="none",files=files}));
   using(var file=File.Create(path))using(var gzip=new GZipStream(file,CompressionMode.Compress)){
    Put(gzip,"manifest.json",manifest);if(signed){byte[] signature=signer.SignData(manifest,CryptoConfig.MapNameToOID("SHA256"));if(damaged)signature[0]^=1;Put(gzip,"signature.bin",signature);}
    foreach(var row in payload)Put(gzip,"files/"+row.Key,row.Value);gzip.Write(new byte[1024],0,1024);
   }
  }
  public static void Run(Action<string,Action> test,Action<bool> assert){
   test("External helper scripts reject missing or tampered files and return verified bytes",()=>{
    string name="test-"+Guid.NewGuid().ToString("N")+".py",path=Path.Combine(SetupScripts.Folder,name);byte[] expected=Encoding.UTF8.GetBytes("verified helper\n");string digest;
    using(var hash=SHA256.Create())digest=BitConverter.ToString(hash.ComputeHash(expected)).Replace("-","").ToLowerInvariant();
    try{
     bool missing=false;try{SetupScripts.Load(name,digest);}catch(IOException){missing=true;}assert(missing);
     File.WriteAllBytes(path,expected);byte[] verified=SetupScripts.Load(name,digest);File.WriteAllText(path,"modified helper");
     assert(Convert.ToBase64String(verified)==Convert.ToBase64String(expected));
     bool rejected=false;try{SetupScripts.Load(name,digest);}catch(InvalidDataException){rejected=true;}assert(rejected);
    }finally{if(File.Exists(path))File.Delete(path);}
   });
   test("SSH host discovery uses supported KEX without offering credentials or opening a session",()=>{
    string path=Path.Combine(Path.GetTempPath(),"profile with spaces","known hosts"),args=SshHostKey.Arguments("192.0.2.8",path);
    string ssh=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System),"OpenSSH","ssh.exe");
    string config=ProcessRunner.Execute(ssh,"-G "+args,10000,System.Threading.CancellationToken.None);
    foreach(string option in new[]{"kexalgorithms curve25519-sha256","hostkeyalgorithms ssh-ed25519","preferredauthentications none","passwordauthentication no","kbdinteractiveauthentication no","gssapiauthentication no","hostbasedauthentication no","identityagent none","sessiontype none"})assert(config.Contains(option));
    assert(config.Contains("pubkeyauthentication false")||config.Contains("pubkeyauthentication no"));
    assert(config.Contains(path.Replace('\\','/'))&&args.Contains("StrictHostKeyChecking=accept-new")&&args.Contains("-F NUL"));
   });
   test("Host discovery accepts a key before expected auth failure and removes its temporary cache",()=>{
    byte[] key=new byte[51];key[3]=11;Encoding.ASCII.GetBytes("ssh-ed25519").CopyTo(key,4);key[18]=32;key[50]=1;
    string line="192.0.2.8 ssh-ed25519 "+Convert.ToBase64String(key)+"\n",cache=null;
    string result=SshHostKey.Probe("192.0.2.8",System.Threading.CancellationToken.None,(host,file,token)=>{cache=file;File.WriteAllText(file,line);throw new IOException("Permission denied");});
    assert(result==line&&!File.Exists(cache)&&!Directory.Exists(Path.GetDirectoryName(cache)));
   });
   test("Host discovery rejects unrelated, malformed and conflicting host keys",()=>{
    byte[] key=new byte[51];key[3]=11;Encoding.ASCII.GetBytes("ssh-ed25519").CopyTo(key,4);key[18]=32;
    string first="192.0.2.8 ssh-ed25519 "+Convert.ToBase64String(key)+"\n";key[50]=1;
    foreach(string input in new[]{"",first.Replace("192.0.2.8","192.0.2.9"),"192.0.2.8 ssh-ed25519 AAAA\n",first+"192.0.2.8 ssh-ed25519 "+Convert.ToBase64String(key)+"\n"}){
     bool rejected=false;try{SshHostKey.Parse("192.0.2.8",input);}catch(IOException){rejected=true;}assert(rejected);
    }
   });
   test("Failed host discovery and cancellation remove temporary files",()=>{
    string cache=null;bool rejected=false;try{SshHostKey.Probe("192.0.2.8",System.Threading.CancellationToken.None,(host,file,token)=>{cache=file;throw new IOException("offline");});}catch(IOException){rejected=true;}assert(rejected&&!Directory.Exists(Path.GetDirectoryName(cache)));
    using(var cancel=new System.Threading.CancellationTokenSource()){
     bool cancelled=false;try{SshHostKey.Probe("192.0.2.8",cancel.Token,(host,file,token)=>{cache=file;File.WriteAllText(file,"untrusted key");cancel.Cancel();});}catch(OperationCanceledException){cancelled=true;}assert(cancelled&&!File.Exists(cache)&&!Directory.Exists(Path.GetDirectoryName(cache)));
    }
   });
   test("Updater rejects invalid signatures even when unsigned packages are supported",()=>{
    string dir=Path.Combine(Path.GetTempPath(),"phoenix-update-test-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(dir);
    try{using(var rsa=new RSACryptoServiceProvider(3072)){rsa.PersistKeyInCsp=false;var key=rsa.ExportParameters(false);string json=Json.Write(new{n=Convert.ToBase64String(key.Modulus),e=Convert.ToBase64String(key.Exponent)}),path=Path.Combine(dir,"package.tar.gz");
     Package(path,rsa,true,false);var signed=UpdatePackage.Inspect(path,json);assert(signed.Signed&&signed.Version=="test-release");
     Package(path,rsa,true,true);bool rejected=false;try{UpdatePackage.Inspect(path,json);}catch(IOException){rejected=true;}assert(rejected);
     Package(path,rsa,false,false);assert(!UpdatePackage.Inspect(path,json).Signed);
    }}finally{Directory.Delete(dir,true);}
   });
   test("Update inference loads installed identity and key without treating the temporary link as LAN",()=>{
    string root=Path.Combine(Path.GetTempPath(),"phoenix-update-logs-"+Guid.NewGuid().ToString("N")),dir=Path.Combine(root,"20260101");Directory.CreateDirectory(Path.Combine(dir,"keys"));
    try{string key=Path.Combine(dir,"keys","maintenance_ed25519");File.WriteAllText(key,"test key file");File.WriteAllText(Path.Combine(dir,"installation-receipt.json"),Json.Write(new{ssh_host_fingerprint="Fingerprint: SHA256:TESTIDENTITY"}));var inferred=UpdateFiles.Infer(root);assert(inferred.Address==null&&inferred.KeyPath==key&&inferred.HostFingerprint=="SHA256:TESTIDENTITY");File.WriteAllText(Path.Combine(dir,"update-session.json"),Json.Write(new UpdateRecord{Address="192.0.2.9",KeyPath=key,HostFingerprint="SHA256:TESTIDENTITY",SessionId=new string('a',32)}));assert(UpdateFiles.Infer(root).Address=="192.0.2.9");}finally{Directory.Delete(root,true);}
   });
   test("Update session persistence contains no login password",()=>{var value=Json.Write(new UpdateRecord{Address="192.0.2.8",SessionId=new string('a',32),AllowUnsigned=true});assert(!value.ToLowerInvariant().Contains("password")&&value.Contains("AllowUnsigned"));});
   test("SSH helper receives password through a private pipe rather than arguments or files",()=>{
    string name="phoenix-update-"+Guid.NewGuid().ToString("N");using(var pipe=new NamedPipeServerStream(name,PipeDirection.Out,1,PipeTransmissionMode.Byte,PipeOptions.Asynchronous)){
     var write=Task.Run(()=>{pipe.WaitForConnection();using(var writer=new StreamWriter(pipe,new UTF8Encoding(false),1024,true)){writer.WriteLine("fixture password with spaces");writer.Flush();}});
     var start=new ProcessStartInfo(System.Windows.Forms.Application.ExecutablePath){UseShellExecute=false,CreateNoWindow=true,RedirectStandardOutput=true};start.EnvironmentVariables["PHOENIX_UPDATE_ASKPASS_PIPE"]=name;
     using(var process=Process.Start(start)){var read=process.StandardOutput.ReadToEndAsync();assert(process.WaitForExit(15000));assert(process.ExitCode==0&&read.Result=="fixture password with spaces");assert(write.Wait(2000));}
    }
   });
   test("SSH argument quoting retains spaces, quotes and trailing backslashes",()=>{assert(UpdateFiles.Quote("a b")=="\"a b\"");assert(UpdateFiles.Quote("a\"b")=="\"a\\\"b\"");assert(UpdateFiles.Quote("folder\\")=="\"folder\\\\\"");});
   test("OpenSSH resolves the pinned-host path as one file when it contains spaces",()=>{string path=Path.Combine(Path.GetTempPath(),"profile with spaces","known hosts"),ssh=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System),"OpenSSH","ssh.exe");string output=ProcessRunner.Execute(ssh,"-G -F NUL -o "+UpdateFiles.KnownHostsOption(path)+" 192.0.2.8",10000,System.Threading.CancellationToken.None);assert(output.Contains(path.Replace('\\','/')));});
  }
 }
}
