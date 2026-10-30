// Wizard.cs - the BoomerGT Setup wizard: pages, Back/Next/Cancel, and the uninstall flow. ASCII only, C# 5.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Text;
using System.Windows.Forms;
using Microsoft.Win32;

namespace BoomerGT
{
    abstract class Page
    {
        public Wizard W;
        public Panel P;
        protected Page(Wizard w)
        {
            W = w;
            P = new Panel(); P.Left = 164; P.Top = 30; P.Width = 386; P.Height = 300; P.BackColor = Color.Transparent; P.Visible = false;
            W.Controls.Add(P);
        }
        public virtual bool Applies() { return true; }
        public virtual void Enter() { }
        public virtual bool Ready() { return true; }
        public virtual void Leave() { }
        public virtual string NextText() { return "Next >"; }
        public virtual bool BackVisible() { return true; }
        public virtual bool NextVisible() { return true; }
        public virtual bool CancelVisible() { return true; }
        public Label Head(string text) { return R.Label(P, text, 0, 0, 380, 16, true); }
        public Label Para(string text, int y)
        {
            int h = R.TextHeight(text, 378);
            return R.Label(P, text, 0, y, 380, h + 2, false);
        }
        public int ParaEnd(Label l) { return l.Top + l.Height + 6; }
    }

    class CheckList : Panel
    {
        int y = 4;
        public CheckList() { AutoScroll = true; BackColor = Color.White; BorderStyle = BorderStyle.Fixed3D; }
        public void Clear() { Controls.Clear(); y = 4; }
        public void Add(string level, string text)
        {
            string tag = level == "ok" ? "OK" : level == "warn" ? "!!" : level == "fail" ? "XX" : "i";
            Color c = level == "ok" ? R.DarkGreen : level == "warn" ? R.Olive : level == "fail" ? R.Maroon : Color.Navy;
            Label st = new Label(); st.Left = 6; st.Top = y; st.Width = 30; st.Height = 14; st.Text = tag; st.Font = R.UIBold; st.ForeColor = c; st.BackColor = Color.Transparent; Controls.Add(st);
            int tw = Width - 70;
            int h = R.TextHeight(text, tw);
            Label t = new Label(); t.Left = 40; t.Top = y; t.Width = tw; t.Height = h + 2; t.Text = text; t.Font = R.UI; t.BackColor = Color.Transparent; t.UseMnemonic = false; Controls.Add(t);
            y += Math.Max(h, 14) + 8;
        }
    }

    // ------------------------------------------------------------------ pages
    class WelcomePage : Page
    {
        public WelcomePage(Wizard w) : base(w)
        {
            Head("Welcome to the BoomerGT Setup program.");
            Label a = Para("This program will install BoomerGT on your computer. BoomerGT is a private AI agent that runs entirely on your own graphics card. Your conversations and memories stay on this computer.", 26);
            Label b = Para("It is strongly recommended that you close any games or other programs that use the graphics card before you continue.", ParaEnd(a));
            Label c = Para("Setup takes about 15 to 45 minutes, depending on your internet speed: most of it is downloading a 14 GB model. You can leave it running.", ParaEnd(b));
            Label m = Para("This installer was built using Claude Code, an AI coding tool made by Anthropic.", ParaEnd(c));
            Label d = Para("Click Next to continue, or Cancel to exit Setup.", ParaEnd(m) + 10);
        }
        public override bool BackVisible() { return false; }
    }

    class CheckPage : Page
    {
        CheckList list; Label status; Button again; bool done;
        public CheckPage(Wizard w) : base(w)
        {
            Head("Checking your computer");
            status = Para("Setup is looking at your computer. This takes a few seconds...", 22);
            list = new CheckList(); list.Left = 0; list.Top = 46; list.Width = 380; list.Height = 214; P.Controls.Add(list);
            again = R.Button(P, "Check again", 0, 268, 90, 23);
            again.Click += delegate { Enter(); };
        }
        public override void Enter()
        {
            done = false; W.Checks.Clear(); list.Clear(); again.Enabled = false;
            status.Text = "Setup is looking at your computer. This takes a few seconds...";
            W.UpdateButtons();
            if (W.Screens) { W.LoadSampleChecks(); Fill(); return; }
            Engine e = new Engine();
            e.Line += delegate (string tag, string text)
            {
                if (tag != "CHECK") return;
                string[] p = text.Split(new char[] { '|' }, 3);
                if (p.Length < 3) return;
                CheckItem ci = new CheckItem(); ci.Level = p[0]; ci.Key = p[1]; ci.Text = p[2];
                W.UI(delegate { W.Checks.Add(ci); });
            };
            e.Exited += delegate (int code) { W.UI(delegate { Fill(); }); };
            try { e.Start("check", "", W.A.InstallDir, false, ""); }
            catch (Exception ex) { W.Checks.Add(MakeItem("fail", "engine", "Setup could not start PowerShell: " + ex.Message)); Fill(); }
        }
        static CheckItem MakeItem(string l, string k, string t) { CheckItem c = new CheckItem(); c.Level = l; c.Key = k; c.Text = t; return c; }
        void Fill()
        {
            list.Clear(); bool fail = false, warn = false;
            foreach (CheckItem c in W.Checks)
            {
                if (c.Key == "model") { W.RecommendedModel = c.Text; continue; }
                if (c.Key == "freegb") continue;
                if (c.Key == "ramgb") { int gb; if (int.TryParse(c.Text, out gb)) W.RamGB = gb; continue; }
                if (c.Key == "laptop") { W.Laptop = c.Text == "yes"; continue; }
                list.Add(c.Level, c.Text);
                if (c.Level == "fail") fail = true;
                if (c.Level == "warn") warn = true;
            }
            if (W.Checks.Count == 0) { list.Add("fail", "The check did not return anything. Is Windows PowerShell available?"); fail = true; }
            done = !fail; again.Enabled = true;
            status.Text = fail ? "Setup found a problem that must be fixed first. Fix it, then click Check again." : (warn ? "You can continue. The items marked !! are worth reading." : "Everything looks good.");
            W.UpdateButtons();
        }
        public override bool Ready() { return done; }
    }

    class DockerPage : Page
    {
        CheckBox accept;
        public DockerPage(Wizard w) : base(w)
        {
            Head("Docker Desktop license");
            Label a = Para("BoomerGT needs Docker Desktop to run its pieces in separate, tidy boxes. It is not installed on this computer, so Setup can install it for you.", 22);
            Label b = Para("Docker Desktop is free for personal use, education, non-commercial open source, and small businesses (fewer than 250 employees and under $10 million a year in revenue). Other businesses need a paid Docker subscription.", ParaEnd(a));
            LinkLabel link = new LinkLabel(); link.Text = "Read the Docker Subscription Service Agreement"; link.Left = 0; link.Top = ParaEnd(b) + 2; link.Width = 380; link.Height = 16; link.Font = R.UI; link.BackColor = Color.Transparent;
            link.LinkClicked += delegate { try { Process.Start("https://www.docker.com/legal/docker-subscription-service-agreement/"); } catch { } };
            P.Controls.Add(link);
            Label c = Para("The agreement is between you and Docker. Setup cannot accept it for you.", link.Top + 24);
            accept = R.Check(P, "I accept the Docker Subscription Service Agreement and want Setup to install Docker Desktop.", 0, ParaEnd(c) + 8, 380, 30, false);
            accept.CheckedChanged += delegate { W.A.AcceptDocker = accept.Checked; W.UpdateButtons(); };
        }
        public override bool Applies() { return W.Has("dockermissing"); }
        public override bool Ready() { return accept.Checked; }
    }

    class SecurityPage : Page
    {
        RadioButton prot, std;
        public SecurityPage(Wizard w) : base(w)
        {
            Head("How much should the agent be allowed to touch?");
            Para("The agent can run commands and change files. If it reads something harmful, or just makes a mistake, the damage is limited to what it can reach.", 22);
            prot = R.Radio(P, "Protected (recommended)", 0, 76, 380, 18, true);
            Label a = R.Label(P, "Its commands run in a sealed box that sees only its own folder and the folders you share. Web search and reading web pages still work. It cannot reach your Windows drives, passwords or other files.", 18, 96, 360, 50, false);
            std = R.Radio(P, "Standard", 0, 156, 380, 18, false);
            Label b = R.Label(P, "Its commands run directly on your computer with your WSL account's access, including your Windows drives. Simpler, but a bigger risk if something goes wrong.", 18, 176, 360, 50, false);
            prot.CheckedChanged += delegate { W.A.Security = prot.Checked ? "protected" : "standard"; };
            CheckBox adv = R.Check(P, "Show advanced options (shared folders, internet for programs)", 0, 250, 380, 18, false);
            adv.CheckedChanged += delegate { W.Advanced = adv.Checked; };
        }
    }

    class FolderPage : Page
    {
        CheckBox share, net; TextBox path; Button browse; RadioButton ro, rw;
        public FolderPage(Wizard w) : base(w)
        {
            Head("Folders and the internet");
            Para("You can let the agent work on a folder of yours, for example the files for a project. It sees nothing else on your computer.", 22);
            share = R.Check(P, "Share a folder with the agent", 0, 70, 380, 18, false);
            path = R.Text(P, "", 18, 92, 270); path.Enabled = false;
            browse = R.Button(P, "Browse...", 296, 91, 75, 23); browse.Enabled = false;
            ro = R.Radio(P, "It may only read the files (safest)", 18, 120, 360, 18, true); ro.Enabled = false;
            rw = R.Radio(P, "It may also change and delete them", 18, 140, 360, 18, false); rw.Enabled = false;
            Label n = Para("Programs the agent runs are normally cut off from the internet, so they cannot send your files anywhere. Searching the web and reading pages still works either way.", 172);
            net = R.Check(P, "Let programs the agent runs use the internet too (to install tools, clone code)", 0, ParaEnd(n) + 4, 380, 30, false);
            share.CheckedChanged += delegate { path.Enabled = browse.Enabled = ro.Enabled = rw.Enabled = share.Checked; if (!share.Checked) { path.Text = ""; } W.UpdateButtons(); };
            browse.Click += delegate
            {
                FolderBrowserDialog d = new FolderBrowserDialog(); d.Description = "Choose the folder to share with the agent";
                if (d.ShowDialog(W) == DialogResult.OK) path.Text = d.SelectedPath;
            };
            path.TextChanged += delegate { W.UpdateButtons(); };
        }
        // Advanced only: the defaults (nothing shared, programs offline) apply unless the user asked to see this page.
        public override bool Applies() { return W.Advanced; }
        public override bool Ready() { return !share.Checked || path.Text.Trim().Length > 0; }
        public override void Leave()
        {
            W.A.ShareFolder = share.Checked ? path.Text.Trim() : ""; W.A.ShareWrite = share.Checked && rw.Checked; W.A.NetworkInternet = net.Checked;
        }
    }

    class ExtrasPage : Page
    {
        CheckBox web, night, voice, auto, jina; Label nightNote;
        public ExtrasPage(Wizard w) : base(w)
        {
            Head("Optional extras");
            web = R.Check(P, "Let the agent search the web (free, no account)", 0, 24, 380, 18, true);
            R.Label(P, "Uses SearXNG, a small search program that runs on your computer. Your search words go to public search engines.", 18, 42, 360, 30, false);
            night = R.Check(P, "Scheduled research on a desktop PC (beta)", 0, 76, 380, 18, false);
            nightNote = R.Label(P, "", 18, 94, 360, 42, false);
            voice = R.Check(P, "Speak and listen (extra download, about 6 GB of disk)", 0, 140, 380, 18, false);
            R.Label(P, "A voice for the agent and a microphone button, all on your computer.", 18, 158, 360, 28, false);
            auto = R.Check(P, "Start BoomerGT when I sign in to Windows", 0, 190, 380, 18, false);
            R.Label(P, "Starts everything except the model, which loads with your first message. So after a restart the graphics card is always free for games.", 18, 208, 360, 30, false);
            jina = R.Check(P, "Read pages that block the agent through Jina AI", 0, 244, 380, 18, false);
            R.Label(P, "Privacy: Jina AI, a company, then sees the address of each such page and your IP address. Off unless you tick it.", 18, 262, 360, 36, false);
        }
        public override void Enter()
        {
            // a desktop is built to run for hours; on a laptop it means heat, fan noise and a flat battery
            nightNote.Text = W.Laptop
                ? "Not for laptops: it keeps the graphics card busy for hours (heat, fan noise, battery). Leave this off."
                : "In hours you choose, while the PC is idle, the graphics card researches your projects and leaves suggestions for you to keep or discard.";
            nightNote.ForeColor = W.Laptop ? R.Maroon : Color.Black;
        }
        public override void Leave() { W.A.WebSearch = web.Checked; W.A.Overnight = night.Checked; W.A.Voice = voice.Checked; W.A.Autostart = auto.Checked; W.A.Jina = jina.Checked; }
    }

    class DestPage : Page
    {
        TextBox dir; Label space, note; ComboBox model; List<string> ids = new List<string> { "qwen3.5-27b-iq4xs", "qwen3-14b-q4km", "qwen3-8b-q4km" };
        const string Bolt = "nemotron-3.5-bolt-74b-128k";
        public DestPage(Wizard w) : base(w)
        {
            Head("Choose where to install");
            Para("Setup will keep BoomerGT, its private Ubuntu and its memory in this folder. Your own Ubuntu, if you have one, is not touched.", 22);
            dir = R.Text(P, W.A.InstallDir, 0, 78, 290);
            Button b = R.Button(P, "Browse...", 296, 77, 75, 23);
            b.Click += delegate { FolderBrowserDialog d = new FolderBrowserDialog(); d.Description = "Choose the install folder"; if (d.ShowDialog(W) == DialogResult.OK) dir.Text = Path.Combine(d.SelectedPath, "BoomerGT"); };
            dir.TextChanged += delegate { UpdateSpace(); W.UpdateButtons(); };
            space = R.Label(P, "", 0, 106, 380, 30, false);
            R.Label(P, "Which model should the agent use?", 0, 150, 380, 16, true);
            model = new ComboBox(); model.DropDownStyle = ComboBoxStyle.DropDownList; model.Font = R.UI; model.Left = 0; model.Top = 170; model.Width = 380;
            model.Items.Add("Qwen3.5 27B (for 16 GB graphics cards, about 14 GB)");
            model.Items.Add("Qwen3 14B (smaller and faster, weaker with tools, about 9 GB)");
            model.Items.Add("Qwen3 8B (smallest, for testing, about 5 GB)");
            model.SelectedIndex = 0; P.Controls.Add(model);
            note = R.Label(P, "", 0, 198, 380, 44, false);
            model.SelectedIndexChanged += delegate { UpdateNote(); UpdateSpace(); };
        }
        bool BoltPicked() { return model.SelectedIndex >= 0 && ids[model.SelectedIndex] == Bolt; }
        void UpdateNote()
        {
            note.Text = BoltPicked()
                ? "Bolt is as fast as the 27B, but only while nothing else uses much memory: close other Linux (WSL) systems and big programs, and keep the monitor on the motherboard."
                : "Setup suggests a model that fits your graphics card. You can change it later.";
        }
        void UpdateSpace()
        {
            try
            {
                string root = Path.GetPathRoot(Path.GetFullPath(dir.Text));
                DriveInfo di = new DriveInfo(root);
                long gb = di.AvailableFreeSpace / (1024L * 1024L * 1024L);
                int need = BoltPicked() ? 55 : 45;
                space.Text = "Space required: about " + need + " GB     Space available: " + gb + " GB";
                space.ForeColor = gb < need ? R.Maroon : Color.Black;
            }
            catch { space.Text = "That folder cannot be used."; space.ForeColor = R.Maroon; }
        }
        public override void Enter()
        {
            // NVIDIA's Bolt 74B keeps ~7 GB of experts in system RAM, so it is listed only with 32 GB (Windows reports ~31.x),
            // first, as the check recommends it then
            bool card16 = W.RecommendedModel == Bolt || W.RecommendedModel == "qwen3.5-27b-iq4xs";
            if (W.RamGB >= 30 && card16 && !ids.Contains(Bolt))
            {
                ids.Insert(0, Bolt);
                model.Items.Insert(0, "Nemotron Bolt 74B (recommended with 32 GB memory, about 22 GB)");
            }
            int i = ids.IndexOf(W.RecommendedModel); if (i >= 0) model.SelectedIndex = i;
            UpdateNote(); UpdateSpace();
        }
        public override bool Ready() { try { Path.GetFullPath(dir.Text); return dir.Text.Trim().Length > 3; } catch { return false; } }
        public override void Leave()
        {
            // A folder that already holds other files (say Documents, typed by hand) gets a BoomerGT subfolder, so the
            // install never mixes with the user's files and Uninstall never touches them. A previous install is reused.
            string d = dir.Text.Trim();
            try
            {
                if (Directory.Exists(d) && Directory.GetFileSystemEntries(d).Length > 0
                    && !File.Exists(Path.Combine(d, "setup.log")) && !File.Exists(Path.Combine(d, "answers.json")))
                    d = Path.Combine(d, "BoomerGT");
            }
            catch { }
            W.A.InstallDir = d; W.A.Model = ids[Math.Max(0, model.SelectedIndex)];
        }
    }

    class ReadyPage : Page
    {
        Label summary;
        public ReadyPage(Wizard w) : base(w)
        {
            Head("Ready to install");
            Para("Setup has enough information to start installing BoomerGT. Click Back to change anything, or Install to begin.", 22);
            summary = R.Label(P, "", 0, 70, 380, 140, false);
            summary.BorderStyle = BorderStyle.Fixed3D; summary.BackColor = Color.White;
            R.Label(P, "Setup will restart Docker Desktop once, which briefly stops any containers you have running. Windows may ask permission to run as administrator. Your other Ubuntu is not touched.", 0, 220, 380, 56, false);
        }
        public override void Enter()
        {
            Answers a = W.A; StringBuilder s = new StringBuilder();
            s.AppendLine(" Install folder:  " + a.InstallDir);
            s.AppendLine(" Model:  " + a.Model);
            s.AppendLine(" Security:  " + (a.Security == "protected" ? "Protected (sealed box)" : "Standard"));
            s.AppendLine(" Shared folder:  " + (a.ShareFolder.Length > 0 ? a.ShareFolder + (a.ShareWrite ? " (read and write)" : " (read only)") : "none"));
            s.AppendLine(" Internet for its programs:  " + (a.NetworkInternet ? "yes" : "no"));
            s.AppendLine(" Web search:  " + (a.WebSearch ? "yes" : "no"));
            s.AppendLine(" Scheduled research (beta):  " + (a.Overnight ? "yes" : "no"));
            s.AppendLine(" Voice:  " + (a.Voice ? "yes" : "no"));
            s.AppendLine(" Start at sign-in:  " + (a.Autostart ? "yes" : "no"));
            s.AppendLine(" Jina reader for pages that block it:  " + (a.Jina ? "yes (Jina sees those page addresses)" : "no"));
            summary.Text = s.ToString();
        }
        public override string NextText() { return "Install"; }
    }

    class ProgressPage : Page
    {
        public ProgressBar Bar; Label step, pct; TextBox log; Button details; bool uninstall;
        public ProgressPage(Wizard w, bool isUninstall) : base(w)
        {
            uninstall = isUninstall;
            Head(isUninstall ? "Removing BoomerGT" : "Installing BoomerGT");
            step = R.Label(P, "Starting...", 0, 30, 380, 30, false);
            Bar = new ProgressBar(); Bar.Left = 0; Bar.Top = 66; Bar.Width = 380; Bar.Height = 22; Bar.Minimum = 0; Bar.Maximum = 100; Bar.Style = ProgressBarStyle.Blocks; P.Controls.Add(Bar);
            pct = R.Label(P, "0%", 0, 92, 380, 14, false);
            R.Label(P, isUninstall ? "Please wait while Setup removes BoomerGT." : "Please wait while Setup installs BoomerGT. This takes about 15 to 45 minutes, depending on your internet speed. You can leave it running.", 0, 118, 380, 44, false);
            details = R.Button(P, "Details >>", 0, 270, 90, 23);
            log = new TextBox(); log.Multiline = true; log.ReadOnly = true; log.ScrollBars = ScrollBars.Vertical; log.Font = R.Mono; log.Left = 0; log.Top = 160; log.Width = 380; log.Height = 104; log.Visible = false; log.BackColor = Color.White; P.Controls.Add(log);
            details.Click += delegate { log.Visible = !log.Visible; details.Text = log.Visible ? "<< Details" : "Details >>"; };
        }
        public override bool BackVisible() { return false; }
        public override bool NextVisible() { return false; }
        public void SetStep(int percent, string text) { Bar.Value = Math.Max(0, Math.Min(100, percent)); step.Text = text; pct.Text = percent + "%"; }
        public void AddLog(string line)
        {
            if (log.TextLength > 60000) log.Text = log.Text.Substring(log.TextLength - 30000);
            log.AppendText(line + "\r\n");
        }
        public override void Enter() { if (!W.Screens) W.StartEngine(uninstall); else SetStep(37, "Copying the agent into Ubuntu"); }
    }

    class FinishPage : Page
    {
        CheckBox open; Label body, head; public Button ViewLog; bool uninstall; PictureBox stop;
        public FinishPage(Wizard w, bool isUninstall) : base(w)
        {
            uninstall = isUninstall;
            head = Head(isUninstall ? "BoomerGT has been removed." : "BoomerGT Setup is complete.");
            body = Para("", 26);
            stop = new PictureBox(); stop.Image = R.Img("stop.png"); stop.SizeMode = PictureBoxSizeMode.AutoSize;
            stop.Left = 0; stop.Top = 26; stop.Visible = false; P.Controls.Add(stop);
            open = R.Check(P, "Open my agent now", 0, 150, 380, 18, true);
            ViewLog = R.Button(P, "View log", 0, 190, 90, 23); ViewLog.Visible = false;
            ViewLog.Click += delegate { try { Process.Start("notepad.exe", Path.Combine(W.A.InstallDir, "setup.log")); } catch { } };
        }
        public override bool BackVisible() { return false; }
        public override bool CancelVisible() { return false; }
        public override string NextText() { return W.Failed ? "Close" : "Finish"; }
        // A failure must not look like the success page: a stop sign, a heading that says it is NOT installed, and plain words.
        // (In the first walkthrough a crash on the first download ended on a page the user read as success.)
        static string Plain(string err)
        {
            const string unexpected = "Unexpected problem: ";
            if (err == null) err = "";
            if (!err.StartsWith(unexpected)) return err;
            string detail = err.Substring(unexpected.Length).Split('\n')[0].Trim();
            if (detail.Length > 140) detail = detail.Substring(0, 140) + "...";
            return "Setup ran into a problem it did not expect, and stopped.\r\n\r\nTechnical detail (for a bug report): " + detail;
        }
        public override void Enter()
        {
            head.Text = uninstall ? "BoomerGT has been removed." : "BoomerGT Setup is complete.";
            head.ForeColor = Color.Black; stop.Visible = false; body.Left = 0; body.Width = 380; ViewLog.Visible = false; ViewLog.Left = 0;
            if (W.Failed)
            {
                head.Text = uninstall ? "Setup did not finish. BoomerGT was NOT fully removed." : "Setup did not finish. BoomerGT is NOT installed.";
                head.ForeColor = R.Maroon;
                stop.Visible = true;
                int x = stop.Width + 10;
                body.Left = x; body.Width = 380 - x;
                body.Text = Plain(W.ErrorText) + "\r\n\r\nNothing is lost: run Setup again and it carries on where it stopped. If it stops at the same place, click View log and include the log in a bug report on the project's GitHub page.";
                body.Height = R.TextHeight(body.Text, body.Width - 2) + 4; open.Visible = false; ViewLog.Visible = true; ViewLog.Left = x; ViewLog.Top = body.Top + body.Height + 8;
            }
            else if (uninstall)
            {
                body.Text = W.A_KeepData ? "BoomerGT was removed. Your agent's memory and notes were kept inside the Ubuntu called BoomerGT. To delete them later, open a command prompt and type: wsl --unregister BoomerGT" : "BoomerGT and the agent's Ubuntu were removed from this computer.";
                body.Height = 70; open.Visible = false;
            }
            else
            {
                body.Text = "Setup has finished installing BoomerGT on your computer. The BoomerGT icon on your desktop opens your agent. Before a game, double-click Game Mode on the desktop to free the graphics card; restarting Windows always frees it too. The Start menu folder has the settings console and the uninstaller.";
                if (W.Warnings.Count > 0) { body.Text += "\r\n\r\nNot everything you chose could be set up:\r\n- " + string.Join("\r\n- ", W.Warnings.ToArray()); ViewLog.Visible = true; }
                body.Height = R.TextHeight(body.Text, 378) + 4;
                open.Top = body.Top + body.Height + 10; ViewLog.Top = open.Top + 28; open.Visible = W.DashboardUrl != null && W.DashboardUrl.Length > 0;
            }
        }
        public override void Leave() { if (!W.Failed && !uninstall && open.Visible && open.Checked) W.OpenAgent = true; }
    }

    class RestartPage : Page
    {
        RadioButton now, later;
        public RestartPage(Wizard w) : base(w)
        {
            Head("Windows needs to restart");
            Para("Setup turned on a Windows feature that only starts working after a restart. After you sign back in, Setup will carry on by itself.", 22);
            now = R.Radio(P, "Yes, restart my computer now.", 0, 90, 380, 18, true);
            later = R.Radio(P, "No, I will restart my computer later.", 0, 114, 380, 18, false);
        }
        public override bool BackVisible() { return false; }
        public override string NextText() { return "Finish"; }
        public override void Leave() { W.RestartNow = now.Checked; }
    }

    class UWelcomePage : Page
    {
        public UWelcomePage(Wizard w) : base(w)
        {
            Head("Remove BoomerGT");
            Label a = Para("This will stop BoomerGT and remove it from your computer: its shortcuts, its sign-in task and its sealed boxes in Docker.", 22);
            Label b = Para("Your own Ubuntu, Docker Desktop, your games and the notes in Documents\\Teach my agent are not touched.", ParaEnd(a));
            Para("Click Next to continue, or Cancel to leave everything as it is.", ParaEnd(b) + 10);
        }
        public override bool BackVisible() { return false; }
    }

    class UKeepPage : Page
    {
        RadioButton keep, wipe;
        public UKeepPage(Wizard w) : base(w)
        {
            Head("Keep your agent's memory and notes?");
            Para("Everything the agent has learned about you and your projects lives inside its private Ubuntu.", 22);
            keep = R.Radio(P, "Yes, keep them (recommended)", 0, 70, 380, 18, true);
            R.Label(P, "Only the program is removed. Installing BoomerGT again later picks up where you left off. The private Ubuntu keeps using some disk space.", 18, 90, 360, 44, false);
            wipe = R.Radio(P, "No, delete everything, including memory and notes", 0, 146, 380, 18, false);
            R.Label(P, "This cannot be undone. The disk space is given back.", 18, 166, 360, 30, false);
        }
        public override string NextText() { return "Remove"; }
        public override void Leave() { W.A_KeepData = keep.Checked; }
    }

    // ------------------------------------------------------------------ the wizard window
    class Wizard : RetroForm
    {
        public Answers A = new Answers();
        public List<CheckItem> Checks = new List<CheckItem>();
        public string RecommendedModel = "qwen3.5-27b-iq4xs";
        public int RamGB; public bool Laptop;
        public bool Screens, Failed, RestartNow, OpenAgent, A_KeepData = true, UninstallMode;
        public string ErrorText = "", DashboardUrl = "", ConsoleUrl = "";
        public string RootfsOverride = "";
        List<Page> pages = new List<Page>();
        int idx = -1;
        public bool Advanced = false;      // set on the Security page; shows the shared-folder / internet page
        Button back, next, cancel; Etched line; PictureBox banner;
        Engine eng; ProgressPage progress; int lastPct;
        bool engineDone;

        public Wizard(bool uninstall) : base(560, 384)
        {
            UninstallMode = uninstall;
            Caption = uninstall ? "BoomerGT Setup - Remove" : "BoomerGT Setup";
            banner = new PictureBox(); banner.Image = R.Img("banner.bmp"); banner.Left = 8; banner.Top = 28; banner.Width = 144; banner.Height = 300; banner.SizeMode = PictureBoxSizeMode.AutoSize;
            Controls.Add(banner);
            line = new Etched(); line.Left = 8; line.Top = 338; line.Width = 544; line.Height = 2; Controls.Add(line);
            cancel = R.Button(this, "Cancel", 475, 348, 75, 23);
            next = R.Button(this, "Next >", 388, 348, 75, 23);
            back = R.Button(this, "< Back", 313, 348, 75, 23);
            back.Click += delegate { Go(-1); };
            next.Click += delegate { Go(1); };
            cancel.Click += delegate { OnCancel(); };
            if (uninstall)
            {
                pages.Add(new UWelcomePage(this)); pages.Add(new UKeepPage(this));
                progress = new ProgressPage(this, true); pages.Add(progress); pages.Add(new FinishPage(this, true));
            }
            else
            {
                pages.Add(new WelcomePage(this)); pages.Add(new CheckPage(this)); pages.Add(new DockerPage(this));
                pages.Add(new DestPage(this));
                pages.Add(new SecurityPage(this)); pages.Add(new FolderPage(this)); pages.Add(new ExtrasPage(this));
                pages.Add(new ReadyPage(this));
                progress = new ProgressPage(this, false); pages.Add(progress);
                pages.Add(new FinishPage(this, false)); pages.Add(new RestartPage(this));
            }
            AcceptButton = next;
        }

        public bool Has(string key) { foreach (CheckItem c in Checks) if (c.Key == key) return true; return false; }
        public void UI(Action a) { try { if (IsHandleCreated && !IsDisposed) BeginInvoke(a); } catch { } }

        public void LoadSampleChecks()
        {
            Checks.Clear();
            string[] s = {
                "ok|gpu|NVIDIA GeForce RTX 5080, 16 GB, driver 617.42", "info|model|nemotron-3.5-bolt-74b-128k", "info|ramgb|32",
                "ok|display|The monitor is not using the graphics card. Good.", "ok|disk|102 GB free on drive C:.", "info|freegb|102",
                "ok|ram|31 GB of memory.", "ok|wsl|Windows Subsystem for Linux is installed.",
                "warn|dockermissing|Docker Desktop is not installed. Setup can install it (you must accept its license).",
                "info|others|Your other Ubuntu(s) (Ubuntu-22.04) are left untouched." };
            foreach (string x in s) { string[] p = x.Split(new char[] { '|' }, 3); CheckItem c = new CheckItem(); c.Level = p[0]; c.Key = p[1]; c.Text = p[2]; Checks.Add(c); }
        }

        public void Start()
        {
            Go(1);
        }

        // after a restart (RunOnce) or a crash: reuse the saved answers and go straight to the progress page
        public void Resume(Dictionary<string, object> d)
        {
            Func<string, string> str = delegate (string k) { object v; return d.TryGetValue(k, out v) && v != null ? v.ToString() : ""; };
            Func<string, bool> bl = delegate (string k) { object v; return d.TryGetValue(k, out v) && v is bool && (bool)v; };
            A.Security = str("security") == "standard" ? "standard" : "protected";
            A.ShareFolder = str("shareFolder"); A.ShareWrite = bl("shareWrite"); A.NetworkInternet = bl("networkInternet");
            A.WebSearch = bl("webSearch"); A.Overnight = bl("overnight"); A.Voice = bl("voice"); A.Autostart = bl("autostart"); A.Jina = bl("jina"); A.AcceptDocker = bl("acceptDocker");
            if (str("model").Length > 0) A.Model = str("model");
            if (str("installDir").Length > 0) A.InstallDir = str("installDir");
            ShowPage(IndexOf(typeof(ProgressPage)));
        }

        public void ShowPage(int i)
        {
            if (idx >= 0 && idx < pages.Count) pages[idx].P.Visible = false;
            idx = i; Page p = pages[idx]; p.P.Visible = true; p.Enter(); UpdateButtons();
        }

        public void Go(int delta)
        {
            if (idx >= 0 && delta > 0) pages[idx].Leave();
            int i = idx + delta;
            while (i >= 0 && i < pages.Count && !pages[i].Applies()) i += delta;
            if (i < 0 || i >= pages.Count) { Finish(); return; }
            // the last two pages are reached by events, not by walking forward from Ready/Progress
            if (delta > 0 && idx >= 0 && pages[idx] is FinishPage) { Finish(); return; }
            if (delta > 0 && idx >= 0 && pages[idx] is RestartPage) { Finish(); return; }
            ShowPage(i);
        }

        public void UpdateButtons()
        {
            if (idx < 0) return;
            Page p = pages[idx];
            back.Visible = p.BackVisible() && !(idx == 0);
            next.Visible = p.NextVisible(); next.Text = p.NextText(); next.Enabled = p.Ready();
            cancel.Visible = p.CancelVisible();
        }

        void Finish()
        {
            bool uninstallDone = UninstallMode;
            if (!UninstallMode && RestartNow) { try { Process.Start("shutdown.exe", "/r /t 15 /c \"BoomerGT Setup: restarting Windows\""); } catch { } }
            if (OpenAgent && DashboardUrl.Length > 0) { try { Process.Start(DashboardUrl); } catch { } }
            Application.Exit();
        }

        protected override void OnCloseBox() { OnCancel(); }

        void OnCancel()
        {
            if (eng != null && eng.Running)
            {
                int r = RetroMsg.Show(this, "Exit Setup", "Setup is not finished. If you exit now, you can run Setup again later and it will carry on where it stopped.\r\n\r\nExit Setup?", "ask", "No", "Yes");
                if (r == 1) { eng.Kill(); Application.Exit(); }
                return;
            }
            Page p = pages[idx];
            if (p is FinishPage) { Application.Exit(); return; }
            int q = RetroMsg.Show(this, "Exit Setup", "Setup is not complete. If you exit now, " + (UninstallMode ? "BoomerGT will not be removed." : "BoomerGT will not be installed.") + "\r\n\r\nExit Setup?", "ask", "No", "Yes");
            if (q == 1) Application.Exit();
        }

        // ---- running the engine
        public void StartEngine(bool uninstall)
        {
            Failed = false; ErrorText = ""; engineDone = false; lastPct = 0;
            string answers = Path.Combine(Path.GetTempPath(), "BoomerGT-answers.json");
            try { Directory.CreateDirectory(A.InstallDir); } catch { }
            File.WriteAllText(answers, A.ToJson());
            try { File.Copy(answers, Path.Combine(A.InstallDir, "answers.json"), true); } catch { }
            eng = new Engine();
            eng.Line += delegate (string tag, string text) { UI(delegate { OnEngineLine(tag, text); }); };
            eng.Exited += delegate (int code) { UI(delegate { OnEngineExit(code); }); };
            try { eng.Start(uninstall ? "uninstall" : "install", answers, A.InstallDir, uninstall && A_KeepData, RootfsOverride); }
            catch (Exception ex) { Failed = true; ErrorText = "Setup could not start PowerShell: " + ex.Message; ShowPage(IndexOf(typeof(FinishPage))); }
        }

        int IndexOf(Type t) { for (int i = 0; i < pages.Count; i++) if (pages[i].GetType() == t) return i; return -1; }

        void OnEngineLine(string tag, string text)
        {
            if (tag == "PROGRESS")
            {
                int bar = text.IndexOf('|'); int pctv; string msg = text;
                if (bar > 0 && int.TryParse(text.Substring(0, bar), out pctv)) { lastPct = pctv; msg = text.Substring(bar + 1); progress.SetStep(pctv, msg); }
                progress.AddLog(msg);
            }
            else if (tag == "LOG") progress.AddLog(text);
            else if (tag == "ERROR") { Failed = true; ErrorText = text; progress.AddLog("ERROR: " + text); }
            else if (tag == "RESULT")
            {
                if (text.StartsWith("dashboard=")) DashboardUrl = text.Substring(10);
                if (text.StartsWith("console=")) ConsoleUrl = text.Substring(8);
                if (text.StartsWith("warn=")) Warnings.Add(text.Substring(5));
            }
            else if (tag == "NEEDRESTART") { RestartNeeded = true; }
        }
        public bool RestartNeeded;
        public List<string> Warnings = new List<string>();   // optional extras that could not be set up (shown on the finish page)

        void OnEngineExit(int code)
        {
            if (engineDone) return; engineDone = true;
            if (RestartNeeded || code == 3010)
            {
                try
                {
                    Directory.CreateDirectory(A.InstallDir);
                    RegistryKey k = Registry.CurrentUser.OpenSubKey("Software\\Microsoft\\Windows\\CurrentVersion\\RunOnce", true);
                    if (k != null) { k.SetValue("BoomerGT", "\"" + Application.ExecutablePath + "\" /resume /dir \"" + A.InstallDir.TrimEnd('\\') + "\""); k.Close(); }
                }
                catch { }
                ShowPage(IndexOf(typeof(RestartPage)));
                return;
            }
            if (code != 0 && !Failed) { Failed = true; ErrorText = "The setup engine stopped unexpectedly (exit code " + code + ")."; }
            if (!Failed && !UninstallMode)
            {
                try { File.Copy(Application.ExecutablePath, Path.Combine(A.InstallDir, "Uninstall.exe"), true); } catch { }
                progress.SetStep(100, "Done");
            }
            ShowPage(IndexOf(typeof(FinishPage)));
        }

        // ---- screenshots of every page (for checking the look without clicking through)
        public void SaveScreens(string dir)
        {
            Screens = true; Directory.CreateDirectory(dir);
            Show(); Application.DoEvents();
            for (int i = 0; i < pages.Count; i++)
            {
                if (!pages[i].Applies() && !(pages[i] is DockerPage)) { }
                ShowPage(i); Application.DoEvents(); System.Threading.Thread.Sleep(150); Application.DoEvents();
                Bitmap bmp = new Bitmap(Width, Height, System.Drawing.Imaging.PixelFormat.Format24bppRgb);
                DrawToBitmap(bmp, new Rectangle(0, 0, Width, Height));
                bmp.Save(Path.Combine(dir, string.Format("page-{0:00}-{1}.png", i + 1, pages[i].GetType().Name)));
                bmp.Dispose();
            }
            // the failure ending, with the real error from the first walkthrough
            Failed = true;
            ErrorText = "Unexpected problem: Cannot convert value \"17592186044416\" to type \"System.Int32\". Error: \"Value was either too large or too small for an Int32.\"";
            int f = IndexOf(typeof(FinishPage));
            if (f >= 0)
            {
                ShowPage(f); Application.DoEvents(); System.Threading.Thread.Sleep(150); Application.DoEvents();
                Bitmap fb = new Bitmap(Width, Height, System.Drawing.Imaging.PixelFormat.Format24bppRgb);
                DrawToBitmap(fb, new Rectangle(0, 0, Width, Height));
                fb.Save(Path.Combine(dir, "page-99-FinishPage-failed.png")); fb.Dispose();
            }
            Failed = false; ErrorText = "";
            // the success ending when an optional extra could not be set up
            Warnings.Add("Web search could not be turned on. Open the console and use its Health page to see why and try again.");
            if (f >= 0)
            {
                ShowPage(f); Application.DoEvents(); System.Threading.Thread.Sleep(150); Application.DoEvents();
                Bitmap wb = new Bitmap(Width, Height, System.Drawing.Imaging.PixelFormat.Format24bppRgb);
                DrawToBitmap(wb, new Rectangle(0, 0, Width, Height));
                wb.Save(Path.Combine(dir, "page-98-FinishPage-warning.png")); wb.Dispose();
            }
            Warnings.Clear();
        }
    }
}
