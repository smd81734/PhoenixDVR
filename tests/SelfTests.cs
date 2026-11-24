using System;
using System.IO;
using System.Text;
using System.Collections.Generic;
namespace PhoenixSetup {
 public sealed class FakeWire:ISerialWire {
  public Queue<string> Reads=new Queue<string>();public List<string> Writes=new List<string>();public bool Closed;public Func<string,string> Reply;
  public string ReadExisting(){return Reads.Count>0?Reads.Dequeue():"";}
  public void Write(byte[] bytes,int offset,int count){string text=Encoding.UTF8.GetString(bytes,offset,count);Writes.Add(text);if(Reply!=null)Reads.Enqueue(Reply(text));}
  public void DiscardInBuffer(){}
  public void Close(){Closed=true;}
  public void Dispose(){Closed=true;}
 }
 public static class SelfTests {
  static int passed;static List<string> failures=new List<string>();
  static void Test(string name,Action test){try{test();passed++;}catch(Exception e){failures.Add(name+": "+e.Message);}}
  static void Assert(bool value){if(!value)throw new Exception("Assertion failed");}
  static void Reject(Action action){try{action();}catch(InvalidDataException){return;}catch(IOException){return;}throw new Exception("Unsafe operation was accepted");}
  public static int Run(){
   UpdateTests.Run(Test,Assert);
   Test("Only an explicit configuration repair allows credentials to be resubmitted",()=>{
    string folder=Path.Combine(Path.GetTempPath(),"phoenix-config-retry-"+Guid.NewGuid().ToString("N"));var wire=new FakeWire();int configurations=0;
    wire.Reads.Enqueue("PHOENIX_EVENT {\"event\":\"need-policy\",\"protocol_version\":2}\nPHOENIX_EVENT {\"event\":\"need-config\",\"echo_disabled\":true}\nPHOENIX_EVENT {\"event\":\"need-retry\",\"protocol_version\":1,\"retry_id\":\"aaaaaaaaaaaaaaaa\",\"code\":\"configuration\",\"message\":\"Correct timezone\"}\nPHOENIX_EVENT {\"event\":\"need-config\",\"echo_disabled\":true}\nPHOENIX_EVENT {\"event\":\"complete\"}\n");
    try{using(var link=new SerialLink(wire,s=>{},folder,System.Threading.CancellationToken.None))link.MonitorInstaller(evt=>{configurations++;return "{}";},evt=>{},false,null,null,evt=>true);Assert(configurations==2&&wire.Writes.Count==4);}finally{Directory.Delete(folder,true);}
   });
   Test("UART repair checkpoint retries without booting or requesting credentials again",()=>{
    string folder=Path.Combine(Path.GetTempPath(),"phoenix-retry-"+Guid.NewGuid().ToString("N"));var wire=new FakeWire();int prompts=0;
    wire.Reads.Enqueue("PHOENIX_EVENT {\"event\":\"need-retry\",\"protocol_version\":1,\"retry_id\":\"aaaaaaaaaaaaaaaa\",\"code\":\"usb-missing\",\"message\":\"Insert USB\"}\nPHOENIX_EVENT {\"event\":\"need-policy\",\"protocol_version\":2}\nPHOENIX_EVENT {\"event\":\"need-config\",\"echo_disabled\":true}\nPHOENIX_EVENT {\"event\":\"need-retry\",\"protocol_version\":1,\"retry_id\":\"bbbbbbbbbbbbbbbb\",\"code\":\"hdd-busy\",\"message\":\"Close files\"}\nPHOENIX_EVENT {\"event\":\"complete\"}\n");
    try{using(var link=new SerialLink(wire,s=>{},folder,System.Threading.CancellationToken.None))link.MonitorInstaller(evt=>"{}",evt=>{},false,null,null,evt=>{prompts++;return true;});Assert(prompts==2&&wire.Writes.Count==4&&wire.Writes[0].StartsWith("PHOENIX_RETRY ")&&wire.Writes[3].StartsWith("PHOENIX_RETRY "));}finally{Directory.Delete(folder,true);}
   });
   Test("Repair replies reject stale or incompatible checkpoint formats",()=>{
    var evt=new Dictionary<string,object>{{"protocol_version",1},{"retry_id","aaaaaaaaaaaaaaaa"}};string line=RetryProtocol.Reply(evt,false);var decoded=Json.Read<Dictionary<string,object>>(Encoding.UTF8.GetString(Convert.FromBase64String(line.Substring(14))));Assert(!Convert.ToBoolean(decoded["retry"])&&Convert.ToString(decoded["retry_id"])=="aaaaaaaaaaaaaaaa");evt["protocol_version"]=2;Reject(()=>RetryProtocol.Reply(evt,true));
   });
   Test("Saved reconnect identity survives reopen and rejects another port",()=>{
    string folder=Path.Combine(Path.GetTempPath(),"phoenix-resume-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(folder);
    try{var saved=new ResumeRecord{DeviceAddress="169.254.3.1",HostAddress="169.254.3.2",Secret=new string('a',64),CidSha=new string('b',64),AdapterId="test-adapter",AdapterMac=new byte[]{1,2,3,4,5,6},Review=new DeviceReview{Identity="BCM97252SSV",Kernel="0",Rootfs="1"},SessionDirectory=folder,State="handoff"};string path=Path.Combine(folder,"resume.json");saved.Save(path);var loaded=ResumeRecord.Load(path);Assert(loaded.Secret==saved.Secret&&loaded.Review.Rootfs=="1");loaded.VerifyAdapter(new EthernetAdapter{Id="test-adapter",Mac=saved.AdapterMac});Reject(()=>loaded.VerifyAdapter(new EthernetAdapter{Id="other",Mac=saved.AdapterMac}));loaded.HostAddress="192.0.2.2";Reject(()=>loaded.Validate());loaded.HostAddress=saved.HostAddress;loaded.State="complete";loaded.Save(path);Assert(ResumeRecord.Load(path).State=="complete");}finally{Directory.Delete(folder,true);}
   });
   Test("Reconnected server must match saved CID and running slots",()=>{
    var saved=new ResumeRecord{CidSha=new string('a',64),Review=new DeviceReview{Kernel="1",Rootfs="0"}};
    var state=new Dictionary<string,object>{{"protocol_version",2},{"device_cid_sha256",saved.CidSha},{"kernel_slot",1},{"root_slot",0}};
    EthernetInstallLink.VerifySessionState(state,saved);state["device_cid_sha256"]=new string('b',64);Reject(()=>EthernetInstallLink.VerifySessionState(state,saved));state["device_cid_sha256"]=saved.CidSha;state["root_slot"]=1;Reject(()=>EthernetInstallLink.VerifySessionState(state,saved));
   });
   Test("Restored SSH host key must match the saved fingerprint",()=>{
    byte[] key=Encoding.ASCII.GetBytes("host-key-wire-fixture");string fingerprint;using(var sha=System.Security.Cryptography.SHA256.Create())fingerprint="SHA256:"+Convert.ToBase64String(sha.ComputeHash(key)).TrimEnd('=');string output="169.254.3.1 ssh-ed25519 "+Convert.ToBase64String(key);RecoverySession.VerifyHostKey(output,fingerprint);Reject(()=>RecoverySession.VerifyHostKey(output,"SHA256:other"));
   });
   Test("SSH recovery can be reopened again using the original key directory",()=>{
    string root=Path.Combine(Path.GetTempPath(),"phoenix-ssh-resume-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(root);string original=Path.Combine(root,"original"),reopened=Path.Combine(root,"reopened");Directory.CreateDirectory(Path.Combine(original,"keys"));Directory.CreateDirectory(reopened);
    try{File.WriteAllText(Path.Combine(original,"keys","maintenance_ed25519"),"key-fixture");var saved=new RecoverySession{Directory=original,HostAddress="169.254.3.2",AdapterId="test-adapter",Failure=new ExistingInstallationException("Retained installation"){Access=new RecoveryAccess{ready=true,address="169.254.3.1",port=22,host_fingerprint="SHA256:fixture"}}};saved.Save(reopened);var loaded=RecoverySession.Load(reopened);Assert(loaded.Directory==original&&loaded.HostAddress==saved.HostAddress&&loaded.Failure.Access.host_fingerprint==saved.Failure.Access.host_fingerprint);loaded.HostAddress="169.254.4.2";Reject(()=>loaded.Validate());}finally{Directory.Delete(root,true);}
   });
   Test("Temporary address waits through Tentative and bind readiness",()=>{long elapsed=0;int binds=0;AddressReadiness.Wait(()=>elapsed<5000?"Tentative":"Preferred",()=>++binds>=2,System.Threading.CancellationToken.None,10000,()=>elapsed+=100,()=>elapsed);Assert(elapsed>=5000&&binds==2);});
   Test("Temporary address duplicate and timeout are explicit",()=>{Reject(()=>AddressReadiness.Wait(()=>"Duplicate",()=>{throw new Exception("Must not bind duplicate");},System.Threading.CancellationToken.None));long elapsed=0;bool timedOut=false;try{AddressReadiness.Wait(()=>"Tentative",()=>{throw new Exception("Must not bind tentative");},System.Threading.CancellationToken.None,500,()=>elapsed+=100,()=>elapsed);}catch(TimeoutException e){timedOut=e.Message.Contains("Tentative");}Assert(timedOut);});
   Test("Temporary address readiness is cancellable",()=>{var source=new System.Threading.CancellationTokenSource();source.Cancel();bool stopped=false;try{AddressReadiness.Wait(()=>"Preferred",()=>{throw new Exception("Cancelled readiness must not bind");},source.Token);}catch(OperationCanceledException){stopped=true;}finally{source.Dispose();}Assert(stopped);});
   Test("Factory PRNG reference outputs",()=>{var values=FactoryCandidates.Vector(1);Assert(values[0]==1804289383&&values[1]==846930886&&values[2]==1681692777);});
   Test("All microsecond seeds produce the validated candidate set",()=>{var values=FactoryCandidates.Generate(System.Threading.CancellationToken.None);Assert(values.Count==970291);uint previous=0;foreach(uint value in values){Assert(value>previous&&(value>>24)==10&&(value&255)>0&&(value&255)<254);previous=value;}});
   Test("Raw ARP request and matched reply",()=>{byte[] local={2,0,0,0,0,1},remote={2,0,0,0,0,2};var packet=ArpPackets.Request(local,0x0a010203);Assert(packet.Length==60&&packet[38]==10&&packet[39]==1&&packet[40]==2&&packet[41]==3);Assert(ArpPackets.Reply(packet,local)==null);Array.Copy(local,0,packet,0,6);Array.Copy(remote,0,packet,6,6);packet[21]=2;Array.Copy(remote,0,packet,22,6);packet[28]=10;packet[29]=1;packet[30]=2;packet[31]=3;Array.Copy(local,0,packet,32,6);packet[38]=169;packet[39]=254;packet[40]=254;packet[41]=1;var box=ArpPackets.Reply(packet,local);Assert(box!=null&&box.Address=="10.1.2.3");packet[32]^=1;Assert(ArpPackets.Reply(packet,local)==null);});
   Test("Octal transport rejects malformed bytes",()=>{Assert(NvramMap.Octal("0000000 000 377 012\n0000003\n")[1]==255);Reject(()=>NvramMap.Octal("0000000 999"));});
   Test("NVRAM checksum and record preservation",()=>{byte[] data=new byte[2560];byte[] record=Encoding.ASCII.GetBytes("RUNLEVEL=4");data[160]=1;data[161]=(byte)(record.Length+1);data[162]=7;Array.Copy(record,0,data,163,record.Length);uint size=(uint)(record.Length+4),sum=0;for(int i=160;i<160+size;i++)sum+=data[i];Array.Copy(BitConverter.GetBytes(sum),0,data,0,4);Array.Copy(BitConverter.GetBytes(~sum),0,data,4,4);Array.Copy(BitConverter.GetBytes(size),0,data,8,4);var map=NvramMap.Parse(data);var copy=map.Copy();copy.Set("RUNLEVEL","1");Assert(map.Get("RUNLEVEL")=="4"&&copy.Records["RUNLEVEL"].Flags==7&&!copy.Same(map));data[163+record.Length-1]=128;sum=0;for(int i=160;i<160+size;i++)sum+=data[i];Array.Copy(BitConverter.GetBytes(sum),0,data,0,4);Array.Copy(BitConverter.GetBytes(~sum),0,data,4,4);var high= NvramMap.Parse(data);data[163+record.Length-1]=129;sum++;Array.Copy(BitConverter.GetBytes(sum),0,data,0,4);Array.Copy(BitConverter.GetBytes(~sum),0,data,4,4);Assert(!high.Same(NvramMap.Parse(data)));data[164]^=1;Reject(()=>NvramMap.Parse(data));});
   Test("Ctrl+C begins before boot without unlocking",()=>{var c=new Capture();Assert(c.Step(0,"")[0]==3&&c.Attempts==0);});
   Test("Split banner and bounded unlock quiet period",()=>{var c=new Capture();c.Step(0,"HYBR");c.Step(.01,"OAD\r\n");Assert(Encoding.ASCII.GetString(c.Step(.22,""))=="28392221221\r");Assert(c.Step(.4,"")==null);Assert(c.Step(.6,"")[0]==3);});
   Test("ANSI and split BOLT prompt stop all transmission",()=>{var c=new Capture();c.Step(0,"\r\n\x1b[32mBO");Assert(c.Step(.1,"LT>\x1b[0m ")==null&&c.Status=="prompt");Assert(c.Step(20,"")==null);});
   Test("Prompt mentioned in text is not a prompt",()=>{var c=new Capture();c.Step(0,"Type at BOLT> when ready\n");Assert(c.Status!="prompt");});
   Test("OS handoff stops before unlock",()=>{var c=new Capture();c.Step(0,"HYBROAD\n");Assert(c.Step(.3,"Starting program at 0x8000\n")==null&&c.Status=="os-started");});
   Test("Timeout stops input",()=>{var c=new Capture();Assert(c.Step(90,"")==null&&c.Status=="timeout");});
   Test("Unlock attempts capped at eight",()=>{var c=new Capture();for(int i=0;i<900;i++)c.Step(i/10.0,i==0?"BOLT v1.29\n":"");Assert(c.Attempts==8);});
   Test("STARTUP existing value",()=>Assert(DeviceReview.Value("printenv STARTUP\r\nSTARTUP=waitusb -t=10 && batch -fatfs usbdisk0:key/boot.txt\r\nBOLT> ","STARTUP")=="waitusb -t=10 && batch -fatfs usbdisk0:key/boot.txt"));
   Test("Linux checksums require LF without BOM",()=>{Usb.RequireLfChecksums(Encoding.ASCII.GetBytes(new string('a',64)+"  phoenix/stage1.sh\n"));foreach(byte[] data in new[]{Encoding.ASCII.GetBytes("hash  file\r\n"),Encoding.ASCII.GetBytes("hash  file"),new byte[0],new byte[]{239,187,191,10}})Reject(()=>Usb.RequireLfChecksums(data));});
   Test("Existing installation retains verified SSH details",()=>{var evt=Json.Read<Dictionary<string,object>>("{\"message\":\"Existing install\",\"access\":{\"ready\":true,\"address\":\"169.254.3.1\",\"port\":22,\"host_fingerprint\":\"SHA256:QUJD\"}}");var error=ExistingInstallationException.FromEvent(evt);Assert(error.Access.Valid&&error.Access.Instructions("key").Contains("root@169.254.3.1"));error.Access.address="1.2.3.4;bad";Assert(!error.Access.Valid&&!error.Access.Instructions("key").Contains("ssh -i"));});
   Test("Factory exit changes only RUNLEVEL for either firmware bank",()=>{foreach(string bank in new[]{"0","1"}){var before=new NvramMap();before.Set("KERNEL",bank);before.Set("ROOTFS",bank);before.Set("RUNLEVEL","4");before.Records["RUNLEVEL"].Flags=7;before.Set("MEMCONFIG","original");var after=EthernetInstallLink.FactoryExitTarget(before);Assert(before.Get("RUNLEVEL")=="4"&&after.Get("RUNLEVEL")=="1"&&after.Records["RUNLEVEL"].Flags==7&&after.Get("STARTUP")==null&&after.Get("MEMCONFIG")=="original");before.Set("STARTUP",BootSlots.Startup(bank,bank));after=EthernetInstallLink.FactoryExitTarget(before);Assert(after.Get("STARTUP")==before.Get("STARTUP"));after.Set("RUNLEVEL","4");Assert(after.Same(before));before.Set("ROOTFS","2");Reject(()=>EthernetInstallLink.FactoryExitTarget(before));}});
   Test("Serial existing-install SSH handoff requests only a public key",()=>{
    string folder=Path.Combine(Path.GetTempPath(),"phoenix-recovery-"+Guid.NewGuid().ToString("N"));var wire=new FakeWire();wire.Reads.Enqueue("PHOENIX_EVENT {\"event\":\"need-recovery-key\",\"protocol_version\":2}\n");
    wire.Reply=text=>{Assert(text.StartsWith("PHOENIX_RECOVERY_KEY "));return "PHOENIX_EVENT {\"event\":\"failed\",\"code\":\"existing-installation\",\"message\":\"Existing install\",\"access\":{\"ready\":true,\"address\":\"169.254.3.1\",\"port\":22,\"host_fingerprint\":\"SHA256:QUJD\"}}\n";};
    try{using(var link=new SerialLink(wire,s=>{},folder,System.Threading.CancellationToken.None)){bool stopped=false;try{link.MonitorInstaller(evt=>{throw new Exception("Passwords must not be requested");},evt=>{},false,()=>"ssh-ed25519 QUJD");}catch(ExistingInstallationException error){stopped=error.Access.Valid;}Assert(stopped&&wire.Writes.Count==1);}}finally{if(Directory.Exists(folder))Directory.Delete(folder,true);}
   });
   Test("Utility captures BOLT and releases serial without commands",()=>{
    string folder=Path.Combine(Path.GetTempPath(),"phoenix-utility-"+Guid.NewGuid().ToString("N"));var wire=new FakeWire();wire.Reads.Enqueue("BOLT> ");
    try{TerminalUtilities.Serial(wire,false,folder,System.Threading.CancellationToken.None,s=>{});Assert(wire.Closed&&wire.Writes.Count==0);}finally{if(Directory.Exists(folder))Directory.Delete(folder,true);}
   });
   Test("Utility Linux boots diagnostics only and releases serial",()=>{
    string folder=Path.Combine(Path.GetTempPath(),"phoenix-utility-"+Guid.NewGuid().ToString("N"));var wire=new FakeWire();wire.Reads.Enqueue("BOLT> ");
    wire.Reply=text=>{string command=text.Trim();if(command=="batch -fatfs usbdisk0:phoenix/d00.txt")return "Starting program at 0x8000\nPHOENIX_DIAGNOSTICS_READY\n";
     string body=command=="show devices"?"BCM97252SSV":command=="printenv STARTUP"?"STARTUP=printenv KERNEL":command=="printenv KERNEL"?"KERNEL=0":command=="printenv ROOTFS"?"ROOTFS=0":command=="printenv RUNLEVEL"?"RUNLEVEL=1":command=="printenv MEMCONFIG"?"MEMCONFIG=vmalloc=380m bmem=768m@1279m bmem=970m@2099m":command=="waitusb -t=10"?"USB ready":null;
     if(body==null)throw new Exception("Unsafe utility command: "+command);return body+"\nBOLT> ";};
    try{TerminalUtilities.Serial(wire,true,folder,System.Threading.CancellationToken.None,s=>{});Assert(wire.Closed&&wire.Writes.Count==8&&wire.Writes[7].Contains("d00.txt"));}finally{if(Directory.Exists(folder))Directory.Delete(folder,true);}
   });
   Test("Utility diagnostic failure releases serial",()=>{
    string folder=Path.Combine(Path.GetTempPath(),"phoenix-utility-"+Guid.NewGuid().ToString("N"));var wire=new FakeWire();wire.Reads.Enqueue("BOLT> ");wire.Reply=text=>{throw new IOException("Disconnected adapter");};
    try{Reject(()=>TerminalUtilities.Serial(wire,true,folder,System.Threading.CancellationToken.None,s=>{}));Assert(wire.Closed);}finally{if(Directory.Exists(folder))Directory.Delete(folder,true);}
   });
   Test("Cancelled utility releases serial",()=>{
    string folder=Path.Combine(Path.GetTempPath(),"phoenix-utility-"+Guid.NewGuid().ToString("N"));var wire=new FakeWire();var stop=new System.Threading.CancellationTokenSource();stop.Cancel();
    try{bool stopped=false;try{TerminalUtilities.Serial(wire,false,folder,stop.Token,s=>{});}catch(OperationCanceledException){stopped=true;}Assert(stopped&&wire.Closed);}finally{stop.Dispose();if(Directory.Exists(folder))Directory.Delete(folder,true);}
   });
   Test("STARTUP absent is distinct from empty",()=>{Assert(DeviceReview.Value("Variable STARTUP is not defined\nBOLT> ","STARTUP")==null);Assert(DeviceReview.Value("STARTUP=\nBOLT> ","STARTUP")=="");});
   Test("Status-only successful STARTUP response means absent",()=>{foreach(string echo in new[]{"printenv STARTUP","BOLT> printenv STARTUP"})Assert(DeviceReview.Value(echo+"\r\n*** command status = 0\r\nBOLT> ","STARTUP")==null);});
   Test("Failed incomplete and unexpected STARTUP responses remain rejected",()=>{foreach(string reply in new[]{"printenv STARTUP\n*** command status = 1\nBOLT> ","printenv STARTUP\n*** command status = 0\n","printenv STARTUP\nUnknown command\n*** command status = 0\nBOLT> ","*** command status = 0\nBOLT> "})Reject(()=>DeviceReview.Value(reply,"STARTUP"));Reject(()=>DeviceReview.Value("printenv KERNEL\n*** command status = 0\nBOLT> ","KERNEL"));});
   Test("Unmodified box inspection and activation accept absent STARTUP",()=>{
    string folder=Path.Combine(Path.GetTempPath(),"phoenix-absent-startup-"+Guid.NewGuid().ToString("N"));var wire=new FakeWire();string startup=null;
    wire.Reply=text=>{string command=text.Trim();string body;
     if(command=="show devices")body="BCM97252SSV flash0.kernel0";
     else if(command=="printenv STARTUP")body=startup==null?"":"STARTUP="+startup;
     else if(command=="printenv KERNEL")body="KERNEL=0";
     else if(command=="printenv ROOTFS")body="ROOTFS=0";
     else if(command=="printenv RUNLEVEL")body="RUNLEVEL=1";
     else if(command=="setenv -p RUNLEVEL 1")body="";
     else if(command=="printenv MEMCONFIG")body="MEMCONFIG=vmalloc=380m bmem=768m@1279m bmem=970m@2099m";
     else if(command.StartsWith("setenv -p STARTUP \"")){startup=command.Substring("setenv -p STARTUP \"".Length).TrimEnd('"');body="";}
     else throw new Exception("Unexpected BOLT command: "+command);
     return "BOLT> "+command+"\r\n"+body+"\r\n*** command status = 0\r\nBOLT> ";};
    try{using(var link=new SerialLink(wire,s=>{},folder,System.Threading.CancellationToken.None)){var review=link.Inspect();Assert(review.Compatible&&review.StartupAbsent&&review.Startup==null);Assert(wire.Writes.Count==6);link.CommitStartup(review);Assert(startup==BootSlots.Startup("0","0"));}}finally{if(Directory.Exists(folder))Directory.Delete(folder,true);}
   });
   Test("Ambiguous environment reply rejected",()=>Reject(()=>DeviceReview.Value("BOLT> ","STARTUP")));
   Test("Original BOLT STARTUP absence read from full table",()=>{Assert(DeviceReview.ListingValue("KERNEL 0\nROOTFS 0\nBOLT> ","STARTUP")==null);Assert(DeviceReview.ListingValue("KERNEL 0\nROOTFS 0\nSTARTUP\nBOLT> ","STARTUP")=="");Reject(()=>DeviceReview.ListingValue("BOLT> ","STARTUP"));});
   Test("Firmware banks 0 and 1 accepted without accepting arbitrary selections",()=>{foreach(string k in new[]{"0","1"})foreach(string r in new[]{"0","1"}){Assert(new DeviceReview{Identity="BCM97252SSV",Kernel=k,Rootfs=r}.Compatible);Assert(BootSlots.Entry("i",k,r)=="phoenix/i"+k+r+".txt");}Assert(!new DeviceReview{Identity="BCM97252SSV",Kernel="2",Rootfs="0"}.Compatible);Assert(!new DeviceReview{Identity="BCM97252SSV",Kernel="1",Rootfs="1",Memory="unknown"}.Compatible);Reject(()=>BootSlots.Entry("i","../1","1"));});
   Test("Running slot mismatch is rejected before policy or passwords",()=>{var expected=new DeviceReview{Kernel="1",Rootfs="1"};Assert(BootSlots.VerifyReported(new Dictionary<string,object>{{"kernel_slot",1},{"root_slot",1}},expected));Reject(()=>BootSlots.VerifyReported(new Dictionary<string,object>{{"kernel_slot",0},{"root_slot",1}},expected));string folder=Path.Combine(Path.GetTempPath(),"phoenix-slot-report-"+Guid.NewGuid().ToString("N"));var wire=new FakeWire();wire.Reads.Enqueue("PHOENIX_EVENT {\"event\":\"progress\",\"kernel_slot\":0,\"root_slot\":0}\n");try{using(var link=new SerialLink(wire,s=>{},folder,System.Threading.CancellationToken.None))Reject(()=>link.MonitorInstaller(evt=>{throw new Exception("Credentials must not be requested");},evt=>{},false,null,expected));Assert(wire.Writes.Count==0);}finally{if(Directory.Exists(folder))Directory.Delete(folder,true);}});
   Test("Slot 1 install and commit preserve stock selectors and verify fallback",()=>{
    string folder=Path.Combine(Path.GetTempPath(),"phoenix-slot1-"+Guid.NewGuid().ToString("N"));var wire=new FakeWire();string startup=null,level="4";
    wire.Reply=text=>{string c=text.Trim(),body;if(c=="show devices")body="BCM97252SSV";else if(c=="printenv KERNEL")body="KERNEL=1";else if(c=="printenv ROOTFS")body="ROOTFS=1";else if(c=="printenv MEMCONFIG")body="MEMCONFIG="+DeviceReview.MemoryLayout;else if(c=="printenv RUNLEVEL")body="RUNLEVEL="+level;else if(c=="printenv STARTUP")body=startup==null?"":"STARTUP="+startup;else if(c=="setenv -p RUNLEVEL 1"){level="1";body="";}else if(c.StartsWith("setenv -p STARTUP \"")){startup=c.Substring("setenv -p STARTUP \"".Length).TrimEnd('"');body="";}else if(c=="waitusb -t=10")body="USB ready";else if(c=="batch -fatfs usbdisk0:phoenix/u11.txt")return "";else throw new Exception("Unexpected command: "+c);return c+"\n"+body+"\n*** command status = 0\nBOLT> ";};
    try{using(var link=new SerialLink(wire,s=>{},folder,System.Threading.CancellationToken.None)){var review=link.Inspect();Assert(review.Compatible);link.BootInstaller(review);Assert(level=="1"&&startup==null&&wire.Writes.Contains("batch -fatfs usbdisk0:phoenix/u11.txt\r"));wire.Reads.Clear();link.CommitStartup(review);Assert(startup==BootSlots.Startup("1","1")&&level=="1");foreach(var command in wire.Writes)Assert(!command.StartsWith("setenv -p KERNEL")&&!command.StartsWith("setenv -p ROOTFS"));}}finally{if(Directory.Exists(folder))Directory.Delete(folder,true);}
   });
   Test("Changed firmware slots stop before boot or environment writes",()=>{
    string folder=Path.Combine(Path.GetTempPath(),"phoenix-slot-change-"+Guid.NewGuid().ToString("N"));var wire=new FakeWire();wire.Reply=text=>{string c=text.Trim();string body=c=="show devices"?"BCM97252SSV":c=="printenv STARTUP"?"STARTUP=printenv KERNEL":c=="printenv KERNEL"?"KERNEL=0":c=="printenv ROOTFS"?"ROOTFS=1":c=="printenv RUNLEVEL"?"RUNLEVEL=1":c=="printenv MEMCONFIG"?"MEMCONFIG="+DeviceReview.MemoryLayout:null;if(body==null)throw new Exception("Must not write: "+c);return body+"\nBOLT> ";};
    try{using(var link=new SerialLink(wire,s=>{},folder,System.Threading.CancellationToken.None))Reject(()=>link.BootInstaller(new DeviceReview{Identity="BCM97252SSV",Kernel="1",Rootfs="1"}));foreach(string c in wire.Writes)Assert(c.StartsWith("printenv")||c.StartsWith("show devices"));}finally{if(Directory.Exists(folder))Directory.Delete(folder,true);}
   });
   Test("Non USB and system disks refused",()=>{Reject(()=>new UsbDisk{BusType="SATA",Drive="E",UniqueId="x"}.Guard());Reject(()=>new UsbDisk{BusType="USB",Drive="E",UniqueId="x",IsSystem=true}.Guard());});
   Test("Unformatted USB allowed only for explicit formatting",()=>{var disk=new UsbDisk{Number=2,Drive="",UniqueId="usb-id",BusType="USB",Size=16000000000};Reject(()=>disk.Guard());disk.Guard(true);});
   Test("Path traversal and drive paths refused",()=>{string root=Path.Combine(Path.GetTempPath(),"payload");Reject(()=>Usb.Resolve(root,"../escape"));Reject(()=>Usb.Resolve(root,Path.Combine(Path.GetPathRoot(root),"escape")));Reject(()=>Usb.Resolve(root,"file:stream"));});
   Test("Valid payload and corruption detection",()=>{
    string folder=Path.Combine(Path.GetTempPath(),"phoenix-payload-test-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(folder);
    try{var files=new List<PayloadFile>();for(int i=0;i<5;i++){string name="file"+i;File.WriteAllText(Path.Combine(folder,name),"test");files.Add(new PayloadFile{Path=name,Sha256=Json.Sha(Path.Combine(folder,name)),Bytes=4});}File.WriteAllText(Path.Combine(folder,"payload.json"),Json.Write(new PayloadManifest{Format=1,ProtocolVersion=4,Files=files}));Reject(()=>Usb.Validate(folder));File.WriteAllText(Path.Combine(folder,"payload.json"),Json.Write(new PayloadManifest{Format=1,ProtocolVersion=5,Files=files}));Assert(Usb.Validate(folder).Files.Count==5);File.WriteAllText(Path.Combine(folder,"file0"),"oops");Reject(()=>Usb.Validate(folder));}finally{Directory.Delete(folder,true);}
   });
   Test("Secrets excluded even if device echoes during configuration",()=>{
    string folder=Path.Combine(Path.GetTempPath(),"phoenix-wire-test-"+Guid.NewGuid().ToString("N"));var wire=new FakeWire();wire.Reads.Enqueue("PHOENIX_EVENT {\"event\":\"need-policy\",\"protocol_version\":2}\n");wire.Reads.Enqueue("PHOENIX_EVENT {\"event\":\"need-config\",\"echo_disabled\":true}\n");wire.Reads.Enqueue("never-log-this-secret\n");wire.Reads.Enqueue("PHOENIX_EVENT {\"event\":\"complete\",\"message\":\"done\"}\n");var visible=new StringBuilder();
    try{using(var link=new SerialLink(wire,s=>visible.Append(s),folder,System.Threading.CancellationToken.None)){link.MonitorInstaller(evt=>"{\"root_password\":\"never-log-this-secret\"}",evt=>{});}Assert(wire.Writes.Count==2&&wire.Writes[1].StartsWith("PHOENIX_CONFIG "));Assert(!Json.Read<Dictionary<string,object>>(Encoding.UTF8.GetString(Convert.FromBase64String(wire.Writes[0].Substring(15).Trim())))["ignore_sha_mismatches"].Equals(true));Assert(!File.ReadAllText(Path.Combine(folder,"serial.log")).Contains("never-log-this-secret"));Assert(!visible.ToString().Contains("never-log-this-secret"));Assert(wire.Closed);}finally{Directory.Delete(folder,true);}
   });
   Test("Credentials refused unless echo-disabled acknowledgement received",()=>{
    string folder=Path.Combine(Path.GetTempPath(),"phoenix-wire-test-"+Guid.NewGuid().ToString("N"));var wire=new FakeWire();wire.Reads.Enqueue("PHOENIX_EVENT {\"event\":\"need-policy\",\"protocol_version\":2}\n");wire.Reads.Enqueue("PHOENIX_EVENT {\"event\":\"need-config\",\"echo_disabled\":false}\n");
    try{using(var link=new SerialLink(wire,s=>{},folder,System.Threading.CancellationToken.None)){Reject(()=>link.MonitorInstaller(evt=>{throw new Exception("Must not request secrets");},evt=>{}));}Assert(wire.Writes.Count==1);}finally{Directory.Delete(folder,true);}
   });
   Test("Cancellation reaches serial monitoring after phase change",()=>{
    string folder=Path.Combine(Path.GetTempPath(),"phoenix-wire-test-"+Guid.NewGuid().ToString("N"));var wire=new FakeWire();var token=new System.Threading.CancellationTokenSource();
    try{using(var link=new SerialLink(wire,s=>{},folder,System.Threading.CancellationToken.None)){link.SetCancellation(token.Token);token.Cancel();try{link.Read();throw new Exception("Cancellation ignored");}catch(OperationCanceledException){}}}finally{Directory.Delete(folder,true);}
   });
   Test("Hung Windows helper is terminated at the timeout",()=>{
    var watch=System.Diagnostics.Stopwatch.StartNew();try{Usb.PowerShell("Start-Sleep -Seconds 30",500);throw new Exception("Timeout ignored");}catch(TimeoutException){}Assert(watch.Elapsed.TotalSeconds<4);
   });
   Test("Windows helper cancellation is prompt",()=>{
    using(var cancel=new System.Threading.CancellationTokenSource()){cancel.CancelAfter(200);var watch=System.Diagnostics.Stopwatch.StartNew();try{Usb.PowerShell("Start-Sleep -Seconds 30",10000,cancel.Token);throw new Exception("Cancellation ignored");}catch(OperationCanceledException){}Assert(watch.Elapsed.TotalSeconds<4);}
   });
   Test("Both redirected pipes drain without deadlock",()=>{
    string result=Usb.PowerShell("[Console]::Error.Write(('E'*262144));[Console]::Out.Write(('O'*262144))",10000);Assert(result.Length==262144&&result[0]=='O');
   });
   Test("Firmware override is transmitted only when explicitly selected",()=>{
    string folder=Path.Combine(Path.GetTempPath(),"phoenix-wire-test-"+Guid.NewGuid().ToString("N"));var wire=new FakeWire();wire.Reads.Enqueue("PHOENIX_EVENT {\"event\":\"need-policy\",\"protocol_version\":2}\n");wire.Reads.Enqueue("PHOENIX_EVENT {\"event\":\"need-config\",\"echo_disabled\":true}\n");wire.Reads.Enqueue("PHOENIX_EVENT {\"event\":\"complete\"}\n");
    try{using(var link=new SerialLink(wire,s=>{},folder,System.Threading.CancellationToken.None))link.MonitorInstaller(evt=>"{}",evt=>{},true);var policy=Json.Read<Dictionary<string,object>>(Encoding.UTF8.GetString(Convert.FromBase64String(wire.Writes[0].Substring(15).Trim())));Assert(Convert.ToBoolean(policy["ignore_sha_mismatches"]));}finally{Directory.Delete(folder,true);}
   });
   Test("Old USB protocol refused before requesting credentials",()=>{
    string folder=Path.Combine(Path.GetTempPath(),"phoenix-wire-test-"+Guid.NewGuid().ToString("N"));var wire=new FakeWire();wire.Reads.Enqueue("PHOENIX_EVENT {\"event\":\"need-config\",\"echo_disabled\":true}\n");
    try{using(var link=new SerialLink(wire,s=>{},folder,System.Threading.CancellationToken.None))Reject(()=>link.MonitorInstaller(evt=>{throw new Exception("Secrets requested too early");},evt=>{}));Assert(wire.Writes.Count==0);}finally{Directory.Delete(folder,true);}
   });
   Test("Completion cannot skip the configuration phase",()=>{
    string folder=Path.Combine(Path.GetTempPath(),"phoenix-wire-test-"+Guid.NewGuid().ToString("N"));var wire=new FakeWire();wire.Reads.Enqueue("PHOENIX_EVENT {\"event\":\"complete\"}\n");
    try{using(var link=new SerialLink(wire,s=>{},folder,System.Threading.CancellationToken.None))Reject(()=>link.MonitorInstaller(evt=>"{}",evt=>{}));}finally{Directory.Delete(folder,true);}
   });
   Test("USB capacity guard rejects unknown and oversized media",()=>{var d=new UsbDisk{BusType="USB",UniqueId="fake",Drive="E",Size=UsbDisk.MaximumSize};d.Guard();d.Size++;Reject(()=>d.Guard());d.Size=0;Reject(()=>d.Guard());});
   Test("Formatting recipe loads from scripts folder and matches its hash",()=>Assert(Usb.FormattingScript(new UsbDisk{Number=99,Size=16000000000}).Contains("function Assert-UsbIdentity")));
   Test("PowerShell errors are readable text",()=>{try{Usb.PowerShell("throw 'synthetic formatter failure'");throw new Exception("Failure ignored");}catch(IOException e){Assert(e.Message.Contains("synthetic formatter failure")&&!e.Message.Contains("CLIXML")&&!e.Message.Contains("<Objs"));}});
   Test("Complete early failure line surfaces its reason",()=>{
    string folder=Path.Combine(Path.GetTempPath(),"phoenix-early-failure-"+Guid.NewGuid().ToString("N"));var wire=new FakeWire();wire.Reads.Enqueue("PHOENIX_STAGE1_STOPPED: checksums\n");
    try{using(var link=new SerialLink(wire,s=>{},folder,System.Threading.CancellationToken.None)){try{link.MonitorInstaller(evt=>{throw new Exception("Credentials requested");},evt=>{});throw new Exception("Failure ignored");}catch(IOException e){Assert(e.Message.Contains("checksums"));}}Assert(wire.Writes.Count==0);}finally{Directory.Delete(folder,true);}
   });
   Test("Existing installation is surfaced as a specific protected stop",()=>{
    string folder=Path.Combine(Path.GetTempPath(),"phoenix-existing-"+Guid.NewGuid().ToString("N"));var wire=new FakeWire();wire.Reads.Enqueue("PHOENIX_EVENT {\"event\":\"failed\",\"code\":\"existing-installation\",\"message\":\"This box already has an installation.\"}\n");
    try{using(var link=new SerialLink(wire,s=>{},folder,System.Threading.CancellationToken.None)){try{link.MonitorInstaller(evt=>{throw new Exception("Credentials requested");},evt=>{});throw new Exception("Failure ignored");}catch(ExistingInstallationException e){Assert(e.Message.Contains("already has an installation"));}}Assert(wire.Writes.Count==0);}finally{Directory.Delete(folder,true);}
   });
   Test("Automatic maintenance key pair matches and cannot be overwritten",()=>{
    string folder=Path.Combine(Path.GetTempPath(),"phoenix key test "+Guid.NewGuid().ToString("N")),path=Path.Combine(folder,"keys","maintenance_ed25519");
    try{string key=MaintenanceKeys.Generate(folder);Assert(File.Exists(path)&&File.Exists(path+".pub"));string before=Json.Sha(path);string generator=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System),"OpenSSH","ssh-keygen.exe");string derived=ProcessRunner.Execute(generator,"-y -f \""+path+"\"",10000,System.Threading.CancellationToken.None).Trim();Assert(key.StartsWith(derived));Reject(()=>MaintenanceKeys.Generate(folder));Assert(Json.Sha(path)==before);}
    finally{if(File.Exists(path))File.Delete(path);if(File.Exists(path+".pub"))File.Delete(path+".pub");if(Directory.Exists(Path.Combine(folder,"keys")))Directory.Delete(Path.Combine(folder,"keys"));if(Directory.Exists(folder))Directory.Delete(folder);}
   });
   Test("Cancelled key generation does not create files",()=>{
    string folder=Path.Combine(Path.GetTempPath(),"phoenix-cancel-key-"+Guid.NewGuid().ToString("N"));using(var cancel=new System.Threading.CancellationTokenSource()){cancel.Cancel();try{MaintenanceKeys.Generate(folder,cancel.Token);throw new Exception("Cancellation ignored");}catch(OperationCanceledException){}Assert(!Directory.Exists(folder));}
   });
   File.WriteAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"self-tests.json"),Json.Write(new{passed=passed,failures=failures}));return failures.Count==0?0:1;
  }
 }
}
