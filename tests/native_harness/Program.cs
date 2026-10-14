using System.Diagnostics;
using System.IO;
using System.Net.Http;
using System.Net.WebSockets;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Interop;
using CloakWorkbench;

internal static class Program
{
    private static readonly List<object> Checks = new();
    private static int _exit = 1;

    [STAThread]
    private static int Main(string[] args)
    {
        if (args.Length > 0 && args[0] == "--switch") return SwitchRegression.Run(args.Skip(1).ToArray());
        var application = new Application();
        var host = new NativeBrowserHost();
        var window = new Window { Width = 720, Height = 500, Left = 40, Top = 40,
            Title = "Owned native embedding regression", ShowActivated = false, Content = host };
        window.Loaded += async (_, _) =>
        {
            string temporary = Path.Combine(Path.GetTempPath(), "cloak-native-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(temporary);
            Process? browser = null;
            object? insets = null;
            try
            {
                string binary = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
                    ".cloakbrowser", "chromium-146.0.7680.177.5", "chrome.exe");
                string fixture = Path.Combine(temporary, "fixture.html");
                File.WriteAllText(fixture, "<!doctype html><title>Owned native dialog fixture</title><input id='upload' type='file'>");
                var start = new ProcessStartInfo(binary) { UseShellExecute = false };
                foreach (string argument in new[] { "--user-data-dir=" + temporary, "--no-first-run",
                    "--no-default-browser-check", "--app=" + new Uri(fixture).AbsoluteUri,
                    "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0",
                    "--window-size=640,480", "--fingerprint=87541" })
                    start.ArgumentList.Add(argument);
                browser = Process.Start(start) ?? throw new InvalidOperationException("No owned browser process");
                IntPtr hwnd = IntPtr.Zero;
                for (int retry = 0; retry < 100 && hwnd == IntPtr.Zero; retry++)
                {
                    await Task.Delay(100);
                    hwnd = NativeBrowserHost.FindBrowserWindow(browser.Id);
                }
                Verify("Owned browser window started", hwnd != IntPtr.Zero);
                await Task.Delay(250);
                GetWindowRect(hwnd, out var original);
                long originalStyle = GetWindowLongPtr(hwnd, -16).ToInt64();
                long originalExStyle = GetWindowLongPtr(hwnd, -20).ToInt64();
                var originalParent = GetParent(hwnd);
                host.Attach(hwnd);
                for (int retry = 0; retry < 10 && !host.ContentInsets.Measured; retry++)
                {
                    await Task.Delay(100);
                    host.RefreshBounds();
                }
                Verify("Real native Chromium content insets measured", host.ContentInsets.Measured);
                insets = host.ContentInsets;
                await Task.Delay(250);
                var frame = AutomationElement.FromHandle(hwnd);
                var content = frame.FindFirst(TreeScope.Descendants,
                    new PropertyCondition(AutomationElement.ClassNameProperty, "MultiContentsView"));
                Verify("Native content view available", content is not null);
                GetClientRect(GetParent(hwnd), out var hostRect);
                MapWindowPoints(GetParent(hwnd), IntPtr.Zero, ref hostRect, 2);
                var bounds = content!.Current.BoundingRectangle;
                Verify("All four webpage edges fill clipped native host", Math.Abs(hostRect.Left - bounds.Left) <= 2
                    && Math.Abs(hostRect.Top - bounds.Top) <= 2 && Math.Abs(hostRect.Right - bounds.Right) <= 2
                    && Math.Abs(hostRect.Bottom - bounds.Bottom) <= 2);
                var manager = host.OwnedWindows!;
                Verify("Primary browser cannot be closed as popup", !manager.RequestClose(hwnd.ToInt64()));
                var shell = new WindowInteropHelper(window).Handle;
                Verify("Unrelated desktop HWND cannot be closed as popup", !manager.RequestClose(shell.ToInt64()));
                Verify("Unrelated desktop HWND cannot be activated as popup", !manager.Activate(shell.ToInt64(), shell));
                using var dialogSocket = await OpenNativeFileDialog(temporary);
                BrowserWindowManager.OwnedWindow? dialog = null;
                for (int retry = 0; retry < 30 && dialog is null; retry++)
                {
                    await Task.Delay(100);
                    dialog = manager.Snapshot().FirstOrDefault(item => item.Kind == "dialog");
                }
                Verify("Embedded browser's actual native file chooser remains visible", dialog is not null);
                Verify("Native file chooser remains enabled", dialog!.Enabled);
                Verify("Explicit popup close cancels only the owned chooser", manager.RequestClose(dialog.Handle));
                for (int retry = 0; retry < 30 && manager.Snapshot().Any(item => item.Handle == dialog.Handle); retry++)
                    await Task.Delay(100);
                Verify("File chooser cancellation leaves primary browser alive", NativeBrowserHost.IsWindowAlive(hwnd)
                    && !manager.Snapshot().Any(item => item.Handle == dialog.Handle));
                host.Detach();
                await Task.Delay(150);
                GetWindowRect(hwnd, out var restored);
                Verify("Detach restores exact standalone geometry", restored.Equals(original));
                Verify("Detach restores original styles", GetWindowLongPtr(hwnd, -16).ToInt64() == originalStyle
                    && GetWindowLongPtr(hwnd, -20).ToInt64() == originalExStyle);
                Verify("Detach restores original parent", GetParent(hwnd) == originalParent);
                Verify("Detached standalone stays visible", IsWindowVisible(hwnd));
                PostMessage(hwnd, 0x0010, IntPtr.Zero, IntPtr.Zero);
                await Task.Delay(250);
                _exit = 0;
            }
            catch (Exception error)
            {
                Checks.Add(new { check = "exception", ok = false, error = error.ToString() });
            }
            finally
            {
                try { host.Detach(); } catch { }
                if (browser is not null)
                {
                    if (!browser.HasExited)
                    {
                        var hwnd = NativeBrowserHost.FindBrowserWindow(browser.Id);
                        if (hwnd != IntPtr.Zero) PostMessage(hwnd, 0x0010, IntPtr.Zero, IntPtr.Zero);
                        await Task.Delay(500);
                        if (!browser.HasExited) browser.Kill(entireProcessTree: true);
                    }
                    browser.Dispose();
                }
                try { Directory.Delete(temporary, recursive: true); } catch { }
                string json = JsonSerializer.Serialize(new { ok = _exit == 0, checks = Checks,
                    contentInsets = insets, scope = "temporary isolated browser and WPF host only",
                    notVerified = new[] { "Physical keyboard", "Native IME candidates", "Other monitor DPI" } },
                    new JsonSerializerOptions { WriteIndented = true });
                if (args.Length > 0) File.WriteAllText(args[0], json, new System.Text.UTF8Encoding(false));
                Console.WriteLine(json);
                window.Close();
            }
        };
        application.Run(window);
        return _exit;
    }

    private static void Verify(string check, bool ok)
    {
        Checks.Add(new { check, ok });
        if (!ok) throw new InvalidOperationException(check);
    }

    private static async Task<ClientWebSocket> OpenNativeFileDialog(string temporary)
    {
        int port = int.Parse(File.ReadAllLines(Path.Combine(temporary, "DevToolsActivePort"))[0]);
        using var http = new HttpClient(new HttpClientHandler { UseProxy = false });
        using var targets = JsonDocument.Parse(await http.GetStringAsync($"http://127.0.0.1:{port}/json/list"));
        var target = targets.RootElement.EnumerateArray().First(item => item.GetProperty("type").GetString() == "page");
        var socket = new ClientWebSocket();
        socket.Options.Proxy = null;
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(8));
        await socket.ConnectAsync(new Uri(target.GetProperty("webSocketDebuggerUrl").GetString()!), timeout.Token);
        byte[] command = Encoding.UTF8.GetBytes(JsonSerializer.Serialize(new { id = 1, method = "Runtime.evaluate",
            @params = new { expression = "document.querySelector('#upload').click()", userGesture = true } }));
        await socket.SendAsync(command, WebSocketMessageType.Text, true, timeout.Token);
        // The chooser intentionally blocks renderer script completion until
        // cancellation. Do not await the CDP reply before observing native HWNDs.
        await Task.Delay(350);
        byte[] reply = new byte[65536];
        using var readTimeout = new CancellationTokenSource(TimeSpan.FromSeconds(2));
        try
        {
            var message = await socket.ReceiveAsync(reply, readTimeout.Token);
            using var response = JsonDocument.Parse(Encoding.UTF8.GetString(reply, 0, message.Count));
            if (response.RootElement.TryGetProperty("error", out var error))
                throw new InvalidOperationException(error.ToString());
        }
        catch (OperationCanceledException) { }
        return socket;
    }

    [StructLayout(LayoutKind.Sequential)] private struct NativeRect { public int Left, Top, Right, Bottom; }
    [DllImport("user32.dll")] private static extern bool GetWindowRect(IntPtr hwnd, out NativeRect rect);
    [DllImport("user32.dll")] private static extern bool GetClientRect(IntPtr hwnd, out NativeRect rect);
    [DllImport("user32.dll")] private static extern int MapWindowPoints(IntPtr from, IntPtr to, ref NativeRect rect, int count);
    [DllImport("user32.dll")] private static extern IntPtr GetWindowLongPtr(IntPtr hwnd, int index);
    [DllImport("user32.dll")] private static extern IntPtr GetParent(IntPtr hwnd);
    [DllImport("user32.dll")] private static extern bool IsWindowVisible(IntPtr hwnd);
    [DllImport("user32.dll")] private static extern bool PostMessage(IntPtr hwnd, uint message, IntPtr wParam, IntPtr lParam);
}
