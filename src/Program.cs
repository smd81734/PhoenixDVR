using System;
using System.IO;
using System.IO.Ports;
using System.Text;
using System.Drawing;
using System.Windows.Forms;
using System.Threading;
using System.Threading.Tasks;
using System.Collections.Generic;
using System.Text.RegularExpressions;
using System.Diagnostics;

namespace PhoenixSetup {
 public static class SetupVersion {
  public const string App="preview30";
  public const string Phoenix="2026.11.26-preview30";
 }
 public static class TimeZones {
  // Windows time zone ids mapped to the IANA names the box uses. Unknown zones fall back to Sydney.
  static readonly Dictionary<string,string> map=new Dictionary<string,string>(StringComparer.OrdinalIgnoreCase){
   {"AUS Eastern Standard Time","Australia/Sydney"},{"E. Australia Standard Time","Australia/Brisbane"},{"Cen. Australia Standard Time","Australia/Adelaide"},
   {"AUS Central Standard Time","Australia/Darwin"},{"Aus Central W. Standard Time","Australia/Eucla"},{"W. Australia Standard Time","Australia/Perth"},
   {"Tasmania Standard Time","Australia/Hobart"},{"Lord Howe Standard Time","Australia/Lord_Howe"},{"New Zealand Standard Time","Pacific/Auckland"},
   {"GMT Standard Time","Europe/London"},{"W. Europe Standard Time","Europe/Berlin"},{"Romance Standard Time","Europe/Paris"},{"UTC","UTC"}};
  public static string Local(){string iana;try{if(map.TryGetValue(TimeZoneInfo.Local.Id,out iana))return iana;}catch(Exception){}return "Australia/Sydney";}
  public static string Friendly(string iana){int slash=iana.LastIndexOf('/');return (slash<0?iana:iana.Substring(slash+1)).Replace('_',' ');}
 }
 public sealed class ConfigDialog:Form {
  readonly TextBox hostname=new TextBox{Text="phoenix"},timezone=new TextBox{Text=TimeZones.Local()};
  readonly ComboBox band=new ComboBox{DropDownStyle=ComboBoxStyle.DropDownList};
  readonly TextBox root=new TextBox{UseSystemPasswordChar=true,MaxLength=128},rootAgain=new TextBox{UseSystemPasswordChar=true,MaxLength=128},tvh=new TextBox{UseSystemPasswordChar=true,MaxLength=128},tvhAgain=new TextBox{UseSystemPasswordChar=true,MaxLength=128};
  readonly CheckBox separateTvh=new CheckBox{Text="Use a different password for Tvheadend (the TV guide and recordings web page)",AutoSize=true,MaximumSize=new Size(590,0),Margin=new Padding(3,10,3,4)};
  // Optional Wi-Fi: saved on the DVR for phoenix-wifi. Ethernet is still used whenever it is plugged in.
  readonly CheckBox useWifi=new CheckBox{Text="Set up Wi-Fi (the DVR still uses Ethernet whenever its cable is plugged in)",AutoSize=true,MaximumSize=new Size(590,0),Margin=new Padding(3,10,3,4)};
  readonly TextBox wifiName=new TextBox{MaxLength=32},wifiPassword=new TextBox{UseSystemPasswordChar=true,MaxLength=64};
  readonly CheckBox showWifiPassword=new CheckBox{Text="Show password",AutoSize=true};
  readonly bool simple;
  public string Result;
  public ConfigDialog(string review,bool simpleView){
   simple=simpleView;Text=simple?"A couple of details":"Configure Phoenix";Size=new Size(660,simple?500:520);StartPosition=FormStartPosition.CenterParent;Font=new Font("Segoe UI",10);MinimizeBox=false;MaximizeBox=false;BackColor=Theme.Bg;ForeColor=Theme.Fg;
   var panel=new TableLayoutPanel{Dock=DockStyle.Fill,Padding=new Padding(22),ColumnCount=2,AutoScroll=true};panel.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,38));panel.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,62));Controls.Add(panel);
   band.Items.AddRange(new object[]{"au","eu"});band.SelectedIndex=0;
   if(simple){
    var intro=new Label{Text="Your DVR and its recordings disk passed the checks ("+review+"). Choose a name and a password, then setup carries on by itself.",AutoSize=true,MaximumSize=new Size(590,0),ForeColor=Theme.Muted,Margin=new Padding(3,0,3,14)};panel.Controls.Add(intro,0,panel.RowCount++);panel.SetColumnSpan(intro,2);panel.RowStyles.Add(new RowStyle(SizeType.AutoSize));
    Add(panel,"Name for your DVR",hostname);Add(panel,"Choose a password",root);Add(panel,"Type it again",rootAgain);
    panel.Controls.Add(separateTvh,0,panel.RowCount++);panel.SetColumnSpan(separateTvh,2);panel.RowStyles.Add(new RowStyle(SizeType.AutoSize));
    var tvhRows=new[]{Add(panel,"Tvheadend password",tvh),Add(panel,"Type it again",tvhAgain)};
    Action showTvh=()=>{foreach(var l in tvhRows)l.Visible=separateTvh.Checked;tvh.Visible=tvhAgain.Visible=separateTvh.Checked;Height=separateTvh.Checked?580:500;};
    separateTvh.CheckedChanged+=(s,e)=>showTvh();
    AddWifi(panel);
    Action resize=()=>{Height=500+(separateTvh.Checked?80:0)+(useWifi.Checked?120:0);};
    separateTvh.CheckedChanged+=(s,e)=>resize();useWifi.CheckedChanged+=(s,e)=>resize();showTvh();resize();
    var note=new Label{Text="You'll need this password to update the DVR later. Write it down somewhere safe.\nTime zone: "+TimeZones.Friendly(timezone.Text)+" (same as this computer).",AutoSize=true,MaximumSize=new Size(590,0),BackColor=Theme.NoteBg,Padding=new Padding(10),Margin=new Padding(3,12,3,12)};panel.Controls.Add(note,0,panel.RowCount++);panel.SetColumnSpan(note,2);panel.RowStyles.Add(new RowStyle(SizeType.AutoSize));
   }else{
    Add(panel,"Target",new Label{Text=review,AutoSize=true,MaximumSize=new Size(350,0)});Add(panel,"Hostname",hostname);Add(panel,"Timezone",timezone);Add(panel,"Band plan",band);
    Add(panel,"Root password",root);Add(panel,"Confirm root password",rootAgain);Add(panel,"Tvheadend password",tvh);Add(panel,"Confirm TVH password",tvhAgain);
    AddWifi(panel);useWifi.CheckedChanged+=(s,e)=>{Height=520+(useWifi.Checked?120:0);};
   }
   var go=new Button{Text=simple?"Continue setup":"Install on this HDD",AutoSize=true,Tag=Theme.Primary};go.Click+=(s,e)=>Submit();Add(panel,"",go);go.Dock=DockStyle.Right;go.Height=38;AcceptButton=go;
   Theme.Apply(this);
  }
  public void ShowSeparateTvh(){separateTvh.Checked=true;}
  public void ShowWifi(){useWifi.Checked=true;wifiName.Text="Home network";wifiPassword.Text="example-only";}
  void AddWifi(TableLayoutPanel panel){
   panel.Controls.Add(useWifi,0,panel.RowCount++);panel.SetColumnSpan(useWifi,2);panel.RowStyles.Add(new RowStyle(SizeType.AutoSize));
   var rows=new[]{Add(panel,"Wi-Fi network name",wifiName),Add(panel,"Wi-Fi password",wifiPassword),Add(panel,"",showWifiPassword)};
   showWifiPassword.CheckedChanged+=(s,e)=>wifiPassword.UseSystemPasswordChar=!showWifiPassword.Checked;
   Action show=()=>{foreach(var l in rows)l.Visible=useWifi.Checked;wifiName.Visible=wifiPassword.Visible=showWifiPassword.Visible=useWifi.Checked;};
   useWifi.CheckedChanged+=(s,e)=>show();show();
  }
  public static string CheckWifi(string name,string password){
   // The same rules the DVR applies (phoenix-wifi): 1-32 byte name; WPA2 passphrase of 8-63
   // characters or 64 hex digits; an empty password means an open network.
   if(String.IsNullOrEmpty(name)||Encoding.UTF8.GetByteCount(name)>32||Regex.IsMatch(name,@"[\x00-\x1f\x7f]"))return "Enter the Wi-Fi network name exactly as it appears on your phone or PC (up to 32 characters).";
   if(password.Length>0&&(Regex.IsMatch(password,@"[\x00-\x1f\x7f]")||!(password.Length>=8&&password.Length<=63||Regex.IsMatch(password,@"^[0-9A-Fa-f]{64}$"))))return "The Wi-Fi password must be 8 to 63 characters. Leave it empty only for an open network.";
   return null;
  }
  static Label Add(TableLayoutPanel panel,string name,Control c){int row=panel.RowCount++;panel.RowStyles.Add(new RowStyle(SizeType.AutoSize));var l=new Label{Text=name,AutoSize=true,Margin=new Padding(0,9,5,9)};c.Dock=DockStyle.Fill;c.Margin=new Padding(3,5,3,5);panel.Controls.Add(l,0,row);panel.Controls.Add(c,1,row);return l;}
  void Submit(){
   try {
    if(!Regex.IsMatch(hostname.Text,@"^[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$"))throw new Exception(simple?"Use only letters, numbers and dashes for the DVR name, with no spaces.":"Choose a valid hostname.");
    if(simple&&!separateTvh.Checked){tvh.Text=root.Text;tvhAgain.Text=rootAgain.Text;}
    foreach(string value in new[]{root.Text,tvh.Text})if(String.IsNullOrEmpty(value)||Regex.IsMatch(value,@"[\x00-\x1f\x7f]"))throw new Exception(simple?"Choose a password.":"Passwords must be nonempty and contain no control characters.");
    if(root.Text!=rootAgain.Text||tvh.Text!=tvhAgain.Text)throw new Exception(simple?"The two passwords don't match. Type them again.":"The password confirmations do not match.");
    if(useWifi.Checked){string problem=CheckWifi(wifiName.Text,wifiPassword.Text);if(problem!=null)throw new Exception(problem);}
    Result=Json.Write(new{hostname=hostname.Text,timezone=timezone.Text,band_plan=Convert.ToString(band.SelectedItem),root_password=root.Text,tvh_password=tvh.Text,wifi_ssid=useWifi.Checked?wifiName.Text:"",wifi_password=useWifi.Checked?wifiPassword.Text:"",utc_seconds=(long)(DateTime.UtcNow-new DateTime(1970,1,1)).TotalSeconds});root.Clear();rootAgain.Clear();tvh.Clear();tvhAgain.Clear();wifiPassword.Clear();DialogResult=DialogResult.OK;Close();
   }catch(Exception e){MessageBox.Show(this,e.Message,Text,MessageBoxButtons.OK,MessageBoxIcon.Information);}
  }
 }
 public sealed class RepairDialog:Form {
  public RepairDialog(string message){
   Text="Setup needs your help";Size=new Size(760,540);StartPosition=FormStartPosition.CenterParent;Font=new Font("Segoe UI",10);MinimizeBox=false;MaximizeBox=false;BackColor=Theme.Bg;ForeColor=Theme.Fg;
   var head=new Label{Text="Setup has paused. Nothing has been lost.\nFix the problem below, then press Try again. Keep the DVR switched on.",Dock=DockStyle.Top,AutoSize=false,Height=58,Font=new Font("Segoe UI Semibold",11)};
   var text=new TextBox{Text=message+"\r\n\r\nCancel setup stops this attempt.",ReadOnly=true,Multiline=true,ScrollBars=ScrollBars.Vertical,Dock=DockStyle.Fill};
   var buttons=new FlowLayoutPanel{Dock=DockStyle.Bottom,Height=54,FlowDirection=FlowDirection.RightToLeft,Padding=new Padding(6)};
   var retry=new Button{Text="Try again",DialogResult=DialogResult.Retry,AutoSize=true,Tag=Theme.Primary};var cancel=new Button{Text="Cancel setup",DialogResult=DialogResult.Cancel,AutoSize=true};buttons.Controls.Add(retry);buttons.Controls.Add(cancel);Controls.Add(text);Controls.Add(head);Controls.Add(buttons);Padding=new Padding(16);AcceptButton=retry;CancelButton=cancel;
   Theme.Apply(this);
  }
 }
 public sealed class MainForm:Form {
  // ---- existing operation controls (behaviour unchanged) ----
  readonly RichTextBox log=new RichTextBox{ReadOnly=true,Dock=DockStyle.Fill,BackColor=Theme.Field,ForeColor=Color.FromArgb(205,184,170),Font=new Font("Consolas",9),BorderStyle=BorderStyle.None};
  readonly Label status=new Label{Text="",Dock=DockStyle.Fill,AutoSize=false,TextAlign=ContentAlignment.MiddleLeft,ForeColor=Theme.Muted,Padding=new Padding(10,0,10,0),AutoEllipsis=true};
  readonly ComboBox disks=new ComboBox{DropDownStyle=ComboBoxStyle.DropDownList,Width=560},ports=new ComboBox{DropDownStyle=ComboBoxStyle.DropDownList,Width=250};
  readonly TextBox bundle=new TextBox{Width=670};readonly CheckBox erase=new CheckBox{Text="Erase everything on the stick first (recommended)",AutoSize=true,Checked=true},off=new CheckBox{Text="The DVR is powered off",AutoSize=true},finalOff=new CheckBox{Text="The DVR is powered off",AutoSize=true};
  readonly Button prepare=new Button{Text="Prepare USB stick",AutoSize=true,Tag=Theme.Primary},verify=new Button{Text="Verify UART adapter",AutoSize=true},capture=new Button{Text="Capture BOLT",AutoSize=true},install=new Button{Text="Install Phoenix",AutoSize=true,Enabled=false,Tag=Theme.Primary},commit=new Button{Text="Capture BOLT and enable Phoenix boot",AutoSize=true,Enabled=false},cancel=new Button{Text="Stop",AutoSize=true,Enabled=false,Visible=false};
  readonly CheckBox ignoreShas=new CheckBox{Text="Ignore SHA mismatches - Proceed at your own risk!",AutoSize=true};
  readonly Label supported=new Label{Text="✓  Your DVR is supported",ForeColor=Theme.Ok,Font=new Font("Segoe UI Semibold",12),AutoSize=true,Visible=false},installHint=new Label{Text="",AutoSize=true,Visible=false};
  readonly Label review=new Label{AutoSize=true,MaximumSize=new Size(820,0),ForeColor=Theme.Muted},finish=new Label{AutoSize=true,MaximumSize=new Size(820,0),ForeColor=Theme.Muted};
  readonly Label friendly=new Label{AutoSize=true,MaximumSize=new Size(820,0),Font=new Font("Segoe UI",10.5f)};
  readonly CheckBox advanced=new CheckBox{Text="Use a serial adapter (UART / BOLT) instead of Ethernet",AutoSize=true},directCable=new CheckBox{Text="The DVR is connected to the selected network connection (directly, or through the same router)",AutoSize=true},sequenceEntered=new CheckBox{Text="I entered the remote sequence and the DVR rebooted",AutoSize=true},fullSearch=new CheckBox{Text="Use full-range discovery (slower fallback)",AutoSize=true};
  readonly ComboBox ethernetAdapters=new ComboBox{DropDownStyle=ComboBoxStyle.DropDownList,Width=560};
  readonly CheckBox knownAddress=new CheckBox{Text="I know the DVR's setup address (skip the search):",AutoSize=true,Margin=new Padding(3,6,3,3)};readonly TextBox knownAddressText=new TextBox{Width=160};
  readonly Button findBox=new Button{Text="I've pressed the buttons, find my DVR",AutoSize=true,Tag=Theme.Primary};
  readonly Button reconnectAlpine=new Button{Text="Reconnect to temporary Alpine…",AutoSize=true},restoreAlpineSsh=new Button{Text="Restore temporary Alpine SSH access…",AutoSize=true};
  string keySession;
  string KeySession{get{return keySession??session;}}
  EthernetInstallLink ethernet;
  EthernetInstallLink utilityFactory;
  EthernetInstallLink recoveryLink;
  readonly FlowLayoutPanel recoveryPanel=new FlowLayoutPanel{FlowDirection=FlowDirection.TopDown,WrapContents=false,AutoSize=true,Visible=false};
  readonly TextBox recoveryDetails=new TextBox{ReadOnly=true,Multiline=true,ScrollBars=ScrollBars.Vertical,Width=820,Height=230};
  readonly Button recoveryRelease=new Button{Text="Release recovery connection",AutoSize=true,Enabled=false};
  readonly ComboBox utilityPorts=new ComboBox{DropDownStyle=ComboBoxStyle.DropDownList,Width=300},utilityAdapters=new ComboBox{DropDownStyle=ComboBoxStyle.DropDownList,Width=670};
  readonly CheckBox utilityOff=new CheckBox{Text="The DVR is powered off; UART connected; other serial terminals closed",AutoSize=true},utilityDirect=new CheckBox{Text="Direct Ethernet cable connected; remote sequence entered; DVR rebooted",AutoSize=true},utilityFull=new CheckBox{Text="Full-range discovery (slower fallback)",AutoSize=true};
  readonly Button utilityBolt=new Button{Text="Capture BOLT and release terminal",AutoSize=true},utilityLinux=new Button{Text="Boot diagnostic Linux from USB",AutoSize=true},utilityFind=new Button{Text="Find factory terminal",AutoSize=true},utilityRelease=new Button{Text="Release factory access",AutoSize=true,Enabled=false};
  readonly Button utilityExitFactory=new Button{Text="Exit factory mode (RUNLEVEL = 1)",AutoSize=true,Enabled=false};
  readonly CheckBox utilityExitReboot=new CheckBox{Text="Reboot after resetting RUNLEVEL",Checked=true,AutoSize=true,Enabled=false};
  readonly TextBox utilityResult=new TextBox{ReadOnly=true,Multiline=true,ScrollBars=ScrollBars.Vertical,Width=820,Height=110};
  bool EthernetMode{get{return !advanced.Checked;}}
  readonly bool demo;bool busy,usbReady,refreshing;CancellationTokenSource discovery;readonly Func<CancellationToken,List<UsbDisk>> scanner;SerialLink link;DeviceReview device;CancellationTokenSource cancellation;readonly string session;bool finalCapture;string maintenancePublicKey;
  UpdatePanel updater;
  // ---- simplified shell ----
  readonly HeaderBand header=new HeaderBand();
  readonly CheckBox advView=new CheckBox{Text="Advanced view",AutoSize=true,ForeColor=Theme.Muted,BackColor=Color.FromArgb(30,20,14),Cursor=Cursors.Hand};
  readonly StepBar stepBar=new StepBar{Dock=DockStyle.Top};
  readonly Panel host=new Panel{Dock=DockStyle.Fill};
  readonly Panel logPanel=new Panel{Dock=DockStyle.Bottom,Height=170,Visible=false};
  readonly Button back=new Button{Text="Back",AutoSize=true},next=new Button{Text="Next",AutoSize=true,Tag=Theme.Primary};
  readonly Label liveStatus=new Label{AutoSize=true,MaximumSize=new Size(820,0),Font=new Font("Segoe UI Semibold",11),ForeColor=Theme.Gold};
  readonly ProgressBar working=new ProgressBar{Style=ProgressBarStyle.Marquee,Width=560,Height=8,Visible=false,MarqueeAnimationSpeed=30};
  readonly List<Control> advancedOnly=new List<Control>(),simpleOnly=new List<Control>();
  readonly Dictionary<FlowLayoutPanel,PageInfo> pages=new Dictionary<FlowLayoutPanel,PageInfo>();
  FlowLayoutPanel pHome,pReady,pUsb,pConnect,pFind,pInstall,pDone,pUtil,pUpdate,pHelp,current;
  Control adapterRow,npcapRow,doneSteps;
  static readonly string[] SetupSteps={"Get ready","USB stick","Connect","Find DVR","Install","Done"};
  sealed class PageInfo {public string Title,Subtitle;public int Step=-1;public FlowLayoutPanel Back,Next;public string NextLabel;}
  bool Simple{get{return !advView.Checked;}}

  public MainForm(bool simulated,Func<CancellationToken,List<UsbDisk>> scan=null){
   demo=simulated;scanner=scan??(token=>Usb.List(token));Text="Phoenix Setup";Size=new Size(1000,800);MinimumSize=new Size(880,660);StartPosition=FormStartPosition.CenterScreen;Font=new Font("Segoe UI",10);BackColor=Theme.Bg;ForeColor=Theme.Fg;
   var icon=Theme.AppIcon();if(icon!=null)Icon=icon;
   session=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"Phoenix","SetupLogs",DateTime.Now.ToString("yyyyMMdd-HHmmss-fff"));Directory.CreateDirectory(session);
   // Shell: header, step bar, pages, technical log (Advanced view) and footer.
   advView.Anchor=AnchorStyles.Top|AnchorStyles.Right;header.Controls.Add(advView);header.Resize+=(s,e)=>advView.Location=new Point(header.Width-advView.Width-24,28);
   var footer=new TableLayoutPanel{Dock=DockStyle.Bottom,Height=62,ColumnCount=3,Padding=new Padding(20,10,20,10),BackColor=Color.FromArgb(15,11,10)};footer.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));footer.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));footer.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
   var right=new FlowLayoutPanel{AutoSize=true,WrapContents=false,BackColor=Color.FromArgb(15,11,10)};right.Controls.Add(cancel);right.Controls.Add(next);footer.Controls.Add(back,0,0);footer.Controls.Add(status,1,0);footer.Controls.Add(right,2,0);
   var logBar=new FlowLayoutPanel{Dock=DockStyle.Top,Height=36,Padding=new Padding(12,4,12,0)};var logTitle=new Label{Text="TECHNICAL LOG",AutoSize=true,ForeColor=Color.FromArgb(215,154,106),Font=new Font("Segoe UI",8.5f,FontStyle.Bold),Margin=new Padding(3,8,12,3)};var openLogs=new Button{Text="Open logs folder",AutoSize=true};openLogs.Click+=(s,e)=>Process.Start("explorer.exe","\""+session+"\"");logBar.Controls.Add(logTitle);logBar.Controls.Add(openLogs);
   var logInner=new Panel{Dock=DockStyle.Fill,Padding=new Padding(16,4,16,8)};logInner.Controls.Add(log);logPanel.Controls.Add(logInner);logPanel.Controls.Add(logBar);
   Controls.Add(host);Controls.Add(logPanel);Controls.Add(footer);Controls.Add(stepBar);Controls.Add(header);
   // Stop is never a default or tab-stop target, and asks first, so a stray Enter can't stop monitoring.
   cancel.TabStop=false;
   cancel.Click+=(s,e)=>{
    if(busy&&cancellation!=null){
     string question=current==pUpdate?"Stop watching this update?\n\nIf the DVR has already started, it carries on by itself. Leave it switched on. Press Update my DVR again later and Setup picks it up where it is.":"Stop here?\n\nIf the DVR has already started installing, it carries on by itself. Leave it switched on with the cable connected. To pick it up again, choose Fix a problem, then Setup stopped partway.";
     if(MessageBox.Show(this,question,"Stop",MessageBoxButtons.YesNo,MessageBoxIcon.Question,MessageBoxDefaultButton.Button2)==DialogResult.Yes)cancellation.Cancel();
    }else if(discovery!=null)discovery.Cancel();
   };cancel.EnabledChanged+=(s,e)=>cancel.Visible=cancel.Enabled;
   back.Click+=(s,e)=>{PageInfo info;if(!busy&&current!=null&&pages.TryGetValue(current,out info)&&info.Back!=null)Go(info.Back);};
   next.Click+=(s,e)=>{PageInfo info;if(current==pDone){Go(pHome);return;}if(!busy&&current!=null&&pages.TryGetValue(current,out info)&&info.Next!=null)Go(info.Next);};
   advView.CheckedChanged+=(s,e)=>ApplyView();

   BuildHome();BuildReady();BuildUsb();BuildConnect();BuildFind();BuildInstall();BuildDone();BuildUtilities();BuildUpdate();BuildHelp();
   pages[pReady].Next=pUsb;pages[pUsb].Next=pConnect;pages[pConnect].Next=pFind;

   prepare.Click+=(s,e)=>Prepare();verify.Click+=(s,e)=>Verify();capture.Click+=(s,e)=>CaptureDevice(false);install.Click+=(s,e)=>Install();commit.Click+=(s,e)=>CaptureDevice(true);
   FormClosing+=(s,e)=>{if(busy&&MessageBox.Show(this,current==pUpdate?"Close Phoenix Setup? The update carries on in the DVR. Keep it switched on with its USB stick in. To check on it later, open Phoenix Setup and choose Update my DVR.":"Close Phoenix Setup? Setup carries on in the DVR. Leave it switched on, with the USB stick and cable connected. To pick it up again, open Phoenix Setup and choose Fix a problem, then Setup stopped partway.","Close Phoenix Setup",MessageBoxButtons.YesNo)!=DialogResult.Yes){e.Cancel=true;return;}if(discovery!=null)discovery.Cancel();if(cancellation!=null)cancellation.Cancel();if(link!=null)link.Dispose();if(ethernet!=null)ethernet.Dispose();ReleaseUtilityFactory();ReleaseRecoveryAccess();};
   Shown+=(s,e)=>{RefreshDisks();RefreshPorts();RefreshUtilityChoices();Append("Session logs: "+session+"\r\n");if(demo)Append("DEMO: no USB or UART operations are performed.\r\n");};
   if(demo){disks.Items.Add(new UsbDisk{Number=2,Drive="E",FriendlyName="Example USB",Size=16000000000,FileSystem="FAT32",PartitionStyle="MBR",PartitionNumber=1,UniqueId="DEMO",BusType="USB"});disks.SelectedIndex=0;ports.Items.Add("COM5 — Example UART");ports.SelectedIndex=0;}
   Theme.Apply(this);header.Title.BackColor=header.Subtitle.BackColor=Color.Transparent;
   Go(pHome);ApplyView();
  }

  // ---------- page building helpers ----------
  FlowLayoutPanel Page(string title,string subtitle,int step){var flow=new FlowLayoutPanel{Dock=DockStyle.Fill,FlowDirection=FlowDirection.TopDown,WrapContents=false,AutoScroll=true,Padding=new Padding(34,24,34,16),Visible=false,BackColor=Theme.Bg};host.Controls.Add(flow);pages[flow]=new PageInfo{Title=title,Subtitle=subtitle,Step=step};return flow;}
  static void Add(FlowLayoutPanel p,Control c){c.Margin=new Padding(3,5,3,7);p.Controls.Add(c);}
  static Label Heading(string text){return new Label{Text=text,AutoSize=true,MaximumSize=new Size(820,0),Font=new Font("Segoe UI Semibold",19),Margin=new Padding(0,0,0,2)};}
  static Label Lead(string text){return new Label{Text=text,AutoSize=true,MaximumSize=new Size(760,0),Font=new Font("Segoe UI",11),ForeColor=Theme.Muted};}
  static Label Body(string text){return new Label{Text=text,AutoSize=true,MaximumSize=new Size(820,0)};}
  static Label Note(string text){return new Label{Text=text,AutoSize=true,MaximumSize=new Size(800,0),BackColor=Theme.NoteBg,ForeColor=Theme.Fg,Padding=new Padding(12,10,12,10)};}
  static Control Item(int number,string title,string detail){
   var row=new TableLayoutPanel{AutoSize=true,ColumnCount=2,BackColor=Theme.Panel,Padding=new Padding(10,8,14,8),Width=780,MinimumSize=new Size(780,0)};row.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute,44));row.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));
   row.Controls.Add(new Label{Text=Convert.ToString(number),Size=new Size(30,30),TextAlign=ContentAlignment.MiddleCenter,BackColor=Theme.Panel2,ForeColor=Theme.Gold,Font=new Font("Segoe UI Semibold",11),Margin=new Padding(0,2,8,0)},0,0);
   var text=new FlowLayoutPanel{FlowDirection=FlowDirection.TopDown,AutoSize=true,WrapContents=false,BackColor=Theme.Panel,Margin=new Padding(0)};
   text.Controls.Add(new Label{Text=title,AutoSize=true,MaximumSize=new Size(700,0),Font=new Font("Segoe UI Semibold",10.5f),Margin=new Padding(0,0,0,2)});
   if(!String.IsNullOrEmpty(detail))text.Controls.Add(new Label{Text=detail,AutoSize=true,MaximumSize=new Size(700,0),ForeColor=Theme.Muted,Margin=new Padding(0)});
   row.Controls.Add(text,1,0);return row;
  }
  static Control Keys(){
   var row=new FlowLayoutPanel{AutoSize=true,WrapContents=true,MaximumSize=new Size(820,0)};
   foreach(string key in new[]{"▲","▼","◀","▶","then","6","9","1","5","then","RED"}){
    if(key=="then"){row.Controls.Add(new Label{Text="then",AutoSize=true,ForeColor=Theme.Faint,Margin=new Padding(4,16,4,0)});continue;}
    bool red=key=="RED";row.Controls.Add(new Label{Text=key,Size=new Size(red?58:48,48),TextAlign=ContentAlignment.MiddleCenter,BackColor=red?Color.FromArgb(179,38,30):Color.FromArgb(40,30,26),ForeColor=Color.White,Font=new Font("Segoe UI Semibold",red?11:14),BorderStyle=BorderStyle.FixedSingle,Margin=new Padding(0,0,8,0)});
   }return row;
  }
  T Adv<T>(T c)where T:Control{advancedOnly.Add(c);return c;}
  T Easy<T>(T c)where T:Control{simpleOnly.Add(c);return c;}
  Label AdvHeading(string text){return Adv(new Label{Text="⚙  ADVANCED"+(String.IsNullOrEmpty(text)?"":"  ·  "+text),AutoSize=true,ForeColor=Color.FromArgb(215,154,106),Font=new Font("Segoe UI",8.5f,FontStyle.Bold),Margin=new Padding(3,18,3,4)});}
  static FlowLayoutPanel Row(params Control[] items){var row=new FlowLayoutPanel{AutoSize=true,WrapContents=true,MaximumSize=new Size(840,0)};foreach(var c in items)row.Controls.Add(c);return row;}

  // ---------- pages ----------
  void BuildHome(){
   pHome=Page("Phoenix Setup","Put Phoenix on your DVR",-1);
   Add(pHome,Heading("What would you like to do?"));
   var setup=new ChoiceCard("First time","Set up a new DVR","Install Phoenix on a DVR that still runs its original software. About 30 minutes.",true);
   var update=new ChoiceCard("Already using Phoenix","Update my DVR","Get a newer version over your home network. Recordings and settings are kept.",false);
   var help=new ChoiceCard("Something went wrong","Fix a problem","Pick up a setup that stopped, or get help with a DVR that isn't working.",false);
   setup.Click+=(s,e)=>Go(pReady);update.Click+=(s,e)=>Go(pUpdate);help.Click+=(s,e)=>Go(pHelp);
   var cards=Row(setup,update,help);cards.Margin=new Padding(0,14,0,10);pHome.Controls.Add(cards);
   Add(pHome,AdvHeading("Tools for technical users"));
   var utilities=new Button{Text="Utilities: UART, BOLT and factory terminal",AutoSize=true};utilities.Click+=(s,e)=>Go(pUtil);
   var prepOnly=new Button{Text="Prepare a USB stick only",AutoSize=true};prepOnly.Click+=(s,e)=>Go(pUsb);
   Add(pHome,Adv(Row(utilities,prepOnly)));
   Add(pHome,Adv(Body("Setup "+SetupVersion.App+" · Phoenix "+SetupVersion.Phoenix+" · USB payload protocol 5")));
  }
  void BuildReady(){
   pReady=Page("Set up a new DVR","About 30 minutes",0);pages[pReady].Back=pHome;pages[pReady].Next=pUsb;pages[pReady].NextLabel="I've got everything";
   Add(pReady,Heading("Before you start, grab these"));
   Add(pReady,Lead("You'll need them in the next few minutes. Setup takes about 30 minutes."));
   Add(pReady,Item(1,"A USB stick, up to 64 GB","It stays in the DVR afterwards. Everything on it will be erased. The recordings disk inside the DVR is kept."));
   Add(pReady,Item(2,"A network cable","To plug the DVR straight into this computer for a little while."));
   Add(pReady,Item(3,"The DVR's remote","You'll press a few buttons to put the DVR into setup mode."));
  }
  void BuildUsb(){
   pUsb=Page("Set up a new DVR","About 30 minutes",1);pages[pUsb].Back=pReady;pages[pUsb].Next=pConnect;pages[pUsb].NextLabel="Skip, my stick is ready";
   Add(pUsb,Heading("Prepare the USB stick"));
   Add(pUsb,Lead("Plug the stick into this computer. We'll copy Phoenix onto it."));
   Add(pUsb,new Label{Text="Which stick?",AutoSize=true,Font=new Font("Segoe UI Semibold",10),Margin=new Padding(3,12,3,0)});Add(pUsb,disks);
   Add(pUsb,Easy(new Label{Text="Don't see it? Unplug it and plug it back in. The list updates by itself.",AutoSize=true,ForeColor=Theme.Muted,MaximumSize=new Size(820,0)}));
   Add(pUsb,erase);Add(pUsb,prepare);
   Add(pUsb,AdvHeading("USB payload"));
   var refresh=new Button{Text="Refresh USB list",AutoSize=true};refresh.Click+=(s,e)=>RefreshDisks();
   Add(pUsb,Adv(new Label{Text="Verified USB payload folder",AutoSize=true}));bundle.Text=Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"usb-payload");Add(pUsb,Adv(bundle));
   var browse=new Button{Text="Browse payload…",AutoSize=true};browse.Click+=(s,e)=>{using(var d=new FolderBrowserDialog())if(d.ShowDialog(this)==DialogResult.OK)bundle.Text=d.SelectedPath;};Add(pUsb,Adv(Row(refresh,browse)));
   Add(pUsb,Adv(Body("Formatting requires running this app as administrator. Only USB media up to 64 GB class are eligible. Formatting creates a FAT32 partition up to 32 GiB; a suitable existing FAT32 stick does not need formatting when Erase is unticked.")));
  }
  void BuildConnect(){
   pConnect=Page("Set up a new DVR","About 30 minutes",2);pages[pConnect].Back=pUsb;pages[pConnect].Next=pFind;pages[pConnect].NextLabel="Next";
   Add(pConnect,Heading("Connect the DVR"));
   Add(pConnect,Lead("Do these in order."));
   Add(pConnect,Item(1,"Turn the DVR off at the wall","Plug it into your TV with HDMI so you can see it start. The aerial can wait until the end."));
   Add(pConnect,Item(2,"Put the USB stick in the rear USB port","Use the USB port on the back of the DVR, not the one on the front."));
   Add(pConnect,Item(3,"Plug the network cable from the DVR into this computer","Straight in works best. You'll move it to the router at the end. If this computer only has Wi-Fi, plug the DVR into the router this computer's Wi-Fi uses instead."));
   Add(pConnect,Item(4,"Turn the DVR back on","Wait until the TV shows the DVR's normal welcome screen, then press Next."));
  }
  void BuildFind(){
   pFind=Page("Set up a new DVR","About 30 minutes",3);pages[pFind].Back=pConnect;
   Add(pFind,AdvHeading("Connection method"));Add(pFind,Adv(advanced));
   var networkPanel=new FlowLayoutPanel{FlowDirection=FlowDirection.TopDown,WrapContents=false,AutoSize=true};Add(pFind,networkPanel);
   Add(networkPanel,Heading("Put the DVR into setup mode"));
   Add(networkPanel,Lead("Point the remote at the DVR and press these buttons one after another. You have 14 seconds, so read them first."));
   Add(networkPanel,Keys());
   Add(networkPanel,Note("Using the small remote? Each time you press 123 to switch between arrows and numbers, cover the front of the remote with your hand so the DVR doesn't see that press.\nThe TV may go blank afterwards. That's normal."));
   Add(networkPanel,Note("Your DVR restarts after the last button. Wait at least 2 minutes after it restarts before you press the button below."));
   var adapterLabel=new Label{Text="How is this computer connected to the DVR?",AutoSize=true,Font=new Font("Segoe UI Semibold",10),Margin=new Padding(3,10,3,0)};
   var adapterPanel=new FlowLayoutPanel{FlowDirection=FlowDirection.TopDown,AutoSize=true,WrapContents=false};adapterPanel.Controls.Add(adapterLabel);adapterPanel.Controls.Add(ethernetAdapters);adapterRow=adapterPanel;Add(networkPanel,adapterPanel);
   var refreshNetwork=new Button{Text="Refresh network connections",AutoSize=true};refreshNetwork.Click+=(s,e)=>{RefreshEthernet();ApplyView();};
   var npcap=new Button{Text="Install the network helper (Npcap)…",AutoSize=true};npcap.Click+=(s,e)=>Process.Start("https://npcap.com/#download");
   npcapRow=Row(new Label{Text="This computer needs the free Npcap network helper to find the DVR.",AutoSize=true,Margin=new Padding(3,8,8,3)},npcap);Add(networkPanel,npcapRow);
   Add(networkPanel,findBox);
   Add(networkPanel,AdvHeading("Discovery"));Add(networkPanel,Adv(Row(refreshNetwork)));Add(networkPanel,Adv(directCable));Add(networkPanel,Adv(sequenceEntered));Add(networkPanel,Adv(fullSearch));
   Add(networkPanel,Adv(Row(knownAddress,knownAddressText)));Add(networkPanel,Adv(Note("The setup address starts with 10. Setup shows it when it finds the DVR, and keeps the last one here. With a typed address there's no search, so it's the quickest way over Wi-Fi.")));
   knownAddressText.Text=LastFoundAddress()??"";knownAddressText.TextChanged+=(s,e)=>{if(knownAddressText.Focused&&knownAddressText.Text.Trim().Length>0)knownAddress.Checked=true;};
   var uart=new FlowLayoutPanel{FlowDirection=FlowDirection.TopDown,WrapContents=false,AutoSize=true,Visible=false};Add(pFind,uart);
   Add(uart,Heading("Connect with a serial adapter"));
   Add(uart,Body("Safely eject the prepared USB and insert it in the rear USB port. Connect Ethernet. With the DVR powered off, connect a 3.3 V TTL UART adapter: GND/TX/RX only, TX and RX crossed. Leave VCC, DTR and RTS disconnected. Close other terminal apps."));Add(uart,ports);var refreshPorts=new Button{Text="Refresh COM ports",AutoSize=true};refreshPorts.Click+=(s,e)=>RefreshPorts();Add(uart,Row(refreshPorts,verify));Add(uart,Body("Adapter verification checks Windows access only. Device communication is confirmed when readable boot output is received."));Add(uart,off);Add(uart,capture);
   Add(pFind,recoveryPanel);Add(recoveryPanel,new Label{Text="This DVR already has Phoenix on it",AutoSize=true,Font=new Font("Segoe UI Semibold",12)});Add(recoveryPanel,Body("Setup stopped so nothing gets overwritten. The details below let a technical user connect to the DVR."));Add(recoveryPanel,recoveryDetails);Add(recoveryPanel,recoveryRelease);recoveryRelease.Click+=(s,e)=>ReleaseRecoveryAccess();
   advanced.CheckedChanged+=(s,e)=>{if(busy)return;networkPanel.Visible=EthernetMode;uart.Visible=!EthernetMode;device=null;supported.Visible=installHint.Visible=false;review.Text=friendly.Text="";install.Enabled=false;if(link!=null){link.Dispose();link=null;}if(ethernet!=null){ethernet.Dispose();ethernet=null;}commit.Visible=finalOff.Visible=!EthernetMode;RefreshPorts();};
   findBox.Click+=(s,e)=>{if(Simple){directCable.Checked=sequenceEntered.Checked=true;}FindEthernet();};RefreshEthernet();
  }
  void BuildInstall(){
   pInstall=Page("Set up a new DVR","About 30 minutes",4);pages[pInstall].Back=pFind;
   Add(pInstall,Heading("Ready to install"));
   Add(pInstall,supported);Add(pInstall,Easy(friendly));Add(pInstall,installHint);
   Add(pInstall,Note("Check the USB stick is in the rear USB port (not the front). If Setup can't find it, it asks you to fix it and then carries on."));
   Add(pInstall,Note("Your DVR will restart during this process. Keep it switched on and the cable plugged in until setup finishes. Partway through, you'll be asked for a name and a password for the DVR."));
   Add(pInstall,install);Add(pInstall,working);Add(pInstall,liveStatus);
   Add(pInstall,AdvHeading("Device review"));Add(pInstall,Adv(review));Add(pInstall,Adv(ignoreShas));
   Add(pInstall,Adv(Body("Installation uses a temporary Alpine environment. The stock eMMC is read-only. The installer refuses an existing Phoenix installation and never formats the internal HDD. Settings and passwords are requested after firmware and disk checks.")));
  }
  void BuildDone(){
   pDone=Page("Set up a new DVR","Finished",5);pages[pDone].NextLabel="Finish";
   Add(pDone,Heading("Phoenix is installed"));
   var steps=new FlowLayoutPanel{FlowDirection=FlowDirection.TopDown,AutoSize=true,WrapContents=false};
   steps.Controls.Add(Lead("A few last things and you're watching TV."));
   steps.Controls.Add(Item(1,"Leave the USB stick in the DVR","It's the key that starts Phoenix. If you take it out, the DVR goes back to its old software."));
   steps.Controls.Add(Item(2,"Turn the DVR off","Switch it off at the wall."));
   steps.Controls.Add(Item(3,"Move the network cable to your home router","Unplug it from this computer and plug it into your router. Skip this if it's already plugged into the router."));
   steps.Controls.Add(Item(4,"Plug in the TV and aerial","Make sure it's plugged into your TV, and plug the aerial into the DVR."));
   steps.Controls.Add(Item(5,"Turn the DVR on and finish on the TV","The Phoenix welcome screen appears. Use the remote to scan for your channels."));
   foreach(Control c in steps.Controls)c.Margin=new Padding(3,5,3,7);
   doneSteps=steps;Add(pDone,steps);
   Add(pDone,Adv(finish));Add(pDone,finalOff);Add(pDone,commit);finalOff.Visible=commit.Visible=false;
   Add(pDone,AdvHeading("Logs"));Add(pDone,Adv(Body("Ethernet setup configures USB boot automatically. Advanced UART / BOLT setup uses the controls above to enable boot after installation. Session logs: "+session)));
  }
  void BuildUtilities(){
   var page=pUtil=Page("Utilities","For technical users",-1);pages[pUtil].Back=pHome;
   Add(page,Heading("Utilities"));
   Add(page,Body("Terminal access and recovery — no Phoenix installation. Exit factory mode resets RUNLEVEL only."));
   Add(page,new Label{Text="UART / BOLT",AutoSize=true,Font=new Font("Segoe UI",11,FontStyle.Bold)});
   Add(page,Body("Connect the 3.3 V UART adapter (GND/TX/RX only), close other serial terminals and power the DVR off. For Linux, leave a freshly prepared Phoenix USB stick in the rear port. Click an action, then power the DVR on when prompted."));
   Add(page,utilityPorts);var refreshSerial=new Button{Text="Refresh COM ports",AutoSize=true};refreshSerial.Click+=(s,e)=>RefreshUtilityChoices();Add(page,refreshSerial);Add(page,utilityOff);
   var row=new FlowLayoutPanel{AutoSize=true};row.Controls.Add(utilityBolt);row.Controls.Add(utilityLinux);Add(page,row);
   Add(page,Body("BOLT capture releases the COM port immediately. Diagnostic Linux opens a RAM-only Alpine shell, then releases the port. Open PuTTY using Serial, 115200 baud, 8N1 and no flow control. No login is required for the diagnostic shell."));
   Add(page,new Label{Text="Factory terminal via remote and direct Ethernet",AutoSize=true,Font=new Font("Segoe UI",11,FontStyle.Bold)});
   Add(page,Body("Connect the DVR directly to the selected PC Ethernet port. From the stock DVR, enter Up → Down → Left → Right → 6 → 9 → 1 → 5 → Red within 14 seconds. Cover the remote for every 123 mode change. A blank factory screen is possible; discovery does not require HDMI output."));
   Add(page,Body("The remote sequence persistently selects factory mode. Reboot-and-rescan is a retry option; UART/BOLT remains the recovery method if discovery fails."));
   Add(page,utilityAdapters);var refreshNetwork=new Button{Text="Refresh Ethernet ports",AutoSize=true};refreshNetwork.Click+=(s,e)=>RefreshUtilityChoices();Add(page,refreshNetwork);Add(page,utilityDirect);Add(page,utilityFull);
   var networkRow=new FlowLayoutPanel{AutoSize=true};networkRow.Controls.Add(utilityFind);networkRow.Controls.Add(utilityRelease);Add(page,networkRow);
   Add(page,Body("To leave factory mode, find the factory terminal first and close PuTTY. Exit factory mode resets RUNLEVEL to 1 in both environment copies, preserving STARTUP and installed files. An existing Phoenix boot hook remains active."));Add(page,utilityExitReboot);Add(page,utilityExitFactory);Add(page,utilityResult);
   utilityBolt.Click+=(s,e)=>UtilitySerial(false);utilityLinux.Click+=(s,e)=>UtilitySerial(true);utilityFind.Click+=(s,e)=>UtilityFactory();utilityRelease.Click+=(s,e)=>{if(busy)return;ReleaseUtilityFactory();utilityResult.Text="Temporary PC address removed. Device boot settings were not changed.";Status("Factory access released.");};
   utilityExitFactory.Click+=(s,e)=>UtilityExitFactory();
  }
  void BuildUpdate(){
   pUpdate=Page("Update my DVR","Over your home network",-1);pages[pUpdate].Back=pHome;
   updater=new UpdatePanel(session,demo,operation=>Run(operation),message=>{Status(message);Append(message+"\r\n");});Add(pUpdate,updater);
  }
  void BuildHelp(){
   pHelp=Page("Fix a problem","",-1);pages[pHelp].Back=pHome;
   Add(pHelp,Heading("What's happening?"));
   var resume=new ChoiceCard("","Setup stopped partway","Carry on from where it stopped. Keep the DVR on and the cable plugged in. Don't press the remote buttons again.",false);
   var running=new ChoiceCard("","Update or check my DVR","For a DVR that already runs Phoenix, including an update that didn't finish.",false);
   var logs=new ChoiceCard("","Something else","Open the folder of setup logs, to send to whoever helps you with your DVR.",false);
   resume.Click+=(s,e)=>ReconnectAlpine(true);running.Click+=(s,e)=>Go(pUpdate);logs.Click+=(s,e)=>{string root=Path.GetDirectoryName(session);Process.Start("explorer.exe","\""+root+"\"");};
   var cards=Row(resume,running,logs);cards.Margin=new Padding(0,14,0,10);pHelp.Controls.Add(cards);
   Add(pHelp,AdvHeading("Temporary Alpine recovery"));
   Add(pHelp,Adv(Body("Already booted into temporary Alpine? Keep the DVR powered on and the direct cable connected. Reopen your saved session below; do not repeat the remote sequence. Uses the Ethernet port selected on the Find DVR page.")));
   Add(pHelp,Adv(Row(reconnectAlpine,restoreAlpineSsh)));
   reconnectAlpine.Click+=(s,e)=>ReconnectAlpine(false);restoreAlpineSsh.Click+=(s,e)=>RestoreAlpineSsh();
  }

  // ---------- navigation and view ----------
  void Go(FlowLayoutPanel page){
   if(page==null)return;current=page;foreach(var p in pages.Keys)p.Visible=p==page;page.AutoScrollPosition=new Point(0,0);
   var info=pages[page];header.Title.Text=info.Title;header.Subtitle.Text=info.Subtitle;stepBar.Visible=info.Step>=0;stepBar.Set(SetupSteps,info.Step);
   if(page==pDone)doneSteps.Visible=!finalCapture;
   if(page==pUsb&&!busy)RefreshDisks();
   UpdateNav();
  }
  void UpdateNav(){
   if(current==null)return;var info=pages[current];
   back.Visible=info.Back!=null;back.Enabled=!busy;
   next.Visible=(info.Next!=null||current==pDone)&&!(current==pDone&&finalCapture);next.Text=info.NextLabel??"Next";next.Enabled=!busy;
  }
  void ApplyView(){
   bool adv=advView.Checked;
   foreach(var c in advancedOnly)c.Visible=adv;foreach(var c in simpleOnly)c.Visible=!adv;
   if(!adv&&advanced.Checked&&!busy)advanced.Checked=false;
   adapterRow.Visible=adv||ethernetAdapters.Items.Count>1;
   npcapRow.Visible=adv||(!demo&&!NpcapDiscovery.Installed);
   logPanel.Visible=adv;
   if(updater!=null)updater.SetSimple(!adv);
   if(!adv&&current==pUtil)Go(pHome);
  }
  string FriendlyReview{get{return "We found your DVR and checked it.\r\n✓  Its original software stays on it. Take the USB stick out and it starts as before.\r\n✓  The recordings disk is checked before anything is copied. Nothing on it is erased.\r\n\r\nPress Install Phoenix when you're ready. It takes about 15 minutes.";}}

  void RefreshUtilityChoices(){
   if(busy||utilityFactory!=null)return;string old=Convert.ToString(utilityPorts.SelectedItem);utilityPorts.Items.Clear();utilityAdapters.Items.Clear();
   if(demo){utilityPorts.Items.Add("COM5");utilityAdapters.Items.Add(new EthernetAdapter{Name="PC Ethernet",Description="Demo direct connection"});}
   else {foreach(string port in SerialPort.GetPortNames())utilityPorts.Items.Add(port);try{foreach(var adapter in EthernetAdapter.List())utilityAdapters.Items.Add(adapter);}catch(Exception e){Append("Utilities Ethernet: "+e.Message+"\r\n");}}
   if(utilityPorts.Items.Contains(old))utilityPorts.SelectedItem=old;else if(utilityPorts.Items.Count>0)utilityPorts.SelectedIndex=0;
   if(utilityAdapters.Items.Count==1)utilityAdapters.SelectedIndex=0;
  }
  void SetUtilityBusy(bool value){utilityBolt.Enabled=utilityLinux.Enabled=utilityFind.Enabled=!value&&utilityFactory==null;utilityPorts.Enabled=utilityAdapters.Enabled=utilityOff.Enabled=utilityDirect.Enabled=utilityFull.Enabled=!value&&utilityFactory==null;utilityRelease.Enabled=utilityExitFactory.Enabled=utilityExitReboot.Enabled=!value&&utilityFactory!=null;}
  void UtilityExitFactory(){
   if(busy||utilityFactory==null){Status("Find the factory terminal in Utilities first.");return;}bool reboot=utilityExitReboot.Checked;
   if(MessageBox.Show(this,"Close PuTTY first. Reset RUNLEVEL to 1 in both environment copies? STARTUP and installed files will be preserved."+(reboot?" The DVR will then reboot.":" The change takes effect on the next reboot."),"Exit factory mode",MessageBoxButtons.YesNo,MessageBoxIcon.Question)!=DialogResult.Yes)return;
   Run(async token=>{utilityFactory.SetCancellation(token);Status("Resetting RUNLEVEL and verifying both environment copies");await Task.Run(()=>utilityFactory.ExitFactoryMode(reboot));if(reboot)ReleaseUtilityFactory();utilityResult.Text="RUNLEVEL = 1 verified in both copies. STARTUP is unchanged. "+(reboot?"Reboot requested. Reconnect Ethernet to the normal LAN; leave the Phoenix USB inserted if using Phoenix boot.":"The current factory session remains available until you reboot.");Append(utilityResult.Text+"\r\n");Status("Factory mode disabled for the next boot.");},true);
  }
  void ReleaseUtilityFactory(){if(utilityFactory!=null){utilityFactory.Dispose();utilityFactory=null;}SetUtilityBusy(busy);}
  void ResetUtilityReview(){if(link!=null){link.Dispose();link=null;}if(ethernet!=null){ethernet.Dispose();ethernet=null;}device=null;finalCapture=false;install.Enabled=commit.Enabled=false;supported.Visible=installHint.Visible=false;review.Text=friendly.Text="";}
  void UtilitySerial(bool linux){
   if(busy)return;if(utilityPorts.SelectedItem==null||!utilityOff.Checked){Status("Select a COM port and confirm the DVR is powered off.");return;}
   string port=Convert.ToString(utilityPorts.SelectedItem);
   Run(async token=>{
    ResetUtilityReview();utilityOff.Checked=false;utilityResult.Text="Listening on "+port+". Power on the DVR now.";Status(utilityResult.Text);
    if(!demo)await Task.Run(()=>TerminalUtilities.Serial(new ComWire(port),linux,Path.Combine(session,(linux?"utility-linux-":"utility-bolt-")+DateTime.Now.ToString("HHmmss-fff")),token,Append));
    utilityResult.Text=(demo?"Demo only. ":"")+(linux?"Diagnostic Alpine is ready. ":"BOLT captured. ")+port+" is released. Open PuTTY: Serial, "+port+", 115200 baud, 8N1, flow control None."+(linux?"\r\nPress Enter for the root shell. Internal drives are unmounted; no installer was started.":"");Append(utilityResult.Text+"\r\n");((FlowLayoutPanel)utilityResult.Parent).ScrollControlIntoView(utilityResult);Status("Terminal released for your own terminal application.");
   });
  }
  void UtilityFactory(){
   if(busy)return;var adapter=utilityAdapters.SelectedItem as EthernetAdapter;if(adapter==null||!utilityDirect.Checked){Status("Select the directly connected Ethernet port and confirm the remote-sequence reboot.");return;}bool full=utilityFull.Checked;
   Run(async token=>{
    ResetUtilityReview();DiscoveredBox found;
    if(demo)found=new DiscoveredBox{Address="10.1.2.3"};
    else {
     found=await Task.Run(()=>{Status("Discovering factory terminal");var candidates=full?new List<uint>():FactoryCandidates.Generate(token);using(var search=new NpcapDiscovery(adapter))return search.Search(adapter,candidates,full,token,Status,Path.Combine(session,"utility-discovery-"+DateTime.Now.ToString("HHmmss-fff")));});
     utilityFactory=new EthernetInstallLink(adapter,found,Path.Combine(session,"utility-factory-"+DateTime.Now.ToString("HHmmss-fff")),Status,token);
     await Task.Run(()=>utilityFactory.Inspect());utilityFactory.ReleaseTerminal();
    }
    utilityResult.Text=(demo?"Demo only. ":"")+"Factory terminal verified and disconnected.\r\nPuTTY: Telnet, host "+found.Address+", port 23. Login: root; password: blank.\r\nKeep this app open for its temporary PC address. Close PuTTY before clicking Release factory access. Device factory mode remains selected.";Append(utilityResult.Text+"\r\n");((FlowLayoutPanel)utilityResult.Parent).ScrollControlIntoView(utilityResult);Status("Factory terminal available for your own terminal application.");
   });
  }
  string RecoveryPublicKey(CancellationToken token){if(maintenancePublicKey==null){Status("Creating the recovery SSH key");maintenancePublicKey=MaintenanceKeys.ReuseOrGenerate(KeySession,token);Append("SSH key saved in: "+Path.Combine(KeySession,"keys")+"\r\n");}return maintenancePublicKey;}
  bool RetryCheckpoint(Dictionary<string,object> evt){
   bool retry=false;string message=Convert.ToString(evt["message"]);Status(Convert.ToString(evt["code"])=="usb-missing"?"Waiting for the USB stick in the DVR's rear USB port":"Waiting for you to fix: "+Convert.ToString(evt["code"]));Append(message+"\r\n");
   if(evt.ContainsKey("access")){
    var access=Json.Read<RecoveryAccess>(Json.Write(evt["access"]));
    if(access!=null&&access.Valid){
     string instructions=access.Instructions(Path.Combine(KeySession,"keys","maintenance_ed25519"));message+="\r\n\r\n"+instructions;
     File.WriteAllText(Path.Combine(session,"recovery-connection.txt"),instructions);
     if(ethernet!=null)ethernet.SaveRecoverySession(new ExistingInstallationException("Installer paused for HDD repair"){Access=access},KeySession,session);
    }else if(access!=null)message+="\r\n\r\n"+access.error+". Check Ethernet/DHCP, then Retry to request maintenance access again.";
   }
   UI(()=>{using(var dialog=new RepairDialog(message))retry=dialog.ShowDialog(this)==DialogResult.Retry;});
   return retry;
  }
  void ShowExistingAccess(ExistingInstallationException error){
   device=null;finalCapture=false;supported.Visible=installHint.Visible=false;friendly.Text="";
   string key=Path.Combine(KeySession,"keys","maintenance_ed25519");
   recoveryDetails.Text=error.Access==null?"This USB payload has no recovery SSH support. Prepare the current payload, or use Utilities for a UART diagnostic shell.":error.Access.Instructions(key);
   if(error.Access==null||!error.Access.Valid)recoveryDetails.Text+=EthernetMode?"\r\nUse Utilities for UART diagnostics or factory-terminal access.":"\r\nUART fallback: "+Convert.ToString(ports.SelectedItem)+", 115200 baud, 8N1, flow control None. Press Enter for the RAM shell.";
   recoveryPanel.Visible=true;recoveryRelease.Enabled=recoveryLink!=null;Go(pFind);pFind.ScrollControlIntoView(recoveryPanel);
   Append(recoveryDetails.Text+"\r\n");File.WriteAllText(Path.Combine(session,"recovery-connection.txt"),recoveryDetails.Text);Status("This DVR already has Phoenix. Nothing was changed.");
   if(recoveryLink!=null)recoveryLink.SaveRecoverySession(error,KeySession,session);
  }
  void ReleaseRecoveryAccess(){if(recoveryLink!=null){recoveryLink.Dispose();recoveryLink=null;}recoveryRelease.Enabled=false;if(recoveryPanel.Visible)recoveryDetails.Text+="\r\nPC recovery address released. This does not stop the SSH service on the DVR.";}
  // ---------- previews used by screenshots and UI tests ----------
  public void PreviewExistingAccess(){ShowExistingAccess(new ExistingInstallationException("Demo existing installation"){Access=new RecoveryAccess{ready=true,address="169.254.3.1",port=22,host_fingerprint="SHA256:DEMO"}});}
  public void PreviewAdvanced(bool value){advView.Checked=value;ApplyView();}
  public bool Preview(string name){
   switch(name){
    case "home":Go(pHome);return true;case "ready":Go(pReady);return true;case "usb":Go(pUsb);return true;case "connect":Go(pConnect);return true;
    case "ethernet":case "find":Go(pFind);return true;case "uart":advView.Checked=true;ApplyView();advanced.Checked=true;Go(pFind);return true;
    case "install":device=new DeviceReview{Identity="BCM97252SSV / M616T",Kernel="0",Rootfs="0",StartupAbsent=true};supported.Visible=true;friendly.Text=FriendlyReview;review.Text="Demo supported factory device. Selected kernel 0, root 0.";install.Enabled=true;Go(pInstall);return true;
    case "done":Go(pDone);return true;case "utilities":advView.Checked=true;ApplyView();Go(pUtil);return true;case "update":Go(pUpdate);return true;case "help":Go(pHelp);return true;
    case "existing":PreviewExistingAccess();return true;
   }return false;
  }
  public bool HasUtilityActions{get{return pUtil!=null&&pUpdate!=null&&pHelp!=null&&utilityBolt.Text.Contains("BOLT")&&utilityLinux.Text.Contains("Linux")&&utilityFind.Text.Contains("factory");}}
  void UI(Action action){if(IsDisposed)return;if(InvokeRequired)Invoke(action);else action();}
  void Append(string text){UI(()=>{log.AppendText(text);log.SelectionStart=log.TextLength;log.ScrollToCaret();});}
  void Status(string text){UI(()=>{status.Text=text;if(busy)liveStatus.Text=text;});}
  async void Run(Func<CancellationToken,Task> operation,bool useFactoryConnection=false){if(busy)return;if(recoveryLink!=null){Status("Release the recovery connection on the Find DVR page (Advanced view) before starting another operation.");return;}if(utilityFactory!=null&&!useFactoryConnection){Status("Release factory access in Utilities before starting another operation.");return;}if(discovery!=null)discovery.Cancel();busy=true;cancellation=new CancellationTokenSource();reconnectAlpine.Enabled=restoreAlpineSsh.Enabled=prepare.Enabled=verify.Enabled=capture.Enabled=install.Enabled=commit.Enabled=findBox.Enabled=advanced.Enabled=ethernetAdapters.Enabled=false;SetUtilityBusy(true);cancel.Enabled=true;ignoreShas.Enabled=false;working.Visible=true;liveStatus.Text="";UpdateNav();
   if(updater!=null)updater.SetBusy(true);
   try{await operation(cancellation.Token);}
   catch(OperationCanceledException){ReleaseUtilityFactory();if(link!=null){link.Dispose();link=null;}if(ethernet!=null){ethernet.Dispose();ethernet=null;}Status("Stopped. Check the current device state before retrying.");}
   catch(Exception e){
    ReleaseUtilityFactory();if(link!=null){link.Dispose();link=null;}
    var existing=e as ExistingInstallationException;
    if(existing!=null&&existing.Access!=null&&existing.Access.Valid&&ethernet!=null){ethernet.RetainRecoveryAddress();recoveryLink=ethernet;ethernet=null;}
    if(ethernet!=null){ethernet.Dispose();ethernet=null;}
    Append("ERROR: "+e.Message+"\r\n");File.AppendAllText(Path.Combine(session,"events.log"),DateTime.UtcNow.ToString("o")+" ERROR "+e.GetType().Name+": "+e.Message+Environment.NewLine);
    if(existing!=null)ShowExistingAccess(existing);else Status("Stopped — "+e.Message);
    MessageBox.Show(this,e.Message+(existing!=null?"\n\nNothing on the DVR was changed. Connection details are shown on the Find DVR page.":"")+"\n\nSetup logs: "+session,existing!=null?"This DVR already has Phoenix":"Phoenix Setup stopped",MessageBoxButtons.OK,existing!=null?MessageBoxIcon.Information:MessageBoxIcon.Error);
   }finally{busy=false;cancel.Enabled=false;working.Visible=false;liveStatus.Text="";ignoreShas.Enabled=!finalCapture;reconnectAlpine.Enabled=restoreAlpineSsh.Enabled=prepare.Enabled=findBox.Enabled=advanced.Enabled=ethernetAdapters.Enabled=true;verify.Enabled=capture.Enabled=link==null;install.Enabled=device!=null&&(link!=null||ethernet!=null)&&!finalCapture;commit.Enabled=finalCapture;SetUtilityBusy(false);if(updater!=null)updater.SetBusy(false);UpdateNav();}
  }
  public bool DiscoveryActive {get{return refreshing;}}
  public void CancelDiscovery(){if(discovery!=null)discovery.Cancel();}
  // Refresh the USB list when a stick is plugged in or removed (WM_DEVICECHANGE arrival/removal).
  readonly System.Windows.Forms.Timer deviceChange=new System.Windows.Forms.Timer{Interval=1200};bool deviceChangeHooked;
  protected override void WndProc(ref Message m){
   base.WndProc(ref m);
   if(m.Msg==0x219&&(m.WParam.ToInt64()==0x8000||m.WParam.ToInt64()==0x8004)){if(!deviceChangeHooked){deviceChangeHooked=true;deviceChange.Tick+=(s,e)=>{deviceChange.Stop();if(current==pUsb&&!busy&&!refreshing)RefreshDisks();};}deviceChange.Stop();deviceChange.Start();}
  }
  async void RefreshDisks(){
   if(demo||busy||refreshing)return;refreshing=true;discovery=new CancellationTokenSource();var token=discovery.Token;disks.Enabled=false;cancel.Enabled=true;Status("Looking for USB sticks…");
   try{
    var found=await Task.Run(()=>scanner(token));if(IsDisposed||Disposing)return;
    disks.Items.Clear();foreach(var disk in found)disks.Items.Add(disk);if(disks.Items.Count>0)disks.SelectedIndex=0;
    if(!busy)Status(found.Count==0?"No USB stick found yet. Plug one in.":"Choose the USB stick to prepare.");
   }catch(OperationCanceledException){if(!IsDisposed&&!Disposing&&!busy)Status("Stopped looking for USB sticks.");}
   catch(Exception error){if(!IsDisposed&&!Disposing){Append("USB discovery: "+error.Message+"\r\n");File.AppendAllText(Path.Combine(session,"events.log"),DateTime.UtcNow.ToString("o")+" USB discovery: "+error.Message+Environment.NewLine);if(!busy)Status(error.Message);}}
   finally{refreshing=false;if(!IsDisposed&&!Disposing){disks.Enabled=true;cancel.Enabled=busy;}}
  }
  void RefreshEthernet(){
   if(busy)return;ethernetAdapters.Items.Clear();
   if(demo)ethernetAdapters.Items.Add(new EthernetAdapter{Name="PC Ethernet",Description="Demo direct connection"});
   else try{foreach(var adapter in EthernetAdapter.List())ethernetAdapters.Items.Add(adapter);}catch(Exception error){Append("Ethernet ports: "+error.Message+"\r\n");}
   if(ethernetAdapters.Items.Count>0)ethernetAdapters.SelectedIndex=0;
  }
  static string LastFoundAddress(){
   // The newest discovery.json from an earlier search, so a retry can skip the search.
   try{
    string root=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"Phoenix","SetupLogs"),latest=null;
    if(!Directory.Exists(root))return null;
    foreach(string folder in Directory.GetDirectories(root))foreach(string search in Directory.GetDirectories(folder,"ethernet-discovery-*")){string path=Path.Combine(search,"discovery.json");if(File.Exists(path)&&(latest==null||File.GetLastWriteTimeUtc(path)>File.GetLastWriteTimeUtc(latest)))latest=path;}
    if(latest==null)return null;var box=Json.Read<DiscoveredBox>(File.ReadAllText(latest));FactoryAddress.Parse(box.Address);return box.Address;
   }catch(Exception){return null;}
  }
  static string LatestSaved(string filename){
   string root=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"Phoenix","SetupLogs"),latest=null;
   if(Directory.Exists(root))foreach(string folder in Directory.GetDirectories(root)){
    string path=Path.Combine(folder,filename=="resume.json"?"ethernet-device":"",filename);
    if(File.Exists(path)&&(latest==null||String.CompareOrdinal(path,latest)>0))latest=path;
   }return latest;
  }
  string SelectSavedSession(string filename){
   string root=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"Phoenix","SetupLogs"),latest=LatestSaved(filename);
   using(var dialog=new OpenFileDialog{Title="Select "+filename+" from the earlier setup session",Filter="Saved Phoenix session|"+filename+"|All files|*.*",InitialDirectory=latest==null?root:Path.GetDirectoryName(latest),FileName=latest==null?filename:latest})return dialog.ShowDialog(this)==DialogResult.OK?dialog.FileName:null;
  }
  void ReconnectAlpine(bool latest){
   var adapter=ethernetAdapters.SelectedItem as EthernetAdapter;if(adapter==null){Status("Plug the network cable into this computer, then try again.");return;}
   string path=latest?LatestSaved("resume.json"):SelectSavedSession("resume.json");
   if(path==null){if(latest)MessageBox.Show(this,"There's no unfinished setup saved on this computer. Use the computer that started the setup, or start again with Set up a new DVR.","Setup stopped partway",MessageBoxButtons.OK,MessageBoxIcon.Information);return;}
   Go(pInstall);
   Run(async token=>{
    var saved=ResumeRecord.Load(path);saved.VerifyAdapter(adapter);keySession=saved.SessionDirectory;maintenancePublicKey=null;device=saved.Review;
    if(ethernet!=null)ethernet.Dispose();ethernet=new EthernetInstallLink(adapter,new DiscoveredBox{Address=saved.DeviceAddress},Path.Combine(session,"ethernet-resumed"),Status,token);
    await Task.Run(()=>ethernet.Reconnect(saved,path,evt=>EthernetConfiguration(evt,token),evt=>{
     string kind=Convert.ToString(evt["event"]),message=evt.ContainsKey("message")?Convert.ToString(evt["message"]):kind;Status(message);Append(message+"\r\n");
     if(kind=="complete")File.WriteAllText(Path.Combine(session,"installation-receipt.json"),Json.Write(evt));
    },()=>RecoveryPublicKey(token),RetryCheckpoint));
    ethernet.Dispose();ethernet=null;device=null;ShowCompletion();
   });
  }
  void RestoreAlpineSsh(){
   var adapter=ethernetAdapters.SelectedItem as EthernetAdapter;if(adapter==null){Status("Select the original PC Ethernet port on the Find DVR page.");return;}
   string path=SelectSavedSession("recovery-connection.txt");if(path==null)return;
   Run(async token=>{
    var saved=RecoverySession.Load(Path.GetDirectoryName(path));keySession=saved.Directory;maintenancePublicKey=null;
    if(ethernet!=null)ethernet.Dispose();ethernet=new EthernetInstallLink(adapter,new DiscoveredBox{Address=saved.Failure.Access.address},Path.Combine(session,"ssh-restored"),Status,token);
    await Task.Run(()=>ethernet.RestoreRecovery(saved));ethernet.RetainRecoveryAddress();recoveryLink=ethernet;ethernet=null;ShowExistingAccess(saved.Failure);
    Status("Temporary Alpine SSH connection restored. Use the original maintenance key shown on the Find DVR page.");
   });
  }
  void FindEthernet(){
   keySession=null;maintenancePublicKey=null;
   if(!directCable.Checked||!sequenceEntered.Checked){Status("Confirm the network connection and remote-sequence reboot first.");return;}
   var adapter=ethernetAdapters.SelectedItem as EthernetAdapter;if(adapter==null){Status("This computer has no network connection that Setup can use. Plug the cable in, then press Find again.");return;}
   if(demo){device=new DeviceReview{Identity="BCM97252SSV / M616T",Kernel="0",Rootfs="0",StartupAbsent=true};supported.Visible=installHint.Visible=true;friendly.Text=FriendlyReview;review.Text="Demo supported factory device found. No device was changed.";Go(pInstall);install.Enabled=true;return;}
   bool full=fullSearch.Checked;uint typed=0;
   if(knownAddress.Checked&&!Simple){try{typed=FactoryAddress.Parse(knownAddressText.Text);}catch(FormatException error){Status(error.Message);knownAddressText.Focus();return;}}
   Run(async token=>{
    device=null;supported.Visible=installHint.Visible=false;review.Text=friendly.Text="";if(ethernet!=null){ethernet.Dispose();ethernet=null;}
    if(adapter.Wireless)Append("Searching over Wi-Fi ("+adapter.Name+"). This works only when the DVR is plugged into the same router, not a guest network.\r\n");
    string folder=Path.Combine(session,"ethernet-discovery-"+DateTime.Now.ToString("HHmmss-fff"));
    var found=await Task.Run(()=>{
     if(typed!=0){using(var probe=new NpcapDiscovery(adapter))return probe.Probe(adapter,typed,token,Status,folder);}
     Status(full?"Preparing full-range discovery":adapter.Wireless?"Looking for your DVR over Wi-Fi. This can take about 10 minutes…":"Looking for your DVR…");var candidates=full?new List<uint>():FactoryCandidates.Generate(token);using(var search=new NpcapDiscovery(adapter))return search.Search(adapter,candidates,full,token,Status,folder);});
    knownAddressText.Text=found.Address;Append("DVR setup address: "+found.Address+"\r\n");
    Status("Found the DVR at "+found.Address+". Checking it…");
    ethernet=new EthernetInstallLink(adapter,found,Path.Combine(session,"ethernet-device"),Status,token);device=await Task.Run(()=>ethernet.Inspect());
    supported.Visible=installHint.Visible=true;friendly.Text=FriendlyReview;review.Text="Supported M616T factory device at "+found.Address+". Selected kernel "+device.Kernel+", root "+device.Rootfs+".\nThe original boot environment is saved in your installation logs. No HDD files have been changed.\nInstallation will preserve these slots, verify RUNLEVEL=1 for stock fallback, configure USB boot and review the HDD before asking for passwords.";Go(pInstall);Status("Your DVR checked out. Press Install Phoenix when you're ready.");
   });
  }
  string EthernetConfiguration(Dictionary<string,object> evt,CancellationToken token){
   string result=null;UI(()=>{string target=Convert.ToString(evt["disk_model"])+"; "+Convert.ToString(evt["free_gib"])+" GiB free. Existing HDD files are retained.";using(var dialog=new ConfigDialog(Simple?Convert.ToString(evt["free_gib"])+" GB free on the recordings disk":target,Simple))if(dialog.ShowDialog(this)==DialogResult.OK)result=dialog.Result;});
   if(result==null)return null;token.ThrowIfCancellationRequested();
   if(maintenancePublicKey==null){Status("Creating the device maintenance key");maintenancePublicKey=MaintenanceKeys.ReuseOrGenerate(KeySession,token);Append("Maintenance key saved in: "+Path.Combine(KeySession,"keys")+"\r\n");}
   var config=Json.Read<Dictionary<string,object>>(result);config["authorized_key"]=maintenancePublicKey;return Json.Write(config);
  }
  void InstallEthernet(){
   if(device==null||!device.Compatible)return;
   if(demo){finish.Text="Demo Ethernet installation complete. No device was changed.";ShowCompletion();return;}
   if(ethernet==null){Status("Find and verify the device first.");return;}
   if(MessageBox.Show(this,"Install Phoenix on this DVR now?\n\nYour DVR will restart during this process. From now on it starts from the USB stick in the rear USB port. Its recordings disk is checked before anything is copied, and nothing on it is erased.","Install Phoenix",MessageBoxButtons.YesNo,MessageBoxIcon.Question)!=DialogResult.Yes)return;
   bool ignore=ignoreShas.Checked;if(ignore&&MessageBox.Show(this,"Ignore known SHA mismatches in the target firmware? Incompatible target files may fail to boot. Installer payload and copy verification remain enforced.","Proceed at your own risk",MessageBoxButtons.YesNo,MessageBoxIcon.Warning,MessageBoxDefaultButton.Button2)!=DialogResult.Yes)return;
   string payload=bundle.Text;
   Run(async token=>{
    ethernet.SetCancellation(token);await Task.Run(()=>ethernet.Install(payload,evt=>EthernetConfiguration(evt,token),evt=>{string kind=Convert.ToString(evt["event"]);string message=evt.ContainsKey("message")?Convert.ToString(evt["message"]):kind;Status(message);Append(message+"\r\n");if(kind=="complete")File.WriteAllText(Path.Combine(session,"installation-receipt.json"),Json.Write(evt));},ignore,()=>RecoveryPublicKey(token),RetryCheckpoint));
    ethernet.Dispose();ethernet=null;finalCapture=false;commit.Visible=finalOff.Visible=false;ShowCompletion();
   });
  }
  void RefreshPorts(){if(demo||busy)return;ports.Items.Clear();foreach(string p in SerialPort.GetPortNames())ports.Items.Add(p);if(ports.Items.Count>0)ports.SelectedIndex=0;}
  void Prepare(){
   var disk=disks.SelectedItem as UsbDisk;if(disk==null){Status("Plug in a USB stick and choose it from the list.");return;}if(demo){usbReady=true;Go(pConnect);Status("Demo USB preparation complete.");return;}
   bool format=erase.Checked;if(format&&MessageBox.Show(this,"Erase everything on "+disk+"?\n\nCheck this is the right stick. Everything on it will be deleted.","Erase USB stick",MessageBoxButtons.YesNo,MessageBoxIcon.Warning)!=DialogResult.Yes)return;
   string path=bundle.Text;Run(async token=>{await Task.Run(()=>Usb.Prepare(disk,path,format,token,Status));usbReady=true;Go(pConnect);Status("The USB stick is ready. Unplug it from this computer.");});
  }
  void Verify(){if(ports.SelectedItem==null){Status("Select the UART COM port.");return;}if(demo){Status("Demo adapter verified; board communication awaits boot output.");return;}string name=Convert.ToString(ports.SelectedItem);Run(async token=>{await Task.Run(()=>{using(var p=new SerialPort(name,115200,Parity.None,8,StopBits.One){DtrEnable=false,RtsEnable=false,Handshake=Handshake.None})p.Open();});Status("Adapter opens at 115200 8N1. Board communication is not yet confirmed.");});}
  void CaptureDevice(bool final){
   if(!(final?finalOff:off).Checked){Status(final?"Power off the DVR, then check the powered-off box on this page.":"Power off the DVR, then select the powered-off checkbox.");return;}if(ports.SelectedItem==null){Status("Select a COM port.");return;}
   (final?finalOff:off).Checked=false;
   if(demo&&final){finalCapture=false;finalOff.Checked=false;commit.Enabled=false;ShowCompletion();return;}
   if(demo){device=new DeviceReview{Identity="BCM97252SSV / M616T",Kernel="0",Rootfs="0",StartupAbsent=true};supported.Visible=installHint.Visible=true;friendly.Text=FriendlyReview;review.Text="Demo M616T; STARTUP absent. Original environment saved before writes.";Go(pInstall);install.Enabled=true;Status("Demo BOLT capture complete.");return;}
   string port=Convert.ToString(ports.SelectedItem);Status("Listening on "+port+". Power on the DVR now.");
   Run(async token=>{
    if(link!=null){link.Dispose();link=null;}link=new SerialLink(port,Append,Path.Combine(session,(final?"bolt-final-":"bolt-initial-")+DateTime.Now.ToString("HHmmss-fff")),token);var found=await Task.Run(()=>link.CaptureAndInspect());
    if(!found.Compatible)throw new IOException("Unsupported board, memory or firmware selection. BCM97252SSV with kernel/root slots 0 or 1 and the expected memory layout is required.");
    if(final){if(MessageBox.Show(this,"Installation completed. Change persistent STARTUP to the Phoenix USB/HDD boot route?\nOriginal value: "+(device.StartupAbsent?"<absent>":device.Startup),"Enable Phoenix boot",MessageBoxButtons.YesNo,MessageBoxIcon.Question)!=DialogResult.Yes)return;await Task.Run(()=>link.CommitStartup(device));link.Dispose();link=null;finalCapture=false;finalOff.Checked=false;ShowCompletion();}
    else {device=found;if(!File.Exists(Path.Combine(session,"original-device.json")))File.WriteAllText(Path.Combine(session,"original-device.json"),Json.Write(device));supported.Visible=installHint.Visible=true;friendly.Text=FriendlyReview;review.Text="BCM97252SSV / M616T; selected kernel "+device.Kernel+", root "+device.Rootfs+".\nSTARTUP: "+(device.StartupAbsent?"<absent>":device.Startup)+"\nThe original values are backed up. Firmware slots are preserved; installation verifies RUNLEVEL=1 for stock fallback. STARTUP remains unchanged until the final step.";Go(pInstall);Status("BOLT captured and device reviewed.");}
   });
  }
  void ShowCompletion(){finish.Text="Installation logs: "+session;Go(pDone);Status("Phoenix is installed.");}
  void Install(){if(EthernetMode){InstallEthernet();return;}if(device==null||!device.Compatible)return;if(!usbReady&&MessageBox.Show(this,"Use an already prepared Phoenix USB key?","USB readiness",MessageBoxButtons.YesNo)!=DialogResult.Yes)return;if(demo){finalCapture=true;finalOff.Checked=false;commit.Enabled=true;finish.Text="Demo installation complete. Power off the DVR, check the box below, then enable Phoenix boot.";Go(pDone);Status("Demo only; no device was modified.");return;}
   if(MessageBox.Show(this,"Boot the temporary installer using kernel "+device.Kernel+", root "+device.Rootfs+"? RUNLEVEL will be set to 1 for stock fallback. The HDD is reviewed before installation, and STARTUP remains unchanged until the final step.","Begin installation",MessageBoxButtons.YesNo)!=DialogResult.Yes)return;
   bool allowMismatch=ignoreShas.Checked;
   if(allowMismatch&&MessageBox.Show(this,"Ignore known SHA mismatches in firmware already on the target? Incompatible libraries or binaries may fail to boot. Installer payload checksums and copy readback stay enforced.","Proceed at your own risk",MessageBoxButtons.YesNo,MessageBoxIcon.Warning,MessageBoxDefaultButton.Button2)!=DialogResult.Yes)return;
   Run(async token=>{await Task.Run(()=>{link.SetCancellation(token);link.BootInstaller(device);link.MonitorInstaller(evt=>{
    string result=null;UI(()=>{string target=Convert.ToString(evt["disk_model"])+"; "+Convert.ToString(evt["free_gib"])+" GiB free. Existing HDD files are retained.";using(var dialog=new ConfigDialog(target,false))if(dialog.ShowDialog(this)==DialogResult.OK)result=dialog.Result;});
    if(result==null)return null;
    token.ThrowIfCancellationRequested();
    if(maintenancePublicKey==null){Status("Creating the device maintenance key");maintenancePublicKey=MaintenanceKeys.ReuseOrGenerate(KeySession,token);Append("Maintenance key saved in: "+Path.Combine(KeySession,"keys")+"\r\n");}
    var config=Json.Read<Dictionary<string,object>>(result);config["authorized_key"]=maintenancePublicKey;return Json.Write(config);
   },evt=>{string kind=Convert.ToString(evt["event"]),message=evt.ContainsKey("message")?Convert.ToString(evt["message"]):kind;Status(message);Append(message+"\r\n");if(kind=="complete")File.WriteAllText(Path.Combine(session,"installation-receipt.json"),Json.Write(evt));},allowMismatch,()=>RecoveryPublicKey(token),device,RetryCheckpoint);});
    link.Dispose();link=null;off.Checked=false;finalOff.Checked=false;finalCapture=true;finish.Text="Installation verified and the HDD unmounted. Power off the DVR, check the box below, then click Enable Phoenix boot and power on when prompted.";Go(pDone);Status("Installation complete. Recapture BOLT to enable Phoenix boot.");});
  }
 }
 public static class UiDiscoveryTest {
  public static int Run(){
   Application.EnableVisualStyles();Application.SetCompatibleTextRenderingDefault(false);int ticks=0;bool observed=false,passed=false;var watch=Stopwatch.StartNew();
   using(var form=new MainForm(false,token=>{while(!token.IsCancellationRequested)Thread.Sleep(10);token.ThrowIfCancellationRequested();return new List<UsbDisk>();}))
   using(var timer=new System.Windows.Forms.Timer{Interval=100}){
    form.ShowInTaskbar=false;form.StartPosition=FormStartPosition.Manual;form.Location=new Point(-4000,-4000);
    timer.Tick+=(s,e)=>{ticks++;observed=observed||form.DiscoveryActive;if(ticks==10)form.CancelDiscovery();if(ticks>=15){passed=observed&&!form.DiscoveryActive&&form.HasUtilityActions;timer.Stop();form.Close();}};
    timer.Start();Application.Run(form);
   }
   File.WriteAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"ui-self-test.json"),Json.Write(new{passed=passed,message_pump_ticks=ticks,hung_discovery_observed=observed,discovery_cancelled=passed,elapsed_ms=watch.ElapsedMilliseconds}));return passed?0:1;
  }
 }
 public static class Program {
  [STAThread]public static int Main(string[] args){
   if(!String.IsNullOrEmpty(Environment.GetEnvironmentVariable("PHOENIX_UPDATE_ASKPASS_PIPE")))return UpdateSsh.AskPass();
   if(args.Length>=2&&args[0]=="--verify-update"){
    try{string publisher=args.Length==3?File.ReadAllText(args[2]):Encoding.UTF8.GetString(UpdateFiles.Resource("PhoenixSetup.UpdatePublicKey"));var result=UpdatePackage.Inspect(args[1],publisher);File.WriteAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"update-package-verification.json"),Json.Write(new{passed=true,version=result.Version,signed=result.Signed,sha256=result.Sha256}));return 0;}
    catch(Exception error){File.WriteAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"update-package-verification.json"),Json.Write(new{passed=false,error=error.Message}));return 1;}
   }
   if(args.Length>0&&args[0]=="--self-test")return SelfTests.Run();
   if(args.Length>0&&args[0]=="--ui-self-test")return UiDiscoveryTest.Run();
   if(args.Length>0&&args[0]=="--inventory-test"){
    try{var watch=Stopwatch.StartNew();var items=Usb.List();File.WriteAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"inventory-test.json"),Json.Write(new{success=true,eligible_usb_volumes=items.Count,elapsed_ms=watch.ElapsedMilliseconds}));return 0;}
    catch(Exception error){File.WriteAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"inventory-test.json"),Json.Write(new{success=false,error=error.Message}));return 1;}
   }
   Application.EnableVisualStyles();Application.SetCompatibleTextRenderingDefault(false);
   // --screenshot <file> or --screenshot-<page> <file> [--advanced] renders a demo page without touching devices.
   bool screenshot=args.Length>=2&&args[0].StartsWith("--screenshot");bool demo=Array.IndexOf(args,"--demo")>=0||screenshot;
   if(!demo&&!new System.Security.Principal.WindowsPrincipal(System.Security.Principal.WindowsIdentity.GetCurrent()).IsInRole(System.Security.Principal.WindowsBuiltInRole.Administrator)){
    try{Process.Start(new ProcessStartInfo(Application.ExecutablePath){UseShellExecute=true,Verb="runas"});return 0;}
    catch(System.ComponentModel.Win32Exception){MessageBox.Show("Phoenix Setup needs administrator permission to prepare the USB stick and talk to the DVR over the network cable. Open it again and choose Yes when Windows asks.","Phoenix Setup",MessageBoxButtons.OK,MessageBoxIcon.Information);return 1;}
   }
   using(var f=new MainForm(demo)){
    if(screenshot){
     f.ShowInTaskbar=false;f.StartPosition=FormStartPosition.Manual;f.Location=new Point(-4000,-4000);f.CreateControl();f.Show();
     string page=args[0]=="--screenshot"?"home":args[0].Substring("--screenshot-".Length);
     if(page=="config"||page=="config-tvh"||page=="config-wifi"){f.Close();using(var d=new ConfigDialog("120 GB free on the recordings disk",true)){d.StartPosition=FormStartPosition.Manual;d.Location=new Point(-4000,-4000);d.ShowInTaskbar=false;if(page=="config-tvh")d.ShowSeparateTvh();if(page=="config-wifi")d.ShowWifi();d.Show();Application.DoEvents();using(var image=new Bitmap(d.Width,d.Height)){d.DrawToBitmap(image,new Rectangle(0,0,image.Width,image.Height));image.Save(args[1]);}d.Close();}return 0;}
     if(page=="update-done"){f.Close();using(var d=new UpdateDoneDialog(SetupVersion.Phoenix)){d.StartPosition=FormStartPosition.Manual;d.Location=new Point(-4000,-4000);d.ShowInTaskbar=false;d.Show();Application.DoEvents();using(var image=new Bitmap(d.Width,d.Height)){d.DrawToBitmap(image,new Rectangle(0,0,image.Width,image.Height));image.Save(args[1]);}d.Close();}return 0;}
     if(Array.IndexOf(args,"--advanced")>=0)f.PreviewAdvanced(true);
     if(!f.Preview(page))return 2;
     Application.DoEvents();using(var image=new Bitmap(f.Width,f.Height)){f.DrawToBitmap(image,new Rectangle(0,0,image.Width,image.Height));image.Save(args[1]);}f.Close();return 0;
    }
    Application.Run(f);
   }return 0;
  }
 }
}
