using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Net;
using System.Security.Cryptography;
using System.Threading;
using System.Windows.Forms;

internal sealed class FishHomeTray : ApplicationContext
{
    private const string Address = "http://127.0.0.1:6196";
    private const string Instance = @"Local\FishHome.DesktopManager";
    private readonly NotifyIcon icon;
    private readonly Icon appIcon;
    private readonly System.Windows.Forms.Timer timer;
    private readonly EventWaitHandle openEvent;
    private readonly EventWaitHandle startEvent;
    private readonly string token;
    private readonly Process backend;
    private bool openPending;
    private bool startPending;
    private bool ready;
    private int ticks;

    [STAThread]
    private static void Main(string[] args)
    {
        bool first;
        using (var mutex = new Mutex(true, Instance, out first))
        using (var open = new EventWaitHandle(false, EventResetMode.AutoReset, Instance + ".open"))
        using (var start = new EventWaitHandle(false, EventResetMode.AutoReset, Instance + ".start"))
        {
            bool show = !args.Contains("--background");
            bool all = args.Contains("--start-all");
            if (!first)
            {
                if (show) open.Set();
                if (all) start.Set();
                return;
            }
            Application.EnableVisualStyles();
            try { Application.Run(new FishHomeTray(open, start, show, all)); }
            catch (Exception error) { MessageBox.Show(error.Message, "FishHome 启动失败"); }
        }
    }

    private FishHomeTray(EventWaitHandle open, EventWaitHandle start, bool show, bool all)
    {
        openEvent = open;
        startEvent = start;
        openPending = show;
        startPending = all;
        var random = new byte[32];
        using (var rng = RandomNumberGenerator.Create()) rng.GetBytes(random);
        token = Convert.ToBase64String(random).TrimEnd('=').Replace('+', '-').Replace('/', '_');
        var dev = new DirectoryInfo(AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\'));
        var repo = Path.Combine(dev.Parent.FullName, "repo");
        var python = Path.Combine(dev.FullName, @"astrbot-4.29.4\.venv\Scripts\pythonw.exe");
        var state = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "FishHome", "manager");
        if (!File.Exists(python) || !Directory.Exists(repo)) throw new InvalidOperationException("找不到 FishHome 开发环境。");
        Directory.CreateDirectory(state);
        var launcher = new ProcessStartInfo(python, "-B -m deploy.desktop_manager.manager --dev " + Quote(dev.FullName) + " --state " + Quote(state));
        launcher.WorkingDirectory = repo;
        launcher.UseShellExecute = false;
        launcher.CreateNoWindow = true;
        launcher.RedirectStandardError = true;
        launcher.EnvironmentVariables["FISH_MANAGER_TOKEN"] = token;
        launcher.EnvironmentVariables["PYTHONUTF8"] = "1";
        backend = Process.Start(launcher);
        backend.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs args) {
            if (!String.IsNullOrEmpty(args.Data)) File.AppendAllText(Path.Combine(state, "manager.log"), args.Data + Environment.NewLine);
        };
        backend.BeginErrorReadLine();
        var menu = new ContextMenuStrip();
        menu.Items.Add("打开 WebUI", null, delegate { OpenPage(); });
        menu.Items.Add("启动全部服务", null, delegate { startPending = true; });
        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add("退出控制台", null, delegate { ExitThread(); });
        appIcon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        icon = new NotifyIcon { Icon = appIcon ?? SystemIcons.Application, Text = "FishHome 正在启动", Visible = true, ContextMenuStrip = menu };
        icon.DoubleClick += delegate { OpenPage(); };
        timer = new System.Windows.Forms.Timer { Interval = 600 };
        timer.Tick += Tick;
        timer.Start();
    }

    private static string Quote(string value) { return "\"" + value + "\""; }

    private void Request(string path, string method, int timeout)
    {
        var request = (HttpWebRequest)WebRequest.Create(Address + path);
        request.Proxy = null;
        request.Timeout = timeout;
        request.Method = method;
        request.Headers[HttpRequestHeader.Authorization] = "Bearer " + token;
        if (method == "POST") request.ContentLength = 0;
        using (request.GetResponse()) { }
    }

    private void Tick(object sender, EventArgs args)
    {
        if (openEvent.WaitOne(0)) openPending = true;
        if (startEvent.WaitOne(0)) startPending = true;
        if (backend.HasExited)
        {
            ready = false;
            icon.Text = "FishHome 控制台未运行";
            timer.Stop();
            icon.ShowBalloonTip(5000, "FishHome", "控制台启动失败，请检查本机 6196 端口和开发环境。", ToolTipIcon.Error);
            return;
        }
        if (!ready)
        {
            try { Request("/api/ping", "GET", 450); ready = true; icon.Text = "FishHome 控制台"; }
            catch (WebException) { if (++ticks == 25) icon.Text = "FishHome 等待本机控制台"; }
        }
        if (ready && openPending) { openPending = false; OpenPage(); }
        if (ready && startPending)
        {
            startPending = false;
            ThreadPool.QueueUserWorkItem(delegate {
                try { Request("/api/services/all/start", "POST", 30000); }
                catch (WebException) { }
            });
        }
    }

    private void OpenPage()
    {
        if (!ready) { openPending = true; return; }
        Process.Start(new ProcessStartInfo(Address + "/#" + token) { UseShellExecute = true });
    }

    protected override void ExitThreadCore()
    {
        timer.Stop();
        timer.Dispose();
        icon.Visible = false;
        icon.Dispose();
        if (appIcon != null) appIcon.Dispose();
        if (!backend.HasExited)
        {
            try { Request("/api/exit", "POST", 5000); }
            catch (WebException) { }
            if (!backend.WaitForExit(2500)) backend.Kill();
        }
        backend.Dispose();
        base.ExitThreadCore();
    }
}
