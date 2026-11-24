using System;
using System.IO;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Windows.Forms;

namespace PhoenixSetup {
 // "Molten" look shared with the Phoenix TV menu: near-black warm neutrals, ember red to gold accent.
 public static class Theme {
  public static readonly Color Bg=Color.FromArgb(18,13,11),Panel=Color.FromArgb(29,21,18),Panel2=Color.FromArgb(38,27,23),Field=Color.FromArgb(12,9,8),Line=Color.FromArgb(70,52,44);
  public static readonly Color Fg=Color.FromArgb(243,233,226),Muted=Color.FromArgb(184,165,154),Faint=Color.FromArgb(138,118,107);
  public static readonly Color Ember=Color.FromArgb(194,65,12),Flame=Color.FromArgb(234,122,23),Gold=Color.FromArgb(245,183,49),Ink=Color.FromArgb(26,14,8);
  public static readonly Color NoteBg=Color.FromArgb(42,26,14),OkBg=Color.FromArgb(18,34,26),Ok=Color.FromArgb(127,207,143),WarnBg=Color.FromArgb(46,20,15);
  public const string Primary="primary",Quiet="quiet";
  static Image logo;
  public static Image Logo{get{if(logo==null)logo=Image.FromStream(new MemoryStream(Convert.FromBase64String(LogoData.Png)));return logo;}}
  public static Icon AppIcon(){try{using(var small=new Bitmap(Logo,32,32))return Icon.FromHandle(small.GetHicon());}catch{return null;}}
  public static void Apply(Control root){
   foreach(Control c in root.Controls)Apply(c);
   var button=root as Button;
   if(button!=null&&!(root is ChoiceCard)){
    button.FlatStyle=FlatStyle.Flat;button.UseVisualStyleBackColor=false;button.Cursor=Cursors.Hand;
    if(Convert.ToString(button.Tag)==Primary){button.BackColor=Flame;button.ForeColor=Ink;button.FlatAppearance.BorderColor=Gold;button.Font=new Font("Segoe UI Semibold",11);button.Padding=new Padding(14,4,14,4);}
    else if(Convert.ToString(button.Tag)==Quiet){button.BackColor=Bg;button.ForeColor=Muted;button.FlatAppearance.BorderSize=0;}
    else {button.BackColor=Panel;button.ForeColor=Fg;button.FlatAppearance.BorderColor=Line;}
    return;
   }
   if(root is TextBoxBase){root.BackColor=Field;root.ForeColor=Fg;var box=root as TextBox;if(box!=null)box.BorderStyle=BorderStyle.FixedSingle;return;}
   var combo=root as ComboBox;if(combo!=null){combo.FlatStyle=FlatStyle.Flat;combo.BackColor=Field;combo.ForeColor=Fg;return;}
   if(root is CheckBox||root is RadioButton){root.ForeColor=Fg;return;}
   if(root is Label){if(root.ForeColor==SystemColors.ControlText)root.ForeColor=Fg;return;}
   if(root is FlowLayoutPanel||root is TableLayoutPanel||root is Form||root.GetType()==typeof(Panel)){if(root.BackColor==SystemColors.Control||root.BackColor==Color.White||root.BackColor==Color.Transparent)root.BackColor=Bg;}
  }
 }

 // Progress strip for the new-box flow: finished steps dimmed, current step underlined in the molten gradient.
 public sealed class StepBar:Control {
  string[] steps=new string[0];int current=-1;
  public StepBar(){SetStyle(ControlStyles.AllPaintingInWmPaint|ControlStyles.OptimizedDoubleBuffer|ControlStyles.UserPaint|ControlStyles.ResizeRedraw,true);Height=40;Font=new Font("Segoe UI",9.5f);}
  public void Set(string[] names,int index){steps=names??new string[0];current=index;Invalidate();}
  protected override void OnPaint(PaintEventArgs e){
   var g=e.Graphics;g.Clear(Color.FromArgb(16,11,10));
   using(var line=new Pen(Theme.Line))g.DrawLine(line,0,Height-1,Width,Height-1);
   if(steps.Length==0)return;int pad=24;float w=(Width-2*pad)/(float)steps.Length;
   for(int i=0;i<steps.Length;i++){
    var r=new Rectangle((int)(pad+i*w),0,(int)w,Height-4);
    Color text=i<current?Theme.Muted:i==current?Theme.Fg:Theme.Faint;
    TextRenderer.DrawText(g,steps[i],Font,r,text,TextFormatFlags.HorizontalCenter|TextFormatFlags.VerticalCenter|TextFormatFlags.EndEllipsis);
    var bar=new Rectangle(r.X,Height-4,r.Width,3);
    if(i==current){using(var b=new LinearGradientBrush(new Rectangle(bar.X-1,bar.Y,bar.Width+2,bar.Height),Theme.Ember,Theme.Gold,LinearGradientMode.Horizontal))g.FillRectangle(b,bar);}
    else if(i<current){using(var b=new SolidBrush(Color.FromArgb(90,58,38)))g.FillRectangle(b,bar);}
   }
  }
 }

 // Large clickable choice used on the start and help screens.
 public sealed class ChoiceCard:Button {
  readonly string eyebrow,title,detail;readonly bool primary;bool hover;
  public ChoiceCard(string eyebrow,string title,string detail,bool primary){
   this.eyebrow=eyebrow;this.title=title;this.detail=detail;this.primary=primary;Text=title;AccessibleName=title;AccessibleDescription=detail;
   Size=new Size(262,184);Cursor=Cursors.Hand;FlatStyle=FlatStyle.Flat;FlatAppearance.BorderSize=0;Margin=new Padding(0,0,16,0);
   SetStyle(ControlStyles.AllPaintingInWmPaint|ControlStyles.OptimizedDoubleBuffer|ControlStyles.UserPaint,true);
   MouseEnter+=(s,e)=>{hover=true;Invalidate();};MouseLeave+=(s,e)=>{hover=false;Invalidate();};GotFocus+=(s,e)=>Invalidate();LostFocus+=(s,e)=>Invalidate();
  }
  protected override void OnPaint(PaintEventArgs e){
   var g=e.Graphics;g.SmoothingMode=SmoothingMode.AntiAlias;var rect=new Rectangle(0,0,Width-1,Height-1);g.Clear(Theme.Bg);
   using(var path=Round(rect,10)){
    if(primary){using(var b=new LinearGradientBrush(rect,Color.FromArgb(62,28,12),Theme.Panel,LinearGradientMode.ForwardDiagonal))g.FillPath(b,path);}
    else using(var b=new SolidBrush(Theme.Panel))g.FillPath(b,path);
    using(var p=new Pen(hover||Focused?Theme.Flame:primary?Color.FromArgb(122,58,20):Theme.Line,Focused?2:1))g.DrawPath(p,path);
   }
   var badge=new Rectangle(18,18,38,38);
   using(var path=Round(badge,8)){
    if(primary){using(var b=new LinearGradientBrush(badge,Theme.Ember,Theme.Gold,LinearGradientMode.ForwardDiagonal))g.FillPath(b,path);}
    else using(var b=new SolidBrush(Theme.Panel2))g.FillPath(b,path);
   }
   using(var f=new Font("Segoe UI Semibold",13))TextRenderer.DrawText(g,primary?"+":title.StartsWith("Update")?"↑":"?",f,badge,primary?Theme.Ink:Theme.Gold,TextFormatFlags.HorizontalCenter|TextFormatFlags.VerticalCenter);
   int y=66;
   if(!String.IsNullOrEmpty(eyebrow))using(var f=new Font("Segoe UI",8.5f,FontStyle.Bold)){TextRenderer.DrawText(g,eyebrow.ToUpperInvariant(),f,new Point(18,y),Theme.Gold);y+=20;}
   using(var f=new Font("Segoe UI Semibold",13.5f)){TextRenderer.DrawText(g,title,f,new Point(16,y),Theme.Fg);y+=30;}
   using(var f=new Font("Segoe UI",9.5f))TextRenderer.DrawText(g,detail,f,new Rectangle(18,y,Width-34,Height-y-10),Theme.Muted,TextFormatFlags.WordBreak);
  }
  static GraphicsPath Round(Rectangle r,int radius){var p=new GraphicsPath();int d=radius*2;p.AddArc(r.X,r.Y,d,d,180,90);p.AddArc(r.Right-d,r.Y,d,d,270,90);p.AddArc(r.Right-d,r.Bottom-d,d,d,0,90);p.AddArc(r.X,r.Bottom-d,d,d,90,90);p.CloseFigure();return p;}
 }

 // Header band: logo, page title and the Advanced view switch.
 public sealed class HeaderBand:Panel {
  public readonly Label Title=new Label{AutoSize=true,Font=new Font("Segoe UI Semibold",15),ForeColor=Theme.Fg,BackColor=Color.Transparent,Location=new Point(84,14)};
  public readonly Label Subtitle=new Label{AutoSize=true,Font=new Font("Segoe UI",10),ForeColor=Theme.Muted,BackColor=Color.Transparent,Location=new Point(86,44)};
  public HeaderBand(){
   Dock=DockStyle.Top;Height=80;DoubleBuffered=true;
   Controls.Add(new PictureBox{Image=Theme.Logo,SizeMode=PictureBoxSizeMode.Zoom,Size=new Size(54,54),Location=new Point(20,13),BackColor=Color.Transparent});
   Controls.Add(Title);Controls.Add(Subtitle);
  }
  protected override void OnPaint(PaintEventArgs e){
   using(var b=new LinearGradientBrush(ClientRectangle,Color.FromArgb(36,23,15),Theme.Bg,LinearGradientMode.Vertical))e.Graphics.FillRectangle(b,ClientRectangle);
   using(var p=new Pen(Theme.Line))e.Graphics.DrawLine(p,0,Height-1,Width,Height-1);
  }
 }
}
