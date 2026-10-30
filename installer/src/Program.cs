// Program.cs - entry point for BoomerGT-setup.exe
//   (no arguments)   install wizard          /uninstall   remove wizard          /resume   carry on after a Windows restart
//   /screens <dir>   save a picture of every wizard page (no changes are made)     /rootfs <file>   use this Ubuntu image instead of downloading
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;

namespace BoomerGT
{
    static class Program
    {
        [STAThread]
        static int Main(string[] args)
        {
            Application.SetCompatibleTextRenderingDefault(false);   // GDI text, like 1997 (and no EnableVisualStyles: classic buttons)
            bool uninstall = false, resume = false, relaunched = false; string screens = "", rootfs = "", selftest = "", dirArg = "";
            for (int i = 0; i < args.Length; i++)
            {
                string a = args[i].ToLowerInvariant();
                if (a == "/uninstall") uninstall = true;
                else if (a == "/resume") resume = true;
                else if (a == "/relaunched") relaunched = true;
                else if (a == "/screens" && i + 1 < args.Length) screens = args[++i];
                else if (a == "/rootfs" && i + 1 < args.Length) rootfs = args[++i];
                else if (a == "/selftest" && i + 1 < args.Length) selftest = args[++i];
                else if (a == "/dir" && i + 1 < args.Length) dirArg = args[++i];
            }
            string installDir = FindInstallDir(dirArg);
            // the uninstaller lives in the folder it is about to delete, so run a copy from the temp folder
            if (uninstall && !relaunched && Application.ExecutablePath.StartsWith(installDir, StringComparison.OrdinalIgnoreCase))
            {
                try
                {
                    string tmp = Path.Combine(Path.GetTempPath(), "BoomerGT-uninstall.exe");
                    File.Copy(Application.ExecutablePath, tmp, true);
                    Process.Start(tmp, "/uninstall /relaunched /dir \"" + installDir.TrimEnd('\\') + "\"");
                    return 0;
                }
                catch { }
            }
            bool created;
            Mutex m = new Mutex(true, "BoomerGT-setup-singleton", out created);
            if (!created && screens.Length == 0) { MessageBox.Show("Setup is already running.", "BoomerGT Setup"); return 1; }
            try { Payload.Extract(); }
            catch (Exception ex) { MessageBox.Show("Setup could not unpack its files: " + ex.Message, "BoomerGT Setup"); return 1; }

            if (selftest.Length > 0)
            {
                // runs the PC check through the same Engine class the wizard uses and writes what it received (no window, no changes)
                StringBuilder sb = new StringBuilder();
                ManualResetEvent done = new ManualResetEvent(false);
                Engine e = new Engine();
                e.Line += delegate (string tag, string text) { lock (sb) { sb.AppendLine(tag + " " + text); } };
                e.Exited += delegate (int code) { lock (sb) { sb.AppendLine("EXIT " + code); } done.Set(); };
                e.Start("check", "", installDir, false, "");
                done.WaitOne(120000);
                File.WriteAllText(selftest, sb.ToString());
                return 0;
            }
            Backdrop bd = new Backdrop();
            Wizard w = new Wizard(uninstall);
            w.RootfsOverride = rootfs;
            if (uninstall || resume) w.A.InstallDir = installDir;
            w.Owner = bd;
            if (screens.Length > 0)
            {
                w.SaveScreens(screens);
                return 0;
            }
            bd.Show();
            w.Show();
            if (resume)
            {
                try
                {
                    string f = Path.Combine(installDir, "answers.json");
                    Dictionary<string, object> d = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(f));
                    w.Resume(d);
                }
                catch { w.Start(); }
            }
            else w.Start();
            Application.Run(w);
            return 0;
        }

        // The folder of an EXISTING install, which may not be the default one (the user can pick any folder):
        // 1. /dir given (the uninstaller relaunched from the temp folder, or a resume after a restart)
        // 2. this program is Uninstall.exe inside an install folder
        // 3. the folder Windows' uninstall entry records
        // 4. the default %LOCALAPPDATA%\BoomerGT
        // Uninstalling with the wrong folder used to leave a custom-folder install behind.
        static string FindInstallDir(string dirArg)
        {
            if (dirArg.Length > 0) return dirArg;
            try
            {
                string own = Path.GetDirectoryName(Application.ExecutablePath);
                if (File.Exists(Path.Combine(own, "setup.log")) || File.Exists(Path.Combine(own, "answers.json"))) return own;
            }
            catch { }
            try
            {
                using (Microsoft.Win32.RegistryKey k = Microsoft.Win32.Registry.CurrentUser.OpenSubKey("Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\BoomerGT"))
                {
                    string loc = k == null ? null : k.GetValue("InstallLocation") as string;
                    if (!string.IsNullOrEmpty(loc) && Directory.Exists(loc)) return loc;
                }
            }
            catch { }
            return Path.Combine(Environment.GetEnvironmentVariable("LOCALAPPDATA") ?? "", "BoomerGT");
        }
    }
}
