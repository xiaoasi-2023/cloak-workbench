using System.Text.Json;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Threading;

namespace CloakWorkbench;

public sealed partial class MainWindow
{
    readonly Dictionary<string, GoogleLoginView> googleLoginViews = new();
    readonly DispatcherTimer googleLoginTimer = new() { Interval = TimeSpan.FromSeconds(1) };
    bool googleLoginPolling, googleLoginInitialized;

    sealed class GoogleLoginView
    {
        internal required StackPanel Panel, Footer;
        internal required TextBox Email;
        internal required PasswordBox Password;
        internal required CheckBox Remember, UseSaved, Onboarding, AgeConfirmed;
        internal required Button Start, Cancel, Verify, Forget, Continue;
        internal required TextBlock Status, SavedHint;
        internal required ProgressBar Progress;
        internal string SavedEmail = "";
        internal bool Bound;
        internal bool Loading, Starting, Active;
        internal string LastJobVersion = "";
        internal string Stage = "";
    }

    async void ShowGoogleLogin(string id)
    {
        await OpenGoogleLogin(id);
    }

    async Task OpenGoogleLogin(string id)
    {
        try {
            ShowAccountCenter();
            var container = CenterPanel("通过 Google 登录", profiles.TryGetValue(id, out var p) ? Str(p, "name") + " · 独立浏览器会话" : "独立浏览器会话");
            centerDetailId = null;
            if (googleLoginViews.TryGetValue(id, out var existing)) {
                if (existing.Panel.Parent is Panel old) old.Children.Remove(existing.Panel);
                container.Children.Add(existing.Panel);
                AttachGoogleLoginFooter(container, existing.Footer);
                await ReloadGoogleCredentials(id, existing);
                await PollGoogleLogin(); return;
            }
            var panel = new StackPanel(); container.Children.Add(panel);
            panel.Children.Add(new TextBlock { Text = "自动填写并处理新账号步骤，返回 Dola 后验证登录。验证码与两步验证仍需手动完成。", Foreground = Muted, FontSize = 11, LineHeight = 19, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 0, 0, 12) });
            var email = CenterField(panel, "Google 账号邮箱", p.ValueKind == JsonValueKind.Object ? Str(p, "accountEmail") : ""); email.MaxLength = 254;
            panel.Children.Add(Label("Google 密码", 11, Muted));
            var password = new PasswordBox { Margin = new Thickness(0, 7, 0, 14), MaxLength = 4096 }; AutomationProperties.SetName(password, "Google 密码"); panel.Children.Add(password);
            var saved = new CheckBox { Content = "使用此环境已保存的密码", Visibility = Visibility.Collapsed, Foreground = Text, Margin = new Thickness(0, 0, 0, 12) }; AutomationProperties.SetName(saved, "使用已保存的 Google 密码"); panel.Children.Add(saved);
            var remember = new CheckBox { Content = "在本机记住密码", Foreground = Text }; AutomationProperties.SetName(remember, "记住 Google 密码"); panel.Children.Add(remember);
            remember.ToolTip = "以明文保存到本机，仅用于 Google 官方登录页；密码不会自动回填。";
            var hint = new TextBlock { Foreground = Muted, FontSize = 11, TextWrapping = TextWrapping.Wrap, LineHeight = 18, Margin = new Thickness(0, 8, 0, 0) }; panel.Children.Add(hint);
            panel.Children.Add(new Border { Height = 1, Background = Line, Margin = new Thickness(0, 18, 0, 14) });
            panel.Children.Add(Label("新账号步骤", 12, Text));
            CheckBox StepChoice(string title, string name, bool selected) {
                var choice = new CheckBox { Content = new TextBlock { Text = title, TextWrapping = TextWrapping.Wrap, LineHeight = 20 }, Foreground = Text, IsChecked = selected, Margin = new Thickness(0, 10, 0, 0), HorizontalContentAlignment = HorizontalAlignment.Stretch };
                AutomationProperties.SetName(choice, name); panel.Children.Add(choice); return choice;
            }
            var onboarding = StepChoice("自动确认 Google 新账号欢迎页与 Dola 基础登录授权", "允许新账号欢迎页与 Dola 基础授权", true);
            onboarding.ToolTip = "仅限当前邮箱的 Google Workspace 欢迎页，以及向 dola.com 提供姓名、头像和邮箱的基础登录授权；额外权限交由你确认。";
            var ageConfirmed = StepChoice("我确认此账号使用者已满 18 岁，允许确认 Dola 年龄提示", "确认账号使用者已满 18 岁", false);
            panel.Children.Add(new TextBlock { Text = "年龄确认仅用于本次任务。未勾选时会暂停；手动完成后可继续，无需重新输入密码。", Foreground = Muted, FontSize = 11, LineHeight = 18, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 8, 0, 0) });
            var footer = new StackPanel { Margin = new Thickness(20, 12, 20, 18) };
            var statusText = new TextBlock { Text = "填写账号后开始登录", Foreground = Text, FontSize = 12, TextWrapping = TextWrapping.Wrap, LineHeight = 22 };
            AutomationProperties.SetName(statusText, "Google 登录进度");
            var progress = new ProgressBar { Height = 3, IsIndeterminate = true, Foreground = Accent, Background = Line, Visibility = Visibility.Collapsed, Margin = new Thickness(0, 14, 0, 0) };
            var statusPanel = new StackPanel(); statusPanel.Children.Add(statusText); statusPanel.Children.Add(progress);
            footer.Children.Add(new Border { Background = Hex("#202D26"), CornerRadius = new CornerRadius(8), Padding = new Thickness(12), Child = statusPanel, Margin = new Thickness(0, 0, 0, 12) });
            GoogleLoginView? view = null;
            var actions = new Grid(); actions.ColumnDefinitions.Add(new ColumnDefinition()); actions.ColumnDefinitions.Add(new ColumnDefinition()); footer.Children.Add(actions);
            var start = Button("开始登录", async () => await StartGoogleLogin(id, view!), true); start.Height = 38; start.Margin = new Thickness(0, 0, 6, 0); AutomationProperties.SetName(start, "开始 Google 登录"); actions.Children.Add(start);
            var open = Button("手动接管", async () => { try { await Launch(id); } catch { view!.Status.Text = "浏览器打开失败，请查看运行诊断。"; } }); open.Height = 38; open.Margin = new Thickness(6, 0, 0, 0); AutomationProperties.SetName(open, "打开浏览器 / 手动接管"); Grid.SetColumn(open, 1); actions.Children.Add(open);
            var verify = Button("已手动完成，验证 Dola 登录", async () => {
                view!.Verify.IsEnabled = false;
                try { var result = await Api($"profiles/{id}/google-login/verify", new { }); view.Status.Text = Str(result, "confirmed").Equals("true", StringComparison.OrdinalIgnoreCase) ? "已确认 Dola 登录，会话已保存。" : "Dola 登录尚未确认，请完成浏览器中的验证后重试。"; await Refresh(); }
                catch { view.Status.Text = "登录结果暂时无法读取，请稍后重试。"; }
                finally { view.Verify.IsEnabled = !view.Active; }
            }); verify.Content = "检测 Dola 登录"; AutomationProperties.SetName(verify, "已手动完成，验证 Dola 登录"); verify.Margin = new Thickness(6, 8, 0, 0); verify.Visibility = Visibility.Collapsed;
            var next = Button("继续登录流程", async () => {
                view!.Starting = true; SetGoogleLoginControls(view, true); view.Status.Text = "正在继续当前页面的新账号步骤…";
                try { var result = await Api($"profiles/{id}/google-login/continue", new { allowOnboarding = view.Onboarding.IsChecked == true, ageConfirmed = view.AgeConfirmed.IsChecked == true }); UpdateGoogleLoginView(view, result); view.AgeConfirmed.IsChecked = false; }
                catch { view.Status.Text = "当前任务无法继续。请确认原浏览器仍打开；若页面需要密码，请重新开始。"; }
                finally { view.Starting = false; SetGoogleLoginControls(view, view.Active); }
            }); AutomationProperties.SetName(next, "继续 Google 登录流程"); next.Visibility = Visibility.Collapsed; next.Margin = new Thickness(0, 8, 6, 0);
            var followup = new Grid(); followup.ColumnDefinitions.Add(new ColumnDefinition()); followup.ColumnDefinitions.Add(new ColumnDefinition());
            followup.Children.Add(next); Grid.SetColumn(verify, 1); followup.Children.Add(verify); footer.Children.Add(followup);
            var cancel = Button("停止自动填写", async () => {
                view!.Cancel.IsEnabled = false;
                try { await Api($"profiles/{id}/google-login/cancel", new { }); await PollGoogleLogin(); }
                catch { view.Status.Text = "停止请求未完成，请稍后重试。"; view.Cancel.IsEnabled = true; }
            }); cancel.Visibility = Visibility.Collapsed; cancel.Margin = new Thickness(0, 8, 0, 0); footer.Children.Add(cancel);
            var forget = Button("清除已保存的 Google 密码", async () => {
                try { await Api($"profiles/{id}/google-credentials/forget", new { }); view!.Password.Clear(); await ReloadGoogleCredentials(id, view); view.Status.Text = "已清除本机保存的密码，浏览器登录会话仍保留。"; }
                catch { view!.Status.Text = "密码清除失败，请先停止登录再重试。"; }
            }); forget.Margin = new Thickness(0, 12, 0, 0); forget.Foreground = Hex("#ED9D9D"); forget.Visibility = Visibility.Collapsed; panel.Children.Add(forget);
            view = new GoogleLoginView { Panel = panel, Footer = footer, Email = email, Password = password, Remember = remember, UseSaved = saved, Onboarding = onboarding, AgeConfirmed = ageConfirmed, Continue = next, Status = statusText, SavedHint = hint, Start = start, Cancel = cancel, Verify = verify, Forget = forget, Progress = progress };
            AttachGoogleLoginFooter(container, footer);
            googleLoginViews[id] = view;
            panel.SizeChanged += (_, _) => RevealGoogleAge(view);
            void SavedChoice() { password.IsEnabled = !view.Active && !view.Starting && saved.IsChecked != true; if (saved.IsChecked == true) password.Clear(); }
            saved.Checked += (_, _) => SavedChoice(); saved.Unchecked += (_, _) => SavedChoice();
            email.TextChanged += (_, _) => { if (!view.Loading && !email.Text.Trim().Equals(view.SavedEmail, StringComparison.OrdinalIgnoreCase)) saved.IsChecked = false; };
            if (!googleLoginInitialized) {
                googleLoginInitialized = true;
                googleLoginTimer.Tick += async (_, _) => await PollGoogleLogin();
                Closed += (_, _) => { googleLoginTimer.Stop(); foreach (var v in googleLoginViews.Values) v.Password.Clear(); };
                googleLoginTimer.Start();
            }
            await ReloadGoogleCredentials(id, view); await PollGoogleLogin();
        } catch { Log("Google 登录表单打开失败，请重新进入账号详情。未提交凭据。"); }
    }

    static void AttachGoogleLoginFooter(StackPanel container, StackPanel footer)
    {
        if (footer.Parent is Panel old) old.Children.Remove(footer);
        var body = (DockPanel)((ScrollViewer)container.Parent).Parent;
        DockPanel.SetDock(footer, Dock.Bottom); body.Children.Insert(body.Children.Count - 1, footer);
    }

    async Task ReloadGoogleCredentials(string id, GoogleLoginView view)
    {
        try {
            var info = await Api($"profiles/{id}/google-credentials"); bool has = Str(info, "passwordSet").Equals("true", StringComparison.OrdinalIgnoreCase);
            view.Loading = true;
            try {
                view.SavedEmail = Str(info, "email"); view.Bound = Str(info, "source") == "account";
                if (view.Bound || has && view.Email.Text.Length == 0) view.Email.Text = view.SavedEmail;
                view.UseSaved.Visibility = has ? Visibility.Visible : Visibility.Collapsed;
                view.UseSaved.Content = view.Bound ? "使用绑定账号已保存的密码" : "使用此环境已保存的密码";
                if (view.Bound) { view.UseSaved.IsChecked = has; view.Remember.IsChecked = false; }
                if (!has) view.UseSaved.IsChecked = false;
                view.Remember.Visibility = view.Bound ? Visibility.Collapsed : Visibility.Visible;
                view.Forget.Visibility = has && !view.Bound ? Visibility.Visible : Visibility.Collapsed;
                view.SavedHint.Text = view.Bound ? "已绑定账号 · " + view.SavedEmail + "。密码在账号列表维护；当前网站若已有会话，不代表它就是绑定账号。" : has ? "此环境已保存凭据，可迁移到账号列表。" : "未绑定账号；可在账号列表保存并绑定，也可仅本次填写。";
                SetGoogleLoginControls(view, view.Active || view.Starting);
            } finally { view.Loading = false; }
        } catch { view.SavedHint.Text = "本机凭据暂时无法读取，可填写密码仅本次登录。"; }
    }

    async Task StartGoogleLogin(string id, GoogleLoginView view)
    {
        if (view.Starting || view.Active || centerBusy) return;
        string email = view.Email.Text.Trim(); bool saved = view.UseSaved.IsChecked == true;
        if (!email.Contains('@') || !saved && view.Password.Password.Length == 0) { view.Status.Text = "请填写完整邮箱与密码，或选择已保存的密码。"; return; }
        // Capture only for this request. Clear the visible PasswordBox after the
        // worker accepts it; never print an HTTP body or exception payload.
        string? secret = view.Password.Password;
        view.Starting = true; SetGoogleLoginControls(view, true); view.Status.Text = "正在准备独立浏览器…";
        try {
            await LaunchQueued(id, selectBrowser: false);
            var job = await Api($"profiles/{id}/google-login", new { email, password = saved ? "" : secret, useSaved = saved, remember = view.Remember.IsChecked == true, allowOnboarding = view.Onboarding.IsChecked == true, ageConfirmed = view.AgeConfirmed.IsChecked == true });
            view.Password.Clear(); view.AgeConfirmed.IsChecked = false; UpdateGoogleLoginView(view, job); await ReloadGoogleCredentials(id, view);
        } catch { view.Status.Text = "未能启动登录。请检查浏览器、邮箱与密码；若使用已存密码，请确认邮箱与原保存账号一致。"; }
        finally { secret = null; view.Starting = false; SetGoogleLoginControls(view, view.Active); }
    }

    void SetGoogleLoginControls(GoogleLoginView view, bool busy)
    {
        view.Start.IsEnabled = !busy; view.Email.IsEnabled = !busy && !view.Bound; view.Password.IsEnabled = !busy && view.UseSaved.IsChecked != true;
        view.Remember.IsEnabled = !busy; view.UseSaved.IsEnabled = !busy; view.Verify.IsEnabled = !busy; view.Forget.IsEnabled = !busy;
        view.Onboarding.IsEnabled = !busy; view.AgeConfirmed.IsEnabled = !busy; view.Continue.IsEnabled = !busy;
        view.Progress.Visibility = busy ? Visibility.Visible : Visibility.Collapsed;
        view.Cancel.Visibility = view.Active ? Visibility.Visible : Visibility.Collapsed;
        view.Cancel.IsEnabled = view.Active;
    }

    static string GoogleLoginStage(string stage) => stage switch {
        "opening_dola" => "正在寻找 Dola 的 Google 登录入口…", "submitting_email" => "正在填写 Google 邮箱…", "submitting_password" => "正在填写 Google 密码…",
        "waiting_google" => "等待 Google 页面加载与跳转…", "waiting_dola" => "等待返回 Dola 并确认登录…",
        "resuming" => "正在继续当前登录页面…",
        "confirming_workspace" => "正在确认 Google Workspace 新账号欢迎页…",
        "authorizing_dola" => "正在确认 Dola 的姓名、头像与邮箱基础登录授权…",
        "confirming_age" => "正在根据你的确认处理 Dola 年龄提示…",
        "workspace_welcome_required" => "Google 新账号需要确认欢迎页。可勾选自动处理后点继续，或在浏览器手动确认。",
        "age_confirmation_required" => "Dola 需要确认使用者已满 18 岁。请如实勾选年龄确认后点继续，或在浏览器手动处理。",
        "credentials_required" => "当前页面仍需填写 Google 邮箱或密码，请重新输入后开始登录。",
        "logged_in" => "已确认 Dola 登录，会话已保存。", "already_logged_in" => "当前环境已登录 Dola，无需重复登录。",
        "existing_session_unverified" => "浏览器已有登录会话，尚未核对是否为绑定账号。请在网页确认；更换账号建议使用新环境。绑定不会自动退出网站账号。",
        "verification_required" => "需要你完成验证码、两步验证或手机确认。请打开浏览器接管，完成后验证 Dola 登录。",
        "consent_required" => "Google 需要确认授权。基础 Dola 授权可勾选自动处理后继续；额外权限或无法识别的页面请手动确认。",
        "choose_account" => "请在 Google 页面选择要登录的账号，再点击继续登录流程。",
        "account_mismatch" => "Google 页面显示的账号与填写邮箱不同，未提交密码。请手动选择正确账号。",
        "google_rejected" => "Google 拒绝了当前登录或提示账号信息错误。自动填写已停止，请查看浏览器中的提示。",
        "region_blocked" => "Dola 提示地区受限，请先检测真实代理出口并调整地区。",
        "open_dola" => "当前页面不是 Dola / Google 登录页。请在浏览器打开 Dola 后再开始，当前页面未被覆盖。",
        "dola_confirmation" => "Dola 需要手动确认或验证，请打开浏览器接管。",
        "multiple_google_windows" => "出现多个 Google 登录窗口，请手动关闭多余窗口后重试。",
        "page_changed" => "登录页面发生变化，自动填写已暂停，请查看浏览器。",
        "timeout" => "登录等待超时，浏览器保持打开，可手动继续或重试。",
        "connection_failed" => "浏览器连接中断，自动填写已停止，请检查网络和会话。",
        "stopping" => "正在停止；已提交的登录请求仍可能完成。", "cancelled" => "自动填写已停止，浏览器保持打开。",
        "session_changed" => "浏览器会话或代理已改变，本次登录已停止。", _ => "填写账号后开始登录"
    };

    void UpdateGoogleLoginView(GoogleLoginView view, JsonElement job)
    {
        string state = Str(job, "state"); view.Active = state is "running" or "cancelling";
        view.Stage = Str(job, "stage");
        string version = Str(job, "id") + ":" + Str(job, "updatedAt");
        bool changed = state != "idle" && version != view.LastJobVersion;
        if (changed) view.Status.Text = GoogleLoginStage(Str(job, "stage"));
        view.LastJobVersion = version;
        view.Verify.Visibility = state is "needs_attention" or "failed" or "cancelled" or "completed" ? Visibility.Visible : Visibility.Collapsed;
        view.Continue.Visibility = state == "needs_attention" ? Visibility.Visible : Visibility.Collapsed;
        Grid.SetColumn(view.Verify, state == "needs_attention" ? 1 : 0);
        Grid.SetColumnSpan(view.Verify, state == "needs_attention" ? 1 : 2);
        view.Verify.Margin = new Thickness(state == "needs_attention" ? 6 : 0, 8, 0, 0);
        view.Status.Foreground = state == "completed" ? WorkbenchTheme.Green : state is "needs_attention" or "failed" ? Hex("#E8B578") : Text;
        SetGoogleLoginControls(view, view.Active || view.Starting);
        if (changed) RevealGoogleAge(view);
    }

    static void RevealGoogleAge(GoogleLoginView view)
    {
        if (view.Stage != "age_confirmation_required" || !view.AgeConfirmed.IsVisible) return;
        view.Panel.Dispatcher.BeginInvoke(new Action(() => {
            if (view.Stage != "age_confirmation_required" || !view.AgeConfirmed.IsVisible) return;
            if (view.Panel.Parent is StackPanel container && container.Parent is ScrollViewer scroll) {
                var top = view.AgeConfirmed.TransformToAncestor(container).Transform(new Point(0, 0));
                scroll.ScrollToVerticalOffset(Math.Max(0, top.Y - 6));
            }
        }), DispatcherPriority.ContextIdle);
    }

    async Task PollGoogleLogin()
    {
        if (googleLoginPolling || closing) return; googleLoginPolling = true;
        try {
            foreach (var (id, view) in googleLoginViews.ToArray()) {
                if (view.Starting || !view.Active && !view.Panel.IsVisible) continue;
                try { var job = await Api($"profiles/{id}/google-login"); UpdateGoogleLoginView(view, job); }
                catch { if (view.Active) view.Status.Text = "登录进度暂时无法读取；可打开浏览器查看，软件不会自动重复提交。"; }
            }
        } finally { googleLoginPolling = false; }
    }
}
