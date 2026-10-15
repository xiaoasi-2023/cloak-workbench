using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace CloakWorkbench;

public sealed partial class MainWindow
{
    readonly ContentControl profileSettingsWorkspace = new() { Visibility = Visibility.Collapsed };
    string? profileSettingsAccountId;
    int profileSettingsOpenVersion;
    readonly Dictionary<string, FrameworkElement> profileSettingsDrafts = new();

    async void EditProfile(string? id)
    {
        int opening = ++profileSettingsOpenVersion;
        var initialAccountId = profileSettingsAccountId; profileSettingsAccountId = null;
        var previous = currentPage;
        try {
            await LoadProxyCatalog(); await LoadLoginAccounts();
            if (opening != profileSettingsOpenVersion || previous != currentPage || closing) return;
            string draftKey = id ?? "new:" + initialAccountId;
            if (profileSettingsDrafts.TryGetValue(draftKey, out var draft)) {
                profileSettingsWorkspace.Content = draft;
                ShowManagementPage(profileSettingsWorkspace, "批量管理");
                return;
            }
            FrameworkElement? editor = null;
            void Back() {
                profileSettingsDrafts.Remove(draftKey);
                if (!ReferenceEquals(profileSettingsWorkspace.Content, editor)) return;
                bool visible = profileSettingsWorkspace.IsVisible;
                profileSettingsWorkspace.Content = null;
                if (!visible) return;
                if (previous == "账号列表") ShowLoginAccounts();
                else if (id != null) ShowAccountDetails(id);
                else ShowAccountCenter();
            }
            editor = BuildProfileSettings(id, this, Back, true, initialAccountId);
            profileSettingsDrafts[draftKey] = editor; profileSettingsWorkspace.Content = editor;
            ShowManagementPage(profileSettingsWorkspace, "批量管理");
        } catch (Exception ex) { Log("环境设置读取失败：" + ex.GetBaseException().Message); }
    }
    void ShowInlineProfileSettings(string? id) => EditProfile(id);
    void CreateEnvironmentForAccount(string accountId) { profileSettingsAccountId = accountId; EditProfile(null); }

    Window CreateProfileDialog(string? id)
    {
        var dialog = new Window { Owner = this, Title = id == null ? "新建浏览器环境" : "环境配置", Width = 820, Height = 790, MinWidth = 710, MinHeight = 600,
            Background = Bg, Foreground = Text, WindowStartupLocation = WindowStartupLocation.CenterOwner }; WorkbenchTheme.Apply(dialog);
        dialog.Content = BuildProfileSettings(id, dialog, dialog.Close, false);
        return dialog;
    }

    FrameworkElement BuildProfileSettings(string? id, Window owner, Action closeEditor, bool inline, string? initialAccountId = null)
    {
        JsonElement p = id != null && profiles.TryGetValue(id, out var existing) ? existing : default;
        string Read(string key, string fallback) => p.ValueKind == JsonValueKind.Object ? Str(p, key, fallback) : fallback;
        var layout = new DockPanel(); var heading = new StackPanel { Margin = new Thickness(28, 22, 28, 18) };
        System.Windows.Automation.AutomationProperties.SetName(layout, "环境设置页面");
        heading.Children.Add(new TextBlock { Text = id == null ? "创建独立浏览器环境" : Read("name", "环境配置"), FontSize = 23, FontWeight = FontWeights.SemiBold, Foreground = Text });
        heading.Children.Add(new TextBlock { Text = "基本资料 → 绑定 Google 账号 → 选择代理 → 确认固定指纹。日常打开此环境即可继续原有会话。", Foreground = Muted, FontSize = 12, Margin = new Thickness(0, 7, 0, 0), TextWrapping = TextWrapping.Wrap });
        DockPanel.SetDock(heading, Dock.Top); layout.Children.Add(heading);
        var footer = new DockPanel { Margin = new Thickness(28, 14, 28, 20) }; DockPanel.SetDock(footer, Dock.Bottom); layout.Children.Add(footer);
        var stack = new StackPanel { Margin = new Thickness(28, 0, 28, 0), MaxWidth = 980, HorizontalAlignment = HorizontalAlignment.Stretch }; layout.Children.Add(new ScrollViewer { Content = stack, VerticalScrollBarVisibility = ScrollBarVisibility.Auto, HorizontalContentAlignment = HorizontalAlignment.Stretch });
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
        Field(basic, "name", "环境名称", "新环境");
        Field(basic, "accountGroup", "环境分组（可选）");
        var notes = Field(basic, "accountNotes", "环境备注（可选）"); notes.AcceptsReturn = true; notes.TextWrapping = TextWrapping.Wrap; notes.MinHeight = 66; notes.MaxLength = 1000;
        var identity = Section("Google 账号", "账号是邮箱和密码；绑定后用于自动登录，不代表当前网页已经登录该账号。未绑定也可以手动登录。");
        var loginAccountPicker = LoginAccountPicker(identity, id);
        if (id == null && initialAccountId != null) {
            loginAccountPicker.SelectedValue = initialAccountId;
            if (loginAccountPicker.SelectedValue as string != initialAccountId) throw new InvalidOperationException("此账号已绑定其他环境或已删除，请返回账号列表刷新后重试。");
        }
        var startup = new StackPanel(); Field(startup, "startUrl", "启动网址", "https://www.dola.com/");
        PairFields(startup, ("screenWidth", "初始窗口宽度", "1280"), ("screenHeight", "初始窗口高度", "800"));
        basic.Children.Add(new Expander { Header = "启动页面与窗口尺寸", IsExpanded = !inline, Content = startup, Foreground = Text, Margin = new Thickness(0, 4, 0, 0) });
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
        var manage = IconButton("settings", "管理代理 / 新建线路", async () => {
            var chosen = picker.SelectedValue as string ?? "";
            if (inline) {
                profileSettingsWorkspace.Content = CreateProxyManagementPage(this, async () => {
                    profileSettingsWorkspace.Content = layout;
                    try { await LoadProxyCatalog(); FillPicker(chosen); } catch (Exception ex) { routeHint.Text = ex.Message; }
                });
            } else {
                await ShowProxyManager(owner);
                try { await LoadProxyCatalog(); FillPicker(chosen); } catch (Exception ex) { routeHint.Text = ex.Message; }
            }
        }); manage.HorizontalAlignment = HorizontalAlignment.Left; manage.Margin = new Thickness(0, 0, 0, 14); proxy.Children.Insert(2, manage);
        Field(legacy, "proxy", "代理地址", "", "支持 HTTP、HTTPS 和 SOCKS5；认证信息与地址分别保存。");
        var credentials = Pair(legacy); var userColumn = new StackPanel(); var passwordColumn = new StackPanel(); Grid.SetColumn(passwordColumn, 2); credentials.Children.Add(userColumn); credentials.Children.Add(passwordColumn);
        Field(userColumn, "proxyUsername", "用户名（可选）"); passwordColumn.Children.Add(Label("密码（可选）", 12));
        var password = new PasswordBox { Margin = new Thickness(0, 5, 0, 0) }; System.Windows.Automation.AutomationProperties.SetName(password, "代理密码"); passwordColumn.Children.Add(password);
        bool hasSavedPassword = Read("proxyPasswordSet", "false").Equals("true", StringComparison.OrdinalIgnoreCase);
        passwordColumn.Children.Add(new TextBlock { Text = hasSavedPassword ? "已保存密码；留空保留原密码" : "密码保存在本机环境配置", Foreground = Muted, FontSize = 10, Margin = new Thickness(0, 5, 0, 0) });
        var clearPassword = new CheckBox { Content = "清除已保存的代理密码", Foreground = Muted, Margin = new Thickness(0, 10, 0, 10), Visibility = hasSavedPassword ? Visibility.Visible : Visibility.Collapsed }; legacy.Children.Add(clearPassword);
        clearPassword.Checked += (_, _) => password.IsEnabled = false; clearPassword.Unchecked += (_, _) => password.IsEnabled = true;
        var proxyResult = new StackPanel(); proxyResult.Children.Add(new TextBlock { Text = "检测后展示出口国家、城市和建议地区。", Foreground = Muted, FontSize = 12, TextWrapping = TextWrapping.Wrap });
        var resultCard = new Border { Child = proxyResult, Background = Hex("#161B19"), BorderBrush = Line, BorderThickness = new Thickness(1), CornerRadius = new CornerRadius(8), Padding = new Thickness(14), Margin = new Thickness(0, 9, 0, 0) };
        Dictionary<string, object?> ProxyDraft() {
            var choice = picker.SelectedValue as string ?? "";
            if (choice != "__legacy") return new() { ["proxyId"] = choice, ["proxy"] = "", ["proxyUsername"] = "", ["clearProxyPassword"] = true };
            var draft = new Dictionary<string, object?> { ["proxyId"] = "", ["proxy"] = inputs["proxy"].Text.Trim(), ["proxyUsername"] = inputs["proxyUsername"].Text.Trim() }; if (password.Password.Length > 0 && clearPassword.IsChecked != true) draft["proxyPassword"] = password.Password; if (clearPassword.IsChecked == true) draft["clearProxyPassword"] = true; return draft;
        }
        FingerprintEditorBinding? fingerprintBinding = null;
        Button? save = null;
        bool running = Read("state", "") is "running" or "starting";
        int networkVersion = 0; bool dialogClosed = false;
        layout.Unloaded += (_, _) => { dialogClosed = true; ++networkVersion; };
        Button? proxyTest = null;
        async Task DetectNetwork() {
            int version = ++networkVersion;
            fingerprintBinding?.Recommend(JsonSerializer.SerializeToElement(new { }), false);
            proxyTest!.IsEnabled = false; if (save != null) save.IsEnabled = false;
            proxyResult.Children.Clear();
            var pending = new DockPanel(); var spinner = ProxySpinner(); spinner.Margin = new Thickness(0, 0, 10, 0); DockPanel.SetDock(spinner, Dock.Left); pending.Children.Add(spinner);
            pending.Children.Add(new TextBlock { Text = "正在检测出口国家和城市…", Foreground = Accent, TextWrapping = TextWrapping.Wrap, VerticalAlignment = VerticalAlignment.Center }); proxyResult.Children.Add(pending);
            System.Windows.Automation.AutomationProperties.SetName(resultCard, "环境网络检测中");
            try { var draft = ProxyDraft(); var manual = (picker.SelectedValue as string) == "__legacy"; var useSaved = manual && id != null && hasSavedPassword && password.Password.Length == 0 && clearPassword.IsChecked != true && inputs["proxy"].Text.Trim() == Read("proxy", "") && inputs["proxyUsername"].Text.Trim() == Read("proxyUsername", "");
                if (manual && !useSaved && hasSavedPassword && password.Password.Length == 0 && clearPassword.IsChecked != true) { draft["id"] = id; draft["keepProxyPassword"] = true; }
                bool savedRoute = id != null && !manual && (picker.SelectedValue as string ?? "") == Read("proxyId", "") && !hasLegacy;
                var result = useSaved || savedRoute ? await Api($"profiles/{id}/proxy-test", new { }) : await Api("proxy/test", draft);
                if (dialogClosed || version != networkVersion) return;
                RenderNetworkResult(proxyResult, result);
                proxyResult.Children.Add(new TextBlock { Text = useSaved || savedRoute ? "本环境的实际出口观测；网站可用性仍需进入 Dola 确认。" : "未保存线路的检测快照；创建环境后请再次检测该环境的实际出口。", Foreground = Muted, FontSize = 12, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 8, 0, 0) });
                fingerprintBinding?.Recommend(result, !running);
                await LoadProxyCatalog(); RenderAccounts();
            } catch (Exception ex) {
                if (dialogClosed || version != networkVersion) return;
                proxyResult.Children.Clear(); proxyResult.Children.Add(new TextBlock { Text = "检测未完成，请检查代理或上游连接后重试。", ToolTip = ex.Message, Foreground = Hex("#E8B578"), TextWrapping = TextWrapping.Wrap });
            } finally {
                if (!dialogClosed && version == networkVersion) { proxyTest.IsEnabled = true; if (save != null) save.IsEnabled = true; System.Windows.Automation.AutomationProperties.SetName(resultCard, "环境网络检测结果"); }
            }
        }
        proxyTest = IconButton("shield", "检测出口国家与网络", async () => await DetectNetwork()); proxy.Children.Add(proxyTest); proxy.Children.Add(resultCard);
        var fingerprintSection = Section("浏览器指纹", "固定指纹随环境保存；只有明确随机生成才更换。匹配语言与时区不能改变网站对出口国家的限制。");
        var fingerprint = new StackPanel();
        fingerprintSection.Children.Add(new Expander { Header = "语言、时区与硬件设置", IsExpanded = !inline, Content = fingerprint, Foreground = Text });
        fingerprintBinding = BuildFingerprintEditor(fingerprint, Read);
        void InvalidateManualNetwork() {
            ++networkVersion; proxyTest.IsEnabled = true; if (save != null) save.IsEnabled = true;
            proxyResult.Children.Clear(); proxyResult.Children.Add(new TextBlock { Text = "代理认证或地址已修改，请重新检测。地区设置暂时保留。", Foreground = Muted, FontSize = 12, TextWrapping = TextWrapping.Wrap });
            fingerprintBinding.Recommend(JsonSerializer.SerializeToElement(new { }), false);
        }
        inputs["proxy"].TextChanged += (_, _) => InvalidateManualNetwork();
        inputs["proxyUsername"].TextChanged += (_, _) => InvalidateManualNetwork();
        password.PasswordChanged += (_, _) => InvalidateManualNetwork();
        clearPassword.Checked += (_, _) => InvalidateManualNetwork(); clearPassword.Unchecked += (_, _) => InvalidateManualNetwork();
        picker.SelectionChanged += async (_, _) => {
            if (fingerprintBinding == null || dialogClosed) return;
            var selectedProxy = picker.SelectedValue as string ?? "";
            if (selectedProxy.Length > 0 && selectedProxy != "__legacy") await DetectNetwork();
            else {
                ++networkVersion; proxyTest.IsEnabled = true; if (save != null) save.IsEnabled = true;
                proxyResult.Children.Clear(); proxyResult.Children.Add(new TextBlock { Text = selectedProxy.Length == 0 ? "当前使用本机直连。点击检测可查看本机出口国家。" : "当前使用手动代理。点击检测可查询真实出口国家。", Foreground = Muted, TextWrapping = TextWrapping.Wrap });
                fingerprintBinding.Recommend(JsonSerializer.SerializeToElement(new { }), false);
            }
        };
        layout.Loaded += (_, _) => {
            dialogClosed = false; proxyTest.IsEnabled = true; if (save != null) save.IsEnabled = true;
            running = id != null && profiles.TryGetValue(id, out var latest) && Str(latest, "state") is "running" or "starting";
            identity.IsEnabled = proxy.IsEnabled = fingerprint.IsEnabled = startup.IsEnabled = !running;
        };
        var error = new TextBlock { Foreground = Hex("#E8B578"), TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 0, 0, 12) }; stack.Children.Add(error);
        bool saving = false; Button? cancel = null;
        save = Button("保存环境", async () => {
            if (saving || dialogClosed) return;
            saving = true; save!.IsEnabled = false; if (cancel != null) cancel.IsEnabled = false; error.Text = "";
            try {
                if (inputs["name"].Text.Trim().Length == 0) throw new InvalidOperationException("请填写环境名称。");
                running = id != null && profiles.TryGetValue(id, out var latest) && Str(latest, "state") is "running" or "starting";
                if (running && id != null) {
                    await Api($"profiles/{id}/account", new { name = inputs["name"].Text.Trim(), accountGroup = inputs["accountGroup"].Text.Trim(), accountNotes = inputs["accountNotes"].Text.Trim() });
                    await Refresh(); closeEditor(); return;
                }
                int Number(string key) => int.TryParse(inputs[key].Text, out var n) ? n : throw new InvalidOperationException("种子和窗口尺寸必须填写整数。");
                var body = ProxyDraft(); body["id"] = id; foreach (var key in new[] { "name", "startUrl", "accountGroup", "accountNotes" }) body[key] = inputs[key].Text.Trim();
                body["loginAccountId"] = loginAccountPicker.SelectedValue as string ?? "";
                foreach (var key in new[] { "screenWidth", "screenHeight" }) body[key] = Number(key);
                fingerprintBinding.Fill(body);
                var saved = await Api("profiles", body); if (id != null) environmentNetworkChecks.Remove(id); await Refresh();
                bool visible = layout.IsVisible; closeEditor();
                if (inline && id == null) { if (visible) ShowAccountDetails(Str(saved, "id")); Log("环境已创建；可直接打开并继续登录。"); }
            } catch (Exception e) { error.Text = e.Message; }
            finally { saving = false; if (!dialogClosed) { save.IsEnabled = true; if (cancel != null) cancel.IsEnabled = true; } }
        }, true); DockPanel.SetDock(save, Dock.Right); footer.Children.Add(save);
        cancel = Button("取消", closeEditor); DockPanel.SetDock(cancel, Dock.Right); footer.Children.Add(cancel);
        if (running) { identity.IsEnabled = false; proxy.IsEnabled = false; fingerprint.IsEnabled = false; startup.IsEnabled = false; }
        footer.Children.Add(new TextBlock { Text = running ? "运行中可修改名称和备注；关闭后可改账号、代理与指纹。" : "保存不会立即登录；打开环境后使用原有会话。", Foreground = Muted, FontSize = 12, TextWrapping = TextWrapping.Wrap, VerticalAlignment = VerticalAlignment.Center });
        return layout;
    }
}
