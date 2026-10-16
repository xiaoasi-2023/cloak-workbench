using System.IO;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Threading;

namespace CloakWorkbench;

public sealed partial class MainWindow : Window
{
    static readonly Brush Bg = Hex("#141716"), Panel = Hex("#1B201E"), Line = WorkbenchTheme.Border;
    static readonly Brush Text = WorkbenchTheme.Ink, Muted = WorkbenchTheme.Muted, Accent = WorkbenchTheme.Blue;
    readonly HttpClient api = new() { BaseAddress = new Uri("http://127.0.0.1:8766/"), Timeout = TimeSpan.FromSeconds(40) };
    readonly StackPanel accountList = new(), tabs = new() { Orientation = Orientation.Horizontal };
    readonly Grid viewport = new();
    readonly ColumnDefinition runningSidebarColumn = new() { Width = new GridLength(236) };
    readonly ContentControl browserPlaceholder = new();
    readonly TextBlock browserContext = new();
    readonly List<Control> browserCommands = new();
    Button? sidebarToggle, presentationButton;
    bool runningSidebarCollapsed;
    readonly Grid environmentWorkspace = new();
    readonly ContentControl proxyWorkspace = new() { Visibility = Visibility.Collapsed };
    readonly ContentControl diagnosticsWorkspace = new() { Visibility = Visibility.Collapsed };
    readonly ContentControl accountWorkspace = new() { Visibility = Visibility.Collapsed };
    readonly Dictionary<string, Button> navigationButtons = new();
    string currentPage = "环境工作台";
    readonly TextBox address = new();
    Button? createEnvironmentButton;
    readonly TextBlock status = new(), counts = new(), engine = new();
    readonly Dictionary<string, NativeBrowserHost> hosts = new();
    readonly Dictionary<string, JsonElement> profiles = new();
    readonly HashSet<string> launching = new();
    readonly HashSet<string> closingProfiles = new();
    readonly List<string> messages = new();
    readonly string localDir = Path.Combine(Environment.GetEnvironmentVariable("CLOAK_WORKBENCH_ROOT") ?? AppContext.BaseDirectory, ".local");
    readonly DispatcherTimer timer = new() { Interval = TimeSpan.FromSeconds(2) };
    readonly bool smoke, resume;
    string? selected;
    string? renderedAccounts, renderedTabs;
    JsonElement capabilities;
    bool polling, closing;

    public MainWindow(bool smoke, bool resume = false)
    {
        this.smoke = smoke; this.resume = resume;
        Directory.CreateDirectory(localDir);
        var token = Environment.GetEnvironmentVariable("CLOAK_WORKBENCH_TOKEN") ?? "";
        if (token.Length == 0) throw new InvalidOperationException("Use start-workbench.bat to launch the application.");
        api.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", token);
        Title = "Cloak Workbench · 多账号工作台";
        Width = 1440; Height = 900; MinWidth = 1050; MinHeight = 680;
        WindowStartupLocation = WindowStartupLocation.CenterScreen;
        Background = Bg; Foreground = Text; WorkbenchTheme.Apply(this);
        BuildLayout();
        timer.Tick += async (_, _) => await Refresh();
        Loaded += async (_, _) => { await Refresh(); try { await LoadProxyCatalog(); RenderAccounts(); } catch (Exception ex) { Log("代理列表读取失败：" + ex.Message); } timer.Start(); if (smoke) await Smoke(); else if (resume) await RestoreSession(); };
        Closing += async (_, e) => {
            if (closing) return;
            e.Cancel = true; closing = true; timer.Stop(); StopFeedbackTimers();
            // Keep native parents alive until Chromium closes. Detaching first
            // restores the standalone window and causes a desktop flash.
            foreach (var host in hosts.Values) { try { host.ShowBrowser(false); } catch (Exception ex) { Log(ex.Message); } }
            try { await Api("shutdown", new { }); } catch (Exception ex) { Log("Shutdown: " + ex.Message); }
            foreach (var host in hosts.Values) { try { host.Detach(showStandalone: false); } catch (Exception ex) { Log(ex.Message); } }
            SaveSnapshot(); Close();
        };
    }

    static Brush Hex(string value) => new SolidColorBrush((Color)ColorConverter.ConvertFromString(value));
    static TextBlock Label(string text, double size = 13, Brush? color = null) => new() {
        Text = text, FontSize = size, Foreground = color ?? Text, VerticalAlignment = VerticalAlignment.Center
    };
    static Button Button(string text, Action callback, bool primary = false) {
        var b = new Button { Content = text, Padding = new Thickness(12, 8, 12, 8), Margin = new Thickness(0, 0, 6, 0),
            Background = primary ? Accent : Panel, Foreground = primary ? Hex("#092219") : Text,
            BorderBrush = Line, BorderThickness = new Thickness(1), Cursor = Cursors.Hand };
        b.Click += (_, _) => callback(); return b;
    }
    static Border Box(UIElement child, Brush? bg = null, Thickness? padding = null) => new() {
        Child = child, Background = bg ?? Panel, BorderBrush = Line, BorderThickness = new Thickness(0, 0, 1, 0),
        Padding = padding ?? new Thickness(12)
    };
    static Button IconButton(string icon, string text, Action callback, bool primary = false) {
        var button = Button(text, callback, primary);
        var content = new StackPanel { Orientation = Orientation.Horizontal };
        content.Children.Add(WorkbenchTheme.Icon(icon, primary ? Hex("#092219") : Text));
        if (text.Length > 0) { var label = Label(text); label.Foreground = primary ? Hex("#092219") : Text; label.Margin = new Thickness(7, 0, 0, 0); content.Children.Add(label); }
        button.Content = content; button.ToolTip = text.Length > 0 ? text : icon;
        System.Windows.Automation.AutomationProperties.SetName(button, text.Length > 0 ? text : icon switch { "back" => "后退", "forward" => "前进", "reload" => "刷新页面", "external" => "恢复独立窗口", "download" => "环境下载文件", "shield" => "验证当前环境", "browser" => "浏览器弹窗", "close" => "关闭环境浏览器", _ => icon }); return button;
    }

    void UpdateNavigation()
    {
        foreach (var (key, button) in navigationButtons)
            button.Background = key == currentPage ? Hex("#203B33") : Brushes.Transparent;
    }
    void ShowProxyPage()
    {
        proxyWorkspace.Content ??= CreateProxyManagementPage(this);
        ShowManagementPage(proxyWorkspace, "代理管理");
    }
    void ShowManagementPage(ContentControl page, string name)
    {
        // Hide native children before showing WPF content, releasing browser input.
        foreach (var host in hosts.Values) { host.ShowBrowser(false); host.Visibility = Visibility.Hidden; }
        currentPage = name;
        environmentWorkspace.Visibility = Visibility.Collapsed;
        proxyWorkspace.Visibility = page == proxyWorkspace ? Visibility.Visible : Visibility.Collapsed;
        diagnosticsWorkspace.Visibility = page == diagnosticsWorkspace ? Visibility.Visible : Visibility.Collapsed;
        accountWorkspace.Visibility = page == accountWorkspace ? Visibility.Visible : Visibility.Collapsed;
        loginAccountsWorkspace.Visibility = page == loginAccountsWorkspace ? Visibility.Visible : Visibility.Collapsed;
        enterpriseWorkspace.Visibility = page == enterpriseWorkspace ? Visibility.Visible : Visibility.Collapsed;
        videoWorkspace.Visibility = page == videoWorkspace ? Visibility.Visible : Visibility.Collapsed;
        profileSettingsWorkspace.Visibility = page == profileSettingsWorkspace ? Visibility.Visible : Visibility.Collapsed;
        UpdateNavigation();
        page.Focus();
    }
    void ShowBrowserPage()
    {
        videoWorkspace.Visibility = Visibility.Collapsed;
        enterpriseWorkspace.Visibility = Visibility.Collapsed;
        currentPage = "环境工作台";
        proxyWorkspace.Visibility = Visibility.Collapsed;
        diagnosticsWorkspace.Visibility = Visibility.Collapsed;
        accountWorkspace.Visibility = Visibility.Collapsed;
        loginAccountsWorkspace.Visibility = Visibility.Collapsed;
        profileSettingsWorkspace.Visibility = Visibility.Collapsed;
        environmentWorkspace.Visibility = Visibility.Visible;
        UpdateNavigation();
        if (selected != null && RuntimeIds().Contains(selected)) Select(selected);
        else createEnvironmentButton?.Focus();
    }

    async Task<JsonElement> Api(string path, object? body = null)
    {
        HttpResponseMessage response;
        if (body == null) {
            // Loopback reads can be interrupted as the local server closes a
            // connection. Retry reads only; a submitted mutation is never replayed.
            for (int attempt = 0; ; attempt++) {
                try { response = await api.GetAsync(path); break; }
                catch (HttpRequestException) when (attempt < 2 && !closing) { await Task.Delay(120 * (attempt + 1)); }
            }
        }
        else response = await api.PostAsync(path, new StringContent(JsonSerializer.Serialize(body), Encoding.UTF8, "application/json"));
        using (response) {
            var text = await response.Content.ReadAsStringAsync();
            if (!response.IsSuccessStatusCode) {
                try { using var error = JsonDocument.Parse(text); throw new InvalidOperationException(Str(error.RootElement, "error", text)); }
                catch (JsonException) { throw new InvalidOperationException($"HTTP {(int)response.StatusCode}: {text}"); }
            }
            using var doc = JsonDocument.Parse(text); return doc.RootElement.Clone();
        }
    }
    static string Str(JsonElement element, string key, string fallback = "") => element.TryGetProperty(key, out var value) ? value.ToString() : fallback;
    static int Num(JsonElement element, string key, int fallback = 0) => element.TryGetProperty(key, out var value) && value.TryGetInt32(out int n) ? n : fallback;

    async Task Refresh()
    {
        if (polling || closing) return; polling = true;
        try {
            var result = await Api("profiles");
            if (closing) return;
            var previousSelection = selected;
            profiles.Clear();
            foreach (var p in result.GetProperty("profiles").EnumerateArray()) profiles[Str(p, "id")] = p.Clone();
            foreach (var id in hosts.Keys.ToList()) {
                if (launching.Contains(id) || closingProfiles.Contains(id)) continue;
                if (!profiles.TryGetValue(id, out var p) || Str(p, "state") is not ("running" or "starting") || !NativeBrowserHost.IsWindowAlive(hosts[id].AttachedHandle) && hosts[id].AttachedHandle != IntPtr.Zero) {
                    hosts[id].Detach(showStandalone: false); viewport.Children.Remove(hosts[id]); hosts[id].Dispose(); hosts.Remove(id);
                    if (selected == id) selected = null;
                }
            }
            if (previousSelection != null && (selected == null || !RuntimeIds().Contains(selected))) {
                selected = hosts.Keys.FirstOrDefault() ?? RuntimeIds().FirstOrDefault();
                if (selected != null && currentPage == "环境工作台") Select(selected);
            }
            UpdateRuntimeCounts();
            if (capabilities.ValueKind != JsonValueKind.Object) capabilities = await Api("capabilities");
            engine.Text = "CloakBrowser " + Str(capabilities, "engineVersion").Split('.').First() + "\n本地服务已连接";
            if (currentPage == "环境工作台" && selected != null && hosts.TryGetValue(selected, out var visibleHost)) visibleHost.RefreshBounds();
            RenderAccounts(); RenderTabs(); RenderAccountCenter();
            UpdateBrowserPresentation();
            if (!videoLoaded || activeVideoCount > 0 || currentPage == "视频生成") await LoadVideos();
            UpdateGlobalActivity();
            if (currentPage == "账号列表") await LoadLoginAccounts();
            if (selected != null && profiles.TryGetValue(selected, out var current) && !address.IsKeyboardFocusWithin) address.Text = Str(current, "url", Str(current, "startUrl"));
            SaveSnapshot();
        } catch (Exception e) { engine.Text = "服务连接失败"; Log(e.Message); }
        finally { polling = false; }
    }

    void RenderTabs()
    {
        var signature = JsonSerializer.Serialize(RuntimeIds().Select(id => new {
            id, name = profiles.TryGetValue(id, out var p) ? Str(p, "name") : id, active = id == selected, mode = RuntimeLabel(id)
        }));
        if (signature == renderedTabs) return;
        renderedTabs = signature;
        tabs.Children.Clear();
        foreach (var id in RuntimeIds()) {
            var name = profiles.TryGetValue(id, out var p) ? Str(p, "name") : id;
            var tabGroup = new StackPanel { Orientation = Orientation.Horizontal, Background = id == selected ? Hex("#203B33") : Brushes.Transparent, Margin = new Thickness(0, 0, 6, 0) };
            var tab = IconButton("browser", name + (hosts.ContainsKey(id) ? "" : " · " + RuntimeLabel(id)), () => Select(id)); tab.BorderThickness = new Thickness(0); tab.Background = Brushes.Transparent; tab.Padding = new Thickness(10, 7, 5, 7); tab.MaxWidth = 205; tab.ToolTip = name + " · " + RuntimeLabel(id); tab.IsEnabled = !closingProfiles.Contains(id); tabGroup.Children.Add(tab);
            var close = IconButton("close", "", async () => await Guard(() => CloseProfile(id))); close.ToolTip = "关闭浏览器并保留环境数据"; close.Padding = new Thickness(5); close.BorderThickness = new Thickness(0); close.Background = Brushes.Transparent; close.IsEnabled = !launching.Contains(id) && !closingProfiles.Contains(id); tabGroup.Children.Add(close); tabs.Children.Add(new Border { Child = tabGroup, CornerRadius = new CornerRadius(6) });
        }
    }
    IEnumerable<string> RuntimeIds() => profiles.Where(kv => Str(kv.Value, "state") is "running" or "starting" || launching.Contains(kv.Key) || closingProfiles.Contains(kv.Key)).Select(kv => kv.Key).ToArray();
    string RuntimeLabel(string id) => closingProfiles.Contains(id) ? "正在关闭" : launching.Contains(id) || profiles.TryGetValue(id, out var p) && Str(p, "state") == "starting" ? "正在打开" : hosts.ContainsKey(id) ? "工作台内" : "独立窗口";
    void UpdateRuntimeCounts() => counts.Text = $"{RuntimeIds().Count()} 个运行环境 · 共 {profiles.Count} 个已保存";
    void ToggleRunningSidebar()
    {
        runningSidebarCollapsed = !runningSidebarCollapsed;
        runningSidebarColumn.Width = new GridLength(runningSidebarCollapsed ? 0 : 236);
        if (sidebarToggle != null) { sidebarToggle.ToolTip = runningSidebarCollapsed ? "展开运行环境列表" : "收起运行环境列表"; System.Windows.Automation.AutomationProperties.SetName(sidebarToggle, runningSidebarCollapsed ? "展开运行环境列表" : "收起运行环境列表"); }
        viewport.UpdateLayout();
        if (selected != null && hosts.TryGetValue(selected, out var host)) host.RefreshBounds();
    }
    void UpdateBrowserPresentation()
    {
        RenderBrowserLoginActivity();
        bool active = selected != null && profiles.TryGetValue(selected, out var p) && Str(p, "state") == "running" && !launching.Contains(selected) && !closingProfiles.Contains(selected);
        foreach (var command in browserCommands) command.IsEnabled = active;
        if (presentationButton != null) presentationButton.ToolTip = selected != null && hosts.ContainsKey(selected) ? "切换为独立窗口" : "嵌回工作台";
        if (selected != null && profiles.TryGetValue(selected, out var profile) && RuntimeIds().Contains(selected)) {
            var network = AccountNetworkObservation(profile);
            var login = LoginObservationLabel(profile) + (IsCurrentLoginObservation(profile) && AccountState(profile) == "logged_in" ? " · " + LoginIdentityLabel(profile) : "");
            browserContext.Text = Str(profile, "name") + "   /   " + RuntimeLabel(selected) + "   /   " + login + "   /   " + (network.ValueKind == JsonValueKind.Object ? "上次出口 · " + NetworkCountryName(network) : "出口国家未检测");
            browserContext.ToolTip = "网站登录与绑定账号身份分别核验；出口信息为上次检测结果。";
        } else browserContext.Text = "浏览器工作台  /  打开原环境可继续使用保存的会话";
        browserPlaceholder.Visibility = selected != null && hosts.ContainsKey(selected) ? Visibility.Collapsed : Visibility.Visible;
        if (browserPlaceholder.Visibility != Visibility.Visible) return;
        var panel = new StackPanel { MaxWidth = 410, HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(30) };
        bool external = selected != null && profiles.TryGetValue(selected, out var current) && Str(current, "state") == "running" && !launching.Contains(selected) && !closingProfiles.Contains(selected);
        panel.Children.Add(new TextBlock { Text = external ? "浏览器正在独立窗口中运行" : RuntimeIds().Any() ? "选择一个运行环境" : "开始你的浏览器会话", Foreground = Text, FontSize = 23, FontWeight = FontWeights.SemiBold, TextAlignment = TextAlignment.Center, TextWrapping = TextWrapping.Wrap });
        panel.Children.Add(new TextBlock { Text = external ? "网页与登录状态保持不变。你可以切到独立窗口，或把它嵌回工作台。" : "在环境管理中打开已有环境，或创建新的独立环境。关闭浏览器会保留浏览器数据。", Foreground = Muted, FontSize = 13, TextAlignment = TextAlignment.Center, TextWrapping = TextWrapping.Wrap, LineHeight = 23, Margin = new Thickness(0, 15, 0, 22) });
        var actions = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Center };
        if (external) {
            var id = selected!; actions.Children.Add(Button("切到独立窗口", () => FocusExternal(id), true)); actions.Children.Add(Button("嵌回工作台", async () => await Guard(() => Launch(id))));
        } else { actions.Children.Add(Button("打开已有环境", ShowAccountCenter, true)); actions.Children.Add(Button("新建环境", () => EditAccount(null))); }
        panel.Children.Add(actions); browserPlaceholder.Content = panel;
    }
    void FocusExternal(string id)
    {
        if (!profiles.TryGetValue(id, out var p) || Str(p, "state") != "running") return;
        var hwnd = NativeBrowserHost.FindBrowserWindow(Num(p, "pid"), includeHidden: true);
        if (hwnd == IntPtr.Zero) { Log("独立窗口尚未就绪，请稍后重试。"); return; }
        if (IsIconic(hwnd)) ShowWindow(hwnd, 9); else ShowWindow(hwnd, 5);
        SetForegroundWindow(hwnd);
    }
    Task Launch(string id) => LaunchQueued(id, selectBrowser: true);
    async Task LaunchQueued(string id, bool selectBrowser)
    {
        if (closing || closingProfiles.Contains(id)) return;
        if (hosts.ContainsKey(id)) { if (selectBrowser) Select(id); return; }
        if (!launching.Add(id)) return;
        bool existingRuntime = profiles.TryGetValue(id, out var savedRuntime) && Str(savedRuntime, "state") is "running" or "starting";
        RenderAccounts(); RenderTabs();
        try {
        Log("正在打开浏览器环境…"); var runtime = await Api($"profiles/{id}/launch", new { embedded = true });
        if (closing) return;
        var pid = Num(runtime, "pid"); IntPtr hwnd = IntPtr.Zero;
        for (int i = 0; i < 100; i++) { hwnd = NativeBrowserHost.FindBrowserWindow(pid, includeHidden: true); if (hwnd != IntPtr.Zero || closing) break; await Task.Delay(150); }
        if (closing) return;
        if (hwnd == IntPtr.Zero) {
            if (!existingRuntime) await Api($"profiles/{id}/close", new { });
            throw new InvalidOperationException(existingRuntime ? "未找到现有浏览器窗口，已保留运行实例。请稍后重试或查看运行诊断。" : "未找到浏览器窗口，已结束本次启动。请查看运行诊断后重试。");
        }
        var host = new NativeBrowserHost(); host.Diagnostic += Log;
        host.ShowBrowser(false);
        if (!selectBrowser) host.Visibility = Visibility.Hidden;
        hosts[id] = host; viewport.Children.Add(host); viewport.UpdateLayout();
        await Dispatcher.InvokeAsync(() => { }, DispatcherPriority.Loaded);
        if (closing) { hosts.Remove(id); viewport.Children.Remove(host); host.Dispose(); return; }
        try { host.Attach(hwnd, showStandaloneOnDetach: true); }
        catch { hosts.Remove(id); viewport.Children.Remove(host); host.Dispose(); if (!existingRuntime) await Api($"profiles/{id}/close", new { }); throw; }
        if (selectBrowser) Select(id);
        else { host.ShowBrowser(false); host.Visibility = Visibility.Hidden; selected ??= id; }
        await Refresh(); Log("浏览器已嵌入工作台，可直接输入和操作。"); SaveSnapshot();
        } finally { launching.Remove(id); RenderAccounts(); RenderTabs(); UpdateBrowserPresentation(); }
    }
    void Select(string id)
    {
        videoWorkspace.Visibility = Visibility.Collapsed;
        enterpriseWorkspace.Visibility = Visibility.Collapsed;
        currentPage = "环境工作台";
        proxyWorkspace.Visibility = Visibility.Collapsed;
        diagnosticsWorkspace.Visibility = Visibility.Collapsed;
        accountWorkspace.Visibility = Visibility.Collapsed;
        loginAccountsWorkspace.Visibility = Visibility.Collapsed;
        profileSettingsWorkspace.Visibility = Visibility.Collapsed;
        environmentWorkspace.Visibility = Visibility.Visible;
        UpdateNavigation();
        selected = id;
        foreach (var (key, host) in hosts) {
            if (key == id) continue;
            host.ShowBrowser(false); host.Visibility = Visibility.Hidden;
        }
        if (hosts.TryGetValue(id, out var active)) {
            active.Visibility = Visibility.Visible;
            viewport.UpdateLayout();
            active.ShowBrowser(true);
        }
        RenderAccounts(); RenderTabs(); UpdateBrowserPresentation();
        if (profiles.TryGetValue(id, out var p)) address.Text = Str(p, "url", Str(p, "startUrl"));
        SaveSnapshot();
    }
    async Task Navigate() { if (selected != null) { await Api($"profiles/{selected}/navigate", new { url = address.Text.Trim() }); await Refresh(); } }
    async Task CloseProfile(string id)
    {
        if (closing || launching.Contains(id) || !closingProfiles.Add(id)) return;
        RenderAccounts(); RenderTabs(); UpdateBrowserPresentation();
        hosts.TryGetValue(id, out var host);
        try {
            host?.ShowBrowser(false);
            // Close while still embedded; only dispose the native host after
            // the worker confirms the owned browser process has exited.
            await Api($"profiles/{id}/close", new { });
            if (hosts.Remove(id, out var removed)) {
                removed.Detach(showStandalone: false); viewport.Children.Remove(removed); removed.Dispose();
            }
            if (selected == id) {
                selected = null;
                if (hosts.Count > 0 && !closing) {
                    if (currentPage == "环境工作台") Select(hosts.Keys.First());
                    else selected = hosts.Keys.First();
                }
            }
            await Refresh(); Log("浏览器已关闭，环境和登录状态保留。");
        } catch {
            if (!closing && currentPage == "环境工作台" && selected == id) host?.ShowBrowser(true);
            throw;
        } finally { closingProfiles.Remove(id); RenderAccounts(); RenderTabs(); UpdateRuntimeCounts(); UpdateBrowserPresentation(); }
    }
    void ToggleExternal()
    {
        if (closing || selected == null || closingProfiles.Contains(selected) || launching.Contains(selected)) return;
        if (!hosts.TryGetValue(selected, out var host)) { var id = selected; _ = Guard(() => Launch(id)); return; }
        var hwnd = host.AttachedHandle; host.Detach(); viewport.Children.Remove(host); host.Dispose(); hosts.Remove(selected);
        RenderTabs(); RenderAccounts(); UpdateBrowserPresentation(); Log("浏览器继续在独立窗口运行，可切到窗口或嵌回工作台。"); SaveSnapshot();
    }
    async Task Guard(Func<Task> action) { try { await action(); } catch (Exception e) { Log(e.Message); MessageBox.Show(this, e.Message, "操作未完成", MessageBoxButton.OK, MessageBoxImage.Warning); } }
    void Log(string message) {
        if (!Dispatcher.CheckAccess()) { Dispatcher.BeginInvoke(() => Log(message)); return; }
        status.Text = message.StartsWith("Browser input queue", StringComparison.Ordinal) || message.StartsWith("Browser content keyboard focus", StringComparison.Ordinal)
            ? "环境已连接，可直接输入。" : message.StartsWith("Native content insets", StringComparison.Ordinal) || message.StartsWith("Attach HWND", StringComparison.Ordinal)
            ? "正在连接浏览器窗口…" : message;
        messages.Add(DateTime.Now.ToString("HH:mm:ss") + " " + message);
        if (messages.Count > 200) messages.RemoveAt(0);
        File.AppendAllText(Path.Combine(localDir, "desktop.log"), messages[^1] + Environment.NewLine, Encoding.UTF8);
        UpdateDiagnostics();
    }

    static string ProxySummary(JsonElement result) => Str(result, "reachable").Equals("true", StringComparison.OrdinalIgnoreCase)
        ? $"连接成功  ·  出口 IP {Str(result, "egressIp", "未知")}  ·  {Str(result, "latencyMs")} ms  ·  {(Str(result, "mode") == "direct" ? "本机直连" : Str(result, "protocol").ToUpperInvariant())}"
        : "连接失败：" + Str(result, "error", "未获取到出口 IP");
    static string DownloadTime(JsonElement file) {
        var value = Str(file, "modifiedAt");
        if (double.TryParse(value, System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var epoch)) try { return DateTimeOffset.FromUnixTimeSeconds((long)epoch).ToLocalTime().ToString("yyyy-MM-dd HH:mm"); } catch (ArgumentOutOfRangeException) { }
        return value;
    }
    static string VerificationTime(JsonElement result) {
        var value = Str(result, "verifiedAt", "本次检测");
        return DateTimeOffset.TryParse(value, System.Globalization.CultureInfo.InvariantCulture, System.Globalization.DateTimeStyles.None, out var timestamp)
            ? timestamp.ToLocalTime().ToString("yyyy-MM-dd HH:mm:ss") : value;
    }

    async Task ShowEnvironment()
    {
        if (selected == null) { Log("请先打开一个浏览器环境。"); return; }
        var id = selected; var result = await Api($"profiles/{id}/environment");
        var window = new Window { Owner = this, Title = "环境验证", Width = 870, Height = 720, MinHeight = 500, MinWidth = 700, Background = Bg, Foreground = Text, WindowStartupLocation = WindowStartupLocation.CenterOwner }; WorkbenchTheme.Apply(window);
        var panel = new StackPanel { Margin = new Thickness(26) }; panel.Children.Add(new TextBlock { Text = "配置与实际环境", FontSize = 23, FontWeight = FontWeights.SemiBold, Foreground = Text });
        panel.Children.Add(new TextBlock { Text = "读取当前浏览器实际暴露的值，并与此环境保存的配置对照。", Foreground = Muted, Margin = new Thickness(0, 7, 0, 20) });
        panel.Children.Add(new TextBlock { Text = "读取时间  " + VerificationTime(result), Foreground = Muted, FontSize = 11, Margin = new Thickness(0, 0, 0, 12) });
        if (result.TryGetProperty("checks", out var checks) && checks.ValueKind == JsonValueKind.Array) {
            foreach (var item in checks.EnumerateArray()) {
                var row = new Grid { Margin = new Thickness(0, 0, 0, 1), Background = Panel }; row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(150) }); row.ColumnDefinitions.Add(new ColumnDefinition()); row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(86) });
                var label = Label(Str(item, "label", Str(item, "field")), 12); label.Margin = new Thickness(12); row.Children.Add(label);
                var expected = Str(item, "expected"); var actualValue = Str(item, "actual");
                var values = new StackPanel { Margin = new Thickness(10) }; values.Children.Add(new TextBlock { Text = "配置  " + (expected.Length > 0 ? expected : "自动"), Foreground = Muted, FontSize = 11, TextWrapping = TextWrapping.Wrap }); values.Children.Add(new TextBlock { Text = "实际  " + (actualValue.Length > 0 ? actualValue : "未暴露"), Foreground = Text, FontFamily = new FontFamily("Cascadia Code, Consolas"), FontSize = 12, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 4, 0, 0) }); Grid.SetColumn(values, 1); row.Children.Add(values);
                var state = Str(item, "status"); var pass = state is "pass" or "match" or "matched"; var badge = Label(pass ? "一致" : state is "auto" or "observed" or "info" ? "已读取" : state is "unsupported" or "unverified" ? "未验证" : state == "mismatch" ? "不一致" : "需检查", 11, pass ? WorkbenchTheme.Green : state == "mismatch" ? Hex("#E8B578") : Muted); Grid.SetColumn(badge, 2); row.Children.Add(badge); panel.Children.Add(row);
            }
        } else panel.Children.Add(new TextBlock { Text = "浏览器尚未返回可比较的指纹结果。", Foreground = Muted });
        var actual = new Expander { Header = "完整实际环境数据", Margin = new Thickness(0, 18, 0, 0), Foreground = Muted };
        if (result.TryGetProperty("actual", out var actualData)) actual.Content = new TextBox { Text = JsonSerializer.Serialize(actualData, new JsonSerializerOptions { WriteIndented = true }), IsReadOnly = true, TextWrapping = TextWrapping.Wrap, FontFamily = new FontFamily("Cascadia Code, Consolas"), Margin = new Thickness(0, 10, 0, 0) }; panel.Children.Add(actual);
        var testResult = new TextBlock { Foreground = Muted, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 12, 0, 0) }; Button? test = null; test = IconButton("shield", "检测此环境的出口 IP", async () => { test!.IsEnabled = false; try { testResult.Text = "正在检测…"; testResult.Text = ProxySummary(await Api($"profiles/{id}/proxy-test", new { })); } catch (Exception ex) { testResult.Text = ex.Message; } finally { test.IsEnabled = true; } }); test.Margin = new Thickness(0, 20, 0, 0); panel.Children.Add(test); panel.Children.Add(testResult);
        window.Content = new ScrollViewer { Content = panel, VerticalScrollBarVisibility = ScrollBarVisibility.Auto }; window.ShowDialog();
    }

    async Task ShowDownloads()
    {
        if (selected == null) { Log("请先打开一个浏览器环境。"); return; }
        var result = await Api($"profiles/{selected}/downloads"); var directory = Str(result, "directory");
        var window = new Window { Owner = this, Title = "环境下载文件", Width = 700, Height = 500, Background = Bg, Foreground = Text, WindowStartupLocation = WindowStartupLocation.CenterOwner }; WorkbenchTheme.Apply(window);
        var panel = new DockPanel { Margin = new Thickness(24) }; var header = new StackPanel(); header.Children.Add(new TextBlock { Text = "下载文件", FontSize = 22, FontWeight = FontWeights.SemiBold, Foreground = Text }); header.Children.Add(new TextBlock { Text = directory, Foreground = Muted, FontFamily = new FontFamily("Consolas"), FontSize = 11, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 8, 0, 16) });
        var open = IconButton("external", "打开下载文件夹", () => { try { if (!Directory.Exists(directory)) throw new InvalidOperationException("下载目录尚未建立。"); System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo("explorer.exe") { ArgumentList = { directory }, UseShellExecute = false }); } catch (Exception ex) { MessageBox.Show(window, ex.Message, "无法打开文件夹"); } }); open.HorizontalAlignment = HorizontalAlignment.Left; open.Margin = new Thickness(0, 0, 0, 18); header.Children.Add(open); DockPanel.SetDock(header, Dock.Top); panel.Children.Add(header);
        var files = new StackPanel(); if (result.TryGetProperty("files", out var list) && list.ValueKind == JsonValueKind.Array && list.GetArrayLength() > 0) foreach (var file in list.EnumerateArray()) { var row = new StackPanel { Margin = new Thickness(12) }; row.Children.Add(new TextBlock { Text = Str(file, "name"), Foreground = Text, TextWrapping = TextWrapping.Wrap }); row.Children.Add(new TextBlock { Text = $"{Str(file, "size")} 字节  ·  {DownloadTime(file)}  ·  {(Str(file, "complete").Equals("true", StringComparison.OrdinalIgnoreCase) ? "已完成" : "下载中")}", Foreground = Muted, FontSize = 11, Margin = new Thickness(0, 5, 0, 0) }); files.Children.Add(new Border { Child = row, Background = Panel, BorderBrush = Line, BorderThickness = new Thickness(0, 0, 0, 1) }); } else files.Children.Add(new TextBlock { Text = "此环境还没有下载文件。", Foreground = Muted, Margin = new Thickness(0, 18, 0, 0) }); panel.Children.Add(new ScrollViewer { Content = files, VerticalScrollBarVisibility = ScrollBarVisibility.Auto }); window.Content = panel; window.ShowDialog();
    }
    void ShowBrowserWindows()
    {
        if (selected == null || !hosts.TryGetValue(selected, out var host) || host.OwnedWindows == null) { Log("请先打开一个浏览器环境。"); return; }
        var manager = host.OwnedWindows;
        var window = new Window { Owner = this, Title = "浏览器弹窗", Width = 700, Height = 460, Background = Bg, Foreground = Text, WindowStartupLocation = WindowStartupLocation.CenterOwner }; WorkbenchTheme.Apply(window);
        var layout = new DockPanel { Margin = new Thickness(24) }; var heading = new StackPanel(); heading.Children.Add(new TextBlock { Text = "此环境的独立窗口", FontSize = 22, FontWeight = FontWeights.SemiBold, Foreground = Text }); heading.Children.Add(new TextBlock { Text = "网页弹窗和文件选择对话框保留原生窗口，输入法候选框不受此列表影响。", Foreground = Muted, FontSize = 12, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 8, 0, 18) }); DockPanel.SetDock(heading, Dock.Top); layout.Children.Add(heading);
        var list = new StackPanel(); void Render() {
            list.Children.Clear(); var items = manager.Snapshot();
            if (items.Count == 0) { list.Children.Add(new TextBlock { Text = "当前没有弹窗或文件对话框。", Foreground = Muted, Margin = new Thickness(0, 14, 0, 0) }); return; }
            foreach (var item in items) {
                var card = new StackPanel { Margin = new Thickness(14) }; card.Children.Add(new TextBlock { Text = item.Title.Length > 0 ? item.Title : "浏览器窗口", Foreground = Text, FontWeight = FontWeights.SemiBold, TextWrapping = TextWrapping.Wrap });
                card.Children.Add(new TextBlock { Text = $"{(item.Kind == "dialog" ? "原生对话框" : "网页弹窗")}  ·  {(item.Minimized ? "已最小化" : item.Enabled ? "可操作" : "等待其他对话框")}", Foreground = Muted, FontSize = 11, Margin = new Thickness(0, 6, 0, 12) });
                var actions = new StackPanel { Orientation = Orientation.Horizontal }; actions.Children.Add(IconButton("external", "切到窗口", () => { window.Close(); Activate(); Dispatcher.BeginInvoke(() => { if (!manager.Activate(item.Handle, new System.Windows.Interop.WindowInteropHelper(this).Handle)) Log("窗口状态已改变，请重新打开弹窗列表。"); }); }));
                actions.Children.Add(Button("请求关闭", () => { if (!manager.RequestClose(item.Handle)) Log("窗口已关闭或不属于此环境。"); Render(); })); card.Children.Add(actions); list.Children.Add(new Border { Child = card, Background = Panel, BorderBrush = Line, BorderThickness = new Thickness(1), CornerRadius = new CornerRadius(8), Margin = new Thickness(0, 0, 0, 10) });
            }
        }
        var refresh = Button("刷新窗口列表", Render); refresh.HorizontalAlignment = HorizontalAlignment.Left; refresh.Margin = new Thickness(0, 14, 0, 0); DockPanel.SetDock(refresh, Dock.Bottom); layout.Children.Add(refresh); layout.Children.Add(new ScrollViewer { Content = list, VerticalScrollBarVisibility = ScrollBarVisibility.Auto }); Render(); window.Content = layout; window.ShowDialog();
    }
    async Task CreateDemos()
    {
        foreach (var (id, name, locale, timezone, seed) in new[] {
            ("demo-a", "测试环境 A · 东京", "ja-JP", "Asia/Tokyo", 24681),
            ("demo-b", "测试环境 B · 首尔", "ko-KR", "Asia/Seoul", 75319) }) {
            if (!profiles.ContainsKey(id)) await Api("profiles", new { id, name, locale, timezone, seed, proxy = "", screenWidth = 1280, screenHeight = 800,
                startUrl = $"http://127.0.0.1:8766/probe?profile={id}" });
        }
        await Refresh(); Log("已创建两个本地测试环境；测试页面不会访问账号网站。");
    }
    async Task Smoke()
    {
        try {
            await CreateDemos(); await Launch("demo-a"); await Task.Delay(700); await Launch("demo-b");
            Select("demo-a"); await Task.Delay(400); Select("demo-b");
            Width = 1500; Height = 920; await Task.Delay(500); SaveSnapshot();
            File.WriteAllText(Path.Combine(localDir, "smoke-result.json"), JsonSerializer.Serialize(new { ok = true, profiles = hosts.Count, selected }, new JsonSerializerOptions { WriteIndented = true }), Encoding.UTF8);
            Log("原生嵌入验证已完成：两个浏览器、标签切换、窗口缩放。");
        } catch (Exception e) {
            Log(e.ToString()); File.WriteAllText(Path.Combine(localDir, "smoke-result.json"), JsonSerializer.Serialize(new { ok = false, error = e.ToString() }), Encoding.UTF8);
        }
    }
    async Task RestoreSession()
    {
        var path = Path.Combine(localDir, "restart-state.json");
        try {
            using var document = JsonDocument.Parse(File.ReadAllText(path, Encoding.UTF8));
            var saved = document.RootElement;
            var workArea = SystemParameters.WorkArea;
            if (saved.TryGetProperty("width", out var width)) Width = Math.Min(workArea.Width, Math.Max(MinWidth, width.GetDouble()));
            if (saved.TryGetProperty("height", out var height)) Height = Math.Min(workArea.Height, Math.Max(MinHeight, height.GetDouble()));
            Left = workArea.Left + Math.Max(0, (workArea.Width - Width) / 2);
            Top = workArea.Top + Math.Max(0, (workArea.Height - Height) / 2);
            foreach (var item in saved.GetProperty("browsers").EnumerateArray()) {
                var id = Str(item, "id");
                if (!profiles.ContainsKey(id)) continue;
                try {
                    bool existingRuntime = Str(profiles[id], "state") is "running" or "starting";
                    if (Str(item, "displayMode", "embedded") == "external") {
                        // Restore the recorded presentation directly. Reattaching and
                        // detaching first would unnecessarily move a live window.
                        await Api($"profiles/{id}/launch", new { embedded = false });
                        await Refresh();
                    } else await LaunchQueued(id, selectBrowser: false);
                    var url = Str(item, "url");
                    // A repeated resume must not reload a live page with unsaved input.
                    if (!existingRuntime && url.Length > 0 && profiles.TryGetValue(id, out var restored) && Str(restored, "url") != url)
                        await Api($"profiles/{id}/navigate", new { url });
                } catch (Exception ex) { Log("恢复环境失败：" + ex.Message); }
            }
            var active = Str(saved, "selected");
            if (RuntimeIds().Contains(active)) Select(active);
            await Refresh();
            File.WriteAllText(Path.Combine(localDir, "resume-result.json"), JsonSerializer.Serialize(new {
                ok = true, profiles = RuntimeIds().Count(), selected
            }), Encoding.UTF8);
        } catch (Exception ex) { Log("恢复工作台失败：" + ex.Message); }
    }
    [DllImport("user32.dll")] static extern IntPtr GetParent(IntPtr hwnd);
    [DllImport("user32.dll")] static extern bool IsIconic(IntPtr hwnd);
    [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr hwnd, int command);
    [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr hwnd);
    void SaveSnapshot()
    {
        var snapshot = new { selected, width = ActualWidth, height = ActualHeight,
            browsers = RuntimeIds().Select(id => {
                hosts.TryGetValue(id, out var host);
                return new { id, displayMode = host != null ? "embedded" : "external", hwnd = host?.AttachedHandle.ToInt64() ?? 0, parent = host != null ? GetParent(host.AttachedHandle).ToInt64() : 0,
                    visible = host?.Visibility == Visibility.Visible, hostWidth = host?.ActualWidth ?? 0, hostHeight = host?.ActualHeight ?? 0, contentInsets = host?.ContentInsets };
            }).ToArray() };
        File.WriteAllText(Path.Combine(localDir, "ui-state.json"), JsonSerializer.Serialize(snapshot, new JsonSerializerOptions { WriteIndented = true }), Encoding.UTF8);
    }
}
