using System;
using System.IO;
using System.Text;
using System.Net;
using System.Net.NetworkInformation;
using System.Runtime.InteropServices;
using System.Collections.Generic;
using System.Threading;
using System.Diagnostics;

namespace PhoenixSetup {
 public sealed class EthernetAdapter {
  public string Id,Name,Description;public byte[] Mac;public int Index;
  public override string ToString(){return Name+" — "+Description;}
  public static List<EthernetAdapter> List(){
   var result=new List<EthernetAdapter>();
   foreach(var n in NetworkInterface.GetAllNetworkInterfaces()){
    string description=n.Description.ToLowerInvariant();
    if(n.NetworkInterfaceType!=NetworkInterfaceType.Ethernet||n.GetPhysicalAddress().GetAddressBytes().Length!=6||description.Contains("virtual")||description.Contains("npcap")||description.Contains("tailscale")||description.Contains("hyper-v"))continue;
    try{result.Add(new EthernetAdapter{Id=n.Id,Name=n.Name,Description=n.Description,Mac=n.GetPhysicalAddress().GetAddressBytes(),Index=n.GetIPProperties().GetIPv4Properties().Index});}catch(NetworkInformationException){}
   }return result;
  }
  public void Verify(){var current=List().Find(n=>String.Equals(n.Id,Id,StringComparison.OrdinalIgnoreCase));if(current==null||BitConverter.ToString(current.Mac)!=BitConverter.ToString(Mac))throw new IOException("The selected Ethernet adapter changed. Select it again.");Index=current.Index;}
 }
 public static class FactoryCandidates {
  static uint Next(uint[] state,ref int f,ref int r){uint sum=unchecked(state[f]+state[r]);state[f]=sum;if(++f==31)f=0;if(++r==31)r=0;return sum>>1;}
  static void Init(uint seed,uint[] state,out int f,out int r){long word=seed==0?1:seed;state[0]=(uint)word;for(int i=1;i<31;i++){word=(16807*word)%2147483647;state[i]=(uint)word;}f=3;r=0;for(int i=0;i<310;i++)Next(state,ref f,ref r);}
  public static uint[] Vector(uint seed){var state=new uint[31];int f,r;Init(seed,state,out f,out r);return new[]{Next(state,ref f,ref r),Next(state,ref f,ref r),Next(state,ref f,ref r)};}
  public static List<uint> Generate(CancellationToken token){
   var seen=new byte[1<<24];var state=new uint[31];int f,r;
   for(uint seed=0;seed<1000000;seed++){
    if((seed&1023)==0)token.ThrowIfCancellationRequested();Init(seed,state,out f,out r);uint a,b,c;
    do{a=Next(state,ref f,ref r)%254;b=Next(state,ref f,ref r)%254;c=Next(state,ref f,ref r)%254;}while((a|b)==0||c==0);
    seen[(a<<16)|(b<<8)|c]=1;
   }
   var result=new List<uint>(1000000);for(uint i=0;i<seen.Length;i++)if(seen[i]!=0)result.Add(0x0a000000|i);return result;
  }
 }
 public sealed class DiscoveredBox {public string Address;public byte[] Mac;public long Probes;public double Seconds;}
 public static class ArpPackets {
  public static byte[] Request(byte[] mac,uint target){
   if(mac==null||mac.Length!=6)throw new ArgumentException("Ethernet MAC required");
   var p=new byte[60];for(int i=0;i<6;i++)p[i]=255;Array.Copy(mac,0,p,6,6);p[12]=8;p[13]=6;p[15]=1;p[16]=8;p[18]=6;p[19]=4;p[21]=1;Array.Copy(mac,0,p,22,6);
   // Raw ARP discovery does not need a Windows IP alias. Replies are captured on this adapter.
   p[28]=169;p[29]=254;p[30]=254;p[31]=1;for(int i=0;i<4;i++)p[38+i]=(byte)(target>>(24-8*i));return p;
  }
  public static DiscoveredBox Reply(byte[] p,byte[] local){
   if(p.Length<42||p[12]!=8||p[13]!=6||p[14]!=0||p[15]!=1||p[16]!=8||p[17]!=0||p[18]!=6||p[19]!=4||p[20]!=0||p[21]!=2)return null;
   for(int i=0;i<6;i++)if(p[32+i]!=local[i]||p[22+i]!=p[6+i])return null;
   if(p[38]!=169||p[39]!=254||p[40]!=254||p[41]!=1)return null;
   var mac=new byte[6];Array.Copy(p,22,mac,0,6);return new DiscoveredBox{Address=new IPAddress(new[]{p[28],p[29],p[30],p[31]}).ToString(),Mac=mac};
  }
 }
 public sealed class NpcapDiscovery:IDisposable {
  [StructLayout(LayoutKind.Sequential)]struct Header{public int Seconds,Microseconds;public uint Captured,Length;}
  [StructLayout(LayoutKind.Sequential)]struct Queue{public uint Maximum,Length;public IntPtr Buffer;}
  [StructLayout(LayoutKind.Sequential)]struct Filter{public uint Length;public IntPtr Instructions;}
  [UnmanagedFunctionPointer(CallingConvention.Cdecl)]delegate IntPtr OpenFn([MarshalAs(UnmanagedType.LPStr)]string name,int snap,int promiscuous,int timeout,StringBuilder error);
  [UnmanagedFunctionPointer(CallingConvention.Cdecl)]delegate void CloseFn(IntPtr handle);
  [UnmanagedFunctionPointer(CallingConvention.Cdecl)]delegate int NonblockFn(IntPtr handle,int value,StringBuilder error);
  [UnmanagedFunctionPointer(CallingConvention.Cdecl)]delegate int NextFn(IntPtr handle,out IntPtr header,out IntPtr data);
  [UnmanagedFunctionPointer(CallingConvention.Cdecl)]delegate int CompileFn(IntPtr handle,out Filter program,[MarshalAs(UnmanagedType.LPStr)]string text,int optimize,uint mask);
  [UnmanagedFunctionPointer(CallingConvention.Cdecl)]delegate int SetFilterFn(IntPtr handle,ref Filter program);
  [UnmanagedFunctionPointer(CallingConvention.Cdecl)]delegate void FreeFn(ref Filter program);
  [UnmanagedFunctionPointer(CallingConvention.Cdecl)]delegate IntPtr AllocateFn(uint bytes);
  [UnmanagedFunctionPointer(CallingConvention.Cdecl)]delegate uint TransmitFn(IntPtr handle,IntPtr queue,int synchronize);
  [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern IntPtr LoadLibraryEx(string path,IntPtr file,uint flags);
  [DllImport("kernel32.dll",CharSet=CharSet.Ansi)]static extern IntPtr GetProcAddress(IntPtr module,string name);
  [DllImport("kernel32.dll")]static extern bool FreeLibrary(IntPtr module);
  IntPtr library,rx,tx;OpenFn open;CloseFn close,destroy;NonblockFn nonblock;NextFn next;CompileFn compile;SetFilterFn setFilter;FreeFn free;AllocateFn allocate;TransmitFn transmit;
  public static bool Installed{get{return File.Exists(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System),"Npcap","wpcap.dll"));}}
  T Function<T>(string name)where T:class{var address=GetProcAddress(library,name);if(address==IntPtr.Zero)throw new IOException("Npcap API missing: "+name);return Marshal.GetDelegateForFunctionPointer(address,typeof(T))as T;}
  public NpcapDiscovery(EthernetAdapter adapter){
   if(!Installed)throw new IOException("This computer needs the free Npcap network helper to find the DVR. Use Install the network helper on the Find DVR page, then try again.");
   adapter.Verify();string path=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System),"Npcap","wpcap.dll");
   library=LoadLibraryEx(path,IntPtr.Zero,8);if(library==IntPtr.Zero)throw new IOException("Npcap could not load. Check its installation and run Phoenix Setup as administrator.");
   try{
    open=Function<OpenFn>("pcap_open_live");close=Function<CloseFn>("pcap_close");nonblock=Function<NonblockFn>("pcap_setnonblock");next=Function<NextFn>("pcap_next_ex");compile=Function<CompileFn>("pcap_compile");setFilter=Function<SetFilterFn>("pcap_setfilter");free=Function<FreeFn>("pcap_freecode");allocate=Function<AllocateFn>("pcap_sendqueue_alloc");destroy=Function<CloseFn>("pcap_sendqueue_destroy");transmit=Function<TransmitFn>("pcap_sendqueue_transmit");
    string name="\\Device\\NPF_"+adapter.Id;var error=new StringBuilder(512);rx=open(name,128,1,20,error);tx=open(name,128,0,20,error);
    if(rx==IntPtr.Zero||tx==IntPtr.Zero||nonblock(rx,1,error)!=0)throw new IOException("Npcap could not open the selected adapter: "+error);
    Filter program;string mac=BitConverter.ToString(adapter.Mac).Replace('-',':');if(compile(rx,out program,"not ether src "+mac,1,0xffffffff)!=0)throw new IOException("Could not prepare Ethernet capture");
    try{if(setFilter(rx,ref program)!=0)throw new IOException("Could not apply Ethernet capture filter");}finally{free(ref program);}
   }catch{Dispose();throw;}
  }
  public DiscoveredBox Search(EthernetAdapter adapter,IList<uint> candidates,bool full,CancellationToken token,Action<string> progress,string folder){
   Directory.CreateDirectory(folder);IntPtr queue=allocate(76*256);if(queue==IntPtr.Zero)throw new IOException("Npcap queue allocation failed");
   using(var capture=new BinaryWriter(File.Create(Path.Combine(folder,"discovery.pcap")))){
    capture.Write(0xa1b2c3d4u);capture.Write((ushort)2);capture.Write((ushort)4);capture.Write(0u);capture.Write(0u);capture.Write(128u);capture.Write(1u);
    var watch=Stopwatch.StartNew();long total=full?16777216:candidates.Count,sent=0;double last=-5;
    try{
     while(sent<total){
      token.ThrowIfCancellationRequested();int count=(int)Math.Min(256,total-sent);var payload=new byte[count*76];
      for(int j=0;j<count;j++){
       uint address=full?0x0a000000u+(uint)(sent+j):candidates[(int)sent+j];byte[] packet=ArpPackets.Request(adapter.Mac,address);
       Array.Copy(BitConverter.GetBytes(j*100),0,payload,j*76+4,4);Array.Copy(BitConverter.GetBytes(60),0,payload,j*76+8,4);Array.Copy(BitConverter.GetBytes(60),0,payload,j*76+12,4);Array.Copy(packet,0,payload,j*76+16,60);
      }
      var q=(Queue)Marshal.PtrToStructure(queue,typeof(Queue));Marshal.Copy(payload,0,q.Buffer,payload.Length);q.Length=(uint)payload.Length;Marshal.StructureToPtr(q,queue,false);
      if(transmit(tx,queue,1)!=(uint)payload.Length)throw new IOException("Ethernet transmission stopped; capture logs saved");sent+=count;
      IntPtr header,data;int result;
      for(int grace=0;grace<=(sent>=total?60:0);grace++){
      while((result=next(rx,out header,out data))==1){
       var h=(Header)Marshal.PtrToStructure(header,typeof(Header));if(h.Captured>128)throw new IOException("Invalid Ethernet capture size. Check you have selected the correct Ethernet port and connected the DVR directly to it.");var packet=new byte[h.Captured];Marshal.Copy(data,packet,0,packet.Length);
       capture.Write(h.Seconds);capture.Write(h.Microseconds);capture.Write(h.Captured);capture.Write(h.Length);capture.Write(packet);
       var box=ArpPackets.Reply(packet,adapter.Mac);if(box!=null){box.Probes=sent;box.Seconds=watch.Elapsed.TotalSeconds;File.WriteAllText(Path.Combine(folder,"discovery.json"),Json.Write(box));return box;}
      }
      if(result<0)throw new IOException("Ethernet capture stopped");
      if(sent>=total&&grace<60){token.ThrowIfCancellationRequested();Thread.Sleep(50);}
      }
      int delay=(int)(sent/10-watch.ElapsedMilliseconds);if(delay>0&&token.WaitHandle.WaitOne(delay))token.ThrowIfCancellationRequested();
      if(watch.Elapsed.TotalSeconds-last>=1){last=watch.Elapsed.TotalSeconds;string message=String.Format("Finding factory IP: {0:N0} / {1:N0} ({2:0.0}%)",sent,total,100.0*sent/total);progress(message);try{File.WriteAllText(Path.Combine(folder,"discovery-progress.json"),Json.Write(new{probes=sent,total=total,seconds=watch.Elapsed.TotalSeconds,full_range=full}));}catch(IOException){}}
     }
    }finally{destroy(queue);}
   }throw new IOException("No factory address found. Check the direct cable and remote sequence, then retry. Full-range search is available under connection options.");
  }
  public void Dispose(){if(rx!=IntPtr.Zero){close(rx);rx=IntPtr.Zero;}if(tx!=IntPtr.Zero){close(tx);tx=IntPtr.Zero;}if(library!=IntPtr.Zero){FreeLibrary(library);library=IntPtr.Zero;}}
 }
}
