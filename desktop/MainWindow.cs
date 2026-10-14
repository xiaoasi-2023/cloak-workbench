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
    static readonly Brush Bg = Hex("#F4F6FB"), Panel = Brushes.White, Line = WorkbenchTheme.Border;
    static readonly Brush Text = WorkbenchTheme.Ink, Muted = WorkbenchTheme.Muted, Accent = WorkbenchTheme.Blue;
    readonly HttpClient api = new() { BaseAddress = new Uri("http://127.0.0.1:8766/"), Timeout = TimeSpan.FromSeconds(40) };
    readonly StackPanel accountList = new(), tabs = new() { Orientation = Orientation.Horizontal };
    readonly Grid viewport = new();
    readonly Grid environmentWorkspace = new();
    readonly ContentControl proxyWorkspace = new() { Visibility = Visibility.Collapsed };
    readonly Dictionary<string, Button> navigationButtons = new();
    bool proxyPageVisible;
    readonly TextBox address = new();
    Button? createEnvironmentButton;
    readonly TextBlock status = new(), counts = new(), engine = new();
    readonly Dictionary<string, NativeBrowserHost> hosts = new();
    readonly Dictionary<string, JsonElement> profiles = new();
    readonly HashSet<string> launching = new();
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
        Title = "Cloak Workbench · 浏览器环境工作台";
        Width = 1440; Height = 900; MinWidth = 1050; MinHeight = 680;
        WindowStartupLocation = WindowStartupLocation.CenterScreen;
        Background = Bg; Foreground = Text; WorkbenchTheme.Apply(this);
        BuildLayout();
        timer.Tick += async (_, _) => await Refresh();
        Loaded += async (_, _) => { await Refresh(); timer.Start(); if (smoke) await Smoke(); else if (resume) await RestoreSession(); };
        Closing += async (_, e) => {
            if (closing) return;
            e.Cancel = true; closing = true; timer.Stop();
            foreach (var host in hosts.Values) { try { host.Detach(); } catch (Exception ex) { Log(ex.Message); } }
            try { await Api("shutdown", new { }); } catch (Exception ex) { Log("Shutdown: " + ex.Message); }
            SaveSnapshot(); Close();
        };
    }

    static Brush Hex(string value) => new SolidColorBrush((Color)ColorConverter.ConvertFromString(value));
    static TextBlock Label(string text, double size = 13, Brush? color = null) => new() {
        Text = text, FontSize = size, Foreground = color ?? Text, VerticalAlignment = VerticalAlignment.Center
    };
    static Button Button(string text, Action callback, bool primary = false) {
        var b = new Button { Content = text, Padding = new Thickness(12, 8, 12, 8), Margin = new Thickness(0, 0, 6, 0),
            Background = primary ? Accent : Panel, Foreground = primary ? Brushes.White : Text,
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
        content.Children.Add(WorkbenchTheme.Icon(icon, primary ? Brushes.White : Text));
        if (text.Length > 0) { var label = Label(text); label.Foreground = primary ? Brushes.White : Text; label.Margin = new Thickness(7, 0, 0, 0); content.Children.Add(label); }
        button.Content = content; button.ToolTip = text.Length > 0 ? text : icon;
        System.Windows.Automation.AutomationProperties.SetName(button, text.Length > 0 ? text : icon switch { "back" => "后退", "forward" => "前进", "reload" => "刷新页面", "external" => "恢复独立窗口", "download" => "环境下载文件", "shield" => "验证当前环境", "browser" => "浏览器弹窗", "close" => "关闭环境浏览器", _ => icon }); return button;
    }

    void BuildLayout()
    {
        var root = new Grid { Background = Bg }; root.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(76) }); root.ColumnDefinitions.Add(new ColumnDefinition());
        var rail = new DockPanel { Background = WorkbenchTheme.Navy, LastChildFill = true };
        var logo = new Border { Background = Accent, CornerRadius = new CornerRadius(12), Width = 40, Height = 40, Margin = new Thickness(0, 20, 0, 30), Child = WorkbenchTheme.Icon("shield", Brushes.White, 23) };
        DockPanel.SetDock(logo, Dock.Top); rail.Children.Add(logo);
        var nav = new StackPanel();
        foreach (var (icon, label, callback) in new (string, string, Action)[] { ("browser", "环境工作台", ShowBrowserPage), ("settings", "代理管理", ShowProxyPage), ("diagnostic", "运行诊断", ShowDiagnostics) }) {
            var b = Button("", callback); b.Content = WorkbenchTheme.Icon(icon, Brushes.White, 22); b.Background = icon == "browser" ? Hex("#243451") : Brushes.Transparent; b.BorderThickness = new Thickness(0); b.Width = 48; b.Height = 48; b.Margin = new Thickness(0, 0, 0, 12); b.ToolTip = label; System.Windows.Automation.AutomationProperties.SetName(b, label); nav.Children.Add(b); navigationButtons[label] = b;
        }
        rail.Children.Add(nav); root.Children.Add(rail);
        var workspace = environmentWorkspace; workspace.RowDefinitions.Add(new RowDefinition { Height = new GridLength(78) }); workspace.RowDefinitions.Add(new RowDefinition()); workspace.RowDefinitions.Add(new RowDefinition { Height = new GridLength(30) }); Grid.SetColumn(workspace, 1); root.Children.Add(workspace);
        Grid.SetColumn(proxyWorkspace, 1); root.Children.Add(proxyWorkspace);
        var header = new DockPanel { Margin = new Thickness(24, 0, 24, 0), LastChildFill = true };
        var heading = new StackPanel { VerticalAlignment = VerticalAlignment.Center }; heading.Children.Add(new TextBlock { Text = "浏览器环境", FontSize = 23, FontWeight = FontWeights.SemiBold, Foreground = Text }); heading.Children.Add(new TextBlock { Text = "CLOAK WORKBENCH  /  独立会话 · 稳定环境", FontSize = 11, Foreground = Muted, Margin = new Thickness(0, 4, 0, 0) }); DockPanel.SetDock(heading, Dock.Left); header.Children.Add(heading);
        engine.Text = "正在连接本地服务"; engine.Foreground = Muted; engine.FontSize = 12; engine.VerticalAlignment = VerticalAlignment.Center; engine.HorizontalAlignment = HorizontalAlignment.Right; header.Children.Add(engine); workspace.Children.Add(header);
        var body = new Grid { Margin = new Thickness(20, 0, 20, 10) }; body.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(278) }); body.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(16) }); body.ColumnDefinitions.Add(new ColumnDefinition()); Grid.SetRow(body, 1); workspace.Children.Add(body);

        var accounts = new DockPanel();
        var accountHead = new StackPanel(); DockPanel.SetDock(accountHead, Dock.Top);
        var accountHeading = Label("环境列表", 15); accountHeading.FontWeight = FontWeights.SemiBold; accountHead.Children.Add(accountHeading); counts.Text = "0 个环境"; counts.Foreground = Muted; counts.Margin = new Thickness(0, 6, 0, 18);
        accountHead.Children.Add(counts);
        var create = IconButton("plus", "新建环境", () => EditProfile(null), true); createEnvironmentButton = create; create.HorizontalAlignment = HorizontalAlignment.Stretch; create.Margin = new Thickness(0, 0, 0, 8); accountHead.Children.Add(create);
        var demos = Button("添加本地演示环境", async () => await Guard(CreateDemos)); demos.Margin = new Thickness(0); demos.Foreground = Muted; accountHead.Children.Add(demos); accounts.Children.Add(accountHead);
        accounts.Children.Add(new ScrollViewer { Content = accountList, VerticalScrollBarVisibility = ScrollBarVisibility.Auto, Margin = new Thickness(0, 12, 0, 0) });
        var accountBox = Box(accounts, Panel, new Thickness(16)); accountBox.CornerRadius = new CornerRadius(12); accountBox.BorderThickness = new Thickness(1); body.Children.Add(accountBox);

        var browser = new Grid(); browser.RowDefinitions.Add(new RowDefinition { Height = new GridLength(46) });
        browser.RowDefinitions.Add(new RowDefinition { Height = new GridLength(48) }); browser.RowDefinitions.Add(new RowDefinition());
        var tabScroll = new ScrollViewer { Content = tabs, HorizontalScrollBarVisibility = ScrollBarVisibility.Auto, VerticalScrollBarVisibility = ScrollBarVisibility.Disabled };
        tabScroll.Margin = new Thickness(8, 8, 8, 0); browser.Children.Add(tabScroll);
        var toolbar = new DockPanel { Margin = new Thickness(8, 5, 8, 5) }; Grid.SetRow(toolbar, 1); browser.Children.Add(toolbar);
        foreach (var (icon, command, tooltip) in new[] { ("back", "back", "后退"), ("forward", "forward", "前进"), ("reload", "reload", "刷新页面") }) {
            var b = IconButton(icon, "", async () => await Guard(async () => { if (selected != null) await Api($"profiles/{selected}/action", new { action = command }); })); b.ToolTip = tooltip; b.Padding = new Thickness(8); b.BorderThickness = new Thickness(0);
            DockPanel.SetDock(b, Dock.Left); toolbar.Children.Add(b);
        }
        var external = IconButton("external", "", ToggleExternal); external.ToolTip = "恢复独立窗口"; DockPanel.SetDock(external, Dock.Right); toolbar.Children.Add(external);
        var windows = IconButton("browser", "", ShowBrowserWindows); windows.ToolTip = "此环境的弹窗与文件对话框"; DockPanel.SetDock(windows, Dock.Right); toolbar.Children.Add(windows);
        var downloads = IconButton("download", "", async () => await Guard(ShowDownloads)); downloads.ToolTip = "环境下载文件"; DockPanel.SetDock(downloads, Dock.Right); toolbar.Children.Add(downloads);
        var validate = IconButton("shield", "", async () => await Guard(ShowEnvironment)); validate.ToolTip = "验证当前环境"; DockPanel.SetDock(validate, Dock.Right); toolbar.Children.Add(validate);
        var navigate = Button("前往", async () => await Guard(Navigate)); DockPanel.SetDock(navigate, Dock.Right); toolbar.Children.Add(navigate);
        address.Background = Bg; address.Foreground = Text; address.BorderBrush = Line; address.Padding = new Thickness(10, 6, 10, 6); address.FontFamily = new FontFamily("Cascadia Code, Consolas"); address.FontSize = 12; address.Margin = new Thickness(0, 0, 6, 0);
        address.VerticalContentAlignment = VerticalAlignment.Center; address.KeyDown += async (_, e) => { if (e.Key == Key.Enter) await Guard(Navigate); };
        System.Windows.Automation.AutomationProperties.SetName(address, "AddressBar");
        toolbar.Children.Add(address);
        viewport.Background = Panel; Grid.SetRow(viewport, 2); browser.Children.Add(viewport);
        var welcome = new StackPanel { VerticalAlignment = VerticalAlignment.Center, HorizontalAlignment = HorizontalAlignment.Center };
        var welcomeIcon = new Border { Background = Hex("#EFF5FF"), Width = 64, Height = 64, CornerRadius = new CornerRadius(16), HorizontalAlignment = HorizontalAlignment.Center, Margin = new Thickness(0, 0, 0, 20), Child = WorkbenchTheme.Icon("browser", Accent, 32) }; welcome.Children.Add(welcomeIcon);
        var welcomeTitle = Label("从一个独立环境开始", 23); welcomeTitle.HorizontalAlignment = HorizontalAlignment.Center; welcome.Children.Add(welcomeTitle);
        var description = Label("配置代理与指纹，再打开浏览器。每个环境单独保存登录状态。", 12, Muted); description.Margin = new Thickness(0, 12, 0, 24); welcome.Children.Add(description);
        var welcomeAction = IconButton("plus", "创建第一个环境", () => EditProfile(null), true); welcomeAction.HorizontalAlignment = HorizontalAlignment.Center; welcome.Children.Add(welcomeAction);
        viewport.Children.Add(welcome);
        var browserSurface = new Border { Child = browser, Background = Panel, BorderBrush = Line, BorderThickness = new Thickness(1), CornerRadius = new CornerRadius(12) }; Grid.SetColumn(browserSurface, 2); body.Children.Add(browserSurface);
        status.Text = "就绪"; status.Foreground = Muted; status.Margin = new Thickness(16, 0, 16, 0);
        status.FontSize = 11; Grid.SetRow(status, 2); workspace.Children.Add(status); Content = root;
    }

    void UpdateNavigation()
    {
        navigationButtons["环境工作台"].Background = proxyPageVisible ? Brushes.Transparent : Hex("#243451");
        navigationButtons["代理管理"].Background = proxyPageVisible ? Hex("#243451") : Brushes.Transparent;
    }
    void ShowProxyPage()
    {
        // Hide native children before showing WPF content, releasing browser input.
        foreach (var host in hosts.Values) { host.ShowBrowser(false); host.Visibility = Visibility.Hidden; }
        proxyPageVisible = true;
        environmentWorkspace.Visibility = Visibility.Collapsed;
        proxyWorkspace.Content ??= CreateProxyManagementPage(this);
        proxyWorkspace.Visibility = Visibility.Visible;
        UpdateNavigation();
        proxyWorkspace.Focus();
    }
    void ShowBrowserPage()
    {
        proxyPageVisible = false;
        proxyWorkspace.Visibility = Visibility.Collapsed;
        environmentWorkspace.Visibility = Visibility.Visible;
        UpdateNavigation();
        if (selected != null && hosts.ContainsKey(selected)) Select(selected);
        else createEnvironmentButton?.Focus();
    }

    async Task<JsonElement> Api(string path, object? body = null)
    {
        HttpResponseMessage response;
        if (body == null) response = await api.GetAsync(path);
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
            var result = await Api("profiles"); profiles.Clear();
            foreach (var p in result.GetProperty("profiles").EnumerateArray()) profiles[Str(p, "id")] = p.Clone();
            foreach (var id in hosts.Keys.ToList()) {
                if (!profiles.TryGetValue(id, out var p) || Str(p, "state") is not ("running" or "starting") || !NativeBrowserHost.IsWindowAlive(hosts[id].AttachedHandle) && hosts[id].AttachedHandle != IntPtr.Zero) {
                    hosts[id].Detach(); viewport.Children.Remove(hosts[id]); hosts[id].Dispose(); hosts.Remove(id);
                    if (selected == id) selected = null;
                }
            }
            counts.Text = $"{profiles.Count} 个环境 · {hosts.Count} 个浏览器";
            if (capabilities.ValueKind != JsonValueKind.Object) capabilities = await Api("capabilities");
            engine.Text = "CloakBrowser " + Str(capabilities, "engineVersion") + "  ·  本机原生引擎";
            if (!proxyPageVisible && selected != null && hosts.TryGetValue(selected, out var visibleHost)) visibleHost.RefreshBounds();
            RenderAccounts(); RenderTabs();
            if (selected != null && profiles.TryGetValue(selected, out var current) && !address.IsKeyboardFocusWithin) address.Text = Str(current, "url", Str(current, "startUrl"));
            SaveSnapshot();
        } catch (Exception e) { engine.Text = "服务连接失败"; Log(e.Message); }
        finally { polling = false; }
    }

    void RenderAccounts()
    {
        // Preserve the actual controls (and keyboard focus) during status polling.
        var signature = JsonSerializer.Serialize(profiles.Select(kv => new {
            id = kv.Key, name = Str(kv.Value, "name"), locale = Str(kv.Value, "locale"),
            timezone = Str(kv.Value, "timezone"), seed = Str(kv.Value, "seed"), state = Str(kv.Value, "state"),
            proxy = Str(kv.Value, "proxy"), proxyId = Str(kv.Value, "proxyId"), attached = hosts.ContainsKey(kv.Key), active = selected == kv.Key
        }));
        if (signature == renderedAccounts) return;
        renderedAccounts = signature;
        accountList.Children.Clear();
        foreach (var (id, p) in profiles) {
            var card = new StackPanel(); var top = new DockPanel(); var running = Str(p, "state") == "running";
            var state = Label(running ? "运行中" : "已保存", 10, running ? WorkbenchTheme.Green : Muted); state.Margin = new Thickness(8, 0, 0, 0); DockPanel.SetDock(state, Dock.Right); top.Children.Add(state);
            var name = Label(Str(p, "name"), 13); name.FontWeight = FontWeights.SemiBold; name.TextTrimming = TextTrimming.CharacterEllipsis; top.Children.Add(name); card.Children.Add(top);
            var details = Label($"{Str(p, "locale")}  /  {Str(p, "timezone")}\n{(Str(p, "proxy").Length > 0 || Str(p, "proxyId").Length > 0 ? "代理连接" : "直连网络")}  ·  种子 {Str(p, "seed")}", 11, Muted);
            details.Margin = new Thickness(0, 9, 0, 12); details.LineHeight = 19; card.Children.Add(details);
            var controls = new WrapPanel();
            controls.Children.Add(Button(hosts.ContainsKey(id) ? "切换" : "打开", async () => await Guard(() => Launch(id)), selected == id));
            controls.Children.Add(Button("配置", () => EditProfile(id)));
            if (Str(p, "state") == "running") controls.Children.Add(Button("关闭", async () => await Guard(() => CloseProfile(id))));
            card.Children.Add(controls);
            var b = new Border { Child = card, Padding = new Thickness(12), Margin = new Thickness(0, 0, 0, 10), CornerRadius = new CornerRadius(8),
                Background = id == selected ? Hex("#EFF5FF") : Brushes.White, BorderBrush = id == selected ? Hex("#A6C1FA") : Line, BorderThickness = new Thickness(1) };
            accountList.Children.Add(b);
        }
    }
    void RenderTabs()
    {
        var signature = JsonSerializer.Serialize(hosts.Keys.Select(id => new {
            id, name = profiles.TryGetValue(id, out var p) ? Str(p, "name") : id, active = id == selected
        }));
        if (signature == renderedTabs) return;
        renderedTabs = signature;
        tabs.Children.Clear();
        foreach (var id in hosts.Keys) {
            var name = profiles.TryGetValue(id, out var p) ? Str(p, "name") : id;
            var tabGroup = new StackPanel { Orientation = Orientation.Horizontal, Background = id == selected ? Hex("#EFF5FF") : Brushes.Transparent, Margin = new Thickness(0, 0, 6, 0) };
            var tab = IconButton("browser", name, () => Select(id)); tab.BorderThickness = new Thickness(0); tab.Background = Brushes.Transparent; tab.Padding = new Thickness(10, 7, 5, 7); tab.MaxWidth = 205; tabGroup.Children.Add(tab);
            var close = IconButton("close", "", async () => await Guard(() => CloseProfile(id))); close.ToolTip = "关闭环境浏览器"; close.Padding = new Thickness(5); close.BorderThickness = new Thickness(0); close.Background = Brushes.Transparent; tabGroup.Children.Add(close); tabs.Children.Add(new Border { Child = tabGroup, CornerRadius = new CornerRadius(6) });
        }
    }
    async Task Launch(string id)
    {
        if (hosts.ContainsKey(id)) { Select(id); return; }
        if (!launching.Add(id)) return;
        try {
        Log("正在打开浏览器环境…"); var runtime = await Api($"profiles/{id}/launch", new { });
        var pid = Num(runtime, "pid"); IntPtr hwnd = IntPtr.Zero;
        for (int i = 0; i < 100; i++) { hwnd = NativeBrowserHost.FindBrowserWindow(pid); if (hwnd != IntPtr.Zero) break; await Task.Delay(150); }
        if (hwnd == IntPtr.Zero) throw new InvalidOperationException("未找到浏览器窗口。浏览器保留为独立窗口，请查看运行诊断。");
        var host = new NativeBrowserHost(); host.Diagnostic += Log;
        hosts[id] = host; viewport.Children.Add(host); viewport.UpdateLayout();
        await Dispatcher.InvokeAsync(() => { }, DispatcherPriority.Loaded);
        try { host.Attach(hwnd); }
        catch { hosts.Remove(id); viewport.Children.Remove(host); host.Dispose(); throw; }
        Select(id); await Refresh(); Log("浏览器已嵌入工作台，可直接输入和操作。"); SaveSnapshot();
        } finally { launching.Remove(id); }
    }
    void Select(string id)
    {
        proxyPageVisible = false;
        proxyWorkspace.Visibility = Visibility.Collapsed;
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
        RenderAccounts(); RenderTabs();
        if (profiles.TryGetValue(id, out var p)) address.Text = Str(p, "url", Str(p, "startUrl"));
        SaveSnapshot();
    }
    async Task Navigate() { if (selected != null) { await Api($"profiles/{selected}/navigate", new { url = address.Text.Trim() }); await Refresh(); } }
    async Task CloseProfile(string id)
    {
        if (hosts.Remove(id, out var host)) { host.Detach(); viewport.Children.Remove(host); host.Dispose(); }
        await Api($"profiles/{id}/close", new { });
        if (selected == id) selected = null;
        if (hosts.Count > 0) Select(hosts.Keys.First()); await Refresh(); Log("浏览器已关闭，环境和登录状态保留。");
    }
    void ToggleExternal()
    {
        if (selected == null || !hosts.TryGetValue(selected, out var host)) return;
        var hwnd = host.AttachedHandle; host.Detach(); viewport.Children.Remove(host); host.Dispose(); hosts.Remove(selected);
        selected = null; RenderTabs(); RenderAccounts(); Log("已恢复独立浏览器窗口，点击账号“打开”可再次嵌入。"); SaveSnapshot();
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
    }
    void ShowDiagnostics() {
        var box = new TextBox { Text = string.Join(Environment.NewLine, messages), IsReadOnly = true, TextWrapping = TextWrapping.Wrap,
            Background = Bg, Foreground = Text, FontFamily = new FontFamily("Consolas"), Padding = new Thickness(16), VerticalScrollBarVisibility = ScrollBarVisibility.Auto };
        var window = new Window { Owner = this, Title = "运行诊断", Width = 800, Height = 450, Content = box, Background = Bg, WindowStartupLocation = WindowStartupLocation.CenterOwner }; WorkbenchTheme.Apply(window); window.ShowDialog();
    }

    async void EditProfile(string? id)
    {
        try { await LoadProxyCatalog(); CreateProfileDialog(id).ShowDialog(); }
        catch (Exception ex) { Log("环境编辑器打开失败：" + ex.Message); MessageBox.Show(this, ex.Message, "无法打开配置", MessageBoxButton.OK, MessageBoxImage.Warning); }
    }
    Window CreateProfileDialog(string? id)
    {
        JsonElement p = id != null && profiles.TryGetValue(id, out var existing) ? existing : default;
        string Read(string key, string fallback) => p.ValueKind == JsonValueKind.Object ? Str(p, key, fallback) : fallback;
        var dialog = new Window { Owner = this, Title = id == null ? "新建浏览器环境" : "环境配置", Width = 820, Height = 790, MinWidth = 710, MinHeight = 600,
            Background = Bg, Foreground = Text, WindowStartupLocation = WindowStartupLocation.CenterOwner }; WorkbenchTheme.Apply(dialog);
        var layout = new DockPanel(); var heading = new StackPanel { Margin = new Thickness(28, 22, 28, 18) };
        heading.Children.Add(new TextBlock { Text = id == null ? "创建独立浏览器环境" : Read("name", "环境配置"), FontSize = 23, FontWeight = FontWeights.SemiBold, Foreground = Text });
        heading.Children.Add(new TextBlock { Text = "代理、登录状态和指纹设置分别保存在此环境中。修改后重新打开浏览器生效。", Foreground = Muted, FontSize = 12, Margin = new Thickness(0, 7, 0, 0), TextWrapping = TextWrapping.Wrap });
        DockPanel.SetDock(heading, Dock.Top); layout.Children.Add(heading);
        var footer = new DockPanel { Margin = new Thickness(28, 14, 28, 20) }; DockPanel.SetDock(footer, Dock.Bottom); layout.Children.Add(footer);
        var stack = new StackPanel { Margin = new Thickness(28, 0, 28, 0) }; layout.Children.Add(new ScrollViewer { Content = stack, VerticalScrollBarVisibility = ScrollBarVisibility.Auto });
        var inputs = new Dictionary<string, TextBox>();
        StackPanel Section(string title, string subtitle) {
            var section = new StackPanel(); section.Children.Add(new TextBlock { Text = title, FontSize = 15, FontWeight = FontWeights.SemiBold, Foreground = Text, Margin = new Thickness(0, 0, 0, 5) });
            section.Children.Add(new TextBlock { Text = subtitle, FontSize = 11, Foreground = Muted, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 0, 0, 16) });
            stack.Children.Add(new Border { Child = section, Background = Panel, BorderBrush = Line, BorderThickness = new Thickness(1), CornerRadius = new CornerRadius(10), Padding = new Thickness(18), Margin = new Thickness(0, 0, 0, 14) }); return section;
        }
        TextBox Field(Panel parent, string key, string label, string fallback = "", string? hint = null) {
            var wrapper = new StackPanel { Margin = new Thickness(0, 0, 0, 12) }; wrapper.Children.Add(Label(label, 12));
            var input = new TextBox { Text = Read(key, fallback), Margin = new Thickness(0, 5, 0, 0) }; System.Windows.Automation.AutomationProperties.SetName(input, label); inputs[key] = input; wrapper.Children.Add(input);
            if (hint != null) wrapper.Children.Add(new TextBlock { Text = hint, Foreground = Muted, FontSize = 10, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 5, 0, 0) }); parent.Children.Add(wrapper); return input;
        }
        Grid Pair(Panel parent) { var grid = new Grid(); grid.ColumnDefinitions.Add(new ColumnDefinition()); grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(16) }); grid.ColumnDefinitions.Add(new ColumnDefinition()); parent.Children.Add(grid); return grid; }
        void PairFields(Panel parent, (string key, string title, string value) left, (string key, string title, string value) right) {
            var grid = Pair(parent); var a = new StackPanel(); var b = new StackPanel(); Grid.SetColumn(b, 2); grid.Children.Add(a); grid.Children.Add(b); Field(a, left.key, left.title, left.value); Field(b, right.key, right.title, right.value);
        }
        var basic = Section("基本信息", "为每个账号保留独立的浏览器数据与登录状态。");
        Field(basic, "name", "环境名称", "新环境"); Field(basic, "startUrl", "启动网址", "https://www.dola.com/");
        PairFields(basic, ("screenWidth", "初始窗口宽度", "1280"), ("screenHeight", "初始窗口高度", "800"));
        var proxy = Section("网络代理", "从代理库选择线路；住宅代理经上游连接。多个环境可复用配置，各自保存浏览器数据。");
        var picker = new ComboBox { MinHeight = 38, Margin = new Thickness(0, 0, 0, 10), Padding = new Thickness(8), DisplayMemberPath = "Label", SelectedValuePath = "Id" }; System.Windows.Automation.AutomationProperties.SetName(picker, "环境代理选择"); proxy.Children.Add(picker);
        var routeHint = new TextBlock { Foreground = Muted, FontSize = 11, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 0, 0, 10) }; proxy.Children.Add(routeHint);
        var legacy = new StackPanel(); proxy.Children.Add(legacy);
        bool hasLegacy = Read("proxy", "").Length > 0 && Read("proxyId", "").Length == 0;
        void FillPicker(string chosen) {
            var choices = new List<ProxyChoice> { new("", "本机直连") };
            choices.AddRange(proxyCatalog.Select(item => new ProxyChoice(Str(item, "id"), Str(item, "name") + (Str(item, "kind") == "residential" ? "  ·  住宅 / 经上游" : "  ·  普通代理"))));
            if (hasLegacy) choices.Add(new("__legacy", "现有手动代理（兼容配置）"));
            if (chosen.Length > 0 && choices.All(item => item.Id != chosen)) choices.Add(new(chosen, "当前代理不可用，请重新选择"));
            picker.ItemsSource = choices; picker.SelectedValue = chosen;
        }
        picker.SelectionChanged += (_, _) => { var chosen = picker.SelectedValue as string ?? ""; legacy.Visibility = chosen == "__legacy" ? Visibility.Visible : Visibility.Collapsed; routeHint.Text = chosen == "" ? "本机 → 网站" : chosen == "__legacy" ? "保留已有手动代理；可在代理管理中建立可复用线路。" : CatalogRoute(chosen); };
        FillPicker(hasLegacy ? "__legacy" : Read("proxyId", ""));
        var manage = IconButton("settings", "管理代理 / 新建线路", async () => { await ShowProxyManager(dialog); var chosen = picker.SelectedValue as string ?? ""; try { await LoadProxyCatalog(); FillPicker(chosen); } catch (Exception ex) { routeHint.Text = ex.Message; } }); manage.HorizontalAlignment = HorizontalAlignment.Left; manage.Margin = new Thickness(0, 0, 0, 14); proxy.Children.Insert(2, manage);
        Field(legacy, "proxy", "代理地址", "", "支持 HTTP、HTTPS 和 SOCKS5；认证信息与地址分别保存。");
        var credentials = Pair(legacy); var userColumn = new StackPanel(); var passwordColumn = new StackPanel(); Grid.SetColumn(passwordColumn, 2); credentials.Children.Add(userColumn); credentials.Children.Add(passwordColumn);
        Field(userColumn, "proxyUsername", "用户名（可选）"); passwordColumn.Children.Add(Label("密码（可选）", 12));
        var password = new PasswordBox { Margin = new Thickness(0, 5, 0, 0) }; System.Windows.Automation.AutomationProperties.SetName(password, "代理密码"); passwordColumn.Children.Add(password);
        bool hasSavedPassword = Read("proxyPasswordSet", "false").Equals("true", StringComparison.OrdinalIgnoreCase);
        passwordColumn.Children.Add(new TextBlock { Text = hasSavedPassword ? "已保存密码；留空保留原密码" : "密码保存在本机环境配置", Foreground = Muted, FontSize = 10, Margin = new Thickness(0, 5, 0, 0) });
        var clearPassword = new CheckBox { Content = "清除已保存的代理密码", Foreground = Muted, Margin = new Thickness(0, 10, 0, 10), Visibility = hasSavedPassword ? Visibility.Visible : Visibility.Collapsed }; legacy.Children.Add(clearPassword);
        clearPassword.Checked += (_, _) => password.IsEnabled = false; clearPassword.Unchecked += (_, _) => password.IsEnabled = true;
        var proxyResult = new TextBlock { Text = "尚未检测网络", Foreground = Muted, FontSize = 12, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 8, 0, 0) };
        Dictionary<string, object?> ProxyDraft() {
            var choice = picker.SelectedValue as string ?? "";
            if (choice != "__legacy") return new() { ["proxyId"] = choice, ["proxy"] = "", ["proxyUsername"] = "", ["clearProxyPassword"] = true };
            var draft = new Dictionary<string, object?> { ["proxyId"] = "", ["proxy"] = inputs["proxy"].Text.Trim(), ["proxyUsername"] = inputs["proxyUsername"].Text.Trim() }; if (password.Password.Length > 0 && clearPassword.IsChecked != true) draft["proxyPassword"] = password.Password; if (clearPassword.IsChecked == true) draft["clearProxyPassword"] = true; return draft;
        }
        Button? proxyTest = null; proxyTest = IconButton("shield", "检测连接与出口 IP", async () => {
            proxyTest!.IsEnabled = false;
            proxyResult.Text = "正在通过所选网络检测出口 IP…"; proxyResult.Foreground = Muted;
            try { var draft = ProxyDraft(); var manual = (picker.SelectedValue as string) == "__legacy"; var useSaved = manual && id != null && hasSavedPassword && password.Password.Length == 0 && clearPassword.IsChecked != true && inputs["proxy"].Text.Trim() == Read("proxy", "") && inputs["proxyUsername"].Text.Trim() == Read("proxyUsername", "");
                if (manual && !useSaved && hasSavedPassword && password.Password.Length == 0 && clearPassword.IsChecked != true) { draft["id"] = id; draft["keepProxyPassword"] = true; }
                var result = useSaved ? await Api($"profiles/{id}/proxy-test", new { }) : await Api("proxy/test", draft);
                proxyResult.Text = ProxySummary(result); proxyResult.Foreground = Str(result, "reachable").Equals("true", StringComparison.OrdinalIgnoreCase) ? WorkbenchTheme.Green : Hex("#B45309");
            } catch (Exception ex) { proxyResult.Text = ex.Message; proxyResult.Foreground = Hex("#B45309"); }
            finally { proxyTest.IsEnabled = true; }
        }); proxy.Children.Add(proxyTest); proxy.Children.Add(proxyResult);
        var fingerprint = Section("浏览器指纹", "常用选项直接选择；固定种子保持每个环境的 Canvas、WebGL 与音频特征稳定。");
        var fillFingerprint = BuildFingerprintEditor(fingerprint, Read);
        var save = Button("保存环境", async () => {
            try {
                int Number(string key) => int.TryParse(inputs[key].Text, out var n) ? n : throw new InvalidOperationException("种子和窗口尺寸必须填写整数。");
                var body = ProxyDraft(); body["id"] = id; foreach (var key in new[] { "name", "startUrl" }) body[key] = inputs[key].Text.Trim();
                foreach (var key in new[] { "screenWidth", "screenHeight" }) body[key] = Number(key);
                fillFingerprint(body);
                await Api("profiles", body); dialog.Close(); await Refresh();
            } catch (Exception e) { MessageBox.Show(dialog, e.Message, "保存失败", MessageBoxButton.OK, MessageBoxImage.Warning); }
        }, true); DockPanel.SetDock(save, Dock.Right); footer.Children.Add(save);
        var cancel = Button("取消", () => dialog.Close()); DockPanel.SetDock(cancel, Dock.Right); footer.Children.Add(cancel);
        var running = Read("state", "") is "running" or "starting"; save.IsEnabled = !running;
        footer.Children.Add(new TextBlock { Text = running ? "请先关闭此环境，再修改配置。" : "配置仅保存到本机。", Foreground = Muted, FontSize = 11, VerticalAlignment = VerticalAlignment.Center });
        dialog.Content = layout; return dialog;
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
                var state = Str(item, "status"); var pass = state is "pass" or "match" or "matched"; var badge = Label(pass ? "一致" : state is "auto" or "observed" or "info" ? "已读取" : state is "unsupported" or "unverified" ? "未验证" : state == "mismatch" ? "不一致" : "需检查", 11, pass ? WorkbenchTheme.Green : state == "mismatch" ? Hex("#B45309") : Muted); Grid.SetColumn(badge, 2); row.Children.Add(badge); panel.Children.Add(row);
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
            if (saved.TryGetProperty("width", out var width)) Width = Math.Max(MinWidth, width.GetDouble());
            if (saved.TryGetProperty("height", out var height)) Height = Math.Max(MinHeight, height.GetDouble());
            foreach (var item in saved.GetProperty("browsers").EnumerateArray()) {
                var id = Str(item, "id");
                if (!profiles.ContainsKey(id)) continue;
                try {
                    await Launch(id);
                    var url = Str(item, "url");
                    if (url.Length > 0) await Api($"profiles/{id}/navigate", new { url });
                } catch (Exception ex) { Log("恢复环境失败：" + ex.Message); }
            }
            var active = Str(saved, "selected");
            if (hosts.ContainsKey(active)) Select(active);
            await Refresh();
            File.WriteAllText(Path.Combine(localDir, "resume-result.json"), JsonSerializer.Serialize(new {
                ok = true, profiles = hosts.Count, selected
            }), Encoding.UTF8);
        } catch (Exception ex) { Log("恢复工作台失败：" + ex.Message); }
    }
    [DllImport("user32.dll")] static extern IntPtr GetParent(IntPtr hwnd);
    void SaveSnapshot()
    {
        var snapshot = new { selected, width = ActualWidth, height = ActualHeight,
            browsers = hosts.Select(kv => new { id = kv.Key, hwnd = kv.Value.AttachedHandle.ToInt64(), parent = GetParent(kv.Value.AttachedHandle).ToInt64(),
                visible = kv.Value.Visibility == Visibility.Visible, hostWidth = kv.Value.ActualWidth, hostHeight = kv.Value.ActualHeight,
                contentInsets = kv.Value.ContentInsets }).ToArray() };
        File.WriteAllText(Path.Combine(localDir, "ui-state.json"), JsonSerializer.Serialize(snapshot, new JsonSerializerOptions { WriteIndented = true }), Encoding.UTF8);
    }
}
