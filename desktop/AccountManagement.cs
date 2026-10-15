using System.Text.Json;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;

namespace CloakWorkbench;

public sealed partial class MainWindow
{
    readonly TextBox accountSearch = new();
    readonly HashSet<string> checkingAccounts = new();

    void BuildLayout()
    {
        var root = new Grid { Background = Bg };
        root.RowDefinitions.Add(new RowDefinition()); root.RowDefinitions.Add(new RowDefinition { Height = new GridLength(28) });
        root.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(138) });
        root.ColumnDefinitions.Add(new ColumnDefinition());
        var rail = new DockPanel { Background = WorkbenchTheme.Navy };
        var brand = new StackPanel { Margin = new Thickness(20, 26, 16, 30) };
        brand.Children.Add(WorkbenchTheme.Icon("shield", Accent, 28));
        brand.Children.Add(new TextBlock { Text = "CLOAK", FontFamily = new FontFamily("Segoe UI"), FontSize = 19, FontWeight = FontWeights.SemiBold, Foreground = Text, Margin = new Thickness(0, 12, 0, 2) });
        brand.Children.Add(Label("多账号工作台", 10, Muted));
        DockPanel.SetDock(brand, Dock.Top); rail.Children.Add(brand);
        var foot = new StackPanel { Margin = new Thickness(20, 0, 14, 22) };
        foot.Children.Add(Label("独立会话 · 本地保存", 10, Muted));
        engine.Text = "连接中…"; engine.Foreground = Muted; engine.FontSize = 10; engine.TextWrapping = TextWrapping.Wrap; engine.Margin = new Thickness(0, 8, 0, 0); foot.Children.Add(engine);
        DockPanel.SetDock(foot, Dock.Bottom); rail.Children.Add(foot);
        var nav = new StackPanel { Margin = new Thickness(10, 0, 10, 0) };
        foreach (var (icon, key, label, callback) in new (string, string, string, Action)[] {
            ("browser", "环境工作台", "浏览器", ShowBrowserPage),
            ("accounts", "账号列表", "账号列表", ShowLoginAccounts),
            ("settings", "批量管理", "环境管理", ShowAccountCenter),
            ("settings", "代理管理", "代理管理", ShowProxyPage),
            ("diagnostic", "运行诊断", "运行诊断", ShowDiagnostics) }) {
            var b = IconButton(icon, label, callback); b.HorizontalContentAlignment = HorizontalAlignment.Left;
            b.Padding = new Thickness(12, 13, 8, 13); b.Margin = new Thickness(0, 0, 0, 6); b.BorderThickness = new Thickness(0);
            b.Background = key == currentPage ? Hex("#203B33") : Brushes.Transparent;
            AutomationProperties.SetName(b, key); navigationButtons[key] = b; nav.Children.Add(b);
        }
        rail.Children.Add(nav); root.Children.Add(rail);
        var workspace = environmentWorkspace;
        workspace.RowDefinitions.Add(new RowDefinition());
        Grid.SetColumn(workspace, 1); root.Children.Add(workspace); Grid.SetColumn(proxyWorkspace, 1); root.Children.Add(proxyWorkspace);
        Grid.SetColumn(diagnosticsWorkspace, 1); root.Children.Add(diagnosticsWorkspace);
        Grid.SetColumn(accountWorkspace, 1); root.Children.Add(accountWorkspace);
        Grid.SetColumn(loginAccountsWorkspace, 1); root.Children.Add(loginAccountsWorkspace);
        Grid.SetColumn(profileSettingsWorkspace, 1); root.Children.Add(profileSettingsWorkspace);
        var body = new Grid(); body.ColumnDefinitions.Add(runningSidebarColumn); body.ColumnDefinitions.Add(new ColumnDefinition()); workspace.Children.Add(body);
        var accounts = new DockPanel();
        var head = new StackPanel { Margin = new Thickness(16, 20, 16, 0) }; DockPanel.SetDock(head, Dock.Top);
        var title = new DockPanel();
        var create = IconButton("plus", "", () => EditAccount(null)); createEnvironmentButton = create; create.ToolTip = "新建环境"; AutomationProperties.SetName(create, "新建环境");
        create.Margin = new Thickness(8, 0, 0, 0); create.Padding = new Thickness(7); create.BorderThickness = new Thickness(0); DockPanel.SetDock(create, Dock.Right); title.Children.Add(create);
        var heading = Label("运行环境", 15); heading.FontWeight = FontWeights.SemiBold; title.Children.Add(heading); head.Children.Add(title);
        counts.Text = "0 个运行环境"; counts.Foreground = Muted; counts.FontSize = 11; counts.Margin = new Thickness(0, 8, 0, 16); head.Children.Add(counts);
        var searchWrap = new Grid { Margin = new Thickness(0, 0, 0, 12) };
        accountSearch.Padding = new Thickness(32, 9, 9, 9); accountSearch.FontSize = 12; accountSearch.Background = Hex("#111613"); accountSearch.ToolTip = "搜索正在运行的环境名称或绑定邮箱";
        AutomationProperties.SetName(accountSearch, "搜索运行环境"); searchWrap.Children.Add(accountSearch);
        var searchIcon = WorkbenchTheme.Icon("search", Muted, 14); ((FrameworkElement)searchIcon).HorizontalAlignment = HorizontalAlignment.Left; ((FrameworkElement)searchIcon).Margin = new Thickness(10, 0, 0, 0); searchIcon.IsHitTestVisible = false; searchWrap.Children.Add(searchIcon);
        var placeholder = Label("搜索运行环境…", 12, Muted); placeholder.Margin = new Thickness(32, 0, 0, 0); placeholder.IsHitTestVisible = false; searchWrap.Children.Add(placeholder);
        accountSearch.TextChanged += (_, _) => { placeholder.Visibility = accountSearch.Text.Length == 0 ? Visibility.Visible : Visibility.Collapsed; RenderAccounts(); };
        head.Children.Add(searchWrap);
        accounts.Children.Add(head);
        var manage = Button("打开 / 管理其他环境", ShowAccountCenter); manage.FontSize = 12; manage.Margin = new Thickness(12, 10, 12, 14); manage.HorizontalAlignment = HorizontalAlignment.Stretch; DockPanel.SetDock(manage, Dock.Bottom); accounts.Children.Add(manage);
        accounts.Children.Add(new ScrollViewer { Content = accountList, VerticalScrollBarVisibility = ScrollBarVisibility.Auto, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled, Margin = new Thickness(12, 0, 10, 12) });
        body.Children.Add(new Border { Child = accounts, ClipToBounds = true, Background = Hex("#191D1B"), BorderBrush = Line, BorderThickness = new Thickness(0, 0, 1, 0) });
        var browser = new Grid { Background = Hex("#111613") };
        browser.RowDefinitions.Add(new RowDefinition { Height = new GridLength(44) }); browser.RowDefinitions.Add(new RowDefinition { Height = new GridLength(46) }); browser.RowDefinitions.Add(new RowDefinition { Height = new GridLength(28) }); browser.RowDefinitions.Add(new RowDefinition());
        var tabHeader = new DockPanel();
        sidebarToggle = IconButton("accounts", "", ToggleRunningSidebar); sidebarToggle.Padding = new Thickness(9); sidebarToggle.Margin = new Thickness(6, 5, 0, 3); sidebarToggle.Background = Brushes.Transparent; sidebarToggle.BorderThickness = new Thickness(0); sidebarToggle.ToolTip = "收起运行环境列表"; AutomationProperties.SetName(sidebarToggle, "收起运行环境列表"); DockPanel.SetDock(sidebarToggle, Dock.Left); tabHeader.Children.Add(sidebarToggle);
        var tabScroll = new ScrollViewer { Content = tabs, HorizontalScrollBarVisibility = ScrollBarVisibility.Auto, VerticalScrollBarVisibility = ScrollBarVisibility.Disabled, Margin = new Thickness(4, 6, 8, 0) }; tabHeader.Children.Add(tabScroll); browser.Children.Add(tabHeader);
        var toolbar = new DockPanel { Margin = new Thickness(8, 5, 8, 6) }; Grid.SetRow(toolbar, 1); browser.Children.Add(toolbar);
        foreach (var (icon, command) in new[] { ("back", "back"), ("forward", "forward"), ("reload", "reload") }) {
            var b = IconButton(icon, "", async () => await Guard(async () => { if (selected != null) await Api($"profiles/{selected}/action", new { action = command }); }));
            b.Padding = new Thickness(7); b.BorderThickness = new Thickness(0); b.Background = Brushes.Transparent; DockPanel.SetDock(b, Dock.Left); toolbar.Children.Add(b); browserCommands.Add(b);
        }
        foreach (var (icon, callback) in new (string, Action)[] { ("external", ToggleExternal), ("browser", ShowBrowserWindows), ("download", async () => await Guard(ShowDownloads)), ("shield", async () => await Guard(ShowEnvironment)) }) {
            var b = IconButton(icon, "", callback); b.Padding = new Thickness(7); b.BorderThickness = new Thickness(0); b.Background = Brushes.Transparent; DockPanel.SetDock(b, Dock.Right); toolbar.Children.Add(b); browserCommands.Add(b); if (icon == "external") { presentationButton = b; AutomationProperties.SetName(b, "切换窗口呈现方式"); }
        }
        address.Background = Hex("#0C100E"); address.Foreground = Text; address.BorderBrush = Line; address.Padding = new Thickness(10, 6, 10, 6); address.FontFamily = new FontFamily("Segoe UI"); address.FontSize = 12; address.Margin = new Thickness(2, 0, 8, 0);
        address.KeyDown += async (_, e) => { if (e.Key == Key.Enter) await Guard(Navigate); }; AutomationProperties.SetName(address, "AddressBar"); toolbar.Children.Add(address); browserCommands.Add(address);
        browserContext.FontSize = 11; browserContext.Foreground = Muted; browserContext.Margin = new Thickness(14, 0, 14, 0); browserContext.VerticalAlignment = VerticalAlignment.Center; browserContext.TextTrimming = TextTrimming.CharacterEllipsis; AutomationProperties.SetName(browserContext, "当前浏览器环境摘要"); Grid.SetRow(browserContext, 2); browser.Children.Add(browserContext);
        viewport.Background = Bg; Grid.SetRow(viewport, 3); browser.Children.Add(viewport);
        browserPlaceholder.HorizontalContentAlignment = HorizontalAlignment.Stretch; browserPlaceholder.VerticalContentAlignment = VerticalAlignment.Stretch; viewport.Children.Add(browserPlaceholder); UpdateBrowserPresentation();
        Grid.SetColumn(browser, 1); body.Children.Add(browser);
        status.Text = "就绪"; status.Foreground = Muted; status.FontSize = 11; status.TextTrimming = TextTrimming.CharacterEllipsis; status.Margin = new Thickness(14, 0, 14, 0); status.VerticalAlignment = VerticalAlignment.Center; Grid.SetRow(status, 1); Grid.SetColumn(status, 1); root.Children.Add(status); Content = root;
    }

    static JsonElement AccountCheck(JsonElement profile) => profile.TryGetProperty("accountCheck", out var check) && check.ValueKind == JsonValueKind.Object ? check : default;
    static string AccountState(JsonElement profile) => AccountCheck(profile) is var check && check.ValueKind == JsonValueKind.Object ? Str(check, "status", "unknown") : "unknown";
    static string AccountStateLabel(string state) => state switch { "logged_in" => "已确认登录", "login_required" => "需要登录", "region_blocked" => "地区受限", _ => "待确认登录" };
    static Brush AccountStateColor(string state) => state switch { "logged_in" => WorkbenchTheme.Green, "login_required" => Hex("#ED9D9D"), "region_blocked" => Hex("#E8B578"), _ => Muted };
    static bool AccountMatches(JsonElement profile, string query, string filter) =>
        (query.Length == 0 || new[] { "name", "accountEmail", "accountGroup", "accountNotes" }.Any(key => Str(profile, key).Contains(query, StringComparison.OrdinalIgnoreCase)) || BoundAccountEmail(profile).Contains(query, StringComparison.OrdinalIgnoreCase))
        && (filter != "已打开" || Str(profile, "state") is "running" or "starting")
        && (filter != "需登录" || AccountState(profile) == "login_required");

    static string BoundAccountEmail(JsonElement profile) => AccountObject(profile, "loginAccount") is var bound && bound.ValueKind == JsonValueKind.Object ? Str(bound, "email") : "";

    void RenderAccounts() => RenderRunningAccounts();

    void RenderRunningAccounts()
    {
        UpdateRuntimeCounts();
        var signature = JsonSerializer.Serialize(new { query = accountSearch.Text, selected, launching = launching.ToArray(), closing = closingProfiles.ToArray(), checking = checkingAccounts.ToArray(), networks = checkingEnvironmentNetworks.ToArray(), profiles = profiles.Values, embedded = hosts.Keys.ToArray() });
        if (signature == renderedAccounts) return; renderedAccounts = signature;
        accountList.Children.Clear();
        foreach (var id in RuntimeIds().Where(id => profiles.TryGetValue(id, out var p) && AccountMatches(p, accountSearch.Text.Trim(), "全部"))) {
            var p = profiles[id]; var name = Str(p, "name", id); var busy = launching.Contains(id) || closingProfiles.Contains(id) || Str(p, "state") == "starting";
            var content = new StackPanel();
            var heading = new DockPanel();
            var dot = Label("●", 9, busy ? Accent : WorkbenchTheme.Green); dot.Margin = new Thickness(8, 0, 0, 0); DockPanel.SetDock(dot, Dock.Right); heading.Children.Add(dot);
            heading.Children.Add(new TextBlock { Text = name, Foreground = Text, FontSize = 12, FontWeight = FontWeights.SemiBold, TextTrimming = TextTrimming.CharacterEllipsis, ToolTip = name }); content.Children.Add(heading);
            var email = BoundAccountEmail(p); var network = AccountNetworkObservation(p);
            var subtitle = email.Length > 0 ? email : "手动登录环境";
            content.Children.Add(new TextBlock { Text = subtitle, Foreground = Muted, FontSize = 11, TextTrimming = TextTrimming.CharacterEllipsis, Margin = new Thickness(0, 5, 0, 0), ToolTip = email.Length > 0 ? "绑定的 Google 凭据；不代表已核验网页账号身份" : "可直接使用保存的浏览器会话，无需绑定账号" });
            var country = network.ValueKind == JsonValueKind.Object ? NetworkCountryName(network) : "出口未检测";
            var detail = RuntimeLabel(id) + " · " + country;
            content.Children.Add(new TextBlock { Text = detail, Foreground = Muted, FontSize = 11, TextTrimming = TextTrimming.CharacterEllipsis, Margin = new Thickness(0, 4, 0, 0), ToolTip = detail + "；" + LoginObservationLabel(p) });
            var row = new Grid(); row.ColumnDefinitions.Add(new ColumnDefinition()); row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(30) });
            var choose = Button("", () => Select(id)); choose.Content = content; choose.Margin = new Thickness(0); choose.Padding = new Thickness(10, 10, 2, 10); choose.Background = Brushes.Transparent; choose.BorderThickness = new Thickness(0); choose.HorizontalContentAlignment = HorizontalAlignment.Stretch; choose.IsEnabled = !closingProfiles.Contains(id); AutomationProperties.SetName(choose, "切换运行环境 " + name); row.Children.Add(choose);
            var more = IconButton("more", "", () => { }); more.Width = 28; more.Height = 32; more.Padding = new Thickness(5); more.Margin = new Thickness(0); more.Background = Brushes.Transparent; more.BorderThickness = new Thickness(0); more.ToolTip = "环境操作"; AutomationProperties.SetName(more, "环境操作 " + name); Grid.SetColumn(more, 1); row.Children.Add(more);
            var menu = new ContextMenu { Style = (Style)FindResource("AccountActionMenu"), Placement = System.Windows.Controls.Primitives.PlacementMode.Bottom, VerticalOffset = 3 };
            void Item(string label, string iconName, Action callback, bool enabled = true, bool danger = false) {
                var icon = (TextBlock)WorkbenchTheme.Icon(iconName, size: 14); var item = new MenuItem { Header = label, Icon = icon, Style = (Style)FindResource("AccountActionItem"), IsEnabled = enabled, Tag = danger ? "danger" : null };
                icon.SetBinding(TextBlock.ForegroundProperty, new System.Windows.Data.Binding("Foreground") { Source = item }); item.Click += (_, _) => callback(); menu.Items.Add(item);
            }
            if (!hosts.ContainsKey(id)) { Item("切到独立窗口", "external", () => FocusExternal(id), !busy); Item("嵌回工作台", "browser", async () => await Guard(() => Launch(id)), !busy); }
            Item("环境详情", "accounts", () => ShowAccountDetails(id)); Item("环境设置", "settings", () => EditProfile(id));
            menu.Items.Add(new Separator { Style = (Style)FindResource("AccountActionSeparator") });
            Item(BoundAccountEmail(p).Length > 0 ? "使用绑定 Google 账号登录" : "通过 Google 登录", "play", () => { if (BoundAccountEmail(p).Length > 0) StartBoundGoogleLogin(id); else ShowGoogleLogin(id); }, !busy);
            Item(checkingAccounts.Contains(id) ? "正在检测登录…" : "检测 Dola 登录", "shield", async () => await CheckAccount(id), !busy && !checkingAccounts.Contains(id));
            Item(checkingEnvironmentNetworks.Contains(id) ? "正在检测出口…" : "检测代理出口", "diagnostic", async () => await CheckEnvironmentNetwork(id), !busy && !checkingEnvironmentNetworks.Contains(id));
            Item("登录备份 / 创建恢复环境", "download", () => ShowSessionBackup(id), !busy);
            menu.Items.Add(new Separator { Style = (Style)FindResource("AccountActionSeparator") });
            Item("关闭浏览器", "close", async () => await Guard(() => CloseProfile(id)), !busy, true);
            more.ContextMenu = menu; more.Click += (_, _) => { menu.PlacementTarget = more; menu.IsOpen = true; };
            accountList.Children.Add(new Border { Child = row, MinHeight = 76, Margin = new Thickness(0, 0, 0, 6), CornerRadius = new CornerRadius(7), Background = id == selected ? Hex("#253344") : Hex("#1B211F"), BorderBrush = id == selected ? Accent : Line, BorderThickness = new Thickness(id == selected ? 2 : 0, 0, 0, 0) });
        }
        if (accountList.Children.Count == 0) accountList.Children.Add(new TextBlock { Text = RuntimeIds().Any() ? "没有匹配的运行环境" : "还没有运行中的浏览器\n从环境管理打开已有环境", Foreground = Muted, FontSize = 12, LineHeight = 23, TextAlignment = TextAlignment.Center, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(12, 25, 12, 0) });
    }

    async Task CheckAccount(string id)
    {
        if (!checkingAccounts.Add(id)) return; RenderAccounts();
        try {
            var result = await Api($"profiles/{id}/account-check", new { });
            var reason = Str(result, "reason");
            Log(AccountStateLabel(Str(result, "status")) + " · " + AccountReason(reason));
            await Refresh();
        } catch (Exception ex) { Log("登录检测未完成：" + ex.Message); }
        finally { checkingAccounts.Remove(id); RenderAccounts(); }
    }

    void EditAccount(string? id)
    {
        EditProfile(id);
    }

    Window CreateAccountDialog(string? id)
    {
        var p = id != null && profiles.TryGetValue(id, out var existing) ? existing : default;
        string Read(string key, string fallback = "") => p.ValueKind == JsonValueKind.Object ? Str(p, key, fallback) : fallback;
        var dialog = new Window { Owner = this, Title = id == null ? "新建浏览器环境" : "编辑环境资料", Width = 520, Height = id == null ? 790 : 610, MinHeight = 480, Background = Bg, Foreground = Text, WindowStartupLocation = WindowStartupLocation.CenterOwner }; WorkbenchTheme.Apply(dialog);
        var layout = new DockPanel { Margin = new Thickness(26) };
        var title = new StackPanel(); title.Children.Add(new TextBlock { Text = id == null ? "创建独立浏览器" : "环境资料", Foreground = Text, FontSize = 22, FontWeight = FontWeights.SemiBold });
        title.Children.Add(new TextBlock { Text = id == null ? "创建独立会话，再打开浏览器完成登录。" : "修改名称和备注，不会中断当前会话。", Foreground = Muted, FontSize = 12, Margin = new Thickness(0, 8, 0, 22) }); DockPanel.SetDock(title, Dock.Top); layout.Children.Add(title);
        var footer = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right, Margin = new Thickness(0, 18, 0, 0) }; DockPanel.SetDock(footer, Dock.Bottom); layout.Children.Add(footer);
        var fields = new StackPanel(); layout.Children.Add(new ScrollViewer { Content = fields, VerticalScrollBarVisibility = ScrollBarVisibility.Auto });
        TextBox Field(string key, string label, string fallback = "") { fields.Children.Add(Label(label, 12)); var input = new TextBox { Text = Read(key, fallback), Margin = new Thickness(0, 7, 0, 16) }; AutomationProperties.SetName(input, label); fields.Children.Add(input); return input; }
        var name = Field("name", "环境名称", "浏览器 " + (profiles.Count + 1));
        var email = Field("accountEmail", "旧版标识（仅备注，可选）"); email.ToolTip = "此字段只保留旧版资料。自动登录使用绑定账号的邮箱和密码。";
        var group = Field("accountGroup", "分组（可选）");
        var notes = Field("accountNotes", "备注（可选）"); notes.AcceptsReturn = true; notes.TextWrapping = TextWrapping.Wrap; notes.Height = 70;
        name.MaxLength = 120; email.MaxLength = 254; group.MaxLength = 60; notes.MaxLength = 1000;
        ComboBox? proxy = null, loginAccountPicker = null; FingerprintEditorBinding? fingerprint = null; Button? save = null;
        bool detecting = false, dialogClosed = false; int detectionVersion = 0;
        dialog.Closed += (_, _) => { dialogClosed = true; ++detectionVersion; };
        if (id == null) {
            loginAccountPicker = LoginAccountPicker(fields, null);
            fields.Children.Add(Label("网络代理", 12));
            proxy = new ComboBox { ItemsSource = new[] { new ProxyChoice("", "本机直连") }.Concat(proxyCatalog.Select(entry => new ProxyChoice(Str(entry, "id"), Str(entry, "name")))).ToList(), DisplayMemberPath = "Label", SelectedValuePath = "Id", SelectedValue = "", MinHeight = 36, Margin = new Thickness(0, 7, 0, 10), Foreground = Text, Background = Panel };
            AutomationProperties.SetName(proxy, "账号代理选择"); fields.Children.Add(proxy);
            var regionInfo = new TextBlock { Text = "检测出口后自动匹配语言与时区。", Foreground = Muted, FontSize = 11, TextWrapping = TextWrapping.Wrap, LineHeight = 20 }; AutomationProperties.SetName(regionInfo, "账号出口地区"); fields.Children.Add(regionInfo);
            var advancedPanel = new StackPanel { Margin = new Thickness(0, 14, 0, 0) };
            fingerprint = BuildFingerprintEditor(advancedPanel, Read);
            fields.Children.Add(new Expander { Header = "语言、时区与指纹设置", Content = advancedPanel, Foreground = Text, Margin = new Thickness(0, 16, 0, 0) });
            Button? detect = null;
            async Task Detect() {
                int version = ++detectionVersion; detecting = true; if (save != null) save.IsEnabled = false; detect!.IsEnabled = false;
                regionInfo.Text = "正在检测出口国家并匹配地区…"; regionInfo.Foreground = Accent;
                fingerprint.Recommend(JsonSerializer.SerializeToElement(new { }), false);
                try {
                    var result = await Api("proxy/test", new { proxyId = proxy.SelectedValue as string ?? "" });
                    if (dialogClosed || version != detectionVersion) return;
                    fingerprint.Recommend(result, true);
                    var draft = new Dictionary<string, object?>(); fingerprint.Fill(draft);
                    regionInfo.Text = Str(result, "reachable").Equals("true", StringComparison.OrdinalIgnoreCase) ? "出口 · " + NetworkCountryName(result) + " · " + Str(result, "city") + "\n" + draft["locale"] + " · " + draft["timezone"] : "检测失败，请检查代理后重试，或展开设置手动选择地区。";
                    regionInfo.Foreground = Muted;
                } catch (Exception ex) { if (!dialogClosed && version == detectionVersion) { regionInfo.Text = "检测未完成，可重试或手动配置地区。"; regionInfo.ToolTip = ex.Message; regionInfo.Foreground = Hex("#E8B578"); } }
                finally { if (!dialogClosed && version == detectionVersion) { detecting = false; detect.IsEnabled = true; if (save != null) save.IsEnabled = true; } }
            }
            detect = Button("检测出口 / 匹配地区", async () => await Detect()); detect.HorizontalAlignment = HorizontalAlignment.Left; detect.Margin = new Thickness(0, 10, 0, 0); fields.Children.Insert(fields.Children.Count - 1, detect);
            proxy.SelectionChanged += async (_, _) => await Detect();
        }
        var error = new TextBlock { Foreground = Hex("#ED9D9D"), TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 12, 0, 0) }; fields.Children.Add(error);
        save = Button(id == null ? "创建浏览器" : "保存环境资料", async () => {
            if (detecting || dialogClosed) return;
            if (name.Text.Trim().Length == 0) { error.Text = "请填写环境名称。"; name.Focus(); return; }
            save!.IsEnabled = false; error.Text = "";
            try {
                var body = new Dictionary<string, object?> { ["name"] = name.Text.Trim(), ["accountEmail"] = email.Text.Trim(), ["accountGroup"] = group.Text.Trim(), ["accountNotes"] = notes.Text.Trim() };
                if (id == null) { body["loginAccountId"] = loginAccountPicker?.SelectedValue as string ?? ""; body["proxyId"] = proxy?.SelectedValue as string ?? ""; body["startUrl"] = "https://www.dola.com/"; fingerprint!.Fill(body); }
                await Api(id == null ? "profiles" : $"profiles/{id}/account", body); dialog.Close(); await Refresh();
            } catch (Exception ex) { error.Text = ex.Message; save.IsEnabled = true; }
        }, true);
        footer.Children.Add(Button("取消", () => dialog.Close())); footer.Children.Add(save); dialog.Content = layout; return dialog;
    }
}
