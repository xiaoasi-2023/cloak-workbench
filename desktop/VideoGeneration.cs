using System.Text.Json;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Media;

namespace CloakWorkbench;

public sealed partial class MainWindow
{
    readonly ContentControl videoWorkspace = new() { Visibility = Visibility.Collapsed };
    readonly ComboBox videoEnvironment = new() { DisplayMemberPath = "Name", SelectedValuePath = "Id" };
    readonly ComboBox videoModel = new(), videoDuration = new(), videoRatio = new();
    readonly TextBox videoPrompt = new() { AcceptsReturn = true, TextWrapping = TextWrapping.Wrap, MinHeight = 150, MaxLength = 4000, VerticalContentAlignment = VerticalAlignment.Top, VerticalScrollBarVisibility = ScrollBarVisibility.Auto };
    readonly StackPanel videoCards = new();
    readonly TextBlock videoFeedback = new() { TextWrapping = TextWrapping.Wrap }, videoCount = new();
    readonly Dictionary<string, JsonElement> videoJobs = new();
    Button? videoSubmit;
    bool videoLoading, videoSubmitting, videoLoaded;
    int activeVideoCount;
    string? videoSignature, videoEnvironmentSignature;
    sealed record VideoEnvironmentChoice(string Id, string Name);
    static bool VideoActive(JsonElement job) => Str(job, "state") is "preparing" or "running" or "needs_attention" or "cancelling";

    async void ShowVideoPage()
    {
        videoWorkspace.Content ??= CreateVideoPage();
        ShowManagementPage(videoWorkspace, "视频生成");
        UpdateVideoEnvironments();
        await Guard(LoadVideos);
    }

    FrameworkElement CreateVideoPage()
    {
        var page = new DockPanel { Margin = new Thickness(28, 28, 28, 24) };
        var heading = new StackPanel { Margin = new Thickness(0, 0, 0, 24) };
        heading.Children.Add(new TextBlock { Text = "视频生成", FontSize = 28, FontWeight = FontWeights.SemiBold, Foreground = Text });
        heading.Children.Add(new TextBlock { Text = "使用现有 Dola 登录环境创作 · 配置与生成进度放在同一页", Foreground = Muted, Margin = new Thickness(0, 8, 0, 0), TextWrapping = TextWrapping.Wrap });
        DockPanel.SetDock(heading, Dock.Top); page.Children.Add(heading);
        var columns = new Grid(); columns.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(340) }); columns.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(24) }); columns.ColumnDefinitions.Add(new ColumnDefinition());
        var form = new StackPanel { Margin = new Thickness(20) };
        form.Children.Add(Label("创作设置", 17));
        void Field(string title, Control control) {
            var label = Label(title, 12, Muted); label.Margin = new Thickness(0, 18, 0, 7); form.Children.Add(label);
            control.HorizontalAlignment = HorizontalAlignment.Stretch; control.MinHeight = Math.Max(control.MinHeight, 36);
            AutomationProperties.SetName(control, title); form.Children.Add(control);
        }
        Field("浏览器环境", videoEnvironment);
        var environmentActions = new WrapPanel { Margin = new Thickness(0, 9, 0, 0) };
        environmentActions.Children.Add(Button("打开环境", async () => await Guard(async () => {
            if (videoEnvironment.SelectedValue is not string id) throw new InvalidOperationException("请先选择环境。");
            await LaunchQueued(id, selectBrowser: false); UpdateVideoEnvironments();
            videoFeedback.Text = "环境已打开。请确保已登录 Dola，再提交视频任务。";
        })));
        environmentActions.Children.Add(Button("查看浏览器", () => { if (videoEnvironment.SelectedValue is string id) Select(id); })); form.Children.Add(environmentActions);
        videoModel.Items.Add("Seedance 2.0"); videoModel.Items.Add("Seedance 2.5"); videoModel.SelectedIndex = 0;
        Field("生成模型", videoModel);
        foreach (int seconds in new[] { 5, 10, 15, 30 }) videoDuration.Items.Add(seconds);
        videoDuration.SelectedItem = 10;
        videoDuration.SelectionChanged += (_, _) => {
            if (videoDuration.SelectedItem is int seconds && seconds == 30) {
                videoModel.SelectedIndex = 1;
                videoFeedback.Text = "30 秒使用 Seedance 2.5。上游支持取决于账号，完成后会核验实际时长。";
            }
        };
        videoModel.SelectionChanged += (_, _) => {
            if (videoModel.SelectedIndex == 0 && videoDuration.SelectedItem is int s && s == 30) {
                videoDuration.SelectedItem = 15;
                videoFeedback.Text = "Seedance 2.0 已切回 15 秒；30 秒请使用 2.5。";
            }
        };
        foreach (var ratio in new[] { "16:9", "9:16", "1:1", "4:3", "3:4", "21:9" }) videoRatio.Items.Add(ratio);
        videoRatio.SelectedIndex = 0;
        var dimensions = new Grid(); dimensions.ColumnDefinitions.Add(new ColumnDefinition()); dimensions.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(12) }); dimensions.ColumnDefinitions.Add(new ColumnDefinition());
        foreach (var (title, control, column) in new (string, ComboBox, int)[] { ("视频时长 / 秒", videoDuration, 0), ("画面比例", videoRatio, 2) }) {
            var field = new StackPanel(); var caption = Label(title, 12, Muted); caption.Margin = new Thickness(0, 18, 0, 7); field.Children.Add(caption);
            control.MinHeight = 36; AutomationProperties.SetName(control, title); field.Children.Add(control); Grid.SetColumn(field, column); dimensions.Children.Add(field);
        }
        form.Children.Add(dimensions); Field("视频提示词", videoPrompt);
        videoFeedback.Foreground = Muted; videoFeedback.FontSize = 12; videoFeedback.Margin = new Thickness(0, 14, 0, 10); AutomationProperties.SetName(videoFeedback, "视频操作反馈");
        form.Children.Add(videoFeedback);
        videoSubmit = Button("生成视频 · 使用 Dola 额度", async () => await SubmitVideo(), true);
        videoSubmit.Margin = new Thickness(0, 10, 0, 0); videoSubmit.MinHeight = 42; AutomationProperties.SetName(videoSubmit, "提交视频生成"); form.Children.Add(videoSubmit);
        form.Children.Add(new TextBlock { Text = "15 / 30 秒为扩展选项，以 Dola 实际返回为准。验证码需手动处理；任务不会自动重发或拼接视频。", Foreground = Muted, FontSize = 11, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 12, 0, 0) });
        columns.Children.Add(new Border { Background = Panel, CornerRadius = new CornerRadius(10), BorderBrush = Line, BorderThickness = new Thickness(1), Child = new ScrollViewer { Content = form, VerticalScrollBarVisibility = ScrollBarVisibility.Auto } });
        var history = new DockPanel(); Grid.SetColumn(history, 2);
        var header = new DockPanel { Margin = new Thickness(0, 0, 0, 16) };
        var refresh = Button("刷新", async () => await Guard(LoadVideos)); DockPanel.SetDock(refresh, Dock.Right); header.Children.Add(refresh);
        videoCount.Foreground = Text; videoCount.FontSize = 17; header.Children.Add(videoCount); DockPanel.SetDock(header, Dock.Top); history.Children.Add(header);
        history.Children.Add(new ScrollViewer { Content = videoCards, VerticalScrollBarVisibility = ScrollBarVisibility.Auto, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled });
        columns.Children.Add(history); page.Children.Add(columns);
        UpdateVideoEnvironments(); RenderVideos();
        return page;
    }

    void UpdateVideoEnvironments()
    {
        var choices = profiles.Values.Select(p => new VideoEnvironmentChoice(Str(p, "id"),
            Str(p, "name") + (BoundAccountEmail(p).Length > 0 ? " · " + BoundAccountEmail(p) : "") + (Str(p, "state") == "running" ? " · 运行中" : " · 未打开"))).ToArray();
        var signature = JsonSerializer.Serialize(choices);
        if (signature == videoEnvironmentSignature) return;
        videoEnvironmentSignature = signature;
        var previous = videoEnvironment.SelectedValue as string;
        videoEnvironment.ItemsSource = choices;
        if (previous != null && choices.Any(c => c.Id == previous)) videoEnvironment.SelectedValue = previous;
        else videoEnvironment.SelectedIndex = choices.Length > 0 ? 0 : -1;
    }

    async Task SubmitVideo()
    {
        if (videoSubmitting) return;
        videoSubmitting = true; videoSubmit!.IsEnabled = false;
        try {
            if (videoEnvironment.SelectedValue is not string id) throw new InvalidOperationException("请先选择环境。");
            if (string.IsNullOrWhiteSpace(videoPrompt.Text)) throw new InvalidOperationException("请填写视频提示词。");
            videoFeedback.Text = "正在创建任务…";
            await Api("video-tasks", new { profileId = id, prompt = videoPrompt.Text, model = videoModel.SelectedIndex == 1 ? "seedance_v2.5" : "seedance_v2.0", duration = (int)videoDuration.SelectedItem, ratio = (string)videoRatio.SelectedItem });
            videoFeedback.Text = "任务已加入右侧列表，可留在这里查看进度。";
            DismissFeedbackLater(videoFeedback, () => videoFeedback.Text = "");
            await LoadVideos();
        } catch (Exception ex) { CancelFeedbackDismissal(videoFeedback); videoFeedback.Text = ex.Message; }
        finally { videoSubmitting = false; videoSubmit.IsEnabled = true; }
    }

    async Task LoadVideos()
    {
        if (videoLoading || closing) return;
        videoLoading = true;
        try {
            var result = await Api("video-tasks");
            videoJobs.Clear();
            foreach (var job in result.GetProperty("tasks").EnumerateArray()) videoJobs[Str(job, "id")] = job.Clone();
            activeVideoCount = videoJobs.Values.Count(VideoActive); videoLoaded = true;
            UpdateGlobalActivity();
            if (videoWorkspace.Content != null) { UpdateVideoEnvironments(); RenderVideos(); }
        } finally { videoLoading = false; }
    }

    void RenderVideos()
    {
        videoCount.Text = $"生成任务  /  {activeVideoCount} 个进行中";
        var signature = JsonSerializer.Serialize(videoJobs.Values);
        if (signature == videoSignature) return;
        videoSignature = signature; videoCards.Children.Clear();
        if (videoJobs.Count == 0) {
            videoCards.Children.Add(new TextBlock { Text = "还没有视频任务\n\n选择已登录的环境，填写提示词开始创作。\n生成后会分别显示请求时长和实际成片时长。", Foreground = Muted, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(16, 48, 16, 0), FontSize = 14 });
        }
        foreach (var job in videoJobs.Values) {
            string id = Str(job, "id"), state = Str(job, "state"), pid = Str(job, "profileId");
            var card = new StackPanel { Margin = new Thickness(18) };
            var title = new DockPanel();
            var badge = Label(state switch { "preparing" => "准备中", "running" => "生成中", "needs_attention" => "需要处理", "completed" => "已完成", "duration_mismatch" => "时长不符", "cancelled" => "已停止监测", "interrupted" => "已中断", "timed_out" => "等待超时", _ => "未完成" }, 12, state == "completed" ? Accent : Muted);
            DockPanel.SetDock(badge, Dock.Right); title.Children.Add(badge);
            var name = Label(Str(job, "profileName"), 15); name.TextTrimming = TextTrimming.CharacterEllipsis; title.Children.Add(name); card.Children.Add(title);
            card.Children.Add(new TextBlock { Text = $"{Str(job, "model").Replace("seedance_v", "Seedance ")}   ·   {Str(job, "ratio")}   ·   请求 {Str(job, "duration")} 秒", Foreground = Muted, FontSize = 12, Margin = new Thickness(0, 10, 0, 10), TextWrapping = TextWrapping.Wrap });
            if (job.TryGetProperty("createdAt", out var created) && created.TryGetDouble(out var timestamp))
                card.Children.Add(new TextBlock { Text = DateTimeOffset.FromUnixTimeSeconds((long)timestamp).ToLocalTime().ToString("MM-dd HH:mm") + "  ·  " + id, Foreground = Muted, FontSize = 10, Margin = new Thickness(0, 0, 0, 10) });
            card.Children.Add(new TextBlock { Text = Str(job, "prompt"), MaxHeight = 64, TextTrimming = TextTrimming.CharacterEllipsis, TextWrapping = TextWrapping.Wrap, Foreground = Text });
            var stage = new TextBlock { Text = Str(job, "stage"), Foreground = Muted, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 14, 0, 8) }; AutomationProperties.SetName(stage, "视频任务状态 " + id); card.Children.Add(stage);
            if (VideoActive(job)) card.Children.Add(new ProgressBar { IsIndeterminate = true, Height = 3, Margin = new Thickness(0, 0, 0, 12), Foreground = Accent, Background = Line });
            if (job.TryGetProperty("actualDuration", out var actual) && actual.ValueKind == JsonValueKind.Number)
                card.Children.Add(new TextBlock { Text = $"实际成片  {actual.GetDouble():0.##} 秒", Foreground = state == "completed" ? Accent : Hex("#EDC180"), FontSize = 19, Margin = new Thickness(0, 0, 0, 12) });
            var actions = new WrapPanel();
            actions.Children.Add(Button("打开任务页面", async () => {
                try {
                    if (!profiles.TryGetValue(pid, out var p) || Str(p, "state") != "running") await LaunchQueued(pid, false);
                    await Api($"video-tasks/{id}/open", new { }); Select(pid);
                } catch (Exception ex) { videoFeedback.Text = ex.Message; }
            }));
            if (VideoActive(job)) actions.Children.Add(Button("停止监测", async () => await Guard(async () => { await Api($"video-tasks/{id}/cancel", new { }); await LoadVideos(); })));
            else actions.Children.Add(Button("删除记录", async () => await Guard(async () => { await Api($"video-tasks/{id}/delete", new { }); await LoadVideos(); })));
            card.Children.Add(actions);
            if (state is "completed" or "duration_mismatch") card.Children.Add(new TextBlock { Text = "在任务页面预览或使用 Dola 的下载按钮保存成片。", Foreground = Muted, FontSize = 11, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 10, 0, 0) });
            videoCards.Children.Add(new Border { Background = Panel, BorderBrush = Line, BorderThickness = new Thickness(1), CornerRadius = new CornerRadius(10), Margin = new Thickness(0, 0, 0, 14), Child = card });
        }
    }
}
