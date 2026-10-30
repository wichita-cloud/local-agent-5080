// Retro.cs - a tiny 1997 Windows toolkit: window frame, title bar, bevels, message boxes. C# 5 (the compiler that ships with Windows).
using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Windows.Forms;

namespace BoomerGT
{
    static class R
    {
        public static readonly Color Silver = Color.FromArgb(192, 192, 192);
        public static readonly Color Navy = Color.FromArgb(0, 0, 128);
        public static readonly Color TitleBlue = Color.FromArgb(16, 132, 208);
        public static readonly Color Teal = Color.FromArgb(0, 128, 128);
        public static readonly Color DarkGreen = Color.FromArgb(0, 110, 0);
        public static readonly Color Maroon = Color.FromArgb(160, 0, 0);
        public static readonly Color Olive = Color.FromArgb(128, 100, 0);

        // "MS Sans Serif" is the raster font Windows 95 drew everything with. GDI (TextRenderer and standard controls) still has it.
        public static Font UI = new Font("MS Sans Serif", 8f, FontStyle.Regular);
        public static Font UIBold = new Font("MS Sans Serif", 8f, FontStyle.Bold);
        public static Font Mono = new Font("Courier New", 8f, FontStyle.Regular);

        public static Stream Res(string name)
        {
            return Assembly.GetExecutingAssembly().GetManifestResourceStream(name);
        }
        public static Image Img(string name)
        {
            Stream s = Res(name);
            if (s == null) return null;
            using (s) { return new Bitmap(s); }
        }
        public static Icon AppIcon()
        {
            Stream s = Res("BoomerGT.ico");
            if (s == null) return null;
            using (s) { return new Icon(s); }
        }

        public static Label Label(Control parent, string text, int x, int y, int w, int h, bool bold)
        {
            Label l = new Label();
            l.Text = text; l.Left = x; l.Top = y; l.Width = w; l.Height = h;
            l.Font = bold ? UIBold : UI; l.BackColor = Color.Transparent; l.UseMnemonic = false;
            parent.Controls.Add(l);
            return l;
        }
        public static Button Button(Control parent, string text, int x, int y, int w, int h)
        {
            Button b = new Button();
            b.Text = text; b.Left = x; b.Top = y; b.Width = w; b.Height = h; b.Font = UI;
            b.FlatStyle = FlatStyle.Standard; b.BackColor = Silver; b.UseVisualStyleBackColor = false;
            parent.Controls.Add(b);
            return b;
        }
        public static CheckBox Check(Control parent, string text, int x, int y, int w, int h, bool isChecked)
        {
            CheckBox c = new CheckBox();
            c.Text = text; c.Left = x; c.Top = y; c.Width = w; c.Height = h; c.Font = UI; c.Checked = isChecked;
            c.FlatStyle = FlatStyle.Standard; c.BackColor = Color.Transparent; c.UseMnemonic = false;
            parent.Controls.Add(c);
            return c;
        }
        public static RadioButton Radio(Control parent, string text, int x, int y, int w, int h, bool isChecked)
        {
            RadioButton c = new RadioButton();
            c.Text = text; c.Left = x; c.Top = y; c.Width = w; c.Height = h; c.Font = UI; c.Checked = isChecked;
            c.FlatStyle = FlatStyle.Standard; c.BackColor = Color.Transparent; c.UseMnemonic = false;
            parent.Controls.Add(c);
            return c;
        }
        public static TextBox Text(Control parent, string text, int x, int y, int w)
        {
            TextBox t = new TextBox();
            t.Text = text; t.Left = x; t.Top = y; t.Width = w; t.Font = UI; t.BorderStyle = BorderStyle.Fixed3D;
            parent.Controls.Add(t);
            return t;
        }
        public static int TextHeight(string text, int width)
        {
            Size s = TextRenderer.MeasureText(text, UI, new Size(width, 2000), TextFormatFlags.WordBreak | TextFormatFlags.NoPadding);
            return s.Height;
        }
    }

    // the etched line that sits above the buttons
    class Etched : Control
    {
        public Etched() { SetStyle(ControlStyles.SupportsTransparentBackColor | ControlStyles.UserPaint, true); BackColor = Color.Transparent; TabStop = false; }
        protected override void OnPaint(PaintEventArgs e)
        {
            e.Graphics.DrawLine(new Pen(Color.FromArgb(128, 128, 128)), 0, 0, Width, 0);
            e.Graphics.DrawLine(Pens.White, 0, 1, Width, 1);
        }
    }

    // a 1997 window: raised 3D border, navy title bar with a gradient, tiny close box
    class RetroForm : Form
    {
        [DllImport("user32.dll")] static extern bool ReleaseCapture();
        [DllImport("user32.dll")] static extern IntPtr SendMessage(IntPtr hWnd, int msg, int wParam, int lParam);

        public string Caption = "";
        Image appIcon;
        public RetroForm(int w, int h)
        {
            FormBorderStyle = FormBorderStyle.None;
            StartPosition = FormStartPosition.CenterScreen;
            ClientSize = new Size(w, h);
            BackColor = R.Silver; Font = R.UI;
            DoubleBuffered = true;
            Icon = R.AppIcon();
            appIcon = R.Img("app.png");
        }
        protected Rectangle CloseBox { get { return new Rectangle(Width - 22, 5, 16, 14); } }
        protected virtual void OnCloseBox() { Close(); }
        protected override void OnPaint(PaintEventArgs e)
        {
            Graphics g = e.Graphics;
            ControlPaint.DrawBorder3D(g, new Rectangle(0, 0, Width, Height), Border3DStyle.Raised);
            Rectangle t = new Rectangle(3, 3, Width - 6, 18);
            using (LinearGradientBrush b = new LinearGradientBrush(t, R.Navy, R.TitleBlue, LinearGradientMode.Horizontal)) { g.FillRectangle(b, t); }
            if (appIcon != null)
            {
                g.InterpolationMode = InterpolationMode.NearestNeighbor;
                g.DrawImage(appIcon, new Rectangle(5, 4, 16, 16));
            }
            TextRenderer.DrawText(g, Caption, R.UIBold, new Point(25, 5), Color.White, TextFormatFlags.NoPadding);
            Rectangle cb = CloseBox;
            ControlPaint.DrawButton(g, cb, ButtonState.Normal);
            using (Pen p = new Pen(Color.Black, 2))
            {
                g.DrawLine(p, cb.Left + 4, cb.Top + 3, cb.Right - 5, cb.Bottom - 4);
                g.DrawLine(p, cb.Right - 5, cb.Top + 3, cb.Left + 4, cb.Bottom - 4);
            }
        }
        protected override void OnMouseDown(MouseEventArgs e)
        {
            base.OnMouseDown(e);
            if (e.Button != MouseButtons.Left) return;
            if (CloseBox.Contains(e.Location)) { OnCloseBox(); return; }
            if (e.Y >= 3 && e.Y <= 21) { ReleaseCapture(); SendMessage(Handle, 0xA1, 2, 0); }
        }
    }

    // a plain solid-colour backdrop behind the dialog, like an old installer on a bare desktop
    class Backdrop : Form
    {
        public string Product = "BoomerGT";
        public Backdrop()
        {
            FormBorderStyle = FormBorderStyle.None;
            WindowState = FormWindowState.Maximized;
            ShowInTaskbar = false;
            BackColor = Color.FromArgb(0, 128, 128);
        }
    }

    // a small dialog with an icon, some text and buttons: used instead of the modern MessageBox
    class RetroMsg : RetroForm
    {
        public int Result = -1;
        RetroMsg(string caption, string text, string icon, string[] buttons) : base(380, 40)
        {
            Caption = caption;
            Image img = R.Img(icon + ".png");
            if (img != null) { PictureBox pb = new PictureBox(); pb.Image = img; pb.SizeMode = PictureBoxSizeMode.AutoSize; pb.Left = 18; pb.Top = 36; pb.BackColor = Color.Transparent; Controls.Add(pb); }
            int tw = 380 - 18 - 32 - 16 - 18;
            int th = Math.Max(32, R.TextHeight(text, tw));
            R.Label(this, text, 18 + 32 + 16, 38, tw, th + 4, false);
            int bw = 75, gap = 8, total = buttons.Length * bw + (buttons.Length - 1) * gap;
            int by = 38 + Math.Max(th, 32) + 24;
            int bx = (380 - total) / 2;
            for (int i = 0; i < buttons.Length; i++)
            {
                int idx = i;
                Button b = R.Button(this, buttons[i], bx + i * (bw + gap), by, bw, 23);
                b.Click += delegate { Result = idx; Close(); };
                if (i == 0) { AcceptButton = b; }
            }
            ClientSize = new Size(380, by + 23 + 16);
            StartPosition = FormStartPosition.CenterParent;
            ShowInTaskbar = false;
        }
        protected override void OnCloseBox() { Result = -1; Close(); }
        public static int Show(IWin32Window owner, string caption, string text, string icon, params string[] buttons)
        {
            RetroMsg m = new RetroMsg(caption, text, icon, buttons);
            m.ShowDialog(owner);
            return m.Result;
        }
    }
}
