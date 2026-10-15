using System.Text.Json;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Data;
using System.Windows.Media;

namespace CloakWorkbench;

public sealed partial class MainWindow
{
    readonly ContentControl loginAccountsWorkspace = new() { Visibility = Visibility.Collapsed };
    readonly Dictionary<string, JsonElement> loginAccounts = new();
    readonly TextBox loginAccountSearch = new();
    readonly TextBlock loginAccountCount = new(), loginAccountFeedback = new();
    readonly ContentControl loginAccountSide = new();
    DataGrid? loginAccountTable;
    TextBox? loginAccountPassword;
    string? loginAccountSelected, loginAccountSignature;
    readonly SemaphoreSlim loginAccountsLoadGate = new(1, 1);
    bool loginAccountSelecting, loginAccountSaving;

    sealed record LoginAccountRow(string Id, string Name, string Email, string Group, string Password, string Browser);

    async Task LoadLoginAccounts()
    {
        await loginAccountsLoadGate.WaitAsync();
        try {
            var data = await Api("login-accounts");
            loginAccounts.Clear();
            foreach (var item in data.GetProperty("accounts").EnumerateArray()) loginAccounts[Str(item, "id")] = item.Clone();
            RenderLoginAccounts();
        } finally { loginAccountsLoadGate.Release(); }
    }

    async void ShowLoginAccounts()
    {
        loginAccountsWorkspace.Content ??= CreateLoginAccountsPage();
        ShowManagementPage(loginAccountsWorkspace, "账号列表");
        try { await LoadLoginAccounts(); }
        catch { loginAccountFeedback.Text = "账号列表读取失败，请稍后重试。"; }
    }

    FrameworkElement CreateLoginAccountsPage()
    {
        var page = new DockPanel { Margin = new Thickness(28, 28, 28, 24) };
        var header = new DockPanel { Margin = new Thickness(0, 0, 0, 24) };
        var add = IconButton("plus", "添加账号", () => ShowLoginAccountEditor(null), true); add.Height = 40;
        DockPanel.SetDock(add, Dock.Right); header.Children.Add(add);
        var titles = new StackPanel(); titles.Children.Add(new TextBlock { Text = "账号列表", FontSize = 28, FontWeight = FontWeights.SemiBold, Foreground = Text });
        titles.Children.Add(new TextBlock { Text = "保存 Google 邮箱与密码，绑定浏览器后自动登录 Dola", FontSize = 12, Foreground = Muted, Margin = new Thickness(0, 8, 0, 0) });
        header.Children.Add(titles); DockPanel.SetDock(header, Dock.Top); page.Children.Add(header);
        var footer = new StackPanel { Margin = new Thickness(0, 14, 0, 0) };
        loginAccountFeedback.Foreground = Hex("#E8B578"); loginAccountFeedback.TextWrapping = TextWrapping.Wrap; loginAccountFeedback.FontSize = 12;
        AutomationProperties.SetName(loginAccountFeedback, "账号列表操作结果"); footer.Children.Add(loginAccountFeedback);
        footer.Children.Add(new TextBlock { Text = "Google 密码以明文保存在本机。账号绑定不会复制 Cookie，也不会替换网站当前登录身份。", Foreground = Muted, FontSize = 11, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 8, 0, 0) });
        DockPanel.SetDock(footer, Dock.Bottom); page.Children.Add(footer);
        var columns = new Grid(); columns.ColumnDefinitions.Add(new ColumnDefinition()); columns.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto }); page.Children.Add(columns);
        var list = new DockPanel();
        var toolbar = new DockPanel { Margin = new Thickness(18) };
        var migrate = Button("迁移已存密码", async () => {
            try {
                var result = await Api("login-accounts/migrate", new { });
                loginAccountFeedback.Text = $"已迁移并绑定 {Num(result, "imported")} 个账号，跳过 {Num(result, "skipped")} 个（已存在、浏览器打开或无法读取）。原数据保留。";
                await LoadLoginAccounts(); await Refresh();
            } catch { loginAccountFeedback.Text = "迁移未完成，请关闭对应浏览器后重试。原凭据继续保留。"; }
        }); migrate.ToolTip = "将按环境保存的 Google 账号导入账号列表；请先关闭对应浏览器。";
        DockPanel.SetDock(migrate, Dock.Right); toolbar.Children.Add(migrate);
        loginAccountSearch.Margin = new Thickness(0, 0, 14, 0); loginAccountSearch.Padding = new Thickness(12, 9, 12, 9); loginAccountSearch.ToolTip = "搜索名称、邮箱、分组、备注";
        var searchWrap = new Grid(); searchWrap.Children.Add(loginAccountSearch);
        var placeholder = new TextBlock { Text = "搜索账号、邮箱或分组…", Foreground = Muted, FontSize = 12, Margin = new Thickness(12, 0, 24, 0), VerticalAlignment = VerticalAlignment.Center, IsHitTestVisible = false, TextTrimming = TextTrimming.CharacterEllipsis }; searchWrap.Children.Add(placeholder);
        AutomationProperties.SetName(loginAccountSearch, "搜索登录账号"); loginAccountSearch.TextChanged += (_, _) => { placeholder.Visibility = loginAccountSearch.Text.Length == 0 ? Visibility.Visible : Visibility.Collapsed; RenderLoginAccounts(); }; toolbar.Children.Add(searchWrap);
        DockPanel.SetDock(toolbar, Dock.Top); list.Children.Add(toolbar);
        loginAccountCount.Foreground = Muted; loginAccountCount.FontSize = 11; loginAccountCount.Margin = new Thickness(18, 12, 18, 12);
        DockPanel.SetDock(loginAccountCount, Dock.Bottom); list.Children.Add(loginAccountCount);
        loginAccountTable = new DataGrid { AutoGenerateColumns = false, IsReadOnly = true, CanUserAddRows = false, CanUserDeleteRows = false,
            HeadersVisibility = DataGridHeadersVisibility.Column, RowHeaderWidth = 0, RowHeight = 68, ColumnHeaderHeight = 42,
            Background = Panel, Foreground = Text, BorderThickness = new Thickness(0), RowBackground = Panel, AlternatingRowBackground = Hex("#1D2521"),
            GridLinesVisibility = DataGridGridLinesVisibility.Horizontal, HorizontalGridLinesBrush = Line, SelectionMode = DataGridSelectionMode.Single };
        AutomationProperties.SetName(loginAccountTable, "登录账号列表");
        var cell = new Style(typeof(DataGridCell)); cell.Setters.Add(new Setter(Control.BorderThicknessProperty, new Thickness(0)));
        var selectedCell = new Trigger { Property = DataGridCell.IsSelectedProperty, Value = true };
        selectedCell.Setters.Add(new Setter(Control.BackgroundProperty, Hex("#254337"))); selectedCell.Setters.Add(new Setter(Control.ForegroundProperty, Text)); cell.Triggers.Add(selectedCell); loginAccountTable.CellStyle = cell;
        var headingStyle = new Style(typeof(System.Windows.Controls.Primitives.DataGridColumnHeader));
        headingStyle.Setters.Add(new Setter(Control.BackgroundProperty, Hex("#19201C"))); headingStyle.Setters.Add(new Setter(Control.ForegroundProperty, Muted));
        headingStyle.Setters.Add(new Setter(Control.BorderThicknessProperty, new Thickness(0))); headingStyle.Setters.Add(new Setter(Control.PaddingProperty, new Thickness(14, 0, 14, 0))); loginAccountTable.ColumnHeaderStyle = headingStyle;
        var identity = new FrameworkElementFactory(typeof(StackPanel)); identity.SetValue(FrameworkElement.VerticalAlignmentProperty, VerticalAlignment.Center); identity.SetValue(FrameworkElement.MarginProperty, new Thickness(14, 0, 14, 0));
        foreach (var key in new[] { "Name", "Email" }) {
            var text = new FrameworkElementFactory(typeof(TextBlock)); text.SetBinding(TextBlock.TextProperty, new Binding(key)); text.SetValue(TextBlock.TextTrimmingProperty, TextTrimming.CharacterEllipsis);
            text.SetValue(TextBlock.ForegroundProperty, key == "Name" ? Text : Muted); text.SetValue(TextBlock.FontSizeProperty, key == "Name" ? 13d : 11d);
            if (key == "Email") text.SetValue(FrameworkElement.MarginProperty, new Thickness(0, 5, 0, 0)); identity.AppendChild(text);
        }
        loginAccountTable.Columns.Add(new DataGridTemplateColumn { Header = "Google 账号", CellTemplate = new DataTemplate { VisualTree = identity }, Width = new DataGridLength(2, DataGridLengthUnitType.Star), MinWidth = 190 });
        var textStyle = new Style(typeof(TextBlock)); textStyle.Setters.Add(new Setter(FrameworkElement.VerticalAlignmentProperty, VerticalAlignment.Center)); textStyle.Setters.Add(new Setter(FrameworkElement.MarginProperty, new Thickness(14, 0, 14, 0))); textStyle.Setters.Add(new Setter(TextBlock.TextTrimmingProperty, TextTrimming.CharacterEllipsis));
        var passwordCell = new FrameworkElementFactory(typeof(TextBox));
        passwordCell.SetBinding(TextBox.TextProperty, new Binding("Password") { Mode = BindingMode.OneWay });
        passwordCell.SetBinding(FrameworkElement.ToolTipProperty, new Binding("Password"));
        passwordCell.SetValue(TextBox.IsReadOnlyProperty, true); passwordCell.SetValue(TextBox.TextWrappingProperty, TextWrapping.Wrap);
        passwordCell.SetValue(Control.BackgroundProperty, Brushes.Transparent); passwordCell.SetValue(Control.ForegroundProperty, Text);
        passwordCell.SetValue(Control.BorderThicknessProperty, new Thickness(0)); passwordCell.SetValue(Control.PaddingProperty, new Thickness(0));
        passwordCell.SetValue(FrameworkElement.MarginProperty, new Thickness(14, 0, 14, 0)); passwordCell.SetValue(FrameworkElement.VerticalAlignmentProperty, VerticalAlignment.Center);
        passwordCell.SetValue(AutomationProperties.NameProperty, "账号明文密码");
        loginAccountTable.Columns.Add(new DataGridTemplateColumn { Header = "密码", CellTemplate = new DataTemplate { VisualTree = passwordCell }, MinWidth = 160, Width = new DataGridLength(1.3, DataGridLengthUnitType.Star) });
        foreach (var (title, key, width) in new[] { ("绑定浏览器", "Browser", 150d), ("分组", "Group", 100d) })
            loginAccountTable.Columns.Add(new DataGridTextColumn { Header = title, Binding = new Binding(key), ElementStyle = textStyle, MinWidth = width, Width = new DataGridLength(1, DataGridLengthUnitType.Star) });
        loginAccountTable.SelectionChanged += (_, _) => { if (!loginAccountSelecting && !loginAccountSaving && loginAccountTable.SelectedItem is LoginAccountRow row) ShowLoginAccountEditor(row.Id); };
        list.Children.Add(loginAccountTable);
        columns.Children.Add(new Border { Background = Panel, BorderBrush = Line, BorderThickness = new Thickness(1), CornerRadius = new CornerRadius(10), ClipToBounds = true, Child = list });
        loginAccountSide.Width = 340; loginAccountSide.Margin = new Thickness(20, 0, 0, 0); Grid.SetColumn(loginAccountSide, 1); columns.Children.Add(loginAccountSide);
        var empty = LoginAccountPanel("账号与浏览器分开管理", "先添加邮箱和密码，再绑定已有浏览器。也可以在新建浏览器时直接选择账号。");
        empty.Children.Add(new TextBlock { Text = "一个账号绑定一个浏览器。独立 Profile 保存日常会话；掉登录时可重新执行自动登录。", Foreground = Muted, TextWrapping = TextWrapping.Wrap, LineHeight = 23 });
        page.SizeChanged += (_, _) => { loginAccountSide.Width = page.ActualWidth < 1050 ? 310 : 340; };
        Closed += (_, _) => loginAccountPassword?.Clear();
        return page;
    }

    StackPanel LoginAccountPanel(string title, string subtitle)
    {
        loginAccountPassword?.Clear(); loginAccountPassword = null;
        var body = new DockPanel();
        var head = new StackPanel { Margin = new Thickness(20) };
        head.Children.Add(new TextBlock { Text = title, Foreground = Text, FontSize = 19, FontWeight = FontWeights.SemiBold, TextWrapping = TextWrapping.Wrap });
        head.Children.Add(new TextBlock { Text = subtitle, Foreground = Muted, FontSize = 11, TextWrapping = TextWrapping.Wrap, LineHeight = 20, Margin = new Thickness(0, 9, 0, 0) });
        DockPanel.SetDock(head, Dock.Top); body.Children.Add(head);
        var panel = new StackPanel { Margin = new Thickness(20, 0, 20, 20) };
        body.Children.Add(new ScrollViewer { Content = panel, VerticalScrollBarVisibility = ScrollBarVisibility.Auto, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled });
        loginAccountSide.Content = new Border { Background = Panel, BorderBrush = Line, BorderThickness = new Thickness(1), CornerRadius = new CornerRadius(10), Child = body };
        return panel;
    }

    void LoginAccountFooter(StackPanel panel, UIElement footer)
    {
        var body = (DockPanel)((ScrollViewer)panel.Parent).Parent;
        var wrap = new Border { BorderBrush = Line, BorderThickness = new Thickness(0, 1, 0, 0), Padding = new Thickness(20, 14, 20, 18), Child = footer };
        DockPanel.SetDock(wrap, Dock.Bottom); body.Children.Insert(body.Children.Count - 1, wrap);
    }

    void RenderLoginAccounts()
    {
        if (loginAccountTable == null) return;
        string signature = JsonSerializer.Serialize(loginAccounts.Values) + loginAccountSearch.Text;
        if (signature == loginAccountSignature) return; loginAccountSignature = signature;
        var query = loginAccountSearch.Text.Trim();
        var rows = loginAccounts.Values.Where(a => query.Length == 0 || new[] { "name", "email", "group", "notes" }.Any(k => Str(a, k).Contains(query, StringComparison.OrdinalIgnoreCase)))
            .Select(a => new LoginAccountRow(Str(a, "id"), Str(a, "name"), Str(a, "email"), Str(a, "group", "未分组"),
                Str(a, "password"),
                a.GetProperty("bindings").GetArrayLength() > 0 ? string.Join("、", a.GetProperty("bindings").EnumerateArray().Select(b => Str(b, "name"))) : "未绑定")).ToArray();
        loginAccountSelecting = true;
        try { loginAccountTable.ItemsSource = rows; loginAccountTable.SelectedItem = rows.FirstOrDefault(r => r.Id == loginAccountSelected); }
        finally { loginAccountSelecting = false; }
        loginAccountCount.Text = $"{loginAccounts.Count} 个账号  ·  显示 {rows.Length} 个" + (loginAccounts.Count == 0 ? "  ·  点击右上角添加账号" : "");
    }

    void ShowLoginAccountEditor(string? id)
    {
        if (loginAccountSaving) return;
        loginAccountSelected = id;
        var existing = id != null && loginAccounts.TryGetValue(id, out var a) ? a : default;
        if (id != null && existing.ValueKind != JsonValueKind.Object) { loginAccountFeedback.Text = "账号资料尚未载入，请稍后重新选择。"; return; }
        string Read(string key) => existing.ValueKind == JsonValueKind.Object ? Str(existing, key) : "";
        var panel = LoginAccountPanel(id == null ? "添加 Google 账号" : "Google 账号资料", "填写 Google 邮箱和 Google 密码，用于通过 Google 登录 Dola。密码直接显示，可查看、复制和修改。");
        var name = CenterField(panel, "账号名称", Read("name")); name.MaxLength = 120;
        var email = CenterField(panel, "Google 邮箱", Read("email")); email.MaxLength = 254;
        var password = CenterField(panel, "Google 密码", Read("password")); password.MaxLength = 4096; loginAccountPassword = password; AutomationProperties.SetName(password, "账号密码");
        var group = CenterField(panel, "账号分组", Read("group")); group.MaxLength = 60;
        var notes = CenterField(panel, "账号备注", Read("notes")); notes.MaxLength = 1000; notes.AcceptsReturn = true; notes.Height = 70; notes.TextWrapping = TextWrapping.Wrap;
        var actions = new StackPanel();
        var error = new TextBlock { Foreground = Hex("#E8B578"), TextWrapping = TextWrapping.Wrap, FontSize = 11, Margin = new Thickness(0, 0, 0, 10) }; AutomationProperties.SetName(error, "账号保存结果"); actions.Children.Add(error);
        Button? save = null;
        save = Button("保存账号", async () => {
            if (loginAccountSaving) return; loginAccountSaving = true; save!.IsEnabled = false; error.Text = "正在保存…";
            try {
                var payload = new Dictionary<string, object?> { ["name"] = name.Text.Trim(), ["email"] = email.Text.Trim(), ["password"] = password.Text, ["group"] = group.Text.Trim(), ["notes"] = notes.Text.Trim() };
                if (id != null) payload["id"] = id;
                var saved = await Api("login-accounts", payload); password.Clear(); loginAccountSelected = Str(saved, "id");
                await LoadLoginAccounts(); await Refresh(); loginAccountFeedback.Text = "账号已保存。可以绑定浏览器并执行自动登录。";
                loginAccountSaving = false; ShowLoginAccountEditor(loginAccountSelected);
            } catch (Exception ex) { error.Text = ex.Message; loginAccountFeedback.Text = "账号操作未完成：" + ex.Message; }
            finally { loginAccountSaving = false; save.IsEnabled = true; }
        }, true); save.Margin = new Thickness(0); actions.Children.Add(save);
        if (id != null) {
            var bind = Button("绑定 / 更换浏览器", () => ShowLoginAccountBinding(id)); bind.Margin = new Thickness(0, 8, 0, 0); actions.Children.Add(bind);
            var bindings = existing.GetProperty("bindings");
            if (bindings.GetArrayLength() == 0) {
                var create = Button("为此账号创建环境", () => CreateEnvironmentForAccount(id)); create.Margin = new Thickness(0, 8, 0, 0); actions.Children.Add(create);
            }
            if (bindings.GetArrayLength() > 0) {
                var pid = Str(bindings[0], "id");
                var open = Button("打开绑定环境", async () => await Guard(async () => { await Launch(pid); ShowBrowserPage(); })); open.Margin = new Thickness(0, 8, 0, 0); actions.Children.Add(open);
                var login = Button("使用绑定账号自动登录", () => StartBoundGoogleLogin(pid)); login.Margin = new Thickness(0, 8, 0, 0); actions.Children.Add(login);
            }
            var delete = Button("删除账号…", () => ShowLoginAccountDelete(id)); delete.Foreground = Hex("#ED9D9D"); delete.Margin = new Thickness(0, 16, 0, 0); panel.Children.Add(delete);
        }
        LoginAccountFooter(panel, actions);
    }

    void ShowLoginAccountDelete(string id)
    {
        if (loginAccountSaving) return;
        var panel = LoginAccountPanel("删除账号", "仅删除账号列表中的邮箱、密码与资料。浏览器和网站登录会话继续保留。");
        panel.Children.Add(Label(Str(loginAccounts[id], "email"), 12));
        var error = new TextBlock { Foreground = Hex("#E8B578"), TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 14, 0, 0) }; panel.Children.Add(error);
        var actions = new StackPanel();
        var confirm = Button("确认删除账号", async () => {
            if (loginAccountSaving) return; loginAccountSaving = true;
            try { await Api($"login-accounts/{id}/delete", new { }); await LoadLoginAccounts(); loginAccountSaving = false; ShowLoginAccountEditor(null); loginAccountFeedback.Text = "账号已删除。"; }
            catch (Exception ex) { error.Text = ex.Message; }
            finally { loginAccountSaving = false; }
        }); confirm.Foreground = Hex("#ED9D9D"); actions.Children.Add(confirm);
        actions.Children.Add(Button("返回", () => ShowLoginAccountEditor(id))); LoginAccountFooter(panel, actions);
    }

    void ShowLoginAccountBinding(string id)
    {
        if (loginAccountSaving) return;
        var record = loginAccounts[id]; var bindings = record.GetProperty("bindings");
        var boundId = bindings.GetArrayLength() > 0 ? Str(bindings[0], "id") : "";
        var panel = LoginAccountPanel("绑定浏览器", Str(record, "email") + " · 一个账号使用一个独立环境");
        panel.Children.Add(new TextBlock { Text = "更改绑定前请关闭相关浏览器。绑定只决定自动登录使用哪个账号；已有网站会话不会自动退出或切换。更换账号建议创建新环境。", Foreground = Muted, FontSize = 12, LineHeight = 23, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 0, 0, 18) });
        var choices = new[] { new ProxyChoice("", "解除绑定 / 暂不绑定") }.Concat(profiles.Values.Where(p => Str(p, "loginAccountId").Length == 0 || Str(p, "loginAccountId") == id).Select(p => new ProxyChoice(Str(p, "id"), Str(p, "name") + (Str(p, "state") is "running" or "starting" ? " · 请先关闭" : "")))).ToArray();
        var picker = new ComboBox { ItemsSource = choices, DisplayMemberPath = "Label", SelectedValuePath = "Id", SelectedValue = boundId, Margin = new Thickness(0, 0, 0, 14) }; AutomationProperties.SetName(picker, "账号绑定浏览器"); panel.Children.Add(picker);
        var error = new TextBlock { Foreground = Hex("#E8B578"), TextWrapping = TextWrapping.Wrap, FontSize = 11 }; panel.Children.Add(error);
        var actions = new StackPanel();
        var save = Button("保存绑定", async () => {
            if (loginAccountSaving) return;
            string target = picker.SelectedValue as string ?? "";
            // Rebinding is two explicit operations; never silently steal a browser.
            if (boundId.Length > 0 && target.Length > 0 && target != boundId) { error.Text = "请先选择解除绑定并保存，再绑定新的浏览器。"; return; }
            if (target.Length == 0 && boundId.Length == 0) { ShowLoginAccountEditor(id); return; }
            loginAccountSaving = true;
            try { await Api($"profiles/{(target.Length > 0 ? target : boundId)}/login-account", new { accountId = target.Length > 0 ? id : "" }); await Refresh(); await LoadLoginAccounts(); loginAccountSaving = false; ShowLoginAccountEditor(id); loginAccountFeedback.Text = "绑定已保存；浏览器内的当前登录身份保持原样。"; }
            catch (Exception ex) { error.Text = ex.Message; }
            finally { loginAccountSaving = false; }
        }, true); actions.Children.Add(save); var back = Button("返回账号资料", () => ShowLoginAccountEditor(id)); back.Margin = new Thickness(0, 8, 0, 0); actions.Children.Add(back); LoginAccountFooter(panel, actions);
    }

    ComboBox LoginAccountPicker(StackPanel parent, string? pid)
    {
        parent.Children.Add(Label("绑定登录账号", 12));
        var current = pid != null && profiles.TryGetValue(pid, out var p) ? Str(p, "loginAccountId") : "";
        var choices = new[] { new ProxyChoice("", "暂不绑定账号") }.Concat(loginAccounts.Values.Where(a => Str(a, "id") == current || a.GetProperty("bindings").GetArrayLength() == 0).Select(a => new ProxyChoice(Str(a, "id"), Str(a, "email") + " · " + Str(a, "name")))).ToArray();
        var picker = new ComboBox { ItemsSource = choices, DisplayMemberPath = "Label", SelectedValuePath = "Id", SelectedValue = current, Margin = new Thickness(0, 7, 0, 12) }; AutomationProperties.SetName(picker, "浏览器绑定账号"); parent.Children.Add(picker);
        parent.Children.Add(new TextBlock { Text = "邮箱与密码在「账号列表」管理。此选择用于自动登录，不会更改网站当前会话。", Foreground = Muted, FontSize = 11, LineHeight = 19, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 0, 0, 14) });
        return picker;
    }

    async void StartBoundGoogleLogin(string pid)
    {
        try {
            await Refresh();
            if (!profiles.TryGetValue(pid, out var profile) || Str(profile, "loginAccountId").Length == 0) { Log("请先为此浏览器绑定账号。"); return; }
            await OpenGoogleLogin(pid);
            if (googleLoginViews.TryGetValue(pid, out var view)) await StartGoogleLogin(pid, view);
        } catch { Log("绑定账号登录未启动，请查看账号绑定与浏览器状态。"); }
    }

    async void ShowEnvironmentAccountBinding(string pid)
    {
        try {
            await LoadLoginAccounts(); await Refresh(); ShowAccountCenter();
            var panel = CenterPanel("绑定登录账号", "请先关闭浏览器。绑定决定自动登录使用的邮箱和密码，不会退出或替换当前网站会话。");
            centerDetailId = null;
            var picker = LoginAccountPicker(panel, pid);
            var result = new TextBlock { Foreground = Hex("#E8B578"), TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 12, 0, 0) };
            Button? save = null;
            save = Button("保存账号绑定", async () => {
                save!.IsEnabled = false;
                try { await Api($"profiles/{pid}/login-account", new { accountId = picker.SelectedValue as string ?? "" }); await Refresh(); await LoadLoginAccounts(); await LoadAccountDetails(pid); }
                catch (Exception ex) { result.Text = ex.Message; save.IsEnabled = true; }
            }, true); panel.Children.Add(save); panel.Children.Add(result);
            var manage = Button("前往账号列表", ShowLoginAccounts); manage.Margin = new Thickness(0, 12, 0, 0); panel.Children.Add(manage);
        } catch { Log("账号绑定页面读取失败，请稍后重试。"); }
    }
}
