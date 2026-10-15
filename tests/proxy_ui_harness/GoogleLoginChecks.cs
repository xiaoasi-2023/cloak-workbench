using System.IO;
using System.Net.Http;
using System.Reflection;
using System.Text;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using CloakWorkbench;

partial class Program
{
    static async Task GoogleLogin(MainWindow main, HttpClient client, string output)
    {
        await (Task)Method("Refresh").Invoke(main, null)!;
        Method("ShowGoogleLogin").Invoke(main, new object[] { "ui-browser" });
        await Wait(() => Nodes<TextBlock>(main).Any(t => t.Text.Contains("未绑定账号")));
        var email = Named<TextBox>(main, "Google 账号邮箱"); var password = Named<PasswordBox>(main, "Google 密码");
        Check("Google login form is inline without another window", main.OwnedWindows.Count == 0 && password.IsVisible);
        Check("Remember Google password is opt-in", Named<CheckBox>(main, "记住 Google 密码").IsChecked != true);
        var onboarding = Named<CheckBox>(main, "允许新账号欢迎页与 Dola 基础授权");
        var adult = Named<CheckBox>(main, "确认账号使用者已满 18 岁");
        Check("New account automation is visible and age assertion requires explicit opt-in", onboarding.IsChecked == true && adult.IsChecked != true);
        Click(main, "开始 Google 登录"); await Task.Delay(80);
        Check("Missing Google credentials produce useful inline validation", Named<TextBlock>(main, "Google 登录进度").Text.Contains("请填写"));
        email.Text = "ui-fixture@example.invalid"; password.Password = "isolated-google-ui-fixture";
        adult.IsChecked = true;
        Click(main, "代理管理"); Method("ShowGoogleLogin").Invoke(main, new object[] { "ui-browser" });
        await Task.Delay(120);
        Check("Navigation preserves unsent login form without duplicating password boxes", ReferenceEquals(email, Named<TextBox>(main, "Google 账号邮箱")) && password.Password == "isolated-google-ui-fixture");
        Check("Navigation preserves explicit new-account choices before submission", adult.IsChecked == true && onboarding.IsChecked == true);
        Click(main, "开始 Google 登录");
        await Wait(() => Nodes<Button>(main).Any(b => b.Content as string == "停止自动填写" && b.IsVisible));
        Check("Google login pending state disables duplicate credential submission", !email.IsEnabled && !Named<Button>(main, "开始 Google 登录").IsEnabled);
        await Wait(() => password.Password.Length == 0);
        Check("Accepted credentials clear masked form value", password.Password.Length == 0);
        Check("Age confirmation is consumed by one accepted task and locked while running", adult.IsChecked != true && !adult.IsEnabled && !onboarding.IsEnabled);
        // The runner substitutes only the provider state machine; this is an
        // actual owned Chromium window and production HTTP/UI pipeline.
        var runtime = await BrowserState(client);
        var hosts = (Dictionary<string, NativeBrowserHost>)typeof(MainWindow).GetField("hosts", BindingFlags.NonPublic | BindingFlags.Instance)!.GetValue(main)!;
        var native = hosts["ui-browser"].AttachedHandle;
        Check("Google helper launches only selected environment and keeps it hidden on management page", hosts.Count == 1 && native != IntPtr.Zero && !IsWindowVisible(native));
        Method("ShowGoogleLogin").Invoke(main, new object[] { "ui-browser" }); await Task.Delay(100);
        Check("Reopening active Google form preserves same controls", ReferenceEquals(email, Named<TextBox>(main, "Google 账号邮箱")));
        Click(main, "停止自动填写");
        await Wait(() => Named<TextBlock>(main, "Google 登录进度").Text.Contains("已停止"));
        Check("Cancelling Google login re-enables fields and retains browser process", email.IsEnabled && Named<Button>(main, "开始 Google 登录").IsEnabled && (await BrowserState(client)).GetProperty("pid").GetInt32() == runtime.GetProperty("pid").GetInt32());
        password.Password = "isolated-google-ui-fixture"; Named<CheckBox>(main, "记住 Google 密码").IsChecked = true;
        Click(main, "开始 Google 登录"); await Wait(() => Named<CheckBox>(main, "使用已保存的 Google 密码").IsVisible);
        Click(main, "停止自动填写"); await Wait(() => Named<Button>(main, "开始 Google 登录").IsEnabled);
        var saved = Named<CheckBox>(main, "使用已保存的 Google 密码"); saved.IsChecked = true;
        Check("Saved Google secret never returns to password field", !password.IsEnabled && password.Password.Length == 0);
        main.Width = 1050; main.Height = 680; await Task.Delay(100); Capture(main, Path.Combine(output, "google-login-min.png"));
        Check("Minimum window preserves Google login fields and close control", email.ActualWidth >= 230 && Named<Button>(main, "关闭环境侧栏").IsVisible);
        bool InWindow(FrameworkElement control) { var end = control.TransformToAncestor(main).Transform(new Point(control.ActualWidth, control.ActualHeight)); return control.IsVisible && end.X <= main.ActualWidth && end.Y <= main.ActualHeight; }
        Check("Minimum window pins Google start and manual handover without scrolling", InWindow(Named<Button>(main, "开始 Google 登录")) && InWindow(Named<Button>(main, "打开浏览器 / 手动接管")));
        Method("ShowGoogleLogin").Invoke(main, new object[] { "ui-browser" }); await Task.Delay(120);
        Check("Credential refresh preserves draft and saved selection", email.Text == "ui-fixture@example.invalid" && saved.IsChecked == true);
        Click(main, "开始 Google 登录"); await Wait(() => !Named<Button>(main, "开始 Google 登录").IsEnabled);
        await Wait(() => Named<Button>(main, "停止自动填写").IsVisible); Click(main, "停止自动填写"); await Wait(() => Named<Button>(main, "开始 Google 登录").IsEnabled);
        Check("Saved credential can be reused for same environment", password.Password.Length == 0 && saved.IsVisible);
        email.Text = "different@example.invalid";
        Check("Changing email invalidates saved-password selection", saved.IsChecked != true && password.IsEnabled);
        Click(main, "清除已保存的 Google 密码"); await Wait(() => !saved.IsVisible);
        Check("Forget saved Google password preserves browser session", !Named<CheckBox>(main, "使用已保存的 Google 密码").IsVisible && (await BrowserState(client)).GetProperty("pid").GetInt32() == runtime.GetProperty("pid").GetInt32());
        email.Text = "ui-fixture@example.invalid"; password.Password = "isolated-google-ui-fixture";
        Named<CheckBox>(main, "记住 Google 密码").IsChecked = false;
        Click(main, "开始 Google 登录");
        await Wait(() => Named<TextBlock>(main, "Google 登录进度").Text.Contains("两步验证"));
        Check("Google verification pauses with explicit manual handover", Named<Button>(main, "开始 Google 登录").IsEnabled && !Named<Button>(main, "停止自动填写").IsVisible && Named<Button>(main, "打开浏览器 / 手动接管").IsVisible);
        main.UpdateLayout();
        Check("Minimum window keeps verification progress and manual confirmation accessible", InWindow(Named<TextBlock>(main, "Google 登录进度")) && InWindow(Named<Button>(main, "已手动完成，验证 Dola 登录")));
        main.Width = 1440; main.Height = 900; await Task.Delay(100); Capture(main, Path.Combine(output, "google-login-desktop.png"));
        Click(main, "打开浏览器 / 手动接管"); await Task.Delay(350);
        Check("Manual handover restores original embedded browser", IsWindowVisible(native) && (await BrowserState(client)).GetProperty("pid").GetInt32() == runtime.GetProperty("pid").GetInt32());
        Method("ShowGoogleLogin").Invoke(main, new object[] { "ui-browser" }); await Task.Delay(120);
        Click(main, "已手动完成，验证 Dola 登录"); await Wait(() => Named<Button>(main, "已手动完成，验证 Dola 登录").IsEnabled);
        await Task.Delay(1300);
        Check("Non-Dola page never produces fabricated Google login success or loses manual verification feedback", Named<TextBlock>(main, "Google 登录进度").Text.Contains("尚未确认"));
        Click(main, "继续 Google 登录流程");
        await Wait(() => Named<TextBlock>(main, "Google 登录进度").Text.Contains("已满 18 岁"));
        Check("Continue without password reaches explicit age handover", password.Password.Length == 0 && adult.IsEnabled && Named<Button>(main, "继续 Google 登录流程").IsEnabled);
        main.Width = 1050; main.Height = 680; await Task.Delay(100); Capture(main, Path.Combine(output, "google-onboarding-min.png"));
        DependencyObject? ancestor = adult;
        while (ancestor != null && ancestor is not ScrollViewer) ancestor = VisualTreeHelper.GetParent(ancestor);
        var ageScroll = (ScrollViewer)ancestor!; var ageTop = adult.TransformToAncestor(ageScroll).Transform(new Point(0, 0));
        Check("Age pause automatically reveals full assertion in the minimum scroll viewport", ageTop.Y >= 0 && ageTop.Y + adult.ActualHeight <= ageScroll.ActualHeight);
        Check("Minimum window keeps continue, age handover progress and verify accessible", InWindow(Named<Button>(main, "继续 Google 登录流程")) && InWindow(Named<TextBlock>(main, "Google 登录进度")) && InWindow(Named<Button>(main, "已手动完成，验证 Dola 登录")));
        adult.IsChecked = true; Click(main, "继续 Google 登录流程");
        await Wait(() => Named<TextBlock>(main, "Google 登录进度").Text.Contains("Dola 需要手动确认"));
        Check("Explicit age confirmation resumes same browser without credential replay", adult.IsChecked != true && password.Password.Length == 0 && (await BrowserState(client)).GetProperty("pid").GetInt32() == runtime.GetProperty("pid").GetInt32());
        var root = Environment.GetEnvironmentVariable("CLOAK_WORKBENCH_ROOT")!;
        Check("Google credentials stay out of profile inventory and desktop logs", !File.ReadAllText(Path.Combine(root, "data", "profiles.json")).Contains("isolated-google-ui-fixture") && !File.ReadAllText(Path.Combine(root, ".local", "desktop.log")).Contains("isolated-google-ui-fixture"));
    }
}
