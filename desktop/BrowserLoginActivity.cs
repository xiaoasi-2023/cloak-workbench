using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Media;

namespace CloakWorkbench;

public sealed partial class MainWindow
{
    readonly Border browserLoginActivity = new() { Visibility = Visibility.Collapsed };
    readonly TextBlock browserLoginHeading = new(), browserLoginStatus = new(), browserLoginSteps = new();
    readonly ProgressBar browserLoginProgress = new() { Height = 2, IsIndeterminate = true, Foreground = Accent, Background = Line };
    readonly CheckBox browserLoginAge = new() { Foreground = Text, Content = "我确认此账号使用者已满 18 岁，允许确认年龄提示", Margin = new Thickness(0, 9, 0, 0) };
    Button? browserLoginStop, browserLoginContinue, browserLoginVerify, browserLoginRetry, browserLoginDismiss, browserLoginOther, browserLoginSettings;
    string? browserLoginDisplayed;

    Border CreateBrowserLoginActivity()
    {
        var body = new StackPanel { Margin = new Thickness(14, 10, 14, 10) };
        var top = new DockPanel();
        var actions = new WrapPanel { HorizontalAlignment = HorizontalAlignment.Right };
        DockPanel.SetDock(actions, Dock.Right); top.Children.Add(actions);
        Button Action(string title, string name, Action callback) {
            var button = Button(title, callback); button.Padding = new Thickness(9, 5, 9, 5); button.FontSize = 11; button.Margin = new Thickness(4, 0, 0, 0);
            AutomationProperties.SetName(button, name); actions.Children.Add(button); return button;
        }
        browserLoginOther = Action("其他任务", "切换其他登录任务", () => {
            var menu = new ContextMenu { Style = (Style)FindResource("AccountActionMenu") };
            foreach (var (id, view) in googleLoginViews.Where(pair => pair.Key != selected && (pair.Value.Active || pair.Value.Starting || pair.Value.State == "needs_attention"))) {
                string target = id;
                var item = new MenuItem { Style = (Style)FindResource("AccountActionItem"), Header = (profiles.TryGetValue(id, out var p) ? Str(p, "name") : id) + " · " + GoogleLoginStage(view.Stage) };
                item.Click += async (_, _) => { if (RuntimeIds().Contains(target)) Select(target); else await OpenGoogleLogin(target); }; menu.Items.Add(item);
            }
            menu.PlacementTarget = browserLoginOther; menu.IsOpen = true;
        });
        browserLoginContinue = Action("已完成，继续", "浏览器继续 Google 登录", async () => {
            if (selected is string id && googleLoginViews.TryGetValue(id, out var view)) {
                if (view.Stage == "age_confirmation_required") view.AgeConfirmed.IsChecked = browserLoginAge.IsChecked == true;
                await ContinueGoogleLogin(id, view);
            }
        });
        browserLoginRetry = Action("重新登录", "浏览器重新 Google 登录", async () => {
            if (selected is string id && googleLoginViews.TryGetValue(id, out var view)) {
                if (view.UseSaved.IsChecked == true) await StartGoogleLogin(id, view);
                else await OpenGoogleLogin(id);
            }
        });
        browserLoginVerify = Action("检测登录", "浏览器检测 Dola 登录", async () => {
            if (selected is string id && googleLoginViews.TryGetValue(id, out var view)) await VerifyGoogleLogin(id, view);
        });
        browserLoginStop = Action("停止", "浏览器停止 Google 登录", async () => {
            if (selected is string id && googleLoginViews.TryGetValue(id, out var view)) await CancelGoogleLogin(id, view);
        });
        browserLoginSettings = Action("登录设置", "浏览器 Google 登录设置", async () => { if (selected is string id) await OpenGoogleLogin(id); });
        browserLoginDismiss = Action("收起", "收起浏览器登录结果", () => {
            if (selected is string id && googleLoginViews.TryGetValue(id, out var view)) view.Dismissed = true;
            RenderBrowserLoginActivity();
        });
        browserLoginHeading.FontSize = 12; browserLoginHeading.FontWeight = FontWeights.SemiBold; browserLoginHeading.Foreground = Accent;
        browserLoginHeading.VerticalAlignment = VerticalAlignment.Center; browserLoginHeading.TextTrimming = TextTrimming.CharacterEllipsis; browserLoginHeading.Margin = new Thickness(0, 0, 10, 0);
        AutomationProperties.SetName(browserLoginHeading, "浏览器登录任务标题"); top.Children.Add(browserLoginHeading); body.Children.Add(top);
        browserLoginStatus.FontSize = 12; browserLoginStatus.Foreground = Text; browserLoginStatus.TextWrapping = TextWrapping.Wrap; browserLoginStatus.LineHeight = 19; browserLoginStatus.Margin = new Thickness(0, 7, 0, 0);
        AutomationProperties.SetName(browserLoginStatus, "浏览器 Google 登录进度"); body.Children.Add(browserLoginStatus);
        browserLoginSteps.FontSize = 10; browserLoginSteps.Foreground = Muted; browserLoginSteps.Margin = new Thickness(0, 5, 0, 0);
        body.Children.Add(browserLoginSteps);
        AutomationProperties.SetName(browserLoginAge, "浏览器确认账号使用者已满 18 岁"); body.Children.Add(browserLoginAge);
        var shell = new DockPanel(); DockPanel.SetDock(browserLoginProgress, Dock.Bottom); shell.Children.Add(browserLoginProgress); shell.Children.Add(body);
        browserLoginActivity.Child = shell; browserLoginActivity.Background = Hex("#192820"); browserLoginActivity.BorderBrush = Hex("#304B3E"); browserLoginActivity.BorderThickness = new Thickness(0, 1, 0, 1);
        AutomationProperties.SetName(browserLoginActivity, "浏览器登录任务");
        return browserLoginActivity;
    }

    void RenderBrowserLoginActivity()
    {
        if (browserLoginStop == null) return;
        var view = selected != null ? googleLoginViews.GetValueOrDefault(selected) : null;
        int other = googleLoginViews.Count(pair => pair.Key != selected && (pair.Value.Active || pair.Value.Starting || pair.Value.State == "needs_attention"));
        bool visible = view != null && !view.Dismissed && (view.Starting || view.State is not ("" or "idle"));
        browserLoginActivity.Visibility = visible || other > 0 ? Visibility.Visible : Visibility.Collapsed;
        if (browserLoginDisplayed != selected) { browserLoginAge.IsChecked = false; browserLoginDisplayed = selected; }
        browserLoginOther!.Visibility = other > 0 ? Visibility.Visible : Visibility.Collapsed;
        browserLoginOther.Content = $"其他任务 {other}";
        browserLoginSettings!.Visibility = visible ? Visibility.Visible : Visibility.Collapsed;
        bool busy = view != null && (view.Active || view.Starting), attention = view?.State == "needs_attention", complete = view?.State == "completed";
        browserLoginStop.Visibility = visible && busy ? Visibility.Visible : Visibility.Collapsed;
        browserLoginStop.IsEnabled = view?.Active == true && view.State != "cancelling";
        browserLoginContinue!.Visibility = visible && attention && view!.Stage != "credentials_required" ? Visibility.Visible : Visibility.Collapsed;
        browserLoginContinue.IsEnabled = !busy;
        browserLoginRetry!.Visibility = visible && !busy && !complete ? Visibility.Visible : Visibility.Collapsed;
        browserLoginVerify!.Visibility = visible && !busy ? Visibility.Visible : Visibility.Collapsed;
        browserLoginDismiss!.Visibility = visible && !busy && !attention ? Visibility.Visible : Visibility.Collapsed;
        browserLoginProgress.Visibility = visible && busy ? Visibility.Visible : Visibility.Collapsed;
        browserLoginAge.Visibility = visible && attention && view!.Stage == "age_confirmation_required" ? Visibility.Visible : Visibility.Collapsed;
        browserLoginAge.IsEnabled = !busy;
        if (!visible) {
            browserLoginHeading.Text = "其他环境有登录任务";
            browserLoginHeading.Foreground = Muted;
            browserLoginStatus.Text = "点击“其他任务”可直接切换到正在登录或等待处理的浏览器。";
            browserLoginSteps.Text = ""; return;
        }
        string name = profiles.TryGetValue(selected!, out var p) ? Str(p, "name") : "当前环境";
        browserLoginHeading.Text = complete ? "Dola 登录已确认" : attention ? "登录等待你处理" : busy ? "Google 登录 Dola" : "登录任务已停止";
        browserLoginHeading.Foreground = complete ? WorkbenchTheme.Green : attention || view!.State == "failed" ? Hex("#E8B578") : Accent;
        browserLoginHeading.ToolTip = name + " · " + view!.Email.Text;
        browserLoginStatus.Text = view.Status.Text;
        browserLoginStatus.Foreground = view.Status.Foreground;
        browserLoginSteps.Text = name + "  ·  " + (complete ? "会话保存在此环境，可继续使用浏览器" : "Dola 入口  ›  Google 账号  ›  授权 / 验证  ›  返回 Dola");
        browserLoginSteps.TextTrimming = TextTrimming.CharacterEllipsis;
    }

    async Task ContinueGoogleLogin(string id, GoogleLoginView view)
    {
        if (view.Starting || view.Active) return;
        view.Starting = true; SetGoogleLoginControls(view, true); view.Status.Text = "正在继续当前登录页面…"; RenderBrowserLoginActivity();
        try {
            var job = await Api($"profiles/{id}/google-login/continue", new { allowOnboarding = view.Onboarding.IsChecked == true, ageConfirmed = view.AgeConfirmed.IsChecked == true });
            UpdateGoogleLoginView(view, job); view.AgeConfirmed.IsChecked = false; browserLoginAge.IsChecked = false;
        } catch { view.Status.Text = "当前任务无法继续。原页面若还需要邮箱或密码，请点击重新登录。"; }
        finally { view.Starting = false; SetGoogleLoginControls(view, view.Active); RenderBrowserLoginActivity(); }
    }

    async Task VerifyGoogleLogin(string id, GoogleLoginView view)
    {
        if (view.Starting || view.Active) return;
        view.Starting = true; SetGoogleLoginControls(view, true); view.Status.Text = "正在检测当前 Dola 页面…"; RenderBrowserLoginActivity();
        try {
            var result = await Api($"profiles/{id}/google-login/verify", new { });
            UpdateGoogleLoginView(view, result.GetProperty("login"));
            view.Status.Text = Str(result, "confirmed").Equals("true", StringComparison.OrdinalIgnoreCase) ? "已确认 Dola 登录，会话已保存。" : "Dola 登录尚未确认，请完成浏览器中的验证后重试。";
            await Refresh();
        } catch { view.Status.Text = "登录结果暂时无法读取，请等待页面加载后重试。"; }
        finally { view.Starting = false; SetGoogleLoginControls(view, view.Active); RenderBrowserLoginActivity(); }
    }

    async Task CancelGoogleLogin(string id, GoogleLoginView view)
    {
        view.Cancel.IsEnabled = false;
        try { UpdateGoogleLoginView(view, await Api($"profiles/{id}/google-login/cancel", new { })); }
        catch { view.Status.Text = "停止请求未完成，请稍后重试。"; view.Cancel.IsEnabled = true; }
        RenderBrowserLoginActivity();
    }
}
