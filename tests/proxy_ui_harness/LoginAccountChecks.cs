using System.IO;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using CloakWorkbench;

partial class Program
{
    static async Task LoginAccounts(MainWindow main, HttpClient client, string output)
    {
        await (Task)Method("Refresh").Invoke(main, null)!;
        Click(main, "账号列表");
        await Wait(() => Nodes<DataGrid>(main).Any(t => System.Windows.Automation.AutomationProperties.GetName(t) == "登录账号列表"));
        Check("Dedicated credential inventory begins empty without importing browser profiles", Named<DataGrid>(main, "登录账号列表").Items.Count == 0);
        Click(main, "添加账号");
        await Wait(() => Nodes<TextBox>(main).Any(t => System.Windows.Automation.AutomationProperties.GetName(t) == "Google 邮箱"));
        var email = Named<TextBox>(main, "Google 邮箱"); var password = Named<TextBox>(main, "账号密码");
        Check("Account editor is inline without owned windows", main.OwnedWindows.Count == 0 && password.IsVisible);
        Check("New account uses an empty visible text input", password.Text.Length == 0 && !Nodes<PasswordBox>(main).Any(p => p.IsVisible));
        Named<TextBox>(main, "账号名称").Text = "主账号 · 工作空间"; email.Text = "ui-account@example.invalid"; password.Text = "isolated-account-secret";
        Named<TextBox>(main, "账号分组").Text = "示例项目"; Named<TextBox>(main, "账号备注").Text = "仅用于隔离验证";
        Click(main, "保存账号");
        await Wait(() => Named<DataGrid>(main, "登录账号列表").Items.Count == 1 && Nodes<TextBox>(main).Any(t => System.Windows.Automation.AutomationProperties.GetName(t) == "账号密码" && t.Text == "isolated-account-secret"));
        Check("Saved account editor displays plaintext password", Named<TextBox>(main, "账号密码").Text == "isolated-account-secret");
        Check("Account table displays selectable plaintext password", Named<TextBox>(main, "账号明文密码").Text == "isolated-account-secret" && Named<TextBox>(main, "账号明文密码").IsReadOnly);
        using var inventory = JsonDocument.Parse(await client.GetStringAsync("login-accounts")); var record = inventory.RootElement.GetProperty("accounts")[0]; string aid = record.GetProperty("id").GetString()!;
        Check("Account creation does not create a browser", (await BrowserState(client)).GetProperty("id").GetString() == "ui-browser");
        Check("Authenticated account inventory provides plaintext password for display", record.GetProperty("passwordSet").GetBoolean() && record.GetProperty("password").GetString() == "isolated-account-secret" && !record.TryGetProperty("protectedPassword", out _));
        Named<TextBox>(main, "账号密码").Text = "updated-account-secret";
        Click(main, "保存账号");
        await Wait(() => Nodes<TextBox>(main).Any(t => System.Windows.Automation.AutomationProperties.GetName(t) == "账号明文密码" && t.Text == "updated-account-secret") && Nodes<TextBox>(main).Any(t => System.Windows.Automation.AutomationProperties.GetName(t) == "账号密码" && t.Text == "updated-account-secret"));
        Check("Password edits update both table and refilled editor", true);
        Named<TextBox>(main, "账号密码").Clear(); Click(main, "保存账号");
        await Wait(() => Nodes<TextBox>(main).Any(t => System.Windows.Automation.AutomationProperties.GetName(t) == "账号密码" && t.Text == "updated-account-secret"));
        Check("Empty edit preserves saved password and refills plaintext", Named<TextBox>(main, "账号明文密码").Text == "updated-account-secret");
        await Task.Delay(300); Capture(main, Path.Combine(output, "after-save.png"));
        var saveResult = Nodes<TextBlock>(main).FirstOrDefault(t => System.Windows.Automation.AutomationProperties.GetName(t) == "账号保存结果");
        if (saveResult != null && saveResult.Text.Length > 0) File.WriteAllText(Path.Combine(output, "save-result.txt"), saveResult.Text);
        await Wait(() => Nodes<Button>(main).Any(b => b.Content as string == "绑定 / 更换浏览器"));
        Click(main, "绑定 / 更换浏览器");
        await Wait(() => Nodes<ComboBox>(main).Any(t => System.Windows.Automation.AutomationProperties.GetName(t) == "账号绑定浏览器"));
        Named<ComboBox>(main, "账号绑定浏览器").SelectedValue = "ui-browser";
        Click(main, "保存绑定");
        await Wait(() => Nodes<Button>(main).Any(b => b.Content as string == "使用绑定账号自动登录" && b.IsVisible));
        Check("Closed existing browser can bind independent account", (await BrowserState(client)).GetProperty("loginAccountId").GetString() == aid);
        main.Width = 1050; main.Height = 680; await Task.Delay(150); Capture(main, Path.Combine(output, "login-accounts-min.png"));
        var save = Named<Button>(main, "保存账号");
        var corner = save.TranslatePoint(new Point(save.ActualWidth, save.ActualHeight), main);
        Check("Minimum window keeps account save action inside viewport", save.IsVisible && corner.Y < main.ActualHeight && corner.X < main.ActualWidth);
        main.Width = 1440; main.Height = 900; await Task.Delay(150); Capture(main, Path.Combine(output, "login-accounts.png"));
        Named<TextBox>(main, "搜索登录账号").Text = "ui-account@";
        Check("Account search matches saved email", Named<DataGrid>(main, "登录账号列表").Items.Count == 1);
        Named<TextBox>(main, "搜索登录账号").Text = "no-match";
        Check("Account search filters unrelated results", Named<DataGrid>(main, "登录账号列表").Items.Count == 0);
        Named<TextBox>(main, "搜索登录账号").Clear();
        Click(main, "使用绑定账号自动登录");
        try { await Wait(() => Named<Border>(main, "浏览器登录任务").IsVisible); Click(main, "浏览器 Google 登录设置"); await Wait(() => Nodes<Button>(main).Any(b => b.Content as string == "停止自动填写" && b.IsVisible)); }
        catch { Capture(main, Path.Combine(output, "login-start-failure.png")); throw; }
        Check("Bound login supplies email without a password roundtrip", Named<TextBox>(main, "Google 账号邮箱").Text == "ui-account@example.invalid" && !Named<TextBox>(main, "Google 账号邮箱").IsEnabled && Named<PasswordBox>(main, "Google 密码").Password.Length == 0);
        Check("Bound account password is selected automatically", Named<CheckBox>(main, "使用已保存的 Google 密码").IsChecked == true && !Named<CheckBox>(main, "记住 Google 密码").IsVisible);
        Check("Account auto login retains explicit age opt-in", Named<CheckBox>(main, "确认账号使用者已满 18 岁").IsChecked != true);
        Click(main, "停止自动填写");
        await Wait(() => Named<Button>(main, "开始 Google 登录").IsEnabled);
        Click(main, "账号列表"); await Task.Delay(150);
        Check("Account page remains inline after browser login", main.OwnedWindows.Count == 0 && Named<DataGrid>(main, "登录账号列表").IsVisible);
        Click(main, "环境工作台"); await Task.Delay(100);
        Check("Browser navigation hides account page", !Named<DataGrid>(main, "登录账号列表").IsVisible);
        await (Task)Method("CloseProfile").Invoke(main, new object[] { "ui-browser" })!;
        Click(main, "账号列表");
        await (Task)Method("LoadLoginAccounts").Invoke(main, null)!;
        Check("Closing browser leaves main application and inventory available", main.IsVisible && Named<DataGrid>(main, "登录账号列表").Items.Count == 1);
        // Exercise actual create/edit environment pickers with an unbound second account.
        using var response = await client.PostAsync("login-accounts", new StringContent(JsonSerializer.Serialize(new { email = "second@example.invalid", password = "second-ui-secret" }), Encoding.UTF8, "application/json"));
        response.EnsureSuccessStatusCode();
        await (Task)Method("LoadLoginAccounts").Invoke(main, null)!;
        var dialog = (Window)Method("CreateAccountDialog").Invoke(main, new object?[] { null })!;
        dialog.Show(); await Task.Delay(100);
        var picker = Named<ComboBox>(dialog, "浏览器绑定账号");
        Check("New browser picker excludes an account bound elsewhere", picker.Items.Count == 2);
        picker.SelectedIndex = 1;
        Named<TextBox>(dialog, "环境名称").Text = "新建绑定环境";
        Click(dialog, "创建浏览器"); await Wait(() => !dialog.IsVisible);
        using var profiles = JsonDocument.Parse(await client.GetStringAsync("profiles"));
        Check("New browser stores selected account during creation", profiles.RootElement.GetProperty("profiles").EnumerateArray().Any(p => p.GetProperty("name").GetString() == "新建绑定环境" && p.GetProperty("loginAccountId").GetString() != ""));
        Method("ShowEnvironmentAccountBinding").Invoke(main, new object[] { "ui-browser" });
        await Wait(() => Nodes<Button>(main).Any(b => b.Content as string == "保存账号绑定" && b.IsVisible));
        Named<ComboBox>(main, "浏览器绑定账号").SelectedValue = ""; Click(main, "保存账号绑定");
        await Wait(() => Nodes<Button>(main).Any(b => b.Content as string == "环境设置" && b.IsVisible));
        Check("Unbinding keeps original browser profile", (await BrowserState(client)).GetProperty("loginAccountId").GetString() == "");
        File.WriteAllText(Path.Combine(output, "login-account-checks.json"), JsonSerializer.Serialize(new { checks = Checks, passed = Checks.Count }, new JsonSerializerOptions { WriteIndented = true }));
    }
}
