using System.ComponentModel;
using System.Diagnostics;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Data;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Threading;

namespace CloakWorkbench;

public sealed partial class MainWindow
{
    readonly List<JsonElement> proxyCatalog = new();
    sealed record ProxyChoice(string Id, string Label);
    sealed class ProxyRow : INotifyPropertyChanged
    {
        public event PropertyChangedEventHandler? PropertyChanged;
        bool selected;
        public bool Selected { get => selected; set { selected = value; PropertyChanged?.Invoke(this, new(nameof(Selected))); } }
        public JsonElement Data { get; private set; }
        public string CheckState { get; private set; } = "";
        public bool Pending => CheckState is "queued" or "running";
        public bool PreviousResult => Pending || CheckState is "cancelled" or "unapplied" or "aborted";
        public Brush CheckBrush => Pending ? WorkbenchTheme.Blue : Reachable == "可连接" ? WorkbenchTheme.Green : Reachable == "连接失败" ? WorkbenchTheme.Color("#E8B578") : WorkbenchTheme.Muted;
        public Brush CheckBackground => Pending ? WorkbenchTheme.Color("#203B33") : Reachable == "可连接" ? WorkbenchTheme.Color("#203B30") : Reachable == "连接失败" ? WorkbenchTheme.Color("#392E20") : WorkbenchTheme.Color("#2B332E");
        public void Update(JsonElement data, string state) {
            if (Data.ValueKind == JsonValueKind.Object && Data.GetRawText() == data.GetRawText() && CheckState == state) return;
            Data = data; CheckState = state; PropertyChanged?.Invoke(this, new(null));
        }
        public string Id => Str(Data, "id");
        public string Name => Str(Data, "name");
        public string Kind => Str(Data, "kind") == "residential" ? Str(Data, "sessionMode") == "per_profile" ? "住宅 · 独立会话" : "住宅 · 固定认证" : "普通代理";
        public string Address => PublicAddress(Str(Data, "proxy"));
        public string Upstream => PublicAddress(Str(Data, "upstreamProxy"));
        public string Bindings => Num(Data, "bindingCount") + " 个环境";
        public JsonElement Check => Data.TryGetProperty("check", out var check) && check.ValueKind == JsonValueKind.Object ? check : default;
        public string Value(string key) => Check.ValueKind == JsonValueKind.Object && Check.TryGetProperty(key, out var v) && v.ValueKind != JsonValueKind.Null && v.ToString().Length > 0 ? v.ToString() : "—";
        public string Reachable => CheckState == "queued" ? "等待检测" : CheckState == "running" ? "检测中…" : CheckState == "cancelled" ? "已取消" : CheckState == "unapplied" ? "未保存" : CheckState == "aborted" ? "未完成" : Check.ValueKind != JsonValueKind.Object ? "未检测" : Value("reachable").Equals("true", StringComparison.OrdinalIgnoreCase) ? "可连接" : "连接失败";
        public string Ip => Value("egressIp");
        public string Latency => Value("latencyMs") == "—" ? "—" : Value("latencyMs") + " ms";
        public string Isp => Value("isp");
        public string Asn => Value("asn");
        public string Country => Check.ValueKind == JsonValueKind.Object ? string.Join(" / ", new[] { NetworkCountryName(Check), Value("region"), Value("city") }.Where(value => value != "—").Distinct()) : "未知";
        public string Residential => ResidentialLabel(Value("isResidential"));
        public string Observation => Pending ? Check.ValueKind == JsonValueKind.Object ? "上次结果 · 更新中" : "正在获取" : CheckState == "cancelled" ? "已取消 · 未更新" : CheckState == "unapplied" ? "本次结果未保存" : CheckState == "aborted" ? "任务中断 · 未更新" : Value("sessionScope") == "temporary" ? "临时会话快照" : Value("rotatingExit").Equals("true", StringComparison.OrdinalIgnoreCase) ? "出口有变化" : Check.ValueKind == JsonValueKind.Object ? "本次快照" : "未检测";
        public string Risk => Value("riskScore") == "—" ? "未知" : Value("riskScore") + " / 100";
        public string Quality => Value("qualityScore") == "—" ? "未知" : Value("qualityScore") + " / 100";
        public string Time => Check.ValueKind == JsonValueKind.Object ? CheckTime(Check) : "—";
    }
    static FrameworkElement ProxySpinner() {
        var spinner = new System.Windows.Shapes.Path { Data = Geometry.Parse("M12,3 A9,9 0 1 1 3,12"), Stroke = WorkbenchTheme.Blue, StrokeThickness = 2.4,
            StrokeStartLineCap = PenLineCap.Round, StrokeEndLineCap = PenLineCap.Round, Width = 24, Height = 24, RenderTransformOrigin = new Point(.5, .5), RenderTransform = new RotateTransform() };
        void Animate() {
            ((RotateTransform)spinner.RenderTransform).BeginAnimation(RotateTransform.AngleProperty,
                spinner.IsVisible && SystemParameters.ClientAreaAnimation ? new DoubleAnimation(0, 360, TimeSpan.FromSeconds(1)) { RepeatBehavior = RepeatBehavior.Forever } : null);
        }
        spinner.IsVisibleChanged += (_, _) => Animate(); spinner.Unloaded += (_, _) => ((RotateTransform)spinner.RenderTransform).BeginAnimation(RotateTransform.AngleProperty, null);
        return spinner;
    }
    static string ResidentialLabel(string value) => value.Equals("true", StringComparison.OrdinalIgnoreCase) ? "住宅" : value.Equals("false", StringComparison.OrdinalIgnoreCase) ? "非住宅" : "未知";
    static string RiskLabel(string value) => value switch { "low" => "低风险", "medium" => "中等风险", "high" => "高风险", _ => "未知" };
    static string PublicAddress(string value)
    {
        if (value.Length == 0) return "—";
        if (!Uri.TryCreate(value, UriKind.Absolute, out var uri) || uri.Host.Length == 0) return "地址格式待检查";
        return new UriBuilder(uri) { UserName = "", Password = "", Path = "", Query = "", Fragment = "" }.Uri.GetLeftPart(UriPartial.Authority);
    }
    static string CheckTime(JsonElement check)
    {
        var value = Str(check, "checkedAt");
        if (DateTimeOffset.TryParse(value, out var date)) return date.ToLocalTime().ToString("MM-dd HH:mm:ss");
        if (double.TryParse(value, System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var epoch)) {
            try { return DateTimeOffset.FromUnixTimeSeconds((long)epoch).ToLocalTime().ToString("MM-dd HH:mm:ss"); } catch (ArgumentOutOfRangeException) { }
        }
        return "—";
    }
    async Task LoadProxyCatalog()
    {
        var result = await Api("proxies"); proxyCatalog.Clear();
        foreach (var item in result.GetProperty("proxies").EnumerateArray()) proxyCatalog.Add(item.Clone());
    }
    string CatalogRoute(string id)
    {
        var item = proxyCatalog.FirstOrDefault(item => Str(item, "id") == id);
        if (item.ValueKind != JsonValueKind.Object) return "代理不可用，请刷新列表或重新选择。";
        return Str(item, "upstreamProxy").Length > 0
            ? "本机 → 上游 " + PublicAddress(Str(item, "upstreamProxy")) + " → " + (Str(item, "kind") == "residential" ? "住宅 " : "代理 ") + PublicAddress(Str(item, "proxy")) + " → 网站"
            : "本机 → " + PublicAddress(Str(item, "proxy")) + " → 网站";
    }
    async Task ShowProxyManager(Window owner)
    {
        var previous = owner.Content;
        var returned = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        void Back() { owner.Content = previous; returned.TrySetResult(); }
        EventHandler closed = (_, _) => returned.TrySetResult();
        owner.Closed += closed;
        owner.Content = CreateProxyManagementPage(owner, Back);
        try { await returned.Task; }
        finally { owner.Closed -= closed; }
    }
    FrameworkElement CreateProxyManagementPage(Window window, Action? returnAction = null)
    {
        var page = new ContentControl { HorizontalContentAlignment = HorizontalAlignment.Stretch, VerticalContentAlignment = VerticalAlignment.Stretch };
        var layout = new DockPanel { Margin = new Thickness(26) };
        page.Content = layout;
        var header = new DockPanel { Margin = new Thickness(0, 0, 0, 18) };
        if (returnAction != null) { var back = IconButton("back", "返回环境配置", returnAction); back.VerticalAlignment = VerticalAlignment.Top; DockPanel.SetDock(back, Dock.Right); header.Children.Add(back); }
        var title = new StackPanel(); title.Children.Add(new TextBlock { Text = "代理管理", FontSize = 25, FontWeight = FontWeights.SemiBold, Foreground = Text });
        title.Children.Add(new TextBlock { Text = "一处管理网络线路，环境按需选择。检测结果为当次出口快照，轮换线路的 IP 可变化。", Foreground = Muted, FontSize = 12, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 7, 0, 0) }); header.Children.Add(title);
        DockPanel.SetDock(header, Dock.Top); layout.Children.Add(header);
        var route = new Border { Background = WorkbenchTheme.Navy, CornerRadius = new CornerRadius(10), Padding = new Thickness(18, 13, 18, 13), Margin = new Thickness(0, 0, 0, 18) };
        var routePanel = new DockPanel(); var routeBadge = Label("住宅线路", 11, Hex("#93C5FD")); routeBadge.Margin = new Thickness(0, 0, 20, 0); DockPanel.SetDock(routeBadge, Dock.Left); routePanel.Children.Add(routeBadge);
        routePanel.Children.Add(new TextBlock { Text = "本机  →  上游代理（外网通道）  →  住宅代理（最终出口）  →  网站", Foreground = Brushes.White, FontSize = 13, TextWrapping = TextWrapping.Wrap }); route.Child = routePanel; DockPanel.SetDock(route, Dock.Top); layout.Children.Add(route);
        var feedback = new TextBlock { Text = "正在读取代理库…", Foreground = Muted, FontSize = 12, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 10, 0, 0) };
        var bottom = new StackPanel(); bottom.Children.Add(feedback); bottom.Children.Add(new TextBlock { Text = "质量分为参考诊断，信息不足时显示未知。编辑线路前请关闭已绑定的运行环境；保存后下次启动生效。", Foreground = Muted, FontSize = 11, Margin = new Thickness(0, 8, 0, 0), TextWrapping = TextWrapping.Wrap }); DockPanel.SetDock(bottom, Dock.Bottom); layout.Children.Add(bottom);
        var tools = new WrapPanel { Margin = new Thickness(0, 0, 0, 14) }; DockPanel.SetDock(tools, Dock.Top); layout.Children.Add(tools);
        var activity = new Border { Background = Hex("#203B33"), BorderBrush = Hex("#345D4F"), BorderThickness = new Thickness(1), CornerRadius = new CornerRadius(10), Padding = new Thickness(16, 12, 16, 12), Margin = new Thickness(0, 0, 0, 14), Visibility = Visibility.Collapsed };
        System.Windows.Automation.AutomationProperties.SetName(activity, "代理检测状态");
        var activityPanel = new StackPanel(); var activityHeading = new DockPanel();
        var spinner = ProxySpinner(); spinner.Margin = new Thickness(0, 0, 12, 0); DockPanel.SetDock(spinner, Dock.Left); activityHeading.Children.Add(spinner);
        var activityMark = new TextBlock { FontSize = 21, FontWeight = FontWeights.SemiBold, Margin = new Thickness(0, 0, 12, 0), Visibility = Visibility.Collapsed }; DockPanel.SetDock(activityMark, Dock.Left); activityHeading.Children.Add(activityMark);
        var counter = new TextBlock { Foreground = Accent, FontSize = 12, FontWeight = FontWeights.SemiBold, VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(12, 0, 0, 0) }; DockPanel.SetDock(counter, Dock.Right); activityHeading.Children.Add(counter);
        System.Windows.Automation.AutomationProperties.SetName(counter, "代理检测进度");
        var activityTitle = new TextBlock { Foreground = Text, FontSize = 14, FontWeight = FontWeights.SemiBold, VerticalAlignment = VerticalAlignment.Center }; activityHeading.Children.Add(activityTitle); activityPanel.Children.Add(activityHeading);
        var activityHint = new TextBlock { Foreground = Muted, FontSize = 11, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(36, 7, 0, 0) }; activityPanel.Children.Add(activityHint);
        var progress = new ProgressBar { Height = 4, Minimum = 0, Maximum = 1, Value = 0, Foreground = Accent, Background = Hex("#2A3931"), Margin = new Thickness(36, 11, 0, 0) };
        System.Windows.Automation.AutomationProperties.SetName(progress, "代理检测进度条"); activityPanel.Children.Add(progress); activity.Child = activityPanel; DockPanel.SetDock(activity, Dock.Top); layout.Children.Add(activity);
        var table = new DataGrid { AutoGenerateColumns = false, CanUserAddRows = false, CanUserDeleteRows = false, HeadersVisibility = DataGridHeadersVisibility.Column, RowHeaderWidth = 0,
            GridLinesVisibility = DataGridGridLinesVisibility.Horizontal, HorizontalGridLinesBrush = Line, BorderBrush = Line, BorderThickness = new Thickness(1), Background = Panel,
            RowBackground = Panel, AlternatingRowBackground = Hex("#171D19"), RowHeight = 60, ColumnHeaderHeight = 38, SelectionMode = DataGridSelectionMode.Single, SelectionUnit = DataGridSelectionUnit.FullRow,
            EnableRowVirtualization = true, FontSize = 12, Foreground = Text, HorizontalScrollBarVisibility = ScrollBarVisibility.Auto, VerticalScrollBarVisibility = ScrollBarVisibility.Auto };
        System.Windows.Automation.AutomationProperties.SetName(table, "代理列表");
        var checkTemplate = new DataTemplate(); var factory = new FrameworkElementFactory(typeof(CheckBox)); factory.SetBinding(CheckBox.IsCheckedProperty, new Binding("Selected") { Mode = BindingMode.TwoWay, UpdateSourceTrigger = UpdateSourceTrigger.PropertyChanged }); factory.SetValue(FrameworkElement.HorizontalAlignmentProperty, HorizontalAlignment.Center); factory.SetValue(FrameworkElement.VerticalAlignmentProperty, VerticalAlignment.Center); checkTemplate.VisualTree = factory;
        var selectAll = new CheckBox { ToolTip = "选择全部代理", VerticalAlignment = VerticalAlignment.Center, HorizontalAlignment = HorizontalAlignment.Center }; System.Windows.Automation.AutomationProperties.SetName(selectAll, "全选代理"); table.Columns.Add(new DataGridTemplateColumn { Header = selectAll, CellTemplate = checkTemplate, Width = 42 });
        var cell = new Style(typeof(TextBlock)); cell.Setters.Add(new Setter(TextBlock.VerticalAlignmentProperty, VerticalAlignment.Center)); cell.Setters.Add(new Setter(TextBlock.MarginProperty, new Thickness(10, 0, 10, 0))); cell.Setters.Add(new Setter(TextBlock.TextTrimmingProperty, TextTrimming.CharacterEllipsis));
        var rowStyle = new Style(typeof(DataGridRow)); var pendingRow = new DataTrigger { Binding = new Binding("Pending"), Value = true }; pendingRow.Setters.Add(new Setter(Control.BackgroundProperty, Hex("#203B33"))); rowStyle.Triggers.Add(pendingRow); table.RowStyle = rowStyle;
        void Column(string headerText, string property, double width) => table.Columns.Add(new DataGridTextColumn { Header = headerText, Binding = new Binding(property), Width = width, IsReadOnly = true, ElementStyle = cell });
        Column("名称", "Name", 150); Column("国家 / 城市", "Country", 170);
        var statusTemplate = new DataTemplate(); var badge = new FrameworkElementFactory(typeof(Border)); badge.SetBinding(Border.BackgroundProperty, new Binding("CheckBackground")); badge.SetValue(Border.CornerRadiusProperty, new CornerRadius(5)); badge.SetValue(Border.PaddingProperty, new Thickness(9, 5, 9, 5)); badge.SetValue(FrameworkElement.HorizontalAlignmentProperty, HorizontalAlignment.Left); badge.SetValue(FrameworkElement.VerticalAlignmentProperty, VerticalAlignment.Center); badge.SetValue(FrameworkElement.MarginProperty, new Thickness(10, 0, 6, 0));
        var statusText = new FrameworkElementFactory(typeof(TextBlock)); statusText.SetBinding(TextBlock.TextProperty, new Binding("Reachable")); statusText.SetBinding(TextBlock.ForegroundProperty, new Binding("CheckBrush")); statusText.SetValue(TextBlock.FontSizeProperty, 11.0); badge.AppendChild(statusText); statusTemplate.VisualTree = badge;
        table.Columns.Add(new DataGridTemplateColumn { Header = "连接 / 状态", CellTemplate = statusTemplate, Width = 110, IsReadOnly = true });
        var resultCell = new Style(typeof(TextBlock), cell); var oldResult = new DataTrigger { Binding = new Binding("PreviousResult"), Value = true }; oldResult.Setters.Add(new Setter(UIElement.OpacityProperty, .45)); resultCell.Triggers.Add(oldResult);
        void ResultColumn(string label, string property, double width) => table.Columns.Add(new DataGridTextColumn { Header = label, Binding = new Binding(property), Width = width, IsReadOnly = true, ElementStyle = resultCell });
        ResultColumn("质量分（高好）", "Quality", 110); Column("绑定环境", "Bindings", 90); Column("最近检测", "Time", 125);
        var advancedColumns = new List<DataGridColumn>();
        void Advanced(string label, string property, double width) { ResultColumn(label, property, width); var column = table.Columns[^1]; column.Visibility = Visibility.Collapsed; advancedColumns.Add(column); }
        Advanced("类型", "Kind", 110); Advanced("出口 IP（快照）", "Ip", 145); Advanced("耗时", "Latency", 85); Advanced("ISP", "Isp", 150); Advanced("风险分（低好）", "Risk", 110); Advanced("住宅属性", "Residential", 90); Advanced("ASN", "Asn", 100); Advanced("出口观测", "Observation", 135); Advanced("代理地址", "Address", 210); Advanced("上游地址", "Upstream", 195);
        layout.Children.Add(new Border { Child = table, CornerRadius = new CornerRadius(10), BorderBrush = Line, BorderThickness = new Thickness(0) });
        List<ProxyRow> rows = new(); bool busy = false, ended = false, pollingJob = false; string? jobId = null; var jobTimer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(1) };
        var rowStates = new Dictionary<string, string>(); var elapsed = new Stopwatch(); string jobState = "";
        var operations = new List<Button>(); Button? cancelCheck = null;
        void SetBusy(bool value) { busy = value; foreach (var operation in operations) operation.IsEnabled = !value; cancelCheck!.IsEnabled = value && jobId != null && jobState != "cancelling"; }
        void ShowActivity(string titleText, string hint, bool active, bool warning = false) {
            proxyActivitySummary = active ? "代理检测 · " + titleText : null; UpdateGlobalActivity();
            bool opening = activity.Visibility != Visibility.Visible; activity.Visibility = Visibility.Visible;
            activityTitle.Text = titleText; activityHint.Text = hint; spinner.Visibility = active ? Visibility.Visible : Visibility.Collapsed;
            activityMark.Visibility = active ? Visibility.Collapsed : Visibility.Visible; activityMark.Text = warning ? "!" : "✓";
            activityMark.Foreground = warning ? Hex("#E8B578") : WorkbenchTheme.Green;
            activity.Background = active ? Hex("#203B33") : warning ? Hex("#392E20") : Hex("#203B30");
            activity.BorderBrush = active ? Hex("#345D4F") : warning ? Hex("#675236") : Hex("#345D4F");
            progress.Visibility = active ? Visibility.Visible : Visibility.Collapsed;
            if (opening && SystemParameters.ClientAreaAnimation) activity.BeginAnimation(UIElement.OpacityProperty, new DoubleAnimation(0, 1, TimeSpan.FromMilliseconds(180)));
        }
        void UpdateRows() { foreach (var row in rows) row.Update(row.Data, rowStates.GetValueOrDefault(row.Id, "")); }
        async Task Reload(bool showCount = true) {
            var checks = rows.Where(row => row.Selected).Select(row => row.Id).ToHashSet(); var active = (table.SelectedItem as ProxyRow)?.Id;
            await LoadProxyCatalog(); if (ended) return;
            if (!rows.Select(row => row.Id).SequenceEqual(proxyCatalog.Select(item => Str(item, "id")))) {
                rows = proxyCatalog.Select(item => { var row = new ProxyRow { Selected = checks.Contains(Str(item, "id")) }; row.Update(item.Clone(), rowStates.GetValueOrDefault(Str(item, "id"), "")); return row; }).ToList(); table.ItemsSource = rows;
                table.SelectedItem = rows.FirstOrDefault(row => row.Id == active);
            } else foreach (var row in rows) row.Update(proxyCatalog.First(item => Str(item, "id") == row.Id).Clone(), rowStates.GetValueOrDefault(row.Id, ""));
            if (showCount && !busy) feedback.Text = $"{rows.Count} 条线路  ·  {rows.Count(row => row.Reachable == "可连接")} 条最近检测可用";
        }
        async Task Safe(Func<Task> action) { try { await action(); } catch (Exception ex) { if (!ended) feedback.Text = ex.Message; } }
        async Task PollJob() {
            if (jobId == null || ended || pollingJob) return; pollingJob = true;
            try {
                var job = await Api("proxy-checks/" + jobId); if (ended) return;
                jobState = Str(job, "state");
                if (job.TryGetProperty("items", out var items)) foreach (var item in items.EnumerateObject()) rowStates[item.Name] = item.Value.GetString() ?? "";
                var results = job.GetProperty("results").EnumerateArray().ToArray();
                foreach (var result in results) if (!result.GetProperty("applied").GetBoolean() && !result.GetProperty("check").TryGetProperty("cancelled", out _)) rowStates[Str(result, "id")] = "unapplied";
                int completed = Num(job, "completed"), total = Num(job, "total"), succeeded = results.Count(r => Str(r.GetProperty("check"), "reachable").Equals("true", StringComparison.OrdinalIgnoreCase)),
                    failed = results.Count(r => !r.GetProperty("check").TryGetProperty("cancelled", out _) && !Str(r.GetProperty("check"), "reachable").Equals("true", StringComparison.OrdinalIgnoreCase)),
                    cancelled = results.Count(r => r.GetProperty("check").TryGetProperty("cancelled", out _)), unapplied = results.Count(r => !r.GetProperty("applied").GetBoolean() && !r.GetProperty("check").TryGetProperty("cancelled", out _));
                progress.Maximum = Math.Max(1, total); progress.IsIndeterminate = completed == 0 && jobState is "running" or "cancelling";
                if (progress.Value != completed) {
                    var from = progress.Value; progress.BeginAnimation(System.Windows.Controls.Primitives.RangeBase.ValueProperty, null); progress.Value = completed;
                    if (SystemParameters.ClientAreaAnimation) progress.BeginAnimation(System.Windows.Controls.Primitives.RangeBase.ValueProperty, new DoubleAnimation(from, completed, TimeSpan.FromMilliseconds(280)) { FillBehavior = FillBehavior.Stop });
                }
                counter.Text = $"{completed} / {total}  ·  {(int)elapsed.Elapsed.TotalSeconds} 秒";
                if (jobState is "running" or "cancelling") {
                    ShowActivity(jobState == "cancelling" ? "正在取消检测…" : "正在检测代理", $"检测中 {rowStates.Values.Count(s => s == "running")}  ·  等待 {rowStates.Values.Count(s => s == "queued")}  ·  成功 {succeeded}  ·  失败 {failed}。" + (jobState == "cancelling" ? "等待已开始的连接结束。" : "旧结果已淡化，完成后逐条更新。"), true);
                    cancelCheck!.IsEnabled = jobState != "cancelling"; feedback.Text = $"正在检测  {completed} / {total}  ·  结果逐项更新";
                }
                await Reload(false);
                if (ended) return;
                if (jobState is "completed" or "cancelled" or "failed") {
                    jobTimer.Stop(); elapsed.Stop(); jobId = null; SetBusy(false); progress.IsIndeterminate = false; progress.BeginAnimation(System.Windows.Controls.Primitives.RangeBase.ValueProperty, null);
                    var summary = $"成功 {succeeded} 条  ·  失败 {failed} 条" + (cancelled > 0 ? $"  ·  跳过 {cancelled} 条" : "") + (unapplied > 0 ? $"  ·  未保存 {unapplied} 条（配置已变化或保存失败）" : "");
                    var titleText = jobState == "completed" ? "检测完成" : jobState == "cancelled" ? "检测已取消" : "检测任务失败";
                    ShowActivity(titleText, summary, false, failed > 0 || jobState != "completed" || unapplied > 0); feedback.Text = titleText + "  ·  " + summary;
                }
            } catch (Exception ex) { if (!ended) { ShowActivity("进度暂时读取失败，正在重试…", "已开始的检测继续进行，可以取消任务。", true); feedback.Text = "检测进度读取失败：" + ex.Message; } }
            finally { pollingJob = false; }
        }
        async Task Check(string[] ids) {
            if (busy) return; if (ids.Length == 0) { feedback.Text = "请先勾选至少一条代理。"; return; }
            jobState = "running"; SetBusy(true); elapsed.Restart(); rowStates.Clear(); foreach (var id in ids) rowStates[id] = "queued"; UpdateRows();
            feedback.Text = "正在创建检测任务…"; counter.Text = $"0 / {ids.Length}  ·  0 秒"; progress.BeginAnimation(System.Windows.Controls.Primitives.RangeBase.ValueProperty, null); progress.Value = 0; progress.Maximum = ids.Length; progress.IsIndeterminate = true;
            ShowActivity("正在启动检测…", "通过所配置的代理线路获取出口与网络信息。", true);
            try { var job = await Api("proxies/check", new { ids }); jobId = Str(job, "id"); if (jobId.Length == 0) throw new InvalidOperationException("检测任务没有返回任务编号。"); if (ended) { try { await Api("proxy-checks/" + jobId + "/cancel", new { }); } catch { } return; } cancelCheck!.IsEnabled = true; jobTimer.Start(); await PollJob(); }
            catch { jobId = null; elapsed.Stop(); SetBusy(false); rowStates.Clear(); UpdateRows(); progress.IsIndeterminate = false; ShowActivity("检测未启动", "任务创建失败，请查看下方说明后重试。", false, true); throw; }
        }
        Button Add(string icon, string caption, Func<Task> action, bool primary = false) { var button = IconButton(icon, caption, async () => await Safe(action), primary); tools.Children.Add(button); operations.Add(button); return button; }
        void BackToList() { page.Content = layout; _ = Safe(() => Reload()); }
        Add("plus", "新建代理", () => { page.Content = BuildProxyEditor(window, null, BackToList); return Task.CompletedTask; }, true);
        Add("settings", "编辑", () => { if (table.SelectedItem is not ProxyRow row) { feedback.Text = "请点击要编辑的代理行。"; return Task.CompletedTask; } if (profiles.Values.Any(profile => Str(profile, "proxyId") == row.Id && Str(profile, "state") is "running" or "starting")) { feedback.Text = "请先关闭使用这条代理的运行环境，再修改线路。"; return Task.CompletedTask; } page.Content = BuildProxyEditor(window, row.Data, BackToList); return Task.CompletedTask; });
        var checkSelected = Add("shield", "检测选中代理", async () => { var ids = rows.Where(row => row.Selected).Select(row => row.Id).ToArray(); if (ids.Length == 0 && table.SelectedItem is ProxyRow row) ids = new[] { row.Id }; await Check(ids); });
        checkSelected.ToolTip = "优先检测勾选的代理；未勾选时检测当前高亮行。";
        Add("shield", "检测全部", async () => await Check(rows.Select(row => row.Id).ToArray()));
        Add("diagnostic", "检测详情", () => { if (table.SelectedItem is ProxyRow row) page.Content = BuildProxyDetails(row, BackToList); else feedback.Text = "请点击要查看的代理行。"; return Task.CompletedTask; });
        Add("close", "删除", async () => { if (table.SelectedItem is not ProxyRow row) { feedback.Text = "请点击要删除的代理行。"; return; } if (Num(row.Data, "bindingCount") > 0) { feedback.Text = "这条代理已被环境使用，请先修改相关环境的代理选择。"; return; } if (MessageBox.Show(window, "删除代理“" + row.Name + "”？", "删除代理", MessageBoxButton.YesNo, MessageBoxImage.Question) == MessageBoxResult.Yes) { await Api("proxies/" + row.Id + "/delete", new { }); await Reload(); } });
        var refresh = IconButton("reload", "刷新", async () => await Safe(() => Reload())); tools.Children.Add(refresh);
        bool showAdvanced = false;
        var advancedToggle = Button("显示详细列", () => { showAdvanced = !showAdvanced; foreach (var column in advancedColumns) column.Visibility = showAdvanced ? Visibility.Visible : Visibility.Collapsed; }); advancedToggle.ToolTip = "展开 IP、ISP、ASN、风险、住宅属性和线路地址"; tools.Children.Add(advancedToggle);
        cancelCheck = Button("取消检测", async () => await Safe(async () => {
            if (jobId == null) return; cancelCheck!.IsEnabled = false;
            try { await Api("proxy-checks/" + jobId + "/cancel", new { }); jobState = "cancelling"; ShowActivity("正在取消检测…", "等待已开始的连接结束，尚未开始的代理将跳过。", true); feedback.Text = "已请求取消；等待正在进行的连接结束…"; await PollJob(); }
            catch { cancelCheck.IsEnabled = busy && jobId != null; throw; }
        })); cancelCheck.IsEnabled = false; tools.Children.Add(cancelCheck);
        void SelectAll(bool selectedValue) { foreach (var row in rows) row.Selected = selectedValue; table.Items.Refresh(); }
        selectAll.Checked += (_, _) => SelectAll(true); selectAll.Unchecked += (_, _) => SelectAll(false);
        table.MouseDoubleClick += (_, _) => { if (!busy && table.SelectedItem is ProxyRow row) page.Content = BuildProxyDetails(row, BackToList); };
        jobTimer.Tick += async (_, _) => await PollJob(); layout.Loaded += async (_, _) => await Safe(() => Reload());
        async void Cleanup(object? sender, EventArgs args) { if (ended) return; ended = true; jobTimer.Stop(); elapsed.Stop(); proxyActivitySummary = null; UpdateGlobalActivity(); progress.IsIndeterminate = false; progress.BeginAnimation(System.Windows.Controls.Primitives.RangeBase.ValueProperty, null); activity.BeginAnimation(UIElement.OpacityProperty, null); window.Closed -= Cleanup; if (jobId != null) try { await Api("proxy-checks/" + jobId + "/cancel", new { }); } catch { } }
        window.Closed += Cleanup;
        page.Unloaded += Cleanup;
        return page;
    }

    Window CreateProxyEditor(Window owner, JsonElement? saved)
    {
        var editor = new Window { Owner = owner, Title = saved == null ? "新建代理线路" : "编辑代理线路", Width = 770, Height = 780, MinWidth = 680, MinHeight = 570, Background = Bg, Foreground = Text, WindowStartupLocation = WindowStartupLocation.CenterOwner }; WorkbenchTheme.Apply(editor);
        editor.Content = BuildProxyEditor(editor, saved, editor.Close); return editor;
    }
    FrameworkElement BuildProxyEditor(Window owner, JsonElement? saved, Action closeEditor)
    {
        var data = saved ?? default;
        string Read(string key, string fallback = "") => data.ValueKind == JsonValueKind.Object ? Str(data, key, fallback) : fallback;
        var layout = new DockPanel(); var heading = new StackPanel { Margin = new Thickness(26, 22, 26, 18) };
        heading.Children.Add(new TextBlock { Text = saved == null ? "建立一条可复用线路" : "编辑代理线路", FontSize = 23, FontWeight = FontWeights.SemiBold, Foreground = Text });
        heading.Children.Add(new TextBlock { Text = "可粘贴带认证的完整代理地址，也可分别填写用户名与密码；密码留空保留已保存值。", Foreground = Muted, FontSize = 12, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 7, 0, 0) }); DockPanel.SetDock(heading, Dock.Top); layout.Children.Add(heading);
        var footer = new DockPanel { Margin = new Thickness(26, 14, 26, 20) }; DockPanel.SetDock(footer, Dock.Bottom); layout.Children.Add(footer);
        var stack = new StackPanel { Margin = new Thickness(26, 0, 26, 0) }; layout.Children.Add(new ScrollViewer { Content = stack, VerticalScrollBarVisibility = ScrollBarVisibility.Auto });
        StackPanel Section(string title, string subtitle) { var panel = new StackPanel(); panel.Children.Add(new TextBlock { Text = title, FontSize = 15, FontWeight = FontWeights.SemiBold, Foreground = Text }); panel.Children.Add(new TextBlock { Text = subtitle, FontSize = 11, Foreground = Muted, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 6, 0, 14) }); stack.Children.Add(new Border { Child = panel, Background = Panel, CornerRadius = new CornerRadius(10), BorderBrush = Line, BorderThickness = new Thickness(1), Padding = new Thickness(18), Margin = new Thickness(0, 0, 0, 14) }); return panel; }
        TextBox Field(Panel parent, string label, string value) { var group = new StackPanel { Margin = new Thickness(0, 0, 0, 12) }; group.Children.Add(Label(label, 12)); var input = new TextBox { Text = value, Margin = new Thickness(0, 5, 0, 0) }; System.Windows.Automation.AutomationProperties.SetName(input, label); group.Children.Add(input); parent.Children.Add(group); return input; }
        var basic = Section("基本信息", "住宅代理强制使用上游，连接失败不会回退为本机直连。"); var name = Field(basic, "代理名称", Read("name"));
        var kind = new ComboBox { ItemsSource = new[] { new ProxyChoice("standard", "普通代理"), new ProxyChoice("residential", "住宅代理 · 必须经上游") }, DisplayMemberPath = "Label", SelectedValuePath = "Id", SelectedValue = Read("kind", "residential"), MinHeight = 36, Padding = new Thickness(8), Margin = new Thickness(0, 0, 0, 12) }; System.Windows.Automation.AutomationProperties.SetName(kind, "代理类型"); basic.Children.Add(kind);
        var routeText = new TextBlock { Text = "本机 → 上游（外网通道）→ 住宅代理 → 网站", Foreground = Accent, FontSize = 12, TextWrapping = TextWrapping.Wrap }; basic.Children.Add(routeText);
        var terminal = Section("最终出口代理", "支持 HTTP / HTTPS / SOCKS5。可直接粘贴 username:password@host:port，未写协议时默认 HTTP；443 端口不会自动改为 HTTPS。用户名中的地区、ASN 和会话参数原样保留。"); var proxy = Field(terminal, "代理地址", Read("proxy"));
        terminal.Children.Add(new TextBlock { Text = "格式示例：demo-region-us-asn-64500-session-abc:demo-password@gateway.example:443\n也可填写 http://gateway.example:443，并在下方单独填写认证。保存时会拆分地址与认证。", Foreground = Muted, FontSize = 10, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, -5, 0, 12) });
        var user = Field(terminal, "代理用户名 / 会话用户名", Read("proxyUsername"));
        (PasswordBox box, CheckBox clear) Password(Panel parent, string key, string title) { parent.Children.Add(Label(title, 12)); var box = new PasswordBox { Margin = new Thickness(0, 5, 0, 0) }; System.Windows.Automation.AutomationProperties.SetName(box, title); parent.Children.Add(box); bool exists = Read(key + "Set", "false").Equals("true", StringComparison.OrdinalIgnoreCase); parent.Children.Add(new TextBlock { Text = exists ? "已保存密码；留空保留" : "密码仅保存在本机", Foreground = Muted, FontSize = 10, Margin = new Thickness(0, 6, 0, 0) }); var clear = new CheckBox { Content = "清除已保存密码", Foreground = Muted, Margin = new Thickness(0, 9, 0, 0), Visibility = exists ? Visibility.Visible : Visibility.Collapsed }; clear.Checked += (_, _) => box.IsEnabled = false; clear.Unchecked += (_, _) => box.IsEnabled = true; parent.Children.Add(clear); return (box, clear); }
        var (password, clearPassword) = Password(terminal, "proxyPassword", "代理密码");
        var sessions = Section("住宅代理会话", "默认保留供应商原始用户名。只有供应商支持会话参数时，才开启每个环境的独立会话。");
        var sessionMode = new ComboBox { ItemsSource = new[] { new ProxyChoice("fixed", "固定认证 · 保留原始用户名"), new ProxyChoice("per_profile", "每个环境独立会话") }, DisplayMemberPath = "Label", SelectedValuePath = "Id", SelectedValue = Read("sessionMode", "fixed"), MinHeight = 36, Margin = new Thickness(0, 0, 0, 12) };
        System.Windows.Automation.AutomationProperties.SetName(sessionMode, "住宅会话模式"); sessions.Children.Add(sessionMode);
        var sessionFields = new StackPanel(); sessions.Children.Add(sessionFields);
        var sessionTemplate = Field(sessionFields, "供应商用户名模板", Read("sessionTemplate"));
        sessionFields.Children.Add(new TextBlock { Text = "{user} 是上方的完整原始用户名，{session} 是环境专属会话号。\n例如：{user}-session-{session}。格式必须以供应商文档为准；已有 session 参数时，请先确认正确模板。", Foreground = Muted, FontSize = 11, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, -3, 0, 14) });
        sessionFields.Children.Add(Label("会话有效期", 12));
        int.TryParse(Read("sessionLifetimeMinutes", "0"), out var savedLifetime);
        var lifetimeOptions = new List<ProxyChoice> { new("0", "长期固定 · 不自动更换"), new("15", "15 分钟"), new("30", "30 分钟"), new("60", "1 小时"), new("120", "2 小时"), new("1440", "24 小时") };
        if (!lifetimeOptions.Any(option => option.Id == savedLifetime.ToString())) lifetimeOptions.Add(new(savedLifetime.ToString(), savedLifetime + " 分钟"));
        var sessionLifetime = new ComboBox { ItemsSource = lifetimeOptions, DisplayMemberPath = "Label", SelectedValuePath = "Id", SelectedValue = savedLifetime.ToString(), MinHeight = 36, Margin = new Thickness(0, 5, 0, 10) };
        System.Windows.Automation.AutomationProperties.SetName(sessionLifetime, "住宅会话有效期"); sessionFields.Children.Add(sessionLifetime);
        sessionFields.Children.Add(new TextBlock { Text = "到期后在下一次打开浏览器或检测环境时更换会话；运行中的浏览器保持原会话。更换会话不保证更换 IP，请再检测实际出口。代理列表检测使用临时会话，不影响账号。", Foreground = Muted, FontSize = 11, TextWrapping = TextWrapping.Wrap });
        void UpdateSessionMode() { sessionFields.Visibility = sessionMode.SelectedValue as string == "per_profile" ? Visibility.Visible : Visibility.Collapsed; }
        sessionMode.SelectionChanged += (_, _) => UpdateSessionMode(); UpdateSessionMode();
        var upstream = Section("上游代理地址", "先经此通道连接住宅网关，再由住宅代理访问网站。通常填写本机已有的外网代理，例如 http://127.0.0.1:7897。");
        var upstreamAddress = Field(upstream, "上游代理地址", Read("upstreamProxy")); var upstreamUser = Field(upstream, "上游代理用户名（可选）", Read("upstreamProxyUsername")); var (upstreamPassword, clearUpstreamPassword) = Password(upstream, "upstreamProxyPassword", "上游代理密码（可选）");
        void UpdateKind() { bool residential = kind.SelectedValue as string == "residential"; routeText.Text = residential ? "本机 → 上游（外网通道）→ 住宅代理 → 网站" : "本机 → 普通代理 → 网站；填写上游时先通过上游连接。"; upstreamAddress.ToolTip = residential ? "住宅代理必须填写上游" : "可选上游通道"; sessionMode.IsEnabled = residential; if (!residential) sessionMode.SelectedValue = "fixed"; } kind.SelectionChanged += (_, _) => UpdateKind(); UpdateKind();
        var result = new TextBlock { Text = "尚未检测这条线路", Foreground = Muted, FontSize = 12, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 5, 0, 18) }; stack.Children.Add(result);
        var editorActivity = new Border { Background = Hex("#203B33"), BorderBrush = Hex("#345D4F"), BorderThickness = new Thickness(1), CornerRadius = new CornerRadius(8), Padding = new Thickness(12), Margin = new Thickness(0, 0, 0, 12), Visibility = Visibility.Collapsed };
        System.Windows.Automation.AutomationProperties.SetName(editorActivity, "线路检测中");
        var editorBusy = new DockPanel(); var editorSpinner = ProxySpinner(); editorSpinner.Margin = new Thickness(0, 0, 12, 0); DockPanel.SetDock(editorSpinner, Dock.Left); editorBusy.Children.Add(editorSpinner);
        editorBusy.Children.Add(new TextBlock { Text = "正在检测线路与出口信息…\n需要等待网络响应，请稍候。", Foreground = Accent, FontSize = 12, TextWrapping = TextWrapping.Wrap }); editorActivity.Child = editorBusy; stack.Children.Insert(stack.Children.Count - 1, editorActivity);
        Dictionary<string, object?> Draft() {
            if (name.Text.Trim().Length == 0) throw new InvalidOperationException("请填写代理名称。");
            if (proxy.Text.Trim().Length == 0) throw new InvalidOperationException("请填写最终出口代理地址。");
            if (kind.SelectedValue as string == "residential" && upstreamAddress.Text.Trim().Length == 0) throw new InvalidOperationException("住宅代理必须填写上游代理地址。");
            if (sessionMode.SelectedValue as string == "per_profile" && (!sessionTemplate.Text.Contains("{user}") || !sessionTemplate.Text.Contains("{session}"))) throw new InvalidOperationException("请按供应商格式填写包含 {user} 和 {session} 的用户名模板。");
            var body = new Dictionary<string, object?> { ["name"] = name.Text.Trim(), ["kind"] = kind.SelectedValue as string ?? "residential", ["proxy"] = proxy.Text.Trim(), ["proxyUsername"] = user.Text.Trim(), ["upstreamProxy"] = upstreamAddress.Text.Trim(), ["upstreamProxyUsername"] = upstreamUser.Text.Trim() };
            body["sessionMode"] = sessionMode.SelectedValue as string ?? "fixed"; body["sessionTemplate"] = sessionTemplate.Text.Trim(); body["sessionLifetimeMinutes"] = int.Parse(sessionLifetime.SelectedValue as string ?? "0");
            if (Read("id").Length > 0) body["id"] = Read("id");
            void Secret(string key, PasswordBox box, CheckBox clear) { if (clear.IsChecked == true) body[key == "proxyPassword" ? "clearProxyPassword" : "clearUpstreamProxyPassword"] = true; else if (box.Password.Length > 0) body[key] = box.Password; }
            Secret("proxyPassword", password, clearPassword); Secret("upstreamProxyPassword", upstreamPassword, clearUpstreamPassword); return body;
        }
        Button? save = null, test = null; bool closed = false;
        test = IconButton("shield", "检测线路与出口信息", async () => { test!.IsEnabled = false; save!.IsEnabled = false; try { var body = Draft(); result.Text = ""; editorActivity.Visibility = Visibility.Visible; editorActivity.BringIntoView(); var check = await Api("proxies/test", body); if (closed) return; result.Text = DiagnosticSummary(check); result.Foreground = Str(check, "reachable").Equals("true", StringComparison.OrdinalIgnoreCase) ? WorkbenchTheme.Green : Hex("#E8B578"); } catch (Exception ex) { if (!closed) { result.Text = ex.Message; result.Foreground = Hex("#E8B578"); } } finally { editorActivity.Visibility = Visibility.Collapsed; if (!closed) { test.IsEnabled = true; save.IsEnabled = true; result.BringIntoView(); } } }); test.HorizontalAlignment = HorizontalAlignment.Left; test.Margin = new Thickness(0, 0, 0, 10); stack.Children.Insert(stack.Children.Count - 2, test);
        Button? cancel = null;
        save = Button("保存代理", async () => { if (!save!.IsEnabled) return; save.IsEnabled = false; test!.IsEnabled = false; if (cancel != null) cancel.IsEnabled = false; try { await Api("proxies", Draft()); if (!closed) closeEditor(); } catch (Exception ex) { if (!closed) { result.Text = ex.Message; result.Foreground = Hex("#E8B578"); result.BringIntoView(); } } finally { if (!closed) { save.IsEnabled = true; test.IsEnabled = true; if (cancel != null) cancel.IsEnabled = true; } } }, true); DockPanel.SetDock(save, Dock.Right); footer.Children.Add(save); cancel = Button("取消", closeEditor); DockPanel.SetDock(cancel, Dock.Right); footer.Children.Add(cancel);
        footer.Children.Add(new TextBlock { Text = "修改仅影响下次启动。", Foreground = Muted, FontSize = 11, VerticalAlignment = VerticalAlignment.Center }); layout.Unloaded += (_, _) => closed = true; layout.Loaded += (_, _) => closed = false; return layout;
    }
    static string DiagnosticSummary(JsonElement check)
    {
        string Value(string key) { var value = Str(check, key); return value.Length == 0 ? "未知" : value; }
        string Score(string key) => Value(key) == "未知" ? "未知" : Value(key) + " / 100";
        var risk = RiskLabel(Value("riskStatus")); var riskHint = risk == "未知" ? "" : "（" + risk + "）";
        if (!Str(check, "reachable").Equals("true", StringComparison.OrdinalIgnoreCase)) return "连接失败：" + Value("error");
        return $"出口国家 {NetworkCountryName(check)} · 区域 {Value("region")} · 城市 {Value("city")}\n出口时区 {Value("timezone")}\n连接成功 · 出口快照 {Value("egressIp")} · {Value("latencyMs")} ms · {ResidentialLabel(Value("isResidential"))}\nISP {Value("isp")} · ASN {Value("asn")}\n质量 {Score("qualityScore")} · 风险 {Score("riskScore")}{riskHint}" + (Str(check, "sessionScope") == "temporary" ? "\n这是临时会话的出口；请在账号详情检测环境的实际出口。" : "") + (Str(check, "observationNote").Length > 0 ? "\n出口观测：" + Str(check, "observationNote") : "") + (Str(check, "metadataError").Length > 0 ? "\n信息查询未完整：" + Str(check, "metadataError") : "");
    }
    void ShowProxyDetails(Window owner, ProxyRow row)
    {
        var window = new Window { Owner = owner, Title = "代理检测详情", Width = 780, Height = 700, MinWidth = 630, MinHeight = 500, Background = Bg, Foreground = Text, WindowStartupLocation = WindowStartupLocation.CenterOwner }; WorkbenchTheme.Apply(window);
        window.Content = BuildProxyDetails(row, window.Close); window.ShowDialog();
    }
    FrameworkElement BuildProxyDetails(ProxyRow row, Action back)
    {
        var panel = new StackPanel { Margin = new Thickness(26) }; panel.Children.Add(new TextBlock { Text = row.Name, Foreground = Text, FontSize = 23, FontWeight = FontWeights.SemiBold, TextWrapping = TextWrapping.Wrap });
        panel.Children.Insert(0, Button("返回代理列表", back));
        panel.Children.Add(new TextBlock { Text = CatalogRoute(row.Id), Foreground = Muted, FontSize = 12, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 8, 0, 20) });
        panel.Children.Add(new Border { Background = Panel, CornerRadius = new CornerRadius(10), Padding = new Thickness(18), Child = new TextBlock { Text = row.Check.ValueKind == JsonValueKind.Object ? DiagnosticSummary(row.Check) : "这条代理还没有检测结果。", Foreground = Text, FontSize = 14, TextWrapping = TextWrapping.Wrap, LineHeight = 25 } });
        panel.Children.Add(new TextBlock { Text = "检测时间  " + row.Time + "   ·   质量分越高越好 / 风险分越低越好", Foreground = Muted, FontSize = 11, Margin = new Thickness(0, 14, 0, 14), TextWrapping = TextWrapping.Wrap });
        if (row.Check.ValueKind == JsonValueKind.Object) {
            foreach (var (key, label) in new[] { ("snapshotSource", "快照来源"), ("qualityComplete", "质量信息完整"), ("sources", "信息来源"), ("error", "连接说明") }) {
                var value = row.Value(key); if (value == "—") continue; if (key == "qualityComplete") value = value.Equals("true", StringComparison.OrdinalIgnoreCase) ? "是" : "否"; panel.Children.Add(new TextBlock { Text = label + "   " + value, Foreground = Muted, FontSize = 12, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 0, 0, 9) });
            }
            panel.Children.Add(new Expander { Header = "完整检测数据", Foreground = Muted, Margin = new Thickness(0, 12, 0, 0), Content = new TextBox { Text = JsonSerializer.Serialize(row.Check, new JsonSerializerOptions { WriteIndented = true }), IsReadOnly = true, TextWrapping = TextWrapping.Wrap, FontFamily = new FontFamily("Cascadia Code, Consolas"), Margin = new Thickness(0, 10, 0, 0) } });
        }
        return new ScrollViewer { Content = panel, VerticalScrollBarVisibility = ScrollBarVisibility.Auto };
    }
}
