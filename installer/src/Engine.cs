// Engine.cs - unpacks the files carried inside the .exe and runs engine.ps1, turning its output lines into events.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text;
using System.Windows.Forms;

namespace BoomerGT
{
    class CheckItem
    {
        public string Level, Key, Text;
    }

    class Answers
    {
        public string Security = "protected";
        public string ShareFolder = "";
        public bool ShareWrite = false;
        public bool NetworkInternet = false;
        public bool WebSearch = true;
        public bool Overnight = false;
        public bool Voice = false;
        public bool Autostart = false;
        public bool Jina = false;
        public bool AcceptDocker = false;
        public string Model = "qwen3.5-27b-iq4xs";
        public string InstallDir = Path.Combine(Environment.GetEnvironmentVariable("LOCALAPPDATA") ?? "", "BoomerGT");

        static string J(string s) { return "\"" + (s ?? "").Replace("\\", "\\\\").Replace("\"", "\\\"") + "\""; }
        static string B(bool b) { return b ? "true" : "false"; }
        public string ToJson()
        {
            return "{\"security\":" + J(Security) + ",\"shareFolder\":" + J(ShareFolder) + ",\"shareWrite\":" + B(ShareWrite)
                + ",\"networkInternet\":" + B(NetworkInternet) + ",\"webSearch\":" + B(WebSearch) + ",\"overnight\":" + B(Overnight)
                + ",\"voice\":" + B(Voice) + ",\"autostart\":" + B(Autostart) + ",\"jina\":" + B(Jina) + ",\"acceptDocker\":" + B(AcceptDocker)
                + ",\"model\":" + J(Model) + ",\"installDir\":" + J(InstallDir) + "}";
        }
    }

    static class Payload
    {
        public static string Dir = Path.Combine(Path.GetTempPath(), "BoomerGT-payload");
        public static void Extract()
        {
            Directory.CreateDirectory(Dir);
            string[] names = { "engine.ps1", "agent.tar.gz", "Autostart.ps1", "BoomerGT.ico" };
            foreach (string n in names)
            {
                Stream s = R.Res(n);
                if (s == null) continue;
                using (s)
                using (FileStream f = new FileStream(Path.Combine(Dir, n), FileMode.Create, FileAccess.Write))
                {
                    byte[] buf = new byte[1 << 16]; int k;
                    while ((k = s.Read(buf, 0, buf.Length)) > 0) f.Write(buf, 0, k);
                }
            }
        }
    }

    class Engine
    {
        public event Action<string, string> Line;     // tag, text (raised on a worker thread)
        public event Action<int> Exited;              // exit code
        Process p;
        public int ExitCode = -1;
        public bool Running { get { return p != null && !p.HasExited; } }

        public void Start(string mode, string answersFile, string installDir, bool keepData, string rootfs)
        {
            string script = Path.Combine(Payload.Dir, "engine.ps1");
            StringBuilder a = new StringBuilder();
            a.Append("-NoProfile -ExecutionPolicy Bypass -File \"" + script + "\" -Mode " + mode);
            a.Append(" -PayloadDir \"" + Payload.Dir + "\"");
            if (!string.IsNullOrEmpty(installDir)) a.Append(" -InstallDir \"" + installDir + "\"");
            if (!string.IsNullOrEmpty(answersFile)) a.Append(" -AnswersFile \"" + answersFile + "\"");
            if (keepData) a.Append(" -KeepData");
            if (!string.IsNullOrEmpty(rootfs)) a.Append(" -RootfsPath \"" + rootfs + "\"");
            ProcessStartInfo psi = new ProcessStartInfo("powershell.exe", a.ToString());
            psi.UseShellExecute = false; psi.CreateNoWindow = true;
            psi.RedirectStandardOutput = true; psi.RedirectStandardError = true;
            psi.StandardOutputEncoding = Encoding.UTF8; psi.StandardErrorEncoding = Encoding.UTF8;
            p = new Process(); p.StartInfo = psi; p.EnableRaisingEvents = true;
            p.OutputDataReceived += delegate (object s, DataReceivedEventArgs e)
            {
                if (e.Data == null) return;
                string line = e.Data; int sp = line.IndexOf(' ');
                string tag = sp > 0 ? line.Substring(0, sp) : line, rest = sp > 0 ? line.Substring(sp + 1) : "";
                if (Line != null) Line(tag, rest);
            };
            p.ErrorDataReceived += delegate (object s, DataReceivedEventArgs e) { if (e.Data != null && Line != null) Line("LOG", "(stderr) " + e.Data); };
            p.Exited += delegate
            {
                p.WaitForExit();
                ExitCode = p.ExitCode;
                if (Exited != null) Exited(ExitCode);
            };
            p.Start();
            p.BeginOutputReadLine(); p.BeginErrorReadLine();
        }

        public void Kill()
        {
            try
            {
                if (p != null && !p.HasExited)
                {
                    Process k = Process.Start(new ProcessStartInfo("taskkill", "/T /F /PID " + p.Id) { CreateNoWindow = true, UseShellExecute = false });
                    k.WaitForExit(5000);
                }
            }
            catch { }
        }
    }
}
