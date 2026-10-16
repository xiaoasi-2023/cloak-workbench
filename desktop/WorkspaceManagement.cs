using System.IO;
using System.Text;
using System.Text.Json;
using System.ComponentModel;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Data;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Threading;
using Microsoft.Win32;

namespace CloakWorkbench;

public sealed partial class MainWindow
{
    readonly ContentControl enterpriseWorkspace = new() { Visibility = Visibility.Collapsed };
    readonly Dictionary<string, JsonElement> enterprises = new();
    readonly Dictionary<string, string> enterpriseJobs = new();
    readonly Dictionary<string, string> enterpriseOAuth = new();
    readonly Dictionary<string, string> enterpriseOAuthUrls = new();
    Action<string> openEnterpriseAuthorization = url => System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(url) { UseShellExecute = true });
    readonly ComboBox enterprisePicker = new() { MinWidth = 210, MaxWidth = 300 };
    readonly TextBox enterpriseSearch = new();
    readonly TextBlock enterpriseStatus = new(), enterpriseResult = new(), enterpriseCount = new();
    readonly ContentControl enterpriseSide = new() { Visibility = Visibility.Collapsed };
    readonly ProgressBar enterpriseProgress = new() { Height = 6, Visibility = Visibility.Collapsed };
    readonly Grid enterpriseTaskOverlay = new() { Visibility = Visibility.Collapsed };
    readonly Border enterpriseResultBanner = new();
    readonly TextBlock enterpriseTaskTitle = new(), enterpriseTaskPhase = new(), enterpriseTaskStats = new();
    readonly Dictionary<string, int> enterpriseRequests = new();
    Button? enterpriseTaskCopyAuth;
    Button? enterpriseCancel;
    readonly List<Button> enterpriseCommands = new();
    readonly DispatcherTimer enterpriseTimer = new() { Interval = TimeSpan.FromMilliseconds(800) };
    readonly List<JsonElement> enterpriseUsers = new();
    DataGrid? enterpriseTable;
    string? enterpriseSelected;
    bool enterpriseSelecting, enterprisePolling;
    int enterprisePanelVersion;
    Action? refreshEnterpriseEditor;
    string? enterpriseFeedbackKey;
    readonly HashSet<string> dismissedEnterpriseFeedback = new();

    void DismissEnterpriseFeedback()
    {
        CancelFeedbackDismissal(enterpriseResultBanner);
        if (enterpriseFeedbackKey != null) dismissedEnterpriseFeedback.Add(enterpriseFeedbackKey);
        enterpriseResult.Text = ""; enterpriseResultBanner.Visibility = Visibility.Collapsed;
    }

    void SetEnterpriseFeedback(string text, bool success = false, string? key = null)
    {
        if (key != null && dismissedEnterpriseFeedback.Contains(key)) {
            CancelFeedbackDismissal(enterpriseResultBanner); enterpriseFeedbackKey = key;
            enterpriseResult.Text = ""; enterpriseResultBanner.Visibility = Visibility.Collapsed; return;
        }
        bool repeated = key != null && key == enterpriseFeedbackKey && enterpriseResult.Text == text;
        enterpriseFeedbackKey = key; enterpriseResult.Text = text;
        if (!repeated) CancelFeedbackDismissal(enterpriseResultBanner);
        UpdateEnterpriseBusy();
        if (success && enterpriseResultBanner.Visibility == Visibility.Visible && !feedbackDismissTimers.ContainsKey(enterpriseResultBanner))
            DismissFeedbackLater(enterpriseResultBanner, () => {
                if (enterpriseResult.Text == text && enterpriseFeedbackKey == key) DismissEnterpriseFeedback();
            });
    }

    bool EnterpriseBusy(string wid) => enterpriseJobs.ContainsKey(wid) || enterpriseOAuth.ContainsKey(wid);
    async Task<JsonElement> EnterpriseApi(string wid, string path, object? body = null, string title = "正在读取员工目录")
    {
        enterpriseRequests[wid] = enterpriseRequests.GetValueOrDefault(wid) + 1;
        if ((enterpriseSelected ?? "") == wid) { enterpriseTaskTitle.Text = title; enterpriseTaskPhase.Text = "正在等待响应，请稍候…"; enterpriseTaskStats.Text = ""; }
        UpdateEnterpriseBusy();
        try { return await Api(path, body); }
        finally { if (--enterpriseRequests[wid] == 0) enterpriseRequests.Remove(wid); UpdateEnterpriseBusy(); }
    }
    string EnterpriseRoute(JsonElement record)
    {
        var id = Str(record, "proxyId");
        return id.Length == 0 ? "本机直连" : proxyCatalog.Where(p => Str(p, "id") == id).Select(p => Str(p, "name")).FirstOrDefault() ?? "已选代理（请检查代理管理）";
    }

    sealed record EnterpriseChoice(string Id, string Name);
    sealed class EnterpriseUserRow : INotifyPropertyChanged
    {
        bool selected;
        public bool Selected { get => selected; set { if (selected == value) return; selected = value; PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(Selected))); } }
        public event PropertyChangedEventHandler? PropertyChanged;
        public string Id { get; init; } = "";
        public string Name { get; init; } = "";
        public string Email { get; init; } = "";
        public string Password { get; init; } = "";
        public string State { get; init; } = "";
        public string Org { get; init; } = "";
        public string Department { get; init; } = "";
        public string WorkspaceId { get; init; } = "";
        public bool IsSuspended { get; init; }
        public bool ProtectedFromDelete { get; init; }
        public bool ConnectedAdmin { get; init; }
        bool canManage;
        public bool CanManage { get => canManage; set {
            if (canManage == value) return; canManage = value;
            foreach (var name in new[] { nameof(CanManage), nameof(CanDelete), nameof(CanChangeAccess) }) PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
        } }
        public bool CanDelete => CanManage && !ProtectedFromDelete;
        public bool CanChangeAccess => CanManage && !ConnectedAdmin;
    }
    List<EnterpriseUserRow> enterpriseRows = new();

    async void ShowEnterprises()
    {
        enterpriseWorkspace.Content ??= CreateEnterprisePage();
        ShowManagementPage(enterpriseWorkspace, "企业目录");
        try { await LoadProxyCatalog(); await LoadEnterprises(); }
        catch (Exception ex) { enterpriseResult.Text = ex.Message; }
    }

    async Task LoadEnterprises()
    {
        var data = await Api("workspaces");
        enterprises.Clear();
        foreach (var record in data.GetProperty("workspaces").EnumerateArray()) {
            string wid = Str(record, "id"); enterprises[wid] = record.Clone();
            if (record.TryGetProperty("lastJob", out var job) && job.ValueKind == JsonValueKind.Object && Str(job, "state") is "queued" or "running" or "cancelling")
                enterpriseJobs[wid] = Str(job, "id");
            if (record.TryGetProperty("authorization", out var auth) && auth.ValueKind == JsonValueKind.Object && Str(auth, "state") is "waiting" or "exchanging")
                enterpriseOAuth[wid] = Str(auth, "id");
        }
        enterpriseSelecting = true;
        try {
            enterprisePicker.ItemsSource = enterprises.Values.Select(r => new EnterpriseChoice(Str(r, "id"), Str(r, "name"))).ToArray();
            if (enterpriseSelected == null || !enterprises.ContainsKey(enterpriseSelected)) enterpriseSelected = enterprises.Keys.FirstOrDefault();
            enterprisePicker.SelectedValue = enterpriseSelected;
        } finally { enterpriseSelecting = false; }
        await LoadEnterpriseUsers();
    }

    async Task LoadEnterpriseUsers()
    {
        var wid = enterpriseSelected;
        if (wid == null) { enterpriseUsers.Clear(); RenderEnterpriseUsers(); UpdateEnterpriseBusy(); enterpriseProgress.Visibility = Visibility.Collapsed; enterpriseStatus.Text = "添加企业连接，集中维护员工 Google 账号"; return; }
        var data = await EnterpriseApi(wid, $"workspaces/{wid}/users");
        if (enterpriseSelected != wid) return;
        enterpriseUsers.Clear(); enterpriseUsers.AddRange(data.GetProperty("users").EnumerateArray().Select(u => u.Clone()));
        var record = data.GetProperty("workspace"); enterprises[wid] = record.Clone();
        var connection = Str(record, "status") == "connected" ? "已连接" : Str(record, "authType") == "oauth" && Str(record, "authorized").Equals("false", StringComparison.OrdinalIgnoreCase) ? "等待管理员授权" : Str(record, "status") == "failed" ? "连接失败，请检查网络线路后重新检测" : Str(record, "authType") == "oauth" ? "已授权，待检测连接" : "尚未检测";
        enterpriseStatus.Text = $"{connection}  ·  {Str(record, "adminEmail")}\n" +
            $"{string.Join(" / ", record.TryGetProperty("domains", out var domains) && domains.ValueKind == JsonValueKind.Array ? domains.EnumerateArray().Select(d => d.GetString()) : Array.Empty<string>())}  ·  网络：{EnterpriseRoute(record)}  ·  最近同步 {(DateTimeOffset.TryParse(Str(record, "syncedAt"), out var synced) ? synced.ToLocalTime().ToString("MM/dd HH:mm") : "尚未同步")}";
        if (!record.GetProperty("enabled").GetBoolean()) enterpriseStatus.Text = "连接已停用  ·  " + Str(record, "adminEmail");
        RenderEnterpriseUsers(); UpdateEnterpriseBusy();
        if (!enterpriseJobs.ContainsKey(wid) && !enterpriseOAuth.ContainsKey(wid)) {
            if (record.TryGetProperty("lastJob", out var last) && last.ValueKind == JsonValueKind.Object) RenderEnterpriseJob(last);
            else { enterpriseProgress.Visibility = Visibility.Collapsed; enterpriseResult.Text = "先检测企业连接，再同步员工或批量创建。"; }
        }
    }

    FrameworkElement CreateEnterprisePage()
    {
        var page = new DockPanel { Margin = new Thickness(28, 26, 28, 20) };
        var header = new DockPanel { Margin = new Thickness(0, 0, 0, 22) };
        var add = Button("＋ 添加企业", () => EnterpriseEditor(null), true); add.Height = 40; add.VerticalAlignment = VerticalAlignment.Top; DockPanel.SetDock(add, Dock.Right); header.Children.Add(add);
        var titles = new StackPanel(); titles.Children.Add(new TextBlock { Text = "企业目录", FontSize = 28, FontWeight = FontWeights.SemiBold, Foreground = Text });
        titles.Children.Add(new TextBlock { Text = "Google Workspace  /  员工创建、资料维护与账号同步", Foreground = Muted, FontSize = 12, Margin = new Thickness(0, 8, 0, 0) });
        header.Children.Add(titles); DockPanel.SetDock(header, Dock.Top); page.Children.Add(header);
        var connection = new DockPanel { Margin = new Thickness(0, 0, 0, 18) };
        var configure = Button("连接设置", async () => {
            try { await LoadProxyCatalog(); await LoadEnterprises(); if (enterpriseSelected != null) EnterpriseEditor(enterpriseSelected); }
            catch (Exception ex) { enterpriseResult.Text = ex.Message; }
        });
        DockPanel.SetDock(configure, Dock.Right); connection.Children.Add(configure);
        enterprisePicker.DisplayMemberPath = "Name"; enterprisePicker.SelectedValuePath = "Id"; enterprisePicker.Margin = new Thickness(0, 0, 18, 0);
        AutomationProperties.SetName(enterprisePicker, "企业选择"); DockPanel.SetDock(enterprisePicker, Dock.Left); connection.Children.Add(enterprisePicker);
        enterpriseStatus.Foreground = Muted; enterpriseStatus.FontSize = 11; enterpriseStatus.TextWrapping = TextWrapping.Wrap; enterpriseStatus.LineHeight = 19;
        connection.Children.Add(enterpriseStatus); DockPanel.SetDock(connection, Dock.Top); page.Children.Add(connection);
        enterprisePicker.SelectionChanged += async (_, _) => {
            if (enterpriseSelecting) return;
            enterpriseSelected = enterprisePicker.SelectedValue as string; enterprisePanelVersion++;
            enterpriseRows.Clear(); enterpriseUsers.Clear(); FilterEnterpriseUsers();
            enterpriseSide.Content = null; enterpriseSide.Visibility = Visibility.Collapsed; enterpriseResult.Text = "";
            try { await LoadEnterpriseUsers(); } catch (Exception ex) { enterpriseResult.Text = ex.Message; }
        };
        AutomationProperties.SetName(enterpriseProgress, "企业任务进度");
        var cancel = Button("取消企业任务", async () => {
            var wid = enterpriseSelected;
            if (wid != null && enterpriseOAuth.TryGetValue(wid, out var aid)) {
                try { await Api($"workspace-oauth/{aid}/cancel", new { }); }
                catch (Exception ex) { enterpriseResult.Text = ex.Message; enterpriseTaskPhase.Text = ex.Message; }
                return;
            }
            if (wid != null && enterpriseJobs.TryGetValue(wid, out var jid)) {
                try { await Api($"workspace-jobs/{jid}/cancel", new { }); enterpriseResult.Text = "正在取消；已发送的 Google 操作会先返回结果。"; enterpriseTaskPhase.Text = enterpriseResult.Text; }
                catch (Exception ex) { enterpriseResult.Text = ex.Message; enterpriseTaskPhase.Text = ex.Message; }
            }
        }); enterpriseCancel = cancel; cancel.Visibility = Visibility.Collapsed;
        enterpriseResult.Foreground = Hex("#C4D8CD"); enterpriseResult.FontSize = 12; enterpriseResult.TextWrapping = TextWrapping.Wrap; enterpriseResult.LineHeight = 20;
        AutomationProperties.SetName(enterpriseResult, "企业操作结果");
        enterpriseResultBanner.Background = Hex("#192720"); enterpriseResultBanner.BorderBrush = Hex("#304B3B"); enterpriseResultBanner.BorderThickness = new Thickness(1);
        enterpriseResultBanner.CornerRadius = new CornerRadius(9); enterpriseResultBanner.Padding = new Thickness(16, 12, 16, 12); enterpriseResultBanner.Margin = new Thickness(0, 0, 0, 14);
        AutomationProperties.SetName(enterpriseResultBanner, "企业操作结果提示");
        var resultLayout = new DockPanel();
        var dismissResult = IconButton("close", "", DismissEnterpriseFeedback); dismissResult.Padding = new Thickness(6); dismissResult.ToolTip = "关闭操作结果提示";
        dismissResult.Background = Brushes.Transparent; dismissResult.BorderThickness = new Thickness(0); dismissResult.VerticalAlignment = VerticalAlignment.Top;
        AutomationProperties.SetName(dismissResult, "关闭企业操作提示"); DockPanel.SetDock(dismissResult, Dock.Right); resultLayout.Children.Add(dismissResult);
        resultLayout.Children.Add(new ScrollViewer { Content = enterpriseResult, MaxHeight = 90, VerticalScrollBarVisibility = ScrollBarVisibility.Auto }); enterpriseResultBanner.Child = resultLayout;
        DockPanel.SetDock(enterpriseResultBanner, Dock.Top); page.Children.Add(enterpriseResultBanner);
        var hint = new TextBlock { Text = "密码为空表示本机未知；创建或重置成功后直接显示。同步目录保留已存密码。", Foreground = Muted, FontSize = 11, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 12, 0, 0) };
        DockPanel.SetDock(hint, Dock.Bottom); page.Children.Add(hint);
        var content = new Grid(); content.ColumnDefinitions.Add(new ColumnDefinition()); content.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto }); page.Children.Add(content);
        var list = new DockPanel();
        var toolbar = new WrapPanel { Margin = new Thickness(14, 12, 10, 10) };
        void Command(string label, Action action) { var b = Button(label, action); b.Margin = new Thickness(0, 0, 6, 6); enterpriseCommands.Add(b); toolbar.Children.Add(b); }
        Command("检测企业连接", () => _ = EnterpriseJob("check"));
        Command("Google 管理员授权", () => { if (enterpriseSelected != null) _ = StartEnterpriseOAuth(enterpriseSelected); });
        var copyAuth = Button("复制授权链接", () => {
            if (enterpriseSelected != null && enterpriseOAuthUrls.TryGetValue(enterpriseSelected, out var url)) {
                try { Clipboard.SetText(url); SetEnterpriseFeedback("授权链接已复制，请在可访问 Google 的浏览器中打开。", true); }
                catch (Exception ex) { enterpriseResult.Text = "复制失败：" + ex.Message; }
            } else enterpriseResult.Text = "请先点击 Google 管理员授权。";
        }); copyAuth.Margin = new Thickness(0, 0, 6, 6); toolbar.Children.Add(copyAuth);
        Command("同步员工", () => _ = EnterpriseJob("sync"));
        Command("批量创建员工", EnterpriseCreate);
        Command("批量修改资料", () => EnterpriseMaintenance("update"));
        Command("批量重置密码", () => EnterpriseMaintenance("reset"));
        Command("批量暂停", () => EnterpriseMaintenance("suspend"));
        Command("批量恢复", () => EnterpriseMaintenance("resume"));
        Command("批量删除", () => EnterpriseMaintenance("delete"));
        enterpriseCommands.Last().Foreground = Hex("#F0A5A5");
        DockPanel.SetDock(toolbar, Dock.Top); list.Children.Add(toolbar);
        var search = new DockPanel { Margin = new Thickness(14, 0, 14, 12) };
        var all = Button("全选 / 取消当前结果", () => { bool value = enterpriseTable!.Items.Cast<EnterpriseUserRow>().Any(r => !r.Selected); foreach (var row in enterpriseTable.Items.Cast<EnterpriseUserRow>()) row.Selected = value; });
        DockPanel.SetDock(all, Dock.Right); search.Children.Add(all);
        enterpriseSearch.ToolTip = "搜索员工邮箱、姓名或部门"; AutomationProperties.SetName(enterpriseSearch, "搜索企业员工"); enterpriseSearch.Margin = new Thickness(0, 0, 10, 0);
        enterpriseSearch.TextChanged += (_, _) => FilterEnterpriseUsers(true);
        var searchWrap = new Grid(); searchWrap.Children.Add(enterpriseSearch);
        var placeholder = new TextBlock { Text = "搜索员工邮箱、姓名或部门…", Foreground = Muted, FontSize = 11, VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(12, 0, 20, 0), IsHitTestVisible = false };
        enterpriseSearch.TextChanged += (_, _) => placeholder.Visibility = enterpriseSearch.Text.Length == 0 ? Visibility.Visible : Visibility.Collapsed;
        searchWrap.Children.Add(placeholder); search.Children.Add(searchWrap); DockPanel.SetDock(search, Dock.Top); list.Children.Add(search);
        enterpriseCount.Foreground = Muted; enterpriseCount.FontSize = 11; enterpriseCount.Margin = new Thickness(16, 12, 16, 12); DockPanel.SetDock(enterpriseCount, Dock.Bottom); list.Children.Add(enterpriseCount);
        enterpriseTable = new DataGrid { AutoGenerateColumns = false, CanUserAddRows = false, CanUserDeleteRows = false, RowHeaderWidth = 0,
            FrozenColumnCount = 2,
            HeadersVisibility = DataGridHeadersVisibility.Column, Background = Panel, Foreground = Text, BorderThickness = new Thickness(0),
            RowBackground = Panel, AlternatingRowBackground = Hex("#1D2521"), RowHeight = 52, ColumnHeaderHeight = 40,
            GridLinesVisibility = DataGridGridLinesVisibility.Horizontal, HorizontalGridLinesBrush = Line, SelectionMode = DataGridSelectionMode.Single };
        AutomationProperties.SetName(enterpriseTable, "企业员工列表");
        var headerStyle = new Style(typeof(System.Windows.Controls.Primitives.DataGridColumnHeader));
        headerStyle.Setters.Add(new Setter(Control.BackgroundProperty, Hex("#19201C"))); headerStyle.Setters.Add(new Setter(Control.ForegroundProperty, Muted)); headerStyle.Setters.Add(new Setter(Control.PaddingProperty, new Thickness(12, 0, 12, 0))); enterpriseTable.ColumnHeaderStyle = headerStyle;
        var cellStyle = new Style(typeof(DataGridCell)); cellStyle.Setters.Add(new Setter(Control.BorderThicknessProperty, new Thickness(0)));
        var selected = new Trigger { Property = DataGridCell.IsSelectedProperty, Value = true }; selected.Setters.Add(new Setter(Control.BackgroundProperty, Hex("#254337"))); selected.Setters.Add(new Setter(Control.ForegroundProperty, Text)); cellStyle.Triggers.Add(selected); enterpriseTable.CellStyle = cellStyle;
        var check = new FrameworkElementFactory(typeof(CheckBox)); check.SetBinding(System.Windows.Controls.Primitives.ToggleButton.IsCheckedProperty, new Binding("Selected") { Mode = BindingMode.TwoWay, UpdateSourceTrigger = UpdateSourceTrigger.PropertyChanged });
        check.SetValue(FrameworkElement.HorizontalAlignmentProperty, HorizontalAlignment.Center); check.SetValue(FrameworkElement.VerticalAlignmentProperty, VerticalAlignment.Center); check.SetValue(AutomationProperties.NameProperty, "选择企业员工");
        enterpriseTable.Columns.Add(new DataGridTemplateColumn { Header = "选", Width = 42, CellTemplate = new DataTemplate { VisualTree = check } });
        var text = new Style(typeof(TextBlock)); text.Setters.Add(new Setter(FrameworkElement.VerticalAlignmentProperty, VerticalAlignment.Center)); text.Setters.Add(new Setter(FrameworkElement.MarginProperty, new Thickness(12, 0, 12, 0))); text.Setters.Add(new Setter(TextBlock.TextTrimmingProperty, TextTrimming.CharacterEllipsis));
        foreach (var (title, key, width) in new[] { ("Google 邮箱", "Email", 320d), ("密码", "Password", 180d), ("员工姓名", "Name", 150d), ("状态", "State", 100d) }) {
            var style = new Style(typeof(TextBlock), text);
            style.Setters.Add(new Setter(FrameworkElement.ToolTipProperty, new Binding(key)));
            if (key == "Password") {
                style.Setters.Add(new Setter(TextBlock.FontFamilyProperty, new FontFamily("Consolas")));
            }
            enterpriseTable.Columns.Add(new DataGridTextColumn { Header = title, Binding = new Binding(key), IsReadOnly = true, MinWidth = width,
                Width = key switch { "Password" => DataGridLength.SizeToCells, "State" => new DataGridLength(100), "Email" => new DataGridLength(2.4, DataGridLengthUnitType.Star), _ => new DataGridLength(1, DataGridLengthUnitType.Star) },
                MaxWidth = key == "Password" ? 360 : double.PositiveInfinity, ElementStyle = style });
        }
        var rowActions = new FrameworkElementFactory(typeof(StackPanel)); rowActions.SetValue(StackPanel.OrientationProperty, Orientation.Horizontal);
        rowActions.SetValue(FrameworkElement.VerticalAlignmentProperty, VerticalAlignment.Center); rowActions.SetValue(FrameworkElement.MarginProperty, new Thickness(6, 0, 6, 0));
        foreach (var (label, operation) in new[] { ("修改", "update"), ("重置密码", "reset"), ("暂停", "suspend"), ("恢复", "resume"), ("删除", "delete") }) {
            var action = new FrameworkElementFactory(typeof(Button)); action.SetValue(ContentControl.ContentProperty, label);
            action.SetBinding(AutomationProperties.NameProperty, new Binding("Email") { StringFormat = label + "员工 {0}" });
            action.SetBinding(FrameworkElement.ToolTipProperty, new Binding("Email") { StringFormat = label + " · {0}" });
            action.SetBinding(UIElement.IsEnabledProperty, new Binding(operation == "delete" ? "CanDelete" : operation is "suspend" or "resume" ? "CanChangeAccess" : "CanManage"));
            var actionStyle = new Style(typeof(Button));
            actionStyle.Setters.Add(new Setter(Control.BackgroundProperty, Brushes.Transparent));
            actionStyle.Setters.Add(new Setter(Control.ForegroundProperty, operation == "delete" ? Hex("#F0A5A5") : Accent));
            actionStyle.Setters.Add(new Setter(Control.PaddingProperty, new Thickness(7, 4, 7, 4))); actionStyle.Setters.Add(new Setter(Control.FontSizeProperty, 11d));
            actionStyle.Setters.Add(new Setter(FrameworkElement.CursorProperty, System.Windows.Input.Cursors.Hand));
            var surface = new FrameworkElementFactory(typeof(Border)); surface.SetValue(Border.CornerRadiusProperty, new CornerRadius(5));
            surface.SetValue(Border.BackgroundProperty, new TemplateBindingExtension(Control.BackgroundProperty)); surface.SetValue(Border.PaddingProperty, new TemplateBindingExtension(Control.PaddingProperty));
            var caption = new FrameworkElementFactory(typeof(ContentPresenter)); caption.SetValue(FrameworkElement.HorizontalAlignmentProperty, HorizontalAlignment.Center); caption.SetValue(FrameworkElement.VerticalAlignmentProperty, VerticalAlignment.Center); surface.AppendChild(caption);
            actionStyle.Setters.Add(new Setter(Control.TemplateProperty, new ControlTemplate(typeof(Button)) { VisualTree = surface }));
            var hover = new Trigger { Property = UIElement.IsMouseOverProperty, Value = true }; hover.Setters.Add(new Setter(Control.BackgroundProperty, Hex("#2A3A30"))); actionStyle.Triggers.Add(hover);
            var disabled = new Trigger { Property = UIElement.IsEnabledProperty, Value = false }; disabled.Setters.Add(new Setter(UIElement.OpacityProperty, .35d)); actionStyle.Triggers.Add(disabled);
            if (operation is "suspend" or "resume") {
                actionStyle.Setters.Add(new Setter(UIElement.VisibilityProperty, Visibility.Collapsed));
                var state = new DataTrigger { Binding = new Binding("IsSuspended"), Value = operation == "resume" }; state.Setters.Add(new Setter(UIElement.VisibilityProperty, Visibility.Visible)); actionStyle.Triggers.Add(state);
            }
            action.SetValue(FrameworkElement.StyleProperty, actionStyle);
            action.AddHandler(System.Windows.Controls.Button.ClickEvent, new RoutedEventHandler((sender, e) => {
                if (sender is Button { DataContext: EnterpriseUserRow employee }) EnterpriseMaintenance(operation, employee);
                e.Handled = true;
            }));
            rowActions.AppendChild(action);
        }
        enterpriseTable.Columns.Add(new DataGridTemplateColumn { Header = "操作", Width = 240, MinWidth = 240, IsReadOnly = true, CellTemplate = new DataTemplate { VisualTree = rowActions } });
        list.Children.Add(enterpriseTable);
        content.Children.Add(new Border { Background = Panel, BorderBrush = Line, BorderThickness = new Thickness(1), CornerRadius = new CornerRadius(10), ClipToBounds = true, Child = list });
        enterpriseSide.Width = 350; enterpriseSide.Margin = new Thickness(18, 0, 0, 0); Grid.SetColumn(enterpriseSide, 1); content.Children.Add(enterpriseSide);
        enterpriseTaskOverlay.Background = Hex("#BD0D1310"); enterpriseTaskOverlay.Focusable = true;
        AutomationProperties.SetName(enterpriseTaskOverlay, "企业任务遮罩"); Grid.SetColumnSpan(enterpriseTaskOverlay, 2); System.Windows.Controls.Panel.SetZIndex(enterpriseTaskOverlay, 20);
        var taskBody = new StackPanel();
        taskBody.Children.Add(new TextBlock { Text = "GOOGLE WORKSPACE", Foreground = Accent, FontSize = 10, FontWeight = FontWeights.SemiBold, Margin = new Thickness(0, 0, 0, 14) });
        enterpriseTaskTitle.FontSize = 23; enterpriseTaskTitle.Foreground = Text; enterpriseTaskTitle.FontWeight = FontWeights.SemiBold; enterpriseTaskTitle.TextWrapping = TextWrapping.Wrap; taskBody.Children.Add(enterpriseTaskTitle);
        enterpriseTaskPhase.FontSize = 13; enterpriseTaskPhase.Foreground = Hex("#B9CBBF"); enterpriseTaskPhase.TextWrapping = TextWrapping.Wrap; enterpriseTaskPhase.LineHeight = 22; enterpriseTaskPhase.Margin = new Thickness(0, 12, 0, 22); taskBody.Children.Add(enterpriseTaskPhase);
        enterpriseProgress.Foreground = Accent; enterpriseProgress.Background = Hex("#31473B"); taskBody.Children.Add(enterpriseProgress);
        enterpriseTaskStats.Foreground = Muted; enterpriseTaskStats.FontSize = 12; enterpriseTaskStats.Margin = new Thickness(0, 12, 0, 20); taskBody.Children.Add(enterpriseTaskStats);
        var taskActions = new WrapPanel(); cancel.Margin = new Thickness(0, 0, 10, 0); taskActions.Children.Add(cancel);
        enterpriseTaskCopyAuth = Button("复制 Google 授权链接", () => {
            if (enterpriseSelected != null && enterpriseOAuthUrls.TryGetValue(enterpriseSelected, out var url))
                try { Clipboard.SetText(url); enterpriseTaskPhase.Text = "授权链接已复制，请在浏览器中打开并完成管理员授权。"; } catch (Exception ex) { enterpriseTaskPhase.Text = ex.Message; }
        }); taskActions.Children.Add(enterpriseTaskCopyAuth); taskBody.Children.Add(taskActions);
        taskBody.Children.Add(new TextBlock { Text = "可以切换企业或前往其他页面，任务会继续执行。", Foreground = Muted, FontSize = 11, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 18, 0, 0) });
        enterpriseTaskOverlay.Children.Add(new Border { Background = Hex("#1B2720"), BorderBrush = Hex("#3A5747"), BorderThickness = new Thickness(1), CornerRadius = new CornerRadius(16), Padding = new Thickness(30),
            HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center, Width = 500, MaxWidth = 510, Margin = new Thickness(20), Child = taskBody });
        content.Children.Add(enterpriseTaskOverlay);
        page.SizeChanged += (_, _) => enterpriseSide.Width = page.ActualWidth < 1100 ? 320 : 370;
        enterpriseTimer.Tick += async (_, _) => await PollEnterpriseJobs(); enterpriseTimer.Start(); Closed += (_, _) => enterpriseTimer.Stop();
        return page;
    }

    void RenderEnterpriseUsers()
    {
        var selectedIds = enterpriseRows.Where(r => r.Selected).Select(r => r.Id).ToHashSet();
        enterpriseRows = enterpriseUsers.Select(u => new EnterpriseUserRow { Id = Str(u, "id"), Name = Str(u, "name"), Email = Str(u, "email"), Password = Str(u, "password"),
            State = u.GetProperty("suspended").GetBoolean() ? "已暂停" : u.GetProperty("changePasswordAtNextLogin").GetBoolean() ? "下次登录需改密" : "正常",
            WorkspaceId = enterpriseSelected ?? "", IsSuspended = u.GetProperty("suspended").GetBoolean(),
            ConnectedAdmin = enterpriseSelected != null && enterprises.TryGetValue(enterpriseSelected, out var tenant) && string.Equals(Str(u, "email"), Str(tenant, "adminEmail"), StringComparison.OrdinalIgnoreCase),
            ProtectedFromDelete = (u.TryGetProperty("isAdmin", out var admin) && admin.GetBoolean()) || (u.TryGetProperty("isDelegatedAdmin", out var delegated) && delegated.GetBoolean()) ||
                (enterpriseSelected != null && enterprises.TryGetValue(enterpriseSelected, out var connection) && string.Equals(Str(u, "email"), Str(connection, "adminEmail"), StringComparison.OrdinalIgnoreCase)),
            Org = Str(u, "orgUnitPath"), Department = Str(u, "department"), Selected = selectedIds.Contains(Str(u, "id")) }).ToList();
        foreach (var row in enterpriseRows) row.PropertyChanged += (_, e) => { if (e.PropertyName == nameof(EnterpriseUserRow.Selected)) { UpdateEnterpriseSelection(); UpdateEnterpriseBusy(); } };
        FilterEnterpriseUsers();
    }
    void FilterEnterpriseUsers(bool clearHidden = false)
    {
        if (enterpriseTable == null) return;
        string query = enterpriseSearch.Text.Trim();
        var rows = enterpriseRows.Where(r => query.Length == 0 || new[] { r.Name, r.Email, r.Department }.Any(v => v.Contains(query, StringComparison.OrdinalIgnoreCase))).ToArray();
        if (clearHidden) { var visible = rows.Select(r => r.Id).ToHashSet(); foreach (var row in enterpriseRows.Where(r => !visible.Contains(r.Id))) row.Selected = false; }
        enterpriseTable.ItemsSource = rows; UpdateEnterpriseSelection(); UpdateEnterpriseBusy();
    }

    void UpdateEnterpriseSelection() => enterpriseCount.Text = enterpriseSelected == null ? "还没有企业连接 · 点击右上角添加企业" :
        $"目录 {enterpriseRows.Count} 人  ·  当前显示 {enterpriseTable?.Items.Count ?? 0} 人  ·  已勾选 {enterpriseRows.Count(r => r.Selected)} 人";

    StackPanel EnterprisePanel(string title, string subtitle, out StackPanel actions)
    {
        refreshEnterpriseEditor = null;
        enterprisePanelVersion++;
        var body = new DockPanel(); var head = new DockPanel { Margin = new Thickness(18) };
        var close = Button("×", () => { refreshEnterpriseEditor = null; enterprisePanelVersion++; enterpriseSide.Visibility = Visibility.Collapsed; enterpriseSide.Content = null; });
        AutomationProperties.SetName(close, "关闭企业侧栏"); DockPanel.SetDock(close, Dock.Right); head.Children.Add(close);
        head.Children.Add(new TextBlock { Text = title, FontSize = 18, Foreground = Text, FontWeight = FontWeights.SemiBold, TextWrapping = TextWrapping.Wrap }); DockPanel.SetDock(head, Dock.Top); body.Children.Add(head);
        actions = new StackPanel { Margin = new Thickness(18, 12, 18, 18) }; DockPanel.SetDock(actions, Dock.Bottom); body.Children.Add(actions);
        var panel = new StackPanel { Margin = new Thickness(18, 0, 18, 16) };
        panel.Children.Add(new TextBlock { Text = subtitle, Foreground = Muted, FontSize = 11, TextWrapping = TextWrapping.Wrap, LineHeight = 20, Margin = new Thickness(0, 0, 0, 14) });
        body.Children.Add(new ScrollViewer { Content = panel, VerticalScrollBarVisibility = ScrollBarVisibility.Auto });
        enterpriseSide.Content = new Border { Background = Panel, BorderBrush = Line, BorderThickness = new Thickness(1), CornerRadius = new CornerRadius(10), Child = body }; enterpriseSide.Visibility = Visibility.Visible;
        return panel;
    }

    void EnterpriseEditor(string? wid)
    {
        var record = wid != null ? enterprises[wid] : default;
        string Read(string key) => wid != null ? Str(record, key) : "";
        var panel = EnterprisePanel(wid == null ? "添加企业连接" : "企业连接设置", "推荐使用 Google 管理员授权。每家企业独立连接，授权成功后即可同步和维护员工。", out var actions);
        var name = CenterField(panel, "企业名称", Read("name")); var admin = CenterField(panel, "管理员邮箱", Read("adminEmail"));
        panel.Children.Add(Label("连接方式", 12));
        var method = new ComboBox { DisplayMemberPath = "Name", SelectedValuePath = "Id", Margin = new Thickness(0, 8, 0, 14),
            ItemsSource = new[] { new EnterpriseChoice("oauth", "管理员 OAuth（推荐）"), new EnterpriseChoice("service_account", "服务账号 JSON（高级）") } };
        AutomationProperties.SetName(method, "企业授权方式"); panel.Children.Add(method);
        method.SelectedValue = wid == null ? "oauth" : Read("authType");
        var keyLabel = new TextBlock { Foreground = Muted, TextWrapping = TextWrapping.Wrap, FontSize = 11, Margin = new Thickness(0, 0, 0, 8) }; panel.Children.Add(keyLabel);
        string? keyJson = null;
        bool IsOAuth() => method.SelectedValue as string == "oauth";
        var import = Button("导入 OAuth 客户端 JSON", () => {
            var picker = new OpenFileDialog { Filter = "Google JSON (*.json)|*.json", Title = IsOAuth() ? "选择桌面应用 OAuth 客户端 JSON" : "选择服务账号 JSON 密钥" };
            if (picker.ShowDialog(this) != true) return;
            try {
                if (new FileInfo(picker.FileName).Length > 32768) throw new InvalidOperationException("文件过大，请选择 Google 下载的 JSON 凭据。");
                var text = File.ReadAllText(picker.FileName, Encoding.UTF8); using var parsed = JsonDocument.Parse(text);
                if (IsOAuth() ? !parsed.RootElement.TryGetProperty("installed", out _) : !parsed.RootElement.TryGetProperty("private_key", out _))
                    throw new InvalidOperationException(IsOAuth() ? "请选择桌面应用 OAuth JSON，不是服务账号或网页客户端文件。" : "请选择服务账号 JSON。");
                keyJson = text; keyLabel.Text = "已选择：" + Path.GetFileName(picker.FileName);
            } catch (Exception ex) { keyJson = null; keyLabel.Text = ex.Message; }
        }); panel.Children.Add(import);
        var proxy = new ComboBox { Margin = new Thickness(0, 8, 0, 14), DisplayMemberPath = "Name", SelectedValuePath = "Id" };
        proxy.ItemsSource = new[] { new EnterpriseChoice("", "本机直连（需要可访问 Google）") }.Concat(proxyCatalog.Select(p => new EnterpriseChoice(Str(p, "id"), Str(p, "name")))).ToArray();
        proxy.SelectedValue = Read("proxyId"); panel.Children.Add(Label("管理 API 网络线路", 12)); panel.Children.Add(proxy); AutomationProperties.SetName(proxy, "企业管理代理");
        var enabled = new CheckBox { Content = "启用此企业连接", IsChecked = wid == null || record.GetProperty("enabled").GetBoolean(), Foreground = Text, Margin = new Thickness(0, 0, 0, 12) }; panel.Children.Add(enabled);
        var instructions = new TextBox { IsReadOnly = true, TextWrapping = TextWrapping.Wrap, AcceptsReturn = true, MinHeight = 140, Margin = new Thickness(0, 8, 0, 0) }; panel.Children.Add(instructions);
        var feedback = new TextBlock { Foreground = Hex("#E8B578"), TextWrapping = TextWrapping.Wrap, FontSize = 11 }; actions.Children.Add(feedback);
        bool saving = false;
        Button? save = null; save = Button("保存企业连接", async () => {
            if (!save!.IsEnabled) return; saving = true; save.IsEnabled = false; panel.IsEnabled = false; int version = enterprisePanelVersion;
            feedback.Text = "正在保存…";
            try {
                bool oauth = IsOAuth();
                var payload = new Dictionary<string, object?> { ["name"] = name.Text.Trim(), ["adminEmail"] = admin.Text.Trim(), ["proxyId"] = proxy.SelectedValue as string ?? "", ["enabled"] = enabled.IsChecked == true, ["authType"] = oauth ? "oauth" : "service_account" };
                if (wid != null) { payload["id"] = wid; payload["expectedRevision"] = record.GetProperty("revision").GetInt32(); }
                if (keyJson != null) payload[oauth ? "oauthClientJson" : "keyJson"] = keyJson;
                var saved = await EnterpriseApi(wid ?? enterpriseSelected ?? "", "workspaces", payload, "正在保存企业连接"); keyJson = null;
                if (version == enterprisePanelVersion) {
                    enterpriseSelected = Str(saved, "id"); await LoadEnterprises(); EnterpriseEditor(enterpriseSelected);
                    SetEnterpriseFeedback(oauth ? saved.GetProperty("authorized").GetBoolean() ? "企业已保存，现有授权已保留。可检测连接或同步员工。" : "企业已保存。完成 Google 管理员授权后可同步员工。" : "企业已保存，请配置域范围委托后检测连接。", true);
                    if (oauth && enabled.IsChecked == true && !saved.GetProperty("authorized").GetBoolean()) await StartEnterpriseOAuth(enterpriseSelected);
                }
            } catch (Exception ex) { feedback.Text = ex.Message; try { await LoadEnterprises(); } catch { } }
            finally { saving = false; UpdateEnterpriseBusy(); }
        }, true); actions.Children.Add(save);
        void RefreshMethod() {
            bool oauth = IsOAuth(), existing = wid != null && Read("authType") == (oauth ? "oauth" : "service_account");
            keyJson = null; keyLabel.Text = existing ? "客户端已保存，留空继续使用" : oauth ? "导入 Google Cloud 下载的桌面应用客户端 JSON" : "尚未选择服务账号密钥";
            import.Content = oauth ? "导入 OAuth 客户端 JSON" : "导入服务账号 JSON";
            instructions.Text = oauth ? "授权流程\n\n1. 导入桌面应用 OAuth JSON 并保存\n2. 系统浏览器中选择此企业管理员并授权\n3. 返回这里同步员工\n\nGoogle 测试模式需添加管理员为测试用户，授权通常 7 天后需重连。系统浏览器使用自身网络；下方代理用于令牌与管理 API。"
                : "Google Admin → 安全性 → API 控制 → 全网域授权\n\n客户端 ID：" + (existing ? Read("clientId") : "保存后显示") + "\n\n作用域（两项，逗号分隔）：\nhttps://www.googleapis.com/auth/admin.directory.user,https://www.googleapis.com/auth/admin.directory.domain.readonly";
        }
        method.SelectionChanged += (_, _) => RefreshMethod(); RefreshMethod();
        Button? reauthorize = null;
        if (wid != null && Read("authType") == "oauth") {
            reauthorize = Button("重新授权已保存的连接", () => _ = StartEnterpriseOAuth(wid)); actions.Children.Add(reauthorize);
            actions.Children.Add(new TextBlock { Text = "修改连接信息后请先保存，再重新授权。", Foreground = Muted, FontSize = 11, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 8, 0, 0) });
        }
        var reload = Button("重新载入最新连接设置", async () => {
            try { await LoadProxyCatalog(); await LoadEnterprises(); if (wid != null) EnterpriseEditor(wid); }
            catch (Exception ex) { feedback.Text = ex.Message; }
        }); reload.Visibility = Visibility.Collapsed; actions.Children.Add(reload);
        refreshEnterpriseEditor = () => {
            bool busy = wid != null && EnterpriseBusy(wid);
            bool stale = wid != null && enterprises.TryGetValue(wid, out var current) && Num(current, "revision") != Num(record, "revision");
            panel.IsEnabled = !saving && !busy && !stale; save.IsEnabled = !saving && !busy && !stale;
            if (reauthorize != null) reauthorize.IsEnabled = !saving && !busy && !stale;
            reload.Visibility = stale ? Visibility.Visible : Visibility.Collapsed; reload.IsEnabled = !saving && !busy;
            if (!saving) feedback.Text = busy ? "此企业任务正在执行，完成后可修改连接设置。" : stale ? "连接配置已更新。请重新载入，避免覆盖最新网络线路。" : feedback.Text.StartsWith("此企业任务正在执行") ? "" : feedback.Text;
        };
        UpdateEnterpriseBusy();
    }

    bool EnterpriseCanEdit()
    {
        if (enterpriseSelected == null) { enterpriseResult.Text = "请先添加并选择企业连接。"; return false; }
        if (EnterpriseBusy(enterpriseSelected)) { enterpriseResult.Text = "此企业任务尚未结束。"; return false; }
        if (Str(enterprises[enterpriseSelected], "status") != "connected") { enterpriseResult.Text = "请先检测企业连接。"; return false; }
        return true;
    }

    void EnterpriseCreate()
    {
        if (!EnterpriseCanEdit()) return;
        string wid = enterpriseSelected!;
        var panel = EnterprisePanel("批量创建员工", enterprises[wid].GetProperty("name").GetString() + " · 预览后再创建，每批最多 200 人。", out var actions);
        panel.Children.Add(new TextBlock { Text = "Google 试用通常限 10 人，Business 系列最多 300 人；实际可创建数量以管理后台的可用许可证为准。", Foreground = Muted, FontSize = 11, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 0, 0, 14) });
        panel.Children.Add(Label("生成方式", 12));
        var mode = new ComboBox { ItemsSource = new[] { "人名＋4位数字（推荐）", "前缀＋递增序号", "CSV 导入" }, SelectedIndex = 0, Margin = new Thickness(0, 8, 0, 12) };
        AutomationProperties.SetName(mode, "员工生成方式"); panel.Children.Add(mode);
        var hint = new TextBlock { Foreground = Accent, FontSize = 11, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 0, 0, 14) }; panel.Children.Add(hint);
        var generated = new StackPanel(); panel.Children.Add(generated);
        var domains = enterprises[wid].GetProperty("domains").EnumerateArray().Select(d => d.GetString()).ToArray();
        generated.Children.Add(Label("邮箱域名", 12)); var domain = new ComboBox { ItemsSource = domains, SelectedIndex = 0, Margin = new Thickness(0, 8, 0, 12) }; generated.Children.Add(domain); AutomationProperties.SetName(domain, "员工邮箱域名");
        var count = CenterField(generated, "创建数量", "5");
        var sequence = new StackPanel(); generated.Children.Add(sequence);
        var prefix = CenterField(sequence, "账号前缀", "employee"); var start = CenterField(sequence, "起始序号", "1"); var last = CenterField(sequence, "姓氏", "User");
        var password = CenterField(generated, "初始密码", ""); var ou = CenterField(generated, "组织单位路径", "/");
        var imported = new StackPanel(); panel.Children.Add(imported);
        var csv = CenterField(imported, "CSV：email,firstName,lastName,password,orgUnitPath", ""); csv.AcceptsReturn = true; csv.TextWrapping = TextWrapping.Wrap; csv.Height = 150;
        imported.Children.Add(Button("读取 CSV 文件", () => { var picker = new OpenFileDialog { Filter = "CSV (*.csv)|*.csv" }; if (picker.ShowDialog(this) != true) return; try { if (new FileInfo(picker.FileName).Length > 200000) throw new InvalidOperationException("CSV 超过 200 KB。"); csv.Text = File.ReadAllText(picker.FileName, Encoding.UTF8); } catch (Exception ex) { enterpriseResult.Text = ex.Message; } }));
        var force = new CheckBox { Content = "要求员工首次登录修改密码", Foreground = Text, Margin = new Thickness(0, 3, 0, 14) }; panel.Children.Add(force);
        void UpdateMode() {
            generated.Visibility = mode.SelectedIndex == 2 ? Visibility.Collapsed : Visibility.Visible;
            imported.Visibility = mode.SelectedIndex == 2 ? Visibility.Visible : Visibility.Collapsed;
            sequence.Visibility = mode.SelectedIndex == 1 ? Visibility.Visible : Visibility.Collapsed;
            hint.Text = mode.SelectedIndex switch {
                0 => "自动生成英文姓名和邮箱，例如 danielcarter2153@企业域名。预览后名单固定，确认创建不会再次随机。",
                1 => "使用自定义前缀和递增序号，例如 employee001@企业域名。",
                _ => "使用 CSV 中每行的邮箱、姓名和密码；自动生成规则不参与导入。" };
        }
        mode.SelectionChanged += (_, _) => UpdateMode(); UpdateMode();
        var feedback = new TextBlock { Foreground = Hex("#E8B578"), TextWrapping = TextWrapping.Wrap }; actions.Children.Add(feedback);
        Button? preview = null; preview = Button("预览待创建员工", async () => {
            var source = enterpriseSide.Content; int version = enterprisePanelVersion;
            preview!.IsEnabled = false; panel.IsEnabled = false;
            try {
                object payload;
                if (mode.SelectedIndex == 2) { if (string.IsNullOrWhiteSpace(csv.Text)) throw new InvalidOperationException("请填写或读取 CSV。"); payload = new { operation = "create", csv = csv.Text, changePasswordAtNextLogin = force.IsChecked == true }; }
                else {
                    if (!int.TryParse(count.Text, out int n)) throw new InvalidOperationException("创建数量应为整数。");
                    int first = 1;
                    if (mode.SelectedIndex == 1 && !int.TryParse(start.Text, out first)) throw new InvalidOperationException("起始序号应为整数。");
                    payload = new { operation = "create", addressStyle = mode.SelectedIndex == 0 ? "human" : "sequence", count = n, start = first, prefix = prefix.Text, domain = domain.SelectedItem as string, lastName = last.Text, password = password.Text, orgUnitPath = ou.Text, changePasswordAtNextLogin = force.IsChecked == true };
                }
                var data = await EnterpriseApi(wid, $"workspaces/{wid}/preview", payload, "正在生成创建预览");
                if (wid == enterpriseSelected && version == enterprisePanelVersion) EnterprisePreview(wid, data, source!);
            } catch (Exception ex) { feedback.Text = ex.Message; } finally { preview.IsEnabled = true; panel.IsEnabled = true; }
        }, true); actions.Children.Add(preview);
    }

    void EnterpriseMaintenance(string operation, EnterpriseUserRow? employee = null)
    {
        if (!EnterpriseCanEdit()) return;
        string wid = enterpriseSelected!;
        if (employee != null && (employee.WorkspaceId != wid || !enterpriseRows.Contains(employee) || !employee.CanManage ||
            (operation == "delete" && !employee.CanDelete) || (operation is "suspend" or "resume" && !employee.CanChangeAccess))) return;
        var ids = employee != null ? new[] { employee.Id } : enterpriseRows.Where(r => r.Selected).Select(r => r.Id).ToArray();
        if (ids.Length == 0 || ids.Length > 200) { enterpriseResult.Text = "请勾选 1–200 位员工。"; return; }
        string title = operation switch { "update" => "修改员工资料", "reset" => "重置员工密码", "suspend" => "暂停员工", "delete" => "删除员工", _ => "恢复员工" };
        var target = employee != null ? employee.Email : $"已勾选 {ids.Length} 人";
        var panel = EnterprisePanel(title, $"{Str(enterprises[wid], "name")} · {target}\n修改 Google 真实账号，执行前请核对预览。", out var actions);
        var fields = new Dictionary<string, (CheckBox Enabled, TextBox Input)>();
        TextBox? password = null; CheckBox? force = null;
        if (operation == "reset") {
            password = CenterField(panel, "新密码", ""); force = new CheckBox { Content = "要求下次登录修改密码", Foreground = Text }; panel.Children.Add(force);
            panel.Children.Add(new TextBlock { Text = "仅 Google 确认重置成功后更新本机密码。重置可能影响现有 Google 登录会话。", Foreground = Muted, FontSize = 11, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 14, 0, 0) });
        } else if (operation == "update") {
            var existing = ids.Length == 1 ? enterpriseUsers.Single(u => Str(u, "id") == ids[0]) : default;
            panel.Children.Add(new TextBlock { Text = ids.Length == 1 ? "已回填当前资料。修改内容会自动勾选对应字段；取消勾选即可保留原值。" : "填写内容会自动勾选对应字段，并应用到所有选中员工；未勾选的字段保持不变。", Foreground = Muted, TextWrapping = TextWrapping.Wrap, FontSize = 11 });
            foreach (var (label, key) in new[] { ("名字", "firstName"), ("姓氏", "lastName"), ("部门", "department"), ("职位", "title"), ("辅助邮箱", "recoveryEmail"), ("组织单位路径", "orgUnitPath") }) {
                var check = new CheckBox { Content = "修改" + label, Foreground = Text, Margin = new Thickness(0, 8, 0, 0) }; panel.Children.Add(check);
                string initial = ids.Length == 1 ? Str(existing, key) : "";
                var input = CenterField(panel, label, initial); fields[key] = (check, input);
                input.TextChanged += (_, _) => check.IsChecked = input.Text != initial;
            }
        } else panel.Children.Add(new TextBlock { Text = operation == "delete" ? "将删除企业中的真实 Google 账号，员工会失去邮箱、文件等服务访问权。本功能不迁移数据，请先在 Google 后台完成需要的交接。\n\n本机账号、已存密码和浏览器保留，并停止该员工的自动登录。管理员不能在此删除。删除后许可证可供其他员工使用，年付费用不自动减少。" : operation == "suspend" ? "暂停会限制员工登录 Google。此操作不会删除员工，也不会删除本机浏览器环境。" : "恢复 Google 员工的可登录状态。", Foreground = operation == "delete" ? Hex("#F0A5A5") : Muted, TextWrapping = TextWrapping.Wrap, LineHeight = 22 });
        var feedback = new TextBlock { Foreground = Hex("#E8B578"), TextWrapping = TextWrapping.Wrap }; actions.Children.Add(feedback);
        Button? preview = null; preview = Button("预览员工变更", async () => {
            var source = enterpriseSide.Content; int version = enterprisePanelVersion; preview!.IsEnabled = false; panel.IsEnabled = false;
            try {
                var patch = fields.Where(f => f.Value.Enabled.IsChecked == true).ToDictionary(f => f.Key, f => f.Value.Input.Text);
                var data = await EnterpriseApi(wid, $"workspaces/{wid}/preview", new { operation, userIds = ids, password = password?.Text ?? "", patch, changePasswordAtNextLogin = force?.IsChecked == true }, "正在核对员工变更");
                if (wid == enterpriseSelected && version == enterprisePanelVersion) EnterprisePreview(wid, data, source!);
            } catch (Exception ex) { feedback.Text = ex.Message; } finally { preview.IsEnabled = true; panel.IsEnabled = true; }
        }, true); actions.Children.Add(preview);
    }

    void EnterprisePreview(string wid, JsonElement data, object source)
    {
        var names = new Dictionary<string, string> { ["create"] = "创建员工", ["update"] = "修改资料", ["reset"] = "重置密码", ["suspend"] = "暂停员工", ["resume"] = "恢复员工", ["delete"] = "删除员工" };
        bool deleting = Str(data, "operation") == "delete";
        var panel = EnterprisePanel("确认" + names[Str(data, "operation")], $"目标企业：{Str(data, "workspaceName")}\n有效 {Num(data, "valid")} 行，错误 {Num(data, "invalid")} 行。只执行有效行；预览十分钟有效。", out var actions);
        foreach (var row in data.GetProperty("rows").EnumerateArray()) {
            string name = Str(data, "operation") == "create" && row.GetProperty("valid").GetBoolean() ? $"\n{Str(row, "firstName")} {Str(row, "lastName")}" : "";
            panel.Children.Add(new TextBlock { Text = $"{Num(row, "row")}. {Str(row, "email")}{name}\n{Str(row, "message")}", Foreground = row.GetProperty("valid").GetBoolean() ? Text : Hex("#E8B578"), FontSize = 11, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 0, 0, 14) });
        }
        if (data.GetProperty("changePasswordAtNextLogin").GetBoolean()) panel.Children.Add(new TextBlock { Text = "员工下次登录需要改密，自动登录可能暂停等待处理。", Foreground = Hex("#E8B578"), TextWrapping = TextWrapping.Wrap });
        TextBox? confirmation = null;
        if (deleting) {
            panel.Children.Add(new TextBlock { Text = "删除会使员工失去 Google 账号访问权。请核对上面的邮箱，确认已完成数据交接。", Foreground = Hex("#F0A5A5"), TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 0, 0, 12) });
            confirmation = CenterField(panel, "输入企业名称确认删除", "");
            confirmation.ToolTip = "请完整输入：" + Str(data, "workspaceName");
        }
        var confirm = new CheckBox { Content = "已核对目标企业与员工，确认执行", Foreground = Text, Margin = new Thickness(0, 0, 0, 12) }; actions.Children.Add(confirm);
        bool Confirmed() => confirm.IsChecked == true && Num(data, "valid") > 0 && (!deleting || confirmation!.Text == Str(data, "workspaceName"));
        Button? execute = null; execute = Button(deleting ? "确认删除有效员工" : "确认执行有效行", async () => {
            if (!execute!.IsEnabled || confirm.IsChecked != true) return;
            if (enterpriseSelected != wid) return; execute.IsEnabled = false;
            var accepted = await EnterpriseJob("execute", new { draftId = Str(data, "draftId"), confirmWorkspaceName = confirmation?.Text }, wid);
            if (accepted && enterpriseSelected == wid) { enterprisePanelVersion++; enterpriseSide.Visibility = Visibility.Collapsed; enterpriseSide.Content = null; }
            else execute.IsEnabled = Confirmed();
        }, true); execute.IsEnabled = false; actions.Children.Add(execute);
        if (deleting) { execute.Background = Hex("#A33D43"); execute.Foreground = Brushes.White; }
        confirm.Checked += (_, _) => execute.IsEnabled = Confirmed(); confirm.Unchecked += (_, _) => execute.IsEnabled = false;
        if (confirmation != null) confirmation.TextChanged += (_, _) => execute.IsEnabled = Confirmed();
        actions.Children.Add(Button("返回修改", () => { enterprisePanelVersion++; enterpriseSide.Content = source; }));
    }

    async Task<bool> EnterpriseJob(string operation, object? body = null, string? wid = null)
    {
        wid ??= enterpriseSelected;
        if (wid == null || EnterpriseBusy(wid)) return false;
        enterpriseJobs[wid] = "starting"; UpdateEnterpriseBusy();
        if (enterpriseSelected == wid) { CancelFeedbackDismissal(enterpriseResultBanner); enterpriseProgress.Visibility = Visibility.Visible; enterpriseProgress.IsIndeterminate = true; enterpriseResult.Text = "正在提交企业任务…"; }
        try {
            var job = await Api($"workspaces/{wid}/{operation}", body ?? new { }); enterpriseJobs[wid] = Str(job, "id");
            if (enterpriseSelected == wid) RenderEnterpriseJob(job); UpdateEnterpriseBusy(); return true;
        } catch (Exception ex) { enterpriseJobs.Remove(wid); if (enterpriseSelected == wid) { enterpriseProgress.Visibility = Visibility.Collapsed; enterpriseResult.Text = "任务未受理或响应中断，请先核实结果：" + ex.Message; } UpdateEnterpriseBusy(); return false; }
    }

    void RenderEnterpriseJob(JsonElement job)
    {
        string state = Str(job, "state"); bool busy = state is "queued" or "running" or "cancelling";
        enterpriseProgress.Visibility = busy ? Visibility.Visible : Visibility.Collapsed;
        enterpriseProgress.Maximum = Math.Max(1, Num(job, "total")); enterpriseProgress.Value = Num(job, "completed"); enterpriseProgress.IsIndeterminate = busy && Num(job, "completed") == 0;
        var result = job.GetProperty("results").EnumerateArray().ToArray();
        string summary = busy ? (Str(job, "phase") is { Length: > 0 } phase ? phase : "处理中") : state == "cancelled" ? "已取消" : state == "failed" ? "任务失败" : state == "interrupted" ? "上次任务中断，请同步核实" : "已完成";
        enterpriseTaskTitle.Text = Str(job, "operation") switch { "check" => "正在检测企业连接", "sync" => "正在同步员工目录", "create" => "正在创建员工", "reset" => "正在重置员工密码", "update" => "正在修改员工资料", "delete" => "正在删除员工", "suspend" => "正在暂停员工", "resume" => "正在恢复员工", _ => "正在处理企业任务" };
        enterpriseTaskPhase.Text = Str(job, "workspaceName") + "\n" + summary;
        enterpriseTaskStats.Text = $"已处理 {Num(job, "completed")} / {Num(job, "total")} {(Str(job, "operation") == "check" ? "项" : "人")}  ·  成功 {result.Count(r => r.GetProperty("success").GetBoolean())}  ·  失败 {result.Count(r => !r.GetProperty("success").GetBoolean())}";
        bool failed = state is "failed" or "interrupted" || result.Any(r => !r.GetProperty("success").GetBoolean());
        enterpriseResultBanner.Background = Hex(failed ? "#30251C" : "#192720"); enterpriseResultBanner.BorderBrush = Hex(failed ? "#72563D" : "#304B3B");
        SetEnterpriseFeedback($"{Str(job, "workspaceName")} · {summary}  {Num(job, "completed")} / {Num(job, "total")}  ·  成功 {result.Count(r => r.GetProperty("success").GetBoolean())}，失败 {result.Count(r => !r.GetProperty("success").GetBoolean())}" +
            (busy ? "\n管理 API 网络：" + EnterpriseRoute(job) : "") +
            string.Concat(result.Where(r => !r.GetProperty("success").GetBoolean()).Select(r => "\n" + Str(r, "email") + "  " + Str(r, "message"))) +
            (result.Length == 1 ? "\n" + Str(result[0], "message") : ""), state == "completed" && !failed && Num(job, "completed") == Num(job, "total"), "job:" + Str(job, "id") + ":" + state);
    }
    void UpdateEnterpriseBusy()
    {
        bool oauth = enterpriseSelected != null && enterpriseOAuth.ContainsKey(enterpriseSelected);
        bool busy = enterpriseSelected != null && (enterpriseJobs.ContainsKey(enterpriseSelected) || oauth);
        bool requesting = enterpriseRequests.ContainsKey(enterpriseSelected ?? "");
        bool showOverlay = busy || requesting;
        if (showOverlay && enterpriseTaskOverlay.Visibility != Visibility.Visible) {
            if (!requesting) { enterpriseTaskTitle.Text = oauth ? "等待 Google 管理员授权" : "正在提交企业任务"; enterpriseTaskPhase.Text = "正在等待响应，请稍候…"; enterpriseTaskStats.Text = ""; }
            enterpriseTaskOverlay.Visibility = Visibility.Visible;
            if (SystemParameters.ClientAreaAnimation) enterpriseTaskOverlay.BeginAnimation(UIElement.OpacityProperty, new DoubleAnimation(0, 1, TimeSpan.FromMilliseconds(160)));
        }
        if (!showOverlay) { enterpriseTaskOverlay.Visibility = Visibility.Collapsed; enterpriseProgress.Visibility = Visibility.Collapsed; }
        else if (requesting || enterpriseProgress.Visibility != Visibility.Visible) { enterpriseProgress.Visibility = Visibility.Visible; enterpriseProgress.IsIndeterminate = true; }
        enterpriseResultBanner.Visibility = showOverlay || string.IsNullOrWhiteSpace(enterpriseResult.Text) ? Visibility.Collapsed : Visibility.Visible;
        if (enterpriseTaskCopyAuth != null) { enterpriseTaskCopyAuth.Visibility = oauth ? Visibility.Visible : Visibility.Collapsed; enterpriseTaskCopyAuth.IsEnabled = oauth && enterpriseSelected != null && enterpriseOAuthUrls.ContainsKey(enterpriseSelected); }
        foreach (var button in enterpriseCommands) {
            button.IsEnabled = enterpriseSelected != null && !showOverlay;
            if (button.Content as string is "批量修改资料" or "批量重置密码" or "批量暂停" or "批量恢复" or "批量删除")
                button.IsEnabled = button.IsEnabled && enterpriseRows.Count(r => r.Selected) is > 0 and <= 200 &&
                    enterprises.TryGetValue(enterpriseSelected!, out var tenant) && Str(tenant, "status") == "connected" && tenant.GetProperty("enabled").GetBoolean();
            if (button.Content as string == "Google 管理员授权") button.IsEnabled = button.IsEnabled && enterprises.TryGetValue(enterpriseSelected!, out var record) && Str(record, "authType") == "oauth";
        }
        bool rowReady = enterpriseSelected != null && !showOverlay && enterprises.TryGetValue(enterpriseSelected, out var rowTenant) && Str(rowTenant, "status") == "connected" && rowTenant.GetProperty("enabled").GetBoolean();
        foreach (var row in enterpriseRows) row.CanManage = rowReady;
        if (enterpriseCancel != null) {
            enterpriseCancel.Content = oauth ? "取消管理员授权" : "取消企业任务";
            enterpriseCancel.Visibility = busy ? Visibility.Visible : Visibility.Collapsed;
            enterpriseCancel.IsEnabled = busy && (oauth ? enterpriseOAuth[enterpriseSelected!] != "starting" : enterpriseJobs[enterpriseSelected!] != "starting");
        }
        refreshEnterpriseEditor?.Invoke(); UpdateGlobalActivity();
    }
    async Task StartEnterpriseOAuth(string wid)
    {
        if (enterpriseOAuth.ContainsKey(wid) || enterpriseJobs.ContainsKey(wid)) return;
        enterpriseOAuth[wid] = "starting"; UpdateEnterpriseBusy();
        try {
            var result = await Api($"workspaces/{wid}/oauth-start", new { });
            enterpriseOAuth[wid] = Str(result, "id"); string url = Str(result, "authorizationUrl"); enterpriseOAuthUrls[wid] = url;
            if (!Uri.TryCreate(url, UriKind.Absolute, out var uri) || uri.Scheme != "https" || uri.Host != "accounts.google.com" || uri.AbsolutePath != "/o/oauth2/v2/auth")
                throw new InvalidOperationException("授权地址校验失败。");
            if (enterpriseSelected == wid) RenderEnterpriseOAuth(result);
            try { openEnterpriseAuthorization(url); }
            catch { enterpriseResult.Text = "系统浏览器未打开，请点击“复制授权链接”后在浏览器中打开。"; }
        } catch (Exception ex) {
            if (enterpriseOAuth.TryGetValue(wid, out var aid) && aid != "starting") try { await Api($"workspace-oauth/{aid}/cancel", new { }); } catch { }
            enterpriseOAuth.Remove(wid); enterpriseOAuthUrls.Remove(wid); enterpriseProgress.Visibility = Visibility.Collapsed;
            enterpriseResult.Text = ex.Message;
        } finally { UpdateEnterpriseBusy(); }
    }
    void RenderEnterpriseOAuth(JsonElement attempt)
    {
        bool busy = Str(attempt, "state") is "waiting" or "exchanging";
        enterpriseProgress.Visibility = busy ? Visibility.Visible : Visibility.Collapsed;
        enterpriseProgress.IsIndeterminate = busy;
        SetEnterpriseFeedback(Str(attempt, "adminEmail") + " · " + Str(attempt, "message"), Str(attempt, "state") == "completed", "oauth:" + Str(attempt, "id") + ":" + Str(attempt, "state"));
        enterpriseTaskTitle.Text = "等待 Google 管理员授权";
        enterpriseTaskPhase.Text = Str(attempt, "adminEmail") + "\n" + Str(attempt, "message"); enterpriseTaskStats.Text = "请在 Google 页面完成授权，工作台会自动继续。";
    }
    async Task PollEnterpriseJobs()
    {
        if (enterprisePolling || closing) return; enterprisePolling = true;
        try {
            foreach (var (wid, aid) in enterpriseOAuth.ToArray()) {
                if (aid == "starting") continue;
                try {
                    var attempt = await Api($"workspace-oauth/{aid}");
                    if (enterpriseSelected == wid) RenderEnterpriseOAuth(attempt);
                    if (Str(attempt, "state") is not ("waiting" or "exchanging")) {
                        await LoadEnterprises();
                        enterpriseOAuth.Remove(wid); enterpriseOAuthUrls.Remove(wid);
                        if (enterpriseSelected == wid) RenderEnterpriseOAuth(attempt);
                    }
                } catch (Exception ex) { if (enterpriseSelected == wid) { enterpriseResult.Text = "授权进度读取中断：" + ex.Message; enterpriseTaskPhase.Text = enterpriseResult.Text; } }
            }
            foreach (var (wid, jid) in enterpriseJobs.ToArray()) {
                if (jid == "starting") continue;
                try {
                    var job = await Api($"workspace-jobs/{jid}");
                    if (enterpriseSelected == wid) RenderEnterpriseJob(job);
                    if (Str(job, "state") is "completed" or "failed" or "cancelled") {
                        await LoadEnterprises(); await LoadLoginAccounts(); enterpriseJobs.Remove(wid);
                        if (enterpriseSelected == wid) RenderEnterpriseJob(job);
                    }
                } catch (Exception ex) { if (enterpriseSelected == wid) { enterpriseResult.Text = "任务进度读取中断，后台任务可能继续执行：" + ex.Message; enterpriseTaskPhase.Text = enterpriseResult.Text; } }
            }
            UpdateEnterpriseBusy();
        } finally { enterprisePolling = false; }
    }
}
