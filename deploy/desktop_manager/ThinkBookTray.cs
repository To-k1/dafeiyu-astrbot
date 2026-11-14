using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Forms;

internal sealed class ThinkBookTray : ApplicationContext
{
    private const string Instance = @"Local\FishHome.ThinkBookTray";
    private const string PagePrefix = "http://127.0.0.1:16196/#";
    private readonly NotifyIcon icon;
    private readonly Icon badge;
    private readonly System.Windows.Forms.Timer timer;
    private readonly EventWaitHandle openEvent;
    private readonly EventWaitHandle closeEvent;
    private Process backend;
    private volatile string page;
    private volatile string failure;
    private bool openPending;
    private bool reconnectPending;

    [DllImport("user32.dll")]
    private static extern bool DestroyIcon(IntPtr handle);

    [STAThread]
    private static void Main(string[] args)
    {
        bool first;
        bool show = Array.IndexOf(args, "--background") < 0;
        using (var mutex = new Mutex(true, Instance, out first))
        using (var open = new EventWaitHandle(false, EventResetMode.AutoReset, Instance + ".open"))
        {
            if (!first)
            {
                if (show) open.Set();
                return;
            }
            Application.EnableVisualStyles();
            Application.Run(new ThinkBookTray(open, show));
        }
    }

    private ThinkBookTray(EventWaitHandle open, bool show)
    {
        openEvent = open;
        closeEvent = new EventWaitHandle(false, EventResetMode.ManualReset,
            Instance + ".close." + Process.GetCurrentProcess().Id);
        badge = CreateBadge();
        var menu = new ContextMenuStrip();
        menu.Items.Add("打开 ThinkBook 控制台", null, delegate { OpenPage(); });
        menu.Items.Add("重新连接 ThinkBook", null, delegate { Reconnect(); });
        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add("退出 ThinkBook 托盘", null, delegate { ExitThread(); });
        icon = new NotifyIcon { Icon = badge, Text = "FishHome · ThinkBook", Visible = true, ContextMenuStrip = menu };
        icon.DoubleClick += delegate { OpenPage(); };
        timer = new System.Windows.Forms.Timer { Interval = 350 };
        timer.Tick += Tick;
        timer.Start();
        if (show) OpenPage();
    }

    private static Icon CreateBadge()
    {
        using (var bitmap = new Bitmap(32, 32))
        using (var graphics = Graphics.FromImage(bitmap))
        using (var font = new Font("Segoe UI", 11, FontStyle.Bold))
        {
            graphics.Clear(Color.FromArgb(94, 45, 164));
            TextRenderer.DrawText(graphics, "TB", font, new Rectangle(0, 0, 32, 32), Color.White,
                TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter);
            var handle = bitmap.GetHicon();
            try { using (var source = Icon.FromHandle(handle)) return (Icon)source.Clone(); }
            finally { DestroyIcon(handle); }
        }
    }

    private void OpenPage()
    {
        openPending = true;
        if (backend == null) StartRelay();
    }

    private void Reconnect()
    {
        if (backend != null && !backend.HasExited)
        {
            reconnectPending = true;
            closeEvent.Set();
            return;
        }
        OpenPage();
    }

    private void StartRelay()
    {
        page = null;
        failure = null;
        closeEvent.Reset();
        var dev = new DirectoryInfo(AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\'));
        var repo = Path.Combine(dev.Parent.FullName, "repo");
        var python = Path.Combine(dev.FullName, @"astrbot-4.29.4\.venv\Scripts\python.exe");
        try
        {
            var start = new ProcessStartInfo(python, "-B -m deploy.desktop_manager.thinkbook --tray-parent " + Process.GetCurrentProcess().Id)
            {
                WorkingDirectory = repo, UseShellExecute = false, CreateNoWindow = true,
                RedirectStandardOutput = true, RedirectStandardError = true,
                StandardOutputEncoding = System.Text.Encoding.UTF8, StandardErrorEncoding = System.Text.Encoding.UTF8
            };
            start.EnvironmentVariables["PYTHONUTF8"] = "1";
            backend = Process.Start(start);
            backend.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e)
            {
                if (e.Data != null && e.Data.StartsWith(PagePrefix, StringComparison.Ordinal) &&
                    System.Text.RegularExpressions.Regex.IsMatch(e.Data.Substring(PagePrefix.Length), @"\A[A-Za-z0-9_-]{43}\z"))
                    page = e.Data;
            };
            backend.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e)
            {
                if (!String.IsNullOrWhiteSpace(e.Data)) failure = e.Data;
            };
            backend.BeginOutputReadLine();
            backend.BeginErrorReadLine();
            icon.Text = "FishHome · 正在连接 ThinkBook";
        }
        catch (Exception exc)
        {
            openPending = false;
            icon.ShowBalloonTip(5000, "ThinkBook 控制台", exc.Message, ToolTipIcon.Error);
        }
    }

    private void Tick(object sender, EventArgs args)
    {
        if (backend != null && backend.HasExited)
        {
            backend.WaitForExit();
            backend.Dispose();
            backend = null;
            page = null;
            if (reconnectPending)
            {
                reconnectPending = false;
                OpenPage();
                return;
            }
            openPending = false;
            icon.Text = "FishHome · ThinkBook 未连接";
            icon.ShowBalloonTip(5000, "ThinkBook 控制台",
                failure ?? "远程连接已结束，可从托盘重新连接。", ToolTipIcon.Warning);
        }
        if (openEvent.WaitOne(0)) OpenPage();
        var address = page;
        if (address == null) return;
        icon.Text = "FishHome · ThinkBook 已连接";
        if (openPending)
        {
            openPending = false;
            try { Process.Start(new ProcessStartInfo(address) { UseShellExecute = true }); }
            catch (Exception exc) { icon.ShowBalloonTip(5000, "ThinkBook 控制台", exc.Message, ToolTipIcon.Error); }
        }
    }

    protected override void ExitThreadCore()
    {
        timer.Dispose();
        icon.Visible = false;
        icon.Dispose();
        badge.Dispose();
        if (backend != null)
        {
            closeEvent.Set();
            backend.Dispose();
        }
        closeEvent.Dispose();
        base.ExitThreadCore();
    }
}
