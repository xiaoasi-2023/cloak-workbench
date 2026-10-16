using System.ComponentModel;
using System.Text.Json;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Data;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Threading;

namespace CloakWorkbench;

public sealed partial class MainWindow
{
    readonly HashSet<string> selectedAccounts = new();
    readonly Dictionary<string, string> accountJobStates = new();
    readonly TextBox centerSearch = new();
    readonly ComboBox centerGroup = new(), centerState = new(), centerAction = new();
    readonly TextBlock centerCount = new(), centerFeedback = new(), centerJobText = new();
    readonly ProgressBar centerProgress = new() { Height = 4, Minimum = 0, Maximum = 1, Foreground = Accent, Background = Hex("#2B3B32") };
    readonly Border centerActivity = new() { Visibility = Visibility.Collapsed };
    readonly ContentControl centerSide = new() { Visibility = Visibility.Collapsed };
    readonly DispatcherTimer centerJobTimer = new() { Interval = TimeSpan.FromSeconds(1) };
    readonly List<Button> centerOperations = new();
    readonly List<AccountCenterRow> centerRows = new();
    DataGrid? centerTable;
    CheckBox? centerSelectAll;
    Button? centerCancel, centerDismiss;
    bool centerResultWarning = true;
    string? centerJobId, centerDetailId, centerSignature;
    bool centerBusy, centerJobPolling, centerSelecting, centerCancelRequested;
    int centerDetailVersion;

    sealed class AccountCenterRow : INotifyPropertyChanged
    {
        internal Action<bool>? Changed;
        bool chosen;
        public bool Chosen { get => chosen; set { if (chosen == value) return; chosen = value; PropertyChanged?.Invoke(this, new(nameof(Chosen))); Changed?.Invoke(value); } }
        public string Id { get; init; } = "";
        public string Name { get; private set; } = "";
        public string Email { get; private set; } = "";
        public string Group { get; private set; } = "";
        public string Session { get; private set; } = "";
        public string Health { get; private set; } = "";
        public string Country { get; private set; } = "";
        public string Time { get; private set; } = "";
        public string NetworkTime { get; private set; } = "";
        public string Proxy { get; private set; } = "";
        public string OpenLabel => Session == "运行中" ? "切换" : "打开";
        public Brush HealthBrush => Health switch { "Dola 已登录" => WorkbenchTheme.Green, "需要登录" => Hex("#ED9D9D"), "地区受限" or "网络异常" => Hex("#E8B578"), "等待检测" or "正在检测…" => Accent, _ => Muted };
        public void Update(string name, string email, string group, string session, string health, string country, string time, string proxy, string networkTime = "")
        {
            Name = name; Email = email; Group = group; Session = session; Health = health; Country = country; Time = time; Proxy = proxy; NetworkTime = networkTime;
            PropertyChanged?.Invoke(this, new(null));
        }
        public event PropertyChangedEventHandler? PropertyChanged;
    }

    void ShowAccountCenter()
    {
        accountWorkspace.Content ??= CreateAccountCenter();
        ShowManagementPage(accountWorkspace, "批量管理");
        RenderAccountCenter();
    }

    void ShowAccountDetails(string id)
    {
        ShowAccountCenter();
        _ = CenterSafe(() => LoadAccountDetails(id));
    }

    static string AccountTime(string value) => DateTimeOffset.TryParse(value, out var time) ? time.ToLocalTime().ToString("MM/dd HH:mm") : "未检测";
    static JsonElement AccountObject(JsonElement value, string name) => value.ValueKind == JsonValueKind.Object && value.TryGetProperty(name, out var child) && child.ValueKind == JsonValueKind.Object ? child : default;
    static JsonElement AccountNetworkObservation(JsonElement profile) => AccountObject(profile, "networkCheck");
    static bool IsCurrentLoginObservation(JsonElement profile)
    {
        var check = AccountCheck(profile);
        return Str(profile, "state") == "running" && check.ValueKind == JsonValueKind.Object
            && Str(check, "isCurrent").Equals("true", StringComparison.OrdinalIgnoreCase);
    }
    static JsonElement LastLoginObservation(JsonElement profile)
    {
        var last = AccountObject(profile, "lastAccountCheck");
        return last.ValueKind == JsonValueKind.Object ? last : AccountCheck(profile);
    }
    static string LoginObservationLabel(JsonElement profile)
    {
        if (IsCurrentLoginObservation(profile)) return AccountState(profile) == "logged_in" ? "Dola 已登录" : AccountStateLabel(AccountState(profile));
        var last = LastLoginObservation(profile);
        return (last.ValueKind == JsonValueKind.Object ? Str(last, "status") : "unknown") switch {
            "logged_in" => "上次已登录", "login_required" => "上次需登录", "region_blocked" => "上次地区受限",
            _ => Str(profile, "state") is "running" or "starting" ? "本次待检测" : "尚未检测" };
    }
    static string LoginObservationTime(JsonElement profile) => LastLoginObservation(profile) is var last && last.ValueKind == JsonValueKind.Object ? AccountTime(Str(last, "checkedAt")) : "未检测";
    static string LoginIdentityLabel(JsonElement profile) => IsCurrentLoginObservation(profile) && AccountState(profile) == "logged_in"
        ? BoundAccountEmail(profile).Length > 0 ? "绑定邮箱未核验" : "网站身份未核验"
        : "尚未核验网站身份";
    static string AccountObservationTime(JsonElement profile)
    {
        return LoginObservationTime(profile);
    }
    static string AccountHealthLabel(JsonElement profile)
    {
        return LoginObservationLabel(profile);
    }
    bool CenterMatches(JsonElement p)
    {
        var q = centerSearch.Text.Trim(); if (q.Length > 0 && !new[] { "name", "accountEmail", "accountNotes" }.Any(k => Str(p, k).Contains(q, StringComparison.OrdinalIgnoreCase)) && !BoundAccountEmail(p).Contains(q, StringComparison.OrdinalIgnoreCase)) return false;
        var group = centerGroup.SelectedItem as string ?? "全部分组"; if (group != "全部分组" && (group == "未分组" ? Str(p, "accountGroup").Length > 0 : Str(p, "accountGroup") != group)) return false;
        var state = centerState.SelectedItem as string ?? "全部状态"; var label = AccountHealthLabel(p);
        return state switch {
            "运行中" or "已打开" => Str(p, "state") is "running" or "starting",
            "本次已登录" or "已确认登录" => IsCurrentLoginObservation(p) && AccountState(p) == "logged_in",
            "本次待检测" or "未确认" => Str(p, "state") is "running" or "starting" && !IsCurrentLoginObservation(p),
            "网络异常" => AccountNetworkObservation(p) is var net && net.ValueKind == JsonValueKind.Object && Str(net, "reachable").Equals("false", StringComparison.OrdinalIgnoreCase),
            "全部状态" => true, _ => label == state };
    }
    void RenderAccountCenter()
    {
        if (centerTable == null || centerSelecting) return;
        var signature = JsonSerializer.Serialize(new { p = profiles.Values, q = centerSearch.Text, group = centerGroup.SelectedItem, state = centerState.SelectedItem, jobs = accountJobStates, proxies = proxyCatalog.Select(p => new { id = Str(p, "id"), name = Str(p, "name") }) });
        if (signature == centerSignature) { UpdateCenterSelection(); return; } centerSignature = signature;
        centerSelecting = true;
        try {
            var groups = new[] { "全部分组", "未分组" }.Concat(profiles.Values.Select(p => Str(p, "accountGroup")).Where(g => g.Length > 0).Distinct().Order()).ToArray();
            if (centerGroup.ItemsSource is not string[] previous || !previous.SequenceEqual(groups)) { var current = centerGroup.SelectedItem as string ?? "全部分组"; centerGroup.ItemsSource = groups; centerGroup.SelectedItem = groups.Contains(current) ? current : "全部分组"; }
            selectedAccounts.RemoveWhere(id => !profiles.ContainsKey(id));
            var activeId = (centerTable.SelectedItem as AccountCenterRow)?.Id;
            var existing = centerRows.ToDictionary(r => r.Id);
            var previousIds = centerRows.Select(r => r.Id).ToArray();
            centerRows.Clear();
            foreach (var (id, p) in profiles.Where(kv => CenterMatches(kv.Value))) {
                var network = AccountNetworkObservation(p);
                var proxyId = Str(p, "proxyId"); var proxy = proxyCatalog.FirstOrDefault(entry => Str(entry, "id") == proxyId);
                var job = accountJobStates.GetValueOrDefault(id, "");
                var row = existing.GetValueOrDefault(id) ?? new AccountCenterRow { Id = id };
                row.Update(Str(p, "name"), BoundAccountEmail(p).Length > 0 ? BoundAccountEmail(p) : "未绑定账号", Str(p, "accountGroup").Length == 0 ? "未分组" : Str(p, "accountGroup"), Str(p, "state") is "running" or "starting" ? "运行中" : "已关闭", job == "running" ? "正在检测…" : job is "queued" or "pending" ? "等待检测" : AccountHealthLabel(p), network.ValueKind == JsonValueKind.Object ? NetworkCountryName(network) : "未检测", AccountObservationTime(p), proxyId.Length == 0 ? Str(p, "proxy").Length > 0 ? "手动代理" : "本机直连" : proxy.ValueKind == JsonValueKind.Object ? Str(proxy, "name") : "代理已移除", network.ValueKind == JsonValueKind.Object ? AccountTime(Str(network, "checkedAt", Str(network, "testedAt"))) : "未检测"); row.Chosen = selectedAccounts.Contains(id);
                row.Changed = chosen => { if (chosen) selectedAccounts.Add(id); else selectedAccounts.Remove(id); UpdateCenterSelection(); }; centerRows.Add(row);
            }
            if (!previousIds.SequenceEqual(centerRows.Select(r => r.Id))) { centerTable.ItemsSource = centerRows.ToArray(); centerTable.SelectedItem = centerRows.FirstOrDefault(r => r.Id == activeId); }
        } finally { centerSelecting = false; }
        UpdateCenterSelection();
    }
    void UpdateCenterSelection()
    {
        if (centerTable == null) return;
        centerTotal.Text = profiles.Count.ToString();
        centerOpened.Text = profiles.Values.Count(p => Str(p, "state") is "running" or "starting").ToString();
        centerConfirmed.Text = profiles.Values.Count(p => IsCurrentLoginObservation(p) && AccountState(p) == "logged_in").ToString();
        centerCount.Text = $"显示 {centerRows.Count} 个环境  ·  已选 {selectedAccounts.Count}";
        var wasSelecting = centerSelecting; centerSelecting = true;
        var n = centerRows.Count(r => selectedAccounts.Contains(r.Id)); centerSelectAll!.IsChecked = n == 0 ? false : n == centerRows.Count ? true : null;
        centerSelecting = wasSelecting;
    }
    async Task CenterSafe(Func<Task> action)
    {
        try { await action(); } catch (Exception ex) { CancelFeedbackDismissal(centerActivity); centerResultWarning = true; centerFeedback.Text = ex.Message; centerFeedback.Foreground = Hex("#ED9D9D"); centerActivity.Visibility = Visibility.Visible; centerJobText.Text = "操作未完成"; CenterResultPresentation(); centerResults.Visibility = Visibility.Visible; centerResultToggle!.Content = "收起明细"; Log("账号中心：" + ex.Message); }
    }
    void CenterSetBusy(bool busy)
    {
        bool finished = centerBusy && !busy;
        if (busy) { CancelFeedbackDismissal(centerActivity); centerResultWarning = true; }
        centerBusy = busy; foreach (var b in centerOperations) b.IsEnabled = !busy; centerAction.IsEnabled = !busy; if (centerSelectAll != null) centerSelectAll.IsEnabled = !busy;
        centerCancel!.IsEnabled = busy && !centerCancelRequested;
        CenterResultPresentation();
        if (finished) ScheduleCenterFeedbackDismissal();
    }
    void ScheduleCenterFeedbackDismissal()
    {
        if (!centerBusy && !centerResultWarning && centerFeedback.Text.Length > 0 && centerResults.Visibility != Visibility.Visible)
            DismissFeedbackLater(centerActivity, () => centerActivity.Visibility = Visibility.Collapsed);
    }
    void CenterProgress(string text, int completed, int total)
    {
        CancelFeedbackDismissal(centerActivity); centerActivity.Visibility = Visibility.Visible; centerJobText.Text = text; centerProgress.Maximum = Math.Max(1, total); centerProgress.Value = completed;
    }
    async Task ExecuteAccountAction()
    {
        if (centerBusy) return; var ids = selectedAccounts.Where(profiles.ContainsKey).ToArray(); if (ids.Length == 0) { CancelFeedbackDismissal(centerActivity); centerResultWarning = true; centerActivity.Visibility = Visibility.Visible; centerJobText.Text = "请先勾选需要操作的环境"; centerFeedback.Text = "全选仅作用于当前筛选结果。跨筛选保留已选环境。"; CenterResultPresentation(); return; }
        var action = centerAction.SelectedIndex;
        if (action >= 4) { ShowAccountBinding(action == 4); return; }
        centerCancelRequested = false; CenterSetBusy(true); centerFeedback.Text = ""; centerFeedback.Foreground = Muted;
        if (action is 2 or 3) {
            try {
                foreach (var id in ids) accountJobStates[id] = "queued"; RenderAccountCenter(); CenterProgress("正在创建检测任务…", 0, ids.Length);
                var job = await Api("accounts/check", new { ids, kind = action == 2 ? "login" : "network" }); centerJobId = Str(job, "id"); if (string.IsNullOrEmpty(centerJobId)) throw new InvalidOperationException("检测任务没有返回编号。"); centerJobTimer.Start(); await PollAccountJob();
            } catch { accountJobStates.Clear(); centerJobId = null; CenterSetBusy(false); throw; }
            return;
        }
        var results = new List<string>(); int succeeded = 0, completed = 0;
        try {
            foreach (var id in ids) {
                if (centerCancelRequested || closing) break;
                var name = profiles.TryGetValue(id, out var p) ? Str(p, "name") : id; CenterProgress($"{(action == 0 ? "依次打开" : "依次关闭")}  {completed} / {ids.Length}  ·  {name}", completed, ids.Length);
                try { if (action == 0) await LaunchQueued(id, selectBrowser: false); else await CloseProfile(id); succeeded++; results.Add(name + "：完成"); } catch (Exception ex) { results.Add(name + "：" + ex.Message); }
                completed++; CenterProgress($"已处理 {completed} / {ids.Length}  ·  成功 {succeeded}", completed, ids.Length);
            }
            centerFeedback.Text = $"成功 {succeeded}，失败 {completed - succeeded}，未执行 {ids.Length - completed}。" + Environment.NewLine + string.Join(Environment.NewLine, results);
            centerResultWarning = succeeded != ids.Length || centerCancelRequested;
        } finally { try { await Refresh(); } finally { CenterSetBusy(false); } }
    }
    async Task PollAccountJob()
    {
        if (centerJobPolling || centerJobId == null || closing) return; centerJobPolling = true;
        try {
            var job = await Api("account-jobs/" + centerJobId); var state = Str(job, "state"); int completed = Num(job, "completed"), total = Num(job, "total");
            if (job.TryGetProperty("items", out var items) && items.ValueKind == JsonValueKind.Object) foreach (var item in items.EnumerateObject()) accountJobStates[item.Name] = item.Value.ToString();
            CenterProgress($"{(state == "cancelling" ? "正在取消剩余检测" : "检测进度")}  {completed} / {total}  ·  只读检查，不发送聊天", completed, total); RenderAccountCenter();
            if (state is "completed" or "cancelled" or "failed") {
                centerJobTimer.Stop(); centerJobId = null; accountJobStates.Clear(); await Refresh();
                var lines = new List<string>(); if (job.TryGetProperty("results", out var results) && results.ValueKind == JsonValueKind.Array) foreach (var result in results.EnumerateArray()) { var id = Str(result, "id"); var name = profiles.TryGetValue(id, out var p) ? Str(p, "name") : id; var check = AccountObject(result, "check");
                    var description = "未完成";
                    if (check.ValueKind == JsonValueKind.Object) description = check.TryGetProperty("reachable", out _) ? Str(check, "reachable").Equals("true", StringComparison.OrdinalIgnoreCase) ? "可连接 · " + NetworkCountryName(check) : "连接失败 · " + Str(check, "error", "请检查代理与上游连接") : AccountStateLabel(Str(check, "status")) + " · " + AccountReason(Str(check, "reason"));
                    if (Str(result, "applied").Equals("false", StringComparison.OrdinalIgnoreCase)) description += " · 未保存（配置可能已变化）";
                    lines.Add(name + "：" + description); }
                centerJobText.Text = state == "cancelled" ? $"检测已取消  ·  已处理 {completed} / {total}" : $"检测结束  ·  已处理 {completed} / {total}"; centerFeedback.Text = string.Join(Environment.NewLine, lines);
                centerResultWarning = state != "completed" || completed != total || !job.TryGetProperty("results", out var finalResults) || finalResults.GetArrayLength() != total || finalResults.EnumerateArray().Any(result => {
                    var check = AccountObject(result, "check");
                    return !Str(result, "applied").Equals("true", StringComparison.OrdinalIgnoreCase) || check.ValueKind != JsonValueKind.Object ||
                        !(check.TryGetProperty("reachable", out _) ? Str(check, "reachable").Equals("true", StringComparison.OrdinalIgnoreCase) : Str(check, "status") == "logged_in");
                });
                if (centerDetailId != null && centerSide.Visibility == Visibility.Visible) await LoadAccountDetails(centerDetailId);
            }
        } finally { centerJobPolling = false; if (centerJobId == null) CenterSetBusy(false); }
    }
    async Task CancelAccountOperation()
    {
        centerCancelRequested = true; centerCancel!.IsEnabled = false; centerJobText.Text = "正在取消剩余操作；已开始的操作完成后停止。";
        if (centerJobId != null) await Api("account-jobs/" + centerJobId + "/cancel", new { });
    }

    StackPanel CenterPanel(string title, string description)
    {
        centerDetailId = null; ++centerDetailVersion; centerSide.Visibility = Visibility.Visible;
        var panel = new StackPanel { Margin = new Thickness(20, 0, 20, 20) };
        var header = new StackPanel { Margin = new Thickness(20, 18, 20, 18) };
        var heading = new DockPanel(); var close = IconButton("close", "", () => { centerSide.Visibility = Visibility.Collapsed; centerDetailId = null; ++centerDetailVersion; }); close.Padding = new Thickness(6); close.Background = Brushes.Transparent; close.BorderThickness = new Thickness(0); close.ToolTip = "关闭环境详情"; AutomationProperties.SetName(close, "关闭环境侧栏"); DockPanel.SetDock(close, Dock.Right); heading.Children.Add(close); heading.Children.Add(new TextBlock { Text = title, Foreground = Text, FontSize = 18, FontWeight = FontWeights.SemiBold, TextWrapping = TextWrapping.Wrap }); header.Children.Add(heading);
        header.Children.Add(new TextBlock { Text = description, Foreground = Muted, FontSize = 11, TextWrapping = TextWrapping.Wrap, LineHeight = 19, Margin = new Thickness(0, 8, 0, 0) });
        var body = new DockPanel(); DockPanel.SetDock(header, Dock.Top); body.Children.Add(header);
        body.Children.Add(new ScrollViewer { Content = panel, VerticalScrollBarVisibility = ScrollBarVisibility.Auto, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled });
        centerSide.Content = new Border { Background = Hex("#1A211D"), BorderBrush = Line, BorderThickness = new Thickness(1), CornerRadius = new CornerRadius(10), Child = body }; return panel;
    }
    TextBox CenterField(StackPanel panel, string label, string text = "")
    {
        panel.Children.Add(Label(label, 11, Muted)); var input = new TextBox { Text = text, Margin = new Thickness(0, 7, 0, 14) }; AutomationProperties.SetName(input, label); panel.Children.Add(input); return input;
    }
    ComboBox CenterProxyPicker(StackPanel panel, string label)
    {
        panel.Children.Add(Label(label, 11, Muted)); var picker = new ComboBox { ItemsSource = new[] { new ProxyChoice("", "本机直连") }.Concat(proxyCatalog.Select(p => new ProxyChoice(Str(p, "id"), Str(p, "name")))).ToList(), DisplayMemberPath = "Label", SelectedValuePath = "Id", SelectedValue = "", Margin = new Thickness(0, 7, 0, 14) }; AutomationProperties.SetName(picker, label); panel.Children.Add(picker); return picker;
    }
    void ShowAccountBinding(bool group)
    {
        var ids = selectedAccounts.ToArray(); var panel = CenterPanel(group ? "更改分组" : "绑定代理", $"将应用到 {ids.Length} 个已选环境。" + (group ? "留空表示移出分组。" : "正在运行的环境不会更改代理，请先关闭会话。住宅会话按环境独立保存。"));
        TextBox? input = group ? CenterField(panel, "目标分组") : null; ComboBox? picker = group ? null : CenterProxyPicker(panel, "目标代理");
        var result = new TextBlock { Foreground = Muted, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 14, 0, 0) }; Button? apply = null;
        apply = Button("应用到所选环境", async () => { if (centerBusy) return; apply!.IsEnabled = false; CancelFeedbackDismissal(result); CenterSetBusy(true); try { var body = new Dictionary<string, object?> { ["ids"] = ids, ["action"] = group ? "group" : "proxy" }; body[group ? "accountGroup" : "proxyId"] = group ? input!.Text.Trim() : picker!.SelectedValue as string ?? ""; var response = await Api("accounts/batch", body); result.Text = $"成功 {Num(response, "succeeded")}，失败 {Num(response, "failed")}。" + Environment.NewLine + CenterResultLines(response); await Refresh(); if (Num(response, "failed") == 0) ShowTemporaryFeedback(result, result.Text); } catch (Exception ex) { result.Text = ex.Message; } finally { apply.IsEnabled = true; CenterSetBusy(false); } }, true); panel.Children.Add(apply); panel.Children.Add(result);
    }
    string CenterResultLines(JsonElement response)
    {
        if (!response.TryGetProperty("results", out var rows) || rows.ValueKind != JsonValueKind.Array) return "";
        return string.Join(Environment.NewLine, rows.EnumerateArray().Select(r => {
            var id = Str(r, "id"); var name = Str(r, "name", profiles.TryGetValue(id, out var p) ? Str(p, "name") : "第 " + Str(r, "row") + " 行");
            var result = Str(r, "status") switch { "created" => "已创建", "ready" => "可创建", "updated" => "已更新", "duplicate" => "重复资料", _ => Str(r, "error", Str(r, "status")) };
            var region = Str(r, "regionStatus") switch { "matched_proxy" => " · 已匹配出口地区", "missing_timezone" => " · 请检测出口时区后配置地区", "not_checked" => " · 出口未确认，请检测后配置地区", _ => "" };
            return name + " · " + result + region;
        }));
    }
    void ShowAccountImport()
    {
        var panel = CenterPanel("批量创建 / 导入环境", "创建独立浏览器环境或导入环境资料。此处不导入密码；邮箱与密码请在账号列表维护并绑定。");
        var mode = new ComboBox { ItemsSource = new[] { "批量创建空白环境", "导入 CSV / JSON 环境资料" }, SelectedIndex = 0, Margin = new Thickness(0, 0, 0, 14) }; AutomationProperties.SetName(mode, "环境导入方式"); panel.Children.Add(mode);
        var creation = new StackPanel(); var prefix = CenterField(creation, "名称前缀", "浏览器"); var count = CenterField(creation, "创建数量（1–200）", "5"); panel.Children.Add(creation);
        var textWrap = new StackPanel { Visibility = Visibility.Collapsed }; var text = CenterField(textWrap, "CSV / JSON 环境资料"); text.AcceptsReturn = true; text.TextWrapping = TextWrapping.Wrap; text.Height = 135; text.VerticalScrollBarVisibility = ScrollBarVisibility.Auto; textWrap.Children.Add(new TextBlock { Text = "CSV 表头：name,accountEmail,accountGroup,accountNotes\nJSON：环境资料数组。accountEmail 是旧版备注，不会绑定 Google 账号；重复标识会提示。", FontSize = 11, Foreground = Muted, TextWrapping = TextWrapping.Wrap, LineHeight = 19, Margin = new Thickness(0, 0, 0, 14) }); panel.Children.Add(textWrap);
        var group = CenterField(panel, "默认分组（可选）"); var proxy = CenterProxyPicker(panel, "默认代理");
        var regionHint = new TextBlock { Text = "创建后请检测各环境的真实出口，再匹配语言与时区。住宅会话的实际国家可能与代理列表快照不同。", Foreground = Muted, FontSize = 11, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 0, 0, 14) }; panel.Children.Add(regionHint);
        var summary = new TextBlock { Foreground = Muted, TextWrapping = TextWrapping.Wrap, FontSize = 11, LineHeight = 19, Margin = new Thickness(0, 14, 0, 0) }; AutomationProperties.SetName(summary, "环境导入预览结果");
        Dictionary<string, object?>? prepared = null; Button? commit = null, preview = null; bool submitting = false;
        void Invalidate() { prepared = null; if (commit != null) commit.IsEnabled = false; if (!submitting) summary.Text = "内容已更改，请重新预览。"; }
        void EnableInputs(bool enabled) { mode.IsEnabled = enabled; creation.IsEnabled = enabled; textWrap.IsEnabled = enabled; group.IsEnabled = enabled; proxy.IsEnabled = enabled; }
        prefix.TextChanged += (_, _) => Invalidate(); count.TextChanged += (_, _) => Invalidate(); text.TextChanged += (_, _) => Invalidate(); group.TextChanged += (_, _) => Invalidate(); proxy.SelectionChanged += (_, _) => Invalidate();
        mode.SelectionChanged += (_, _) => { creation.Visibility = mode.SelectedIndex == 0 ? Visibility.Visible : Visibility.Collapsed; textWrap.Visibility = mode.SelectedIndex == 1 ? Visibility.Visible : Visibility.Collapsed; Invalidate(); };
        preview = Button("预览并检查", async () => {
            if (submitting || centerBusy) return; submitting = true; preview!.IsEnabled = false; commit!.IsEnabled = false; EnableInputs(false); summary.Text = "正在检查资料…";
            try {
                var body = new Dictionary<string, object?> { ["accountGroup"] = group.Text.Trim(), ["proxyId"] = proxy.SelectedValue as string ?? "", ["preview"] = true };
                if (mode.SelectedIndex == 0) { if (!int.TryParse(count.Text, out var quantity) || quantity is < 1 or > 200) throw new InvalidOperationException("数量请填写 1–200 的整数。"); body["count"] = quantity; body["prefix"] = prefix.Text.Trim(); }
                else body["text"] = text.Text;
                var response = await Api("accounts/import", body); prepared = body; summary.Text = $"共 {Num(response, "total")} 行  ·  可创建 {Num(response, "valid")}  ·  不可创建 {Num(response, "invalid")}" + Environment.NewLine + CenterResultLines(response); commit.IsEnabled = Num(response, "valid") > 0;
            } catch (Exception ex) { prepared = null; summary.Text = ex.Message; } finally { submitting = false; preview.IsEnabled = true; EnableInputs(true); }
        }); panel.Children.Add(preview);
        commit = Button("确认创建可用行", async () => {
            if (submitting || centerBusy || prepared == null) return; submitting = true; CenterSetBusy(true); preview.IsEnabled = false; commit!.IsEnabled = false; EnableInputs(false);
            try { prepared["preview"] = false; var response = await Api("accounts/import", prepared); prepared = null; summary.Text = $"本次已创建 {Num(response, "created")} 个环境。" + Environment.NewLine + CenterResultLines(response); await Refresh(); }
            catch (Exception ex) { summary.Text = ex.Message + "\n请重新预览，核对已经创建的账号后再操作。"; prepared = null; }
            finally { submitting = false; CenterSetBusy(false); preview.IsEnabled = true; EnableInputs(true); }
        }, true); commit.IsEnabled = false; commit.Margin = new Thickness(0, 10, 0, 0); panel.Children.Add(commit); panel.Children.Add(summary);
    }

    async Task LoadAccountDetails(string id)
    {
        var panel = CenterPanel("环境详情", "正在读取浏览器环境资料…"); centerDetailId = id; int version = centerDetailVersion;
        var p = await Api($"profiles/{id}/details"); if (version != centerDetailVersion || closing) return;
        panel = CenterPanel(Str(p, "name", "环境详情"), "独立浏览器环境 · 关闭窗口会保留本地登录数据"); centerDetailId = id;
        bool running = Str(p, "state") is "running" or "starting";
        var primary = new Grid { Margin = new Thickness(0, 0, 0, 18) };
        primary.ColumnDefinitions.Add(new ColumnDefinition()); primary.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        var open = Button(running ? "切到浏览器" : "打开浏览器", async () => await CenterSafe(() => Launch(id)), true); open.Height = 38; open.Margin = new Thickness(0, 0, 8, 0); primary.Children.Add(open);
        var settings = Button("环境设置", () => ShowInlineProfileSettings(id)); settings.Height = 38; Grid.SetColumn(settings, 1); primary.Children.Add(settings); panel.Children.Add(primary);

        var tabs = new WrapPanel { Margin = new Thickness(0, 0, 0, 18) }; panel.Children.Add(tabs);
        var detailContent = new ContentControl(); panel.Children.Add(detailContent);
        var pages = new Dictionary<string, StackPanel>(); var tabButtons = new Dictionary<string, Button>();
        StackPanel Page(string title) { var page = new StackPanel(); pages[title] = page; return page; }
        void Detail(StackPanel page, string label, string value, Brush? color = null) {
            var row = new Grid { Margin = new Thickness(0, 0, 0, 13) };
            row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(84) }); row.ColumnDefinitions.Add(new ColumnDefinition());
            row.Children.Add(new TextBlock { Text = label, Foreground = Muted, FontSize = 12, LineHeight = 21, TextWrapping = TextWrapping.Wrap });
            var text = new TextBlock { Text = value.Length == 0 ? "—" : value, Foreground = color ?? Text, FontSize = 12, TextWrapping = TextWrapping.Wrap, LineHeight = 21 }; Grid.SetColumn(text, 1); row.Children.Add(text); page.Children.Add(row);
        }
        void Hint(StackPanel page, string text) => page.Children.Add(new TextBlock { Text = text, Foreground = Muted, FontSize = 12, LineHeight = 21, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 0, 0, 16) });
        void Action(StackPanel page, string label, Action callback, bool enabled = true) { var button = Button(label, callback); button.IsEnabled = enabled; button.HorizontalAlignment = HorizontalAlignment.Stretch; button.Margin = new Thickness(0, 0, 0, 10); page.Children.Add(button); }

        var overview = Page("概览");
        Detail(overview, "浏览器", running ? "运行中" : "已关闭 · 本地数据已保留", running ? Accent : Muted);
        Detail(overview, "Dola 登录", LoginObservationLabel(p), IsCurrentLoginObservation(p) ? AccountStateColor(AccountState(p)) : Muted);
        Detail(overview, "身份核验", LoginIdentityLabel(p), Muted);
        Detail(overview, "登录检测", LoginObservationTime(p));
        var check = IsCurrentLoginObservation(p) ? AccountCheck(p) : LastLoginObservation(p);
        if (check.ValueKind == JsonValueKind.Object && Str(check, "checkedAt").Length > 0) Detail(overview, IsCurrentLoginObservation(p) ? "检测依据" : "上次依据", AccountReason(Str(check, "reason")));
        if (check.ValueKind == JsonValueKind.Object && Str(check, "routeMatches").Equals("false", StringComparison.OrdinalIgnoreCase)) Hint(overview, "这条登录记录来自修改代理之前。当前线路与登录状态需要重新检测。");
        Hint(overview, running ? "检测只读取当前 Dola 页面。网站已登录，并不代表已核对绑定的 Google 邮箱。" : "打开后继续原来的会话；历史检测结果不代表当前仍在线。登录状态需在打开后重新检测。");
        Action(overview, "检测 Dola 登录", async () => await RunEnvironmentCheck(id, 2), running);
        if (!running) Hint(overview, "先打开浏览器，才能读取当前网站的登录状态。");
        Detail(overview, "环境分组", Str(p, "accountGroup", "未分组").Length > 0 ? Str(p, "accountGroup") : "未分组");
        if (Str(p, "accountNotes").Length > 0) Detail(overview, "环境备注", Str(p, "accountNotes"));
        Detail(overview, "最近打开", Str(p, "lastUsedAt").Length > 0 ? AccountTime(Str(p, "lastUsedAt")) : "尚未打开");

        var accountPage = Page("账号"); var bound = AccountObject(p, "loginAccount"); bool hasBound = bound.ValueKind == JsonValueKind.Object;
        Detail(accountPage, "Google 账号", hasBound ? Str(bound, "email") : "手动登录环境", hasBound ? Accent : Muted);
        Detail(accountPage, "网站身份", LoginIdentityLabel(p));
        Hint(accountPage, hasBound ? "绑定决定自动登录时填写的 Google 邮箱和密码，不会替换已有的网站会话。" : "你可以直接在浏览器中手动登录，也可以在环境设置中绑定 Google 账号后自动登录。");
        Action(accountPage, hasBound ? "使用绑定账号登录 Dola" : "通过 Google 登录 Dola", () => { if (hasBound) StartBoundGoogleLogin(id); else ShowGoogleLogin(id); });
        Action(accountPage, "修改账号绑定", () => ShowInlineProfileSettings(id));
        Action(accountPage, "管理 Google 邮箱与密码", ShowLoginAccounts);

        var proxyPage = Page("代理"); var network = AccountNetworkObservation(p);
        Detail(proxyPage, "出口国家", network.ValueKind == JsonValueKind.Object ? NetworkCountryName(network) : "尚未检测", Accent);
        if (network.ValueKind == JsonValueKind.Object) {
            Detail(proxyPage, "出口城市", Str(network, "city", "未知"));
            Detail(proxyPage, "网络检测", AccountTime(Str(network, "checkedAt", Str(network, "testedAt"))));
            Detail(proxyPage, "连接结果", Str(network, "reachable").Equals("true", StringComparison.OrdinalIgnoreCase) ? "上次检测可连接" : Str(network, "error", "连接未确认"));
        }
        Action(proxyPage, "检测此环境的代理出口", async () => await RunEnvironmentCheck(id, 3));
        Hint(proxyPage, "这里检测此环境实际使用的出口。线路可连接，不代表 Dola 支持该国家或地区。");
        var proxyId = Str(p, "proxyId"); var proxy = proxyCatalog.FirstOrDefault(entry => Str(entry, "id") == proxyId);
        Detail(proxyPage, "代理线路", proxyId.Length == 0 ? Str(p, "proxy").Length > 0 ? "手动代理" : "本机直连" : proxy.ValueKind == JsonValueKind.Object ? Str(proxy, "name") : "代理已移除");
        var session = AccountObject(p, "proxySession"); bool independent = session.ValueKind == JsonValueKind.Object && Str(session, "mode") == "per_profile";
        if (session.ValueKind == JsonValueKind.Object) {
            Detail(proxyPage, "住宅会话", independent ? "此环境独立会话" : "固定供应商会话");
            Detail(proxyPage, "会话到期", double.TryParse(Str(session, "expiresAt"), System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var expires) ? DateTimeOffset.FromUnixTimeSeconds((long)expires).ToLocalTime().ToString("MM/dd HH:mm") : "按供应商配置");
            if (Str(session, "expired").Equals("true", StringComparison.OrdinalIgnoreCase)) Hint(proxyPage, "住宅会话已到期。请关闭浏览器后更换会话，再检测出口。");
        }
        Action(proxyPage, "选择 / 修改代理", () => ShowInlineProfileSettings(id));
        if (independent) Action(proxyPage, "更换住宅会话", async () => await CenterSafe(async () => { if (centerBusy) return; await Api($"profiles/{id}/proxy-session/rotate", new { }); environmentNetworkChecks.Remove(id); await Refresh(); await LoadAccountDetails(id); }), !running);
        if (running) Hint(proxyPage, "修改代理或更换住宅会话前，请先关闭此浏览器。");

        var fingerprint = Page("指纹");
        Detail(fingerprint, "语言", Str(p, "locale")); Detail(fingerprint, "时区", Str(p, "timezone"));
        Detail(fingerprint, "系统", Str(p, "fingerprintPlatform", "默认"));
        Detail(fingerprint, "屏幕指纹", Str(p, "fingerprintScreenWidth").Length > 0 && Str(p, "fingerprintScreenWidth") != "null" ? Str(p, "fingerprintScreenWidth") + " × " + Str(p, "fingerprintScreenHeight") : "由内核按固定种子生成");
        Detail(fingerprint, "地区方案", Str(p, "regionMode") == "proxy" ? "按检测出口推荐后保存" : "手动配置");
        Hint(fingerprint, "指纹在保存后固定使用。检测代理不会自动更改运行中的语言、时区或硬件特征。");
        if (network.ValueKind == JsonValueKind.Object && Str(network, "reachable").Equals("true", StringComparison.OrdinalIgnoreCase)) {
            var countryCode = NetworkCountryCode(network); var exitZone = Str(network, "timezone");
            bool languageMismatch = FingerprintRegions.Any(r => r.Locale.Split('-').Last().Equals(countryCode, StringComparison.OrdinalIgnoreCase))
                && !Str(p, "locale").Split('-').Last().Equals(countryCode, StringComparison.OrdinalIgnoreCase);
            if (languageMismatch || exitZone.Length > 0 && exitZone != Str(p, "timezone")) {
                Hint(fingerprint, "出口与保存的地区设置不同：" + NetworkCountryName(network) + (exitZone.Length > 0 ? " · " + exitZone : "") + "。请关闭浏览器后在环境设置中检测并确认推荐地区；不会自动修改指纹。");
            }
        }
        Action(fingerprint, "配置语言、时区与指纹", () => ShowInlineProfileSettings(id));

        var backup = Page("登录备份");
        Hint(backup, "日常使用直接打开原环境，即可继续本地保存的登录。无需反复导入 Cookie。");
        Detail(backup, "备份内容", "某个时间的网站会话快照"); Detail(backup, "恢复方式", "使用备份创建新的恢复环境，保留原环境");
        Hint(backup, "数据恢复完成后，仍需检测 Dola 是否接受该会话。同一份 Cookie 不代表两个独立账号。");
        Action(backup, "管理 / 导出登录备份", () => ShowSessionBackup(id));

        void SelectPage(string title) { detailContent.Content = pages[title]; foreach (var (key, button) in tabButtons) { button.Background = key == title ? Hex("#293D32") : Brushes.Transparent; button.Foreground = key == title ? Accent : Muted; } }
        foreach (var title in pages.Keys) {
            var button = Button(title, () => SelectPage(title)); button.FontSize = 12; button.Padding = new Thickness(10, 8, 10, 8); button.Margin = new Thickness(0, 0, 4, 4); button.BorderThickness = new Thickness(0); tabButtons[title] = button; tabs.Children.Add(button);
        }
        SelectPage("概览");
    }
    async Task RunEnvironmentCheck(string id, int kind)
    {
        if (centerBusy) return;
        selectedAccounts.Clear(); selectedAccounts.Add(id); centerAction.SelectedIndex = kind;
        await CenterSafe(ExecuteAccountAction);
    }
    static string AccountReason(string reason) => reason switch {
        "session_not_checked" => "本次打开尚未检测；上次登录记录仅供参考。",
        "bound_account_not_verified" => "网站已登录，但尚未核验是否对应绑定的 Google 邮箱。",
        "site_identity_not_observed" => "已观察到网站会话，尚未读取可核验的账号身份。",
        "page_session_confirmed" => "Dola 页面提供了已登录依据；尚未核验是否为绑定的 Google 邮箱。",
        "page_session_signed_out" => "Dola 返回未登录状态，请在浏览器中重新登录。",
        "browser_closed" => "请先打开浏览器，再检测登录状态。", "not_dola_page" => "当前页面不是 Dola，请进入 Dola 后检测。", "insufficient_evidence" => "页面尚未提供完整登录依据，可等待加载完成或展开账号菜单后重试。", "region_blocked" or "region_message" => "服务提示地区受限。请检测真实出口国家，并检查环境地区配置。", "network_error" => "代理连接异常，请检测出口并检查上游线路。", "login_required" or "visible_login" => "页面显示登录入口，请打开浏览器完成登录。", "visible_logout" => "页面出现明确的退出登录入口，已确认登录。", "not_checked" => "尚未检测。请打开浏览器后读取登录状态。", "proxy_changed" => "代理配置已变化，请重新检测登录与出口。", "session_changed" => "浏览器会话已变化，请重新检测。", "check_failed" => "检测未完成，请检查连接后重试。", "unknown" => "尚无足够证据确认账号可用。", _ => reason };
    void ShowInlineAccountEdit(string id, JsonElement p)
    {
        ShowInlineProfileSettings(id);
    }
}
