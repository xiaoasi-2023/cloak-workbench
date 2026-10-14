using System.Diagnostics;
using System.IO;
using System.Net.Http;
using System.Net.WebSockets;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using CloakWorkbench;

internal static class SwitchRegression
{
    private static int _exit = 1;
    private static readonly List<object> Observations = new();

    public static int Run(string[] args)
    {
        var app = new Application();
        var grid = new Grid();
        var hosts = new[] { new NativeBrowserHost(), new NativeBrowserHost() };
        foreach (var host in hosts) grid.Children.Add(host);
        var window = new Window { Title = "Owned tab compositor regression", Width = 720, Height = 500,
            Left = 40, Top = 40, ShowActivated = false, Topmost = true, Content = grid };
        var browsers = new List<Process>();
        var directory = Path.Combine(Path.GetTempPath(), "cloak-switch-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(directory);
        window.Loaded += async (_, _) =>
        {
            try
            {
                for (int index = 0; index < 2; index++)
                {
                    var temporary = Path.Combine(directory, index.ToString());
                    Directory.CreateDirectory(temporary);
                    string fixture = Path.Combine(temporary, "fixture.html");
                    File.WriteAllText(fixture, $"<!doctype html><title>Owned switch {index}</title><style>html,body{{margin:0;width:100%;height:100%;background:{(index == 0 ? "#e04030" : "#2050d0")}}}</style><h1>Owned switch {index}</h1><input value='persistent draft {index}'><script>localStorage.setItem('ownedMarker','marker-{index}');window.navigationIdentity=crypto.randomUUID();</script>");
                    string binary = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
                        ".cloakbrowser", "chromium-146.0.7680.177.5", "chrome.exe");
                    var start = new ProcessStartInfo(binary) { UseShellExecute = false };
                    foreach (string argument in new[] { "--user-data-dir=" + temporary, "--no-first-run",
                        "--no-default-browser-check", "--app=" + new Uri(fixture).AbsoluteUri,
                        "--window-size=640,480", "--fingerprint=" + (87541 + index),
                        "--remote-debugging-port=0", "--remote-debugging-address=127.0.0.1" })
                        start.ArgumentList.Add(argument);
                    var browser = Process.Start(start)!;
                    browsers.Add(browser);
                    IntPtr hwnd = IntPtr.Zero;
                    for (int retry = 0; retry < 100 && hwnd == IntPtr.Zero; retry++)
                    {
                        await Task.Delay(100);
                        hwnd = NativeBrowserHost.FindBrowserWindow(browser.Id);
                    }
                    if (hwnd == IntPtr.Zero) throw new InvalidOperationException("Owned browser missing");
                    hosts[index].Attach(hwnd);
                    await Task.Delay(500);
                }
                var identities = new Dictionary<int, string>();
                for (int round = 0; round < 16; round++)
                {
                    int selected = round % 2;
                    if (args.Contains("--rapid"))
                    {
                        for (int quick = 0; quick < 4; quick++)
                        {
                            int next = (selected + quick) % 2;
                            hosts[1 - next].ShowBrowser(false);
                            hosts[1 - next].Visibility = Visibility.Hidden;
                            hosts[next].Visibility = Visibility.Visible;
                            window.UpdateLayout();
                            await window.Dispatcher.InvokeAsync(() => { }, System.Windows.Threading.DispatcherPriority.Loaded);
                            hosts[next].ShowBrowser(true);
                            await Task.Delay(15);
                            // Reselect the same tab before its 100 ms repair is
                            // due; cancellation must reschedule required repair.
                            hosts[next].ShowBrowser(true);
                            await Task.Delay(15);
                        }
                    }
                    hosts[1 - selected].ShowBrowser(false);
                    for (int index = 0; index < 2; index++)
                        hosts[index].Visibility = index == selected ? Visibility.Visible : Visibility.Hidden;
                    window.UpdateLayout();
                    await window.Dispatcher.InvokeAsync(() => { }, System.Windows.Threading.DispatcherPriority.Loaded);
                    hosts[selected].ShowBrowser(true);
                    await Task.Delay(700);
                    hosts[selected].RefreshBounds();
                    var dom = await Inspect(Path.Combine(directory, selected.ToString()));
                    bool inactiveHidden = !IsWindowVisible(hosts[1 - selected].AttachedHandle)
                        && !IsWindowVisible(GetParent(hosts[1 - selected].AttachedHandle));
                    if (!inactiveHidden) throw new InvalidOperationException("Canceled presentation callback exposed inactive browser");
                    string identity = dom.GetProperty("identity").GetString()!;
                    if (identities.TryGetValue(selected, out var originalIdentity) && originalIdentity != identity)
                        throw new InvalidOperationException("Tab switching recreated or reloaded the document");
                    identities[selected] = identity;
                    if (dom.GetProperty("title").GetString() != $"Owned switch {selected}"
                        || dom.GetProperty("draft").GetString() != $"persistent draft {selected}"
                        || dom.GetProperty("marker").GetString() != $"marker-{selected}"
                        || dom.GetProperty("url").GetString() != new Uri(Path.Combine(directory, selected.ToString(), "fixture.html")).AbsoluteUri)
                        throw new InvalidOperationException("Profile identity, URL, draft or storage changed");
                    var parent = GetParent(hosts[selected].AttachedHandle);
                    GetClientRect(parent, out var rect);
                    MapWindowPoints(parent, IntPtr.Zero, ref rect, 2);
                    var point = new PointNative { X = rect.Left + (rect.Right - rect.Left) / 2,
                        Y = rect.Top + (rect.Bottom - rect.Top) * 3 / 4 };
                    var hit = WindowFromPoint(point);
                    GetWindowThreadProcessId(hit, out var hitPid);
                    if (hitPid != Environment.ProcessId && hitPid != browsers[selected].Id)
                        throw new InvalidOperationException("Owned capture region obscured by another app; no pixel read");
                    var dc = GetDC(IntPtr.Zero);
                    uint pixel;
                    try { pixel = GetPixel(dc, point.X, point.Y); }
                    finally { ReleaseDC(IntPtr.Zero, dc); }
                    uint expected = selected == 0 ? 0x3040e0u : 0xd05020u;
                    bool painted = pixel == expected;
                    var descendants = new List<object>();
                    EnumChildWindows(hosts[selected].AttachedHandle, (child, _) =>
                    {
                        var name = new StringBuilder(256);
                        GetClassName(child, name, name.Capacity);
                        GetWindowRect(child, out var bounds);
                        descendants.Add(new { hwnd = child.ToInt64(), className = name.ToString(),
                            visible = IsWindowVisible(child), style = GetWindowLongPtr(child, -16).ToInt64(),
                            bounds = new[] { bounds.Left, bounds.Top, bounds.Right, bounds.Bottom } });
                        return true;
                    }, IntPtr.Zero);
                    Observations.Add(new { round, selected, painted, pixel = $"0x{pixel:X6}", expected = $"0x{expected:X6}",
                        rootVisible = IsWindowVisible(hosts[selected].AttachedHandle), hostVisible = IsWindowVisible(parent),
                        hitPid, descendants, insets = hosts[selected].ContentInsets, dom, inactiveHidden });
                    if (!painted) throw new InvalidOperationException($"Native page not painted after tab switch {round}");
                }
                _exit = 0;
            }
            catch (Exception error) { Observations.Add(new { error = error.ToString() }); }
            finally
            {
                foreach (var host in hosts) { try { host.Detach(); } catch { } }
                foreach (var browser in browsers)
                {
                    if (!browser.HasExited)
                    {
                        var hwnd = NativeBrowserHost.FindBrowserWindow(browser.Id);
                        if (hwnd != IntPtr.Zero) PostMessage(hwnd, 0x0010, IntPtr.Zero, IntPtr.Zero);
                        await Task.Delay(300);
                        if (!browser.HasExited) browser.Kill(entireProcessTree: true);
                    }
                    browser.Dispose();
                }
                try { Directory.Delete(directory, recursive: true); } catch { }
                string json = JsonSerializer.Serialize(new { ok = _exit == 0, observations = Observations,
                    scope = "two temporary own browser profiles; actual owned-region desktop pixel read",
                    globalInput = "none", hostsRecreated = false,
                    nativeTestWorkarounds = Array.Empty<string>(),
                    rapidSelections = args.Contains("--rapid") ? 64 : 0,
                    rapidSameTabReselections = args.Contains("--rapid") ? 64 : 0,
                    sequence = "inactive-first visibility/layout then selected show" }, new JsonSerializerOptions { WriteIndented = true });
                if (args.Length > 0) File.WriteAllText(args[0], json, new UTF8Encoding(false));
                Console.WriteLine(json);
                window.Close();
            }
        };
        app.Run(window);
        return _exit;
    }

    [StructLayout(LayoutKind.Sequential)] private struct RectNative { public int Left, Top, Right, Bottom; }
    [StructLayout(LayoutKind.Sequential)] private struct PointNative { public int X, Y; }
    private delegate bool EnumWindowsProc(IntPtr hwnd, IntPtr parameter);
    [DllImport("user32.dll")] private static extern bool GetWindowRect(IntPtr hwnd, out RectNative rect);
    [DllImport("user32.dll")] private static extern bool GetClientRect(IntPtr hwnd, out RectNative rect);
    [DllImport("user32.dll")] private static extern int MapWindowPoints(IntPtr from, IntPtr to, ref RectNative rect, int count);
    [DllImport("user32.dll")] private static extern IntPtr GetParent(IntPtr hwnd);
    [DllImport("user32.dll")] private static extern bool IsWindowVisible(IntPtr hwnd);
    [DllImport("user32.dll")] private static extern IntPtr GetWindowLongPtr(IntPtr hwnd, int index);
    [DllImport("user32.dll")] private static extern IntPtr WindowFromPoint(PointNative point);
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);
    [DllImport("user32.dll")] private static extern IntPtr GetDC(IntPtr hwnd);
    [DllImport("user32.dll")] private static extern int ReleaseDC(IntPtr hwnd, IntPtr dc);
    [DllImport("gdi32.dll")] private static extern uint GetPixel(IntPtr dc, int x, int y);
    [DllImport("user32.dll")] private static extern bool EnumChildWindows(IntPtr parent, EnumWindowsProc callback, IntPtr parameter);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern int GetClassName(IntPtr hwnd, StringBuilder text, int capacity);
    [DllImport("user32.dll")] private static extern bool PostMessage(IntPtr hwnd, uint message, IntPtr wParam, IntPtr lParam);

    private static async Task<JsonElement> Inspect(string profile)
    {
        int port = int.Parse(File.ReadAllLines(Path.Combine(profile, "DevToolsActivePort"))[0]);
        using var http = new HttpClient(new HttpClientHandler { UseProxy = false });
        using var targets = JsonDocument.Parse(await http.GetStringAsync($"http://127.0.0.1:{port}/json/list"));
        var target = targets.RootElement.EnumerateArray().First(item => item.GetProperty("type").GetString() == "page");
        using var socket = new ClientWebSocket();
        socket.Options.Proxy = null;
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
        await socket.ConnectAsync(new Uri(target.GetProperty("webSocketDebuggerUrl").GetString()!), timeout.Token);
        byte[] command = Encoding.UTF8.GetBytes(JsonSerializer.Serialize(new { id = 1, method = "Runtime.evaluate",
            @params = new { expression = "({title:document.title,url:location.href,visibility:document.visibilityState,background:getComputedStyle(document.body).backgroundColor,draft:document.querySelector('input')?.value,ready:document.readyState,marker:localStorage.getItem('ownedMarker'),identity:window.navigationIdentity})", returnByValue = true } }));
        await socket.SendAsync(command, WebSocketMessageType.Text, true, timeout.Token);
        byte[] reply = new byte[65536];
        var received = await socket.ReceiveAsync(reply, timeout.Token);
        using var response = JsonDocument.Parse(Encoding.UTF8.GetString(reply, 0, received.Count));
        return response.RootElement.GetProperty("result").GetProperty("result").GetProperty("value").Clone();
    }
}
