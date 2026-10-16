using System.IO;
using System.Net.Http;
using System.Reflection;
using System.Text.Json;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using CloakWorkbench;

partial class Program
{
    static async Task Workspaces(MainWindow main, HttpClient client, string output)
    {
        async Task<JsonElement> Get(string path) { using var doc = JsonDocument.Parse(await client.GetStringAsync(path)); return doc.RootElement.Clone(); }
        string Text(object row, string property) => row.GetType().GetProperty(property)!.GetValue(row)?.ToString() ?? "";
        void SelectOnly(DataGrid table, string email) {
            foreach (var row in table.Items.Cast<object>()) row.GetType().GetProperty("Selected")!.SetValue(row, Text(row, "Email") == email);
            table.Items.Refresh();
        }
        bool Done() => Named<Button>(main, "同步员工").IsEnabled;
        await (Task)Method("Launch").Invoke(main, new object[] { "ui-browser" })!; await Task.Delay(400);
        var browser = await BrowserState(client); int port = browser.GetProperty("cdpPort").GetInt32();
        await Evaluate(port, "(()=>{window.__enterpriseMarker='same-document';document.querySelector('#entry').value='workspace-preserved-draft';return true})()");
        Click(main, "企业目录"); await Wait(() => Nodes<ComboBox>(main).Any(c => AutomationProperties.GetName(c) == "企业选择" && c.Items.Count == 2));
        var picker = Named<ComboBox>(main, "企业选择");
        var companies = (await Get("workspaces")).GetProperty("workspaces").EnumerateArray().ToArray();
        string alpha = companies.Single(c => c.GetProperty("adminEmail").GetString() == "admin@alpha.invalid").GetProperty("id").GetString()!;
        string beta = companies.Single(c => c.GetProperty("adminEmail").GetString() == "admin@beta.invalid").GetProperty("id").GetString()!;
        picker.SelectedValue = alpha; await Task.Delay(150);
        var table = Named<DataGrid>(main, "企业员工列表");
        Check("Enterprise page stays within main window", main.OwnedWindows.Count == 0 && table.Items.Count == 0);
        Click(main, "连接设置"); await Wait(() => Nodes<Button>(main).Any(b => b.Content as string == "保存企业连接"));
        Click(main, "检测企业连接"); await Wait(() => !Done());
        await Wait(() => Named<Button>(main, "取消企业任务").IsEnabled); await Task.Delay(180);
        Check("Connection job shows progress and blocks duplicate actions", Named<ProgressBar>(main, "企业任务进度").Visibility == Visibility.Visible && !Named<Button>(main, "批量创建员工").IsEnabled);
        Check("Waiting uses an inline task overlay with an accessible cancel action", Named<Grid>(main, "企业任务遮罩").IsVisible && Named<Button>(main, "取消企业任务").IsVisible && main.OwnedWindows.Count == 0);
        Capture(main, Path.Combine(output, "workspace-task-overlay.png"));
        main.Width = 1050; main.Height = 680; main.UpdateLayout();
        Check("Task card and cancellation fit the minimum window", Named<Button>(main, "取消企业任务").TransformToAncestor(main).Transform(new Point()).Y + Named<Button>(main, "取消企业任务").ActualHeight < main.ActualHeight);
        Capture(main, Path.Combine(output, "workspace-task-overlay-min.png")); main.Width = 1440; main.Height = 900;
        Check("Running job locks already-open connection editor", !Named<Button>(main, "保存企业连接").IsEnabled && !Named<ComboBox>(main, "企业管理代理").IsEnabled);
        await Wait(Done); Check("Connection resolves actual tenant domain", Nodes<TextBlock>(main).Any(t => t.Text.Contains("alpha.invalid") && t.Text.Contains("已连接")));
        Check("Completed task removes the waiting overlay", !Named<Grid>(main, "企业任务遮罩").IsVisible);
        await Task.Delay(4500);
        Check("Successful enterprise task notice expires", !Named<Border>(main, "企业操作结果提示").IsVisible);
        await (Task)Method("LoadEnterpriseUsers").Invoke(main, null)!;
        Check("Reloading the directory does not resurrect an expired job notice", !Named<Border>(main, "企业操作结果提示").IsVisible);
        Check("Finished job unlocks connection editor", Named<Button>(main, "保存企业连接").IsEnabled);
        var beforeSave = (await Get("workspaces")).GetProperty("workspaces").EnumerateArray().Single(w => w.GetProperty("id").GetString() == alpha);
        Click(main, "保存企业连接"); await Wait(() => Named<Button>(main, "保存企业连接").IsEnabled);
        var afterSave = (await Get("workspaces")).GetProperty("workspaces").EnumerateArray().Single(w => w.GetProperty("id").GetString() == alpha);
        Check("Unchanged editor save preserves verified connection and revision", afterSave.GetProperty("status").GetString() == "connected" && beforeSave.GetProperty("revision").GetInt32() == afterSave.GetProperty("revision").GetInt32());
        Click(main, "关闭企业侧栏");
        Click(main, "同步员工"); await Wait(() => !Done()); await Wait(Done); await Wait(() => table.Items.Count == 1);
        var inventory = (await Get("login-accounts")).GetProperty("accounts").EnumerateArray().ToArray();
        Check("Sync imports passwordless account with enterprise provenance", inventory.Length == 1 && inventory[0].GetProperty("password").GetString() == "" && inventory[0].GetProperty("workspaceId").GetString() == alpha);
        Check("Directory password column leaves unknown passwords blank", table.Columns.Any(c => c.Header as string == "密码") && Text(table.Items[0], "Password") == "");
        Click(main, "批量创建员工"); main.UpdateLayout();
        Check("Human name generation is the default and hides sequence and CSV inputs", Named<ComboBox>(main, "员工生成方式").SelectedIndex == 0 && !Named<TextBox>(main, "账号前缀").IsVisible && !Named<TextBox>(main, "CSV：email,firstName,lastName,password,orgUnitPath").IsVisible);
        Named<ComboBox>(main, "员工生成方式").SelectedIndex = 1;
        Named<TextBox>(main, "账号前缀").Text = "qa"; Named<TextBox>(main, "创建数量").Text = "2"; Named<TextBox>(main, "初始密码").Text = "fixture-create-password";
        Check("First-login password change defaults off", Nodes<CheckBox>(main).Single(c => c.Content as string == "要求员工首次登录修改密码").IsChecked == false);
        main.Width = 1050; main.Height = 680; await Task.Delay(180); Capture(main, Path.Combine(output, "workspace-create-min.png"));
        Check("Preview action remains visible at minimum size", Named<Button>(main, "预览待创建员工").IsVisible);
        main.Width = 1440; main.Height = 900; await Task.Delay(150);
        Click(main, "预览待创建员工"); await Wait(() => Nodes<Button>(main).Any(b => b.Content as string == "确认执行有效行"));
        Check("Preview requires explicit confirmation", !Named<Button>(main, "确认执行有效行").IsEnabled);
        Check("Preview identifies tenant and generated users without passwords", Nodes<TextBlock>(main).Any(t => t.Text.Contains("Alpha 企业") && t.Text.Contains("有效 2")) && Nodes<TextBlock>(main).Any(t => t.Text.Contains("qa001@alpha.invalid")) && !Nodes<TextBlock>(main).Any(t => t.Text.Contains("fixture-create-password")));
        Click(main, "返回修改"); main.UpdateLayout(); Named<TextBox>(main, "创建数量").Text = "2";
        Check("Back from preview preserves original draft fields", Named<TextBox>(main, "账号前缀").Text == "qa" && Named<TextBox>(main, "初始密码").Text == "fixture-create-password");
        Click(main, "预览待创建员工"); await Wait(() => Nodes<Button>(main).Any(b => b.Content as string == "确认执行有效行"));
        Nodes<CheckBox>(main).Single(c => c.Content as string == "已核对目标企业与员工，确认执行").IsChecked = true;
        Click(main, "确认执行有效行"); await Wait(() => !Done());
        Click(main, "运行诊断"); await Task.Delay(150); Click(main, "企业目录"); await Wait(Done); await Wait(() => table.Items.Count == 3);
        Check("Batch creation continues across management navigation", table.Items.Cast<object>().Count(r => Text(r, "Email").StartsWith("qa")) == 2);
        inventory = (await Get("login-accounts")).GetProperty("accounts").EnumerateArray().ToArray();
        Check("Created users save initial passwords only after cloud confirmation", inventory.Count(a => a.GetProperty("password").GetString() == "fixture-create-password") == 2);
        Check("Batch-created passwords appear directly in employee rows", table.Items.Cast<object>().Count(r => Text(r, "Password") == "fixture-create-password") == 2 && Text(table.Items.Cast<object>().Single(r => Text(r, "Email") == "existing@alpha.invalid"), "Password") == "");
        Capture(main, Path.Combine(output, "workspace-directory-desktop.png"));
        SelectOnly(table, "qa001@alpha.invalid"); Click(main, "批量重置密码"); main.UpdateLayout();
        Named<TextBox>(main, "新密码").Text = "fixture-reset-password";
        Click(main, "预览员工变更"); await Wait(() => Nodes<Button>(main).Any(b => b.Content as string == "确认执行有效行"));
        Nodes<CheckBox>(main).Single(c => c.Content as string == "已核对目标企业与员工，确认执行").IsChecked = true;
        Click(main, "确认执行有效行"); await Wait(() => !Done()); await Wait(Done);
        inventory = (await Get("login-accounts")).GetProperty("accounts").EnumerateArray().ToArray();
        Check("Confirmed password reset updates matching local account only", inventory.Single(a => a.GetProperty("email").GetString() == "qa001@alpha.invalid").GetProperty("password").GetString() == "fixture-reset-password" && inventory.Single(a => a.GetProperty("email").GetString() == "qa002@alpha.invalid").GetProperty("password").GetString() == "fixture-create-password");
        Check("Reset password refreshes only its employee row", Text(table.Items.Cast<object>().Single(r => Text(r, "Email") == "qa001@alpha.invalid"), "Password") == "fixture-reset-password" && Text(table.Items.Cast<object>().Single(r => Text(r, "Email") == "qa002@alpha.invalid"), "Password") == "fixture-create-password");
        SelectOnly(table, "qa001@alpha.invalid"); Click(main, "批量修改资料"); main.UpdateLayout();
        Nodes<CheckBox>(main).Single(c => c.Content as string == "修改部门").IsChecked = true; Named<TextBox>(main, "部门").Text = "Engineering";
        Click(main, "预览员工变更"); await Wait(() => Nodes<Button>(main).Any(b => b.Content as string == "确认执行有效行"));
        Nodes<CheckBox>(main).Single(c => c.Content as string == "已核对目标企业与员工，确认执行").IsChecked = true;
        Click(main, "确认执行有效行"); await Wait(() => !Done()); await Wait(Done);
        Check("Employee metadata patch updates directory row", table.Items.Cast<object>().Single(r => Text(r, "Email") == "qa001@alpha.invalid") is var updated && Text(updated, "Department") == "Engineering");
        SelectOnly(table, "qa001@alpha.invalid"); Click(main, "批量暂停"); main.UpdateLayout(); Click(main, "预览员工变更"); await Wait(() => Nodes<Button>(main).Any(b => b.Content as string == "确认执行有效行"));
        Nodes<CheckBox>(main).Single(c => c.Content as string == "已核对目标企业与员工，确认执行").IsChecked = true;
        Click(main, "确认执行有效行"); await Wait(() => !Done()); await Wait(Done);
        Check("Suspension clearly appears in directory", Text(table.Items.Cast<object>().Single(r => Text(r, "Email") == "qa001@alpha.invalid"), "State") == "已暂停");
        picker.SelectedValue = beta; await Task.Delay(180);
        Check("Switching enterprise clears stale rows and selections", table.Items.Count == 0);
        Click(main, "检测企业连接"); await Wait(() => !Done()); await Wait(Done);
        Click(main, "同步员工"); await Wait(() => !Done()); await Wait(Done); await Wait(() => table.Items.Count == 1);
        Check("Second enterprise displays only its own staff", Text(table.Items[0], "Email") == "existing@beta.invalid" && !(bool)table.Items[0].GetType().GetProperty("Selected")!.GetValue(table.Items[0])!);
        Check("Switching enterprise cannot display another enterprise password", Text(table.Items[0], "Password") == "");
        Click(main, "批量创建员工"); main.UpdateLayout(); Named<TextBox>(main, "初始密码").Text = "fixture-beta-password";
        Click(main, "预览待创建员工"); await Wait(() => Nodes<Button>(main).Any(b => b.Content as string == "确认执行有效行"));
        picker.SelectedValue = alpha; await Task.Delay(200);
        Check("Tenant switch dismisses previous tenant confirmation", !Nodes<Button>(main).Any(b => b.Content as string == "确认执行有效行") && table.Items.Count == 3);
        Click(main, "账号列表"); await Wait(() => Nodes<DataGrid>(main).Any(t => AutomationProperties.GetName(t) == "登录账号列表" && t.Items.Count == 4));
        var accounts = Named<DataGrid>(main, "登录账号列表"); var filter = Named<ComboBox>(main, "账号所属企业筛选"); filter.SelectedValue = beta;
        Check("Account inventory filters by enterprise", accounts.Items.Count == 1 && Text(accounts.Items[0], "Email") == "existing@beta.invalid");
        Check("Synced account explicitly indicates missing password", Text(accounts.Items[0], "Password") == "未保存密码");
        filter.SelectedValue = alpha; Check("Alpha filter retains its three accounts", accounts.Items.Count == 3);
        Click(main, "企业目录"); await Wait(() => table.Items.Count == 3); Click(main, "连接设置"); await Wait(() => Nodes<Button>(main).Any(b => b.Content as string == "保存企业连接")); main.UpdateLayout();
        Check("Connection editor shows delegation setup and preserves key privately", Nodes<TextBox>(main).Any(t => t.Text.Contains("admin.directory.domain.readonly")) && !Nodes<TextBox>(main).Any(t => t.Text.Contains("BEGIN PRIVATE KEY")));
        main.Width = 1050; main.Height = 680; await Task.Delay(180); Capture(main, Path.Combine(output, "workspace-connection-min.png"));
        Check("Connection save is visible at minimum window size", Named<Button>(main, "保存企业连接").IsVisible);
        main.Width = 1440; main.Height = 900; Click(main, "关闭企业侧栏");
        Click(main, "环境工作台"); await Task.Delay(350);
        var after = await BrowserState(client);
        var retained = await Evaluate(port, "({marker:window.__enterpriseMarker,draft:document.querySelector('#entry').value})");
        Check("Enterprise flows preserve original Chromium process", after.GetProperty("pid").GetInt32() == browser.GetProperty("pid").GetInt32());
        Check("Enterprise navigation preserves page and unsent input", retained.GetProperty("marker").GetString() == "same-document" && retained.GetProperty("draft").GetString() == "workspace-preserved-draft");
        await WorkspaceOAuth(main, client, output, alpha);
        await WorkspaceUsability(main, client, output, alpha);
        await WorkspaceHumanNames(main, client, output, alpha);
        await WorkspaceRowActions(main, client, output, alpha, beta);
    }

    static async Task WorkspaceRowActions(MainWindow main, HttpClient client, string output, string alpha, string beta)
    {
        string Text(object row, string key) => row.GetType().GetProperty(key)!.GetValue(row)?.ToString() ?? "";
        var table = Named<DataGrid>(main, "企业员工列表");
        object Row(string email) => table.Items.Cast<object>().Single(r => Text(r, "Email") == email);
        Button Action(string email, string label) {
            table.ScrollIntoView(Row(email), table.Columns.Single(c => c.Header as string == "操作")); table.UpdateLayout();
            return Nodes<Button>(table).Single(b => b.Content as string == label && ReferenceEquals(b.DataContext, Row(email)) && b.IsVisible);
        }
        async Task Preview() { Click(main, "预览员工变更"); await Wait(() => Nodes<Button>(main).Any(b => b.Content as string is "确认执行有效行" or "确认删除有效员工")); }
        async Task Execute(string name = "确认执行有效行") {
            Nodes<CheckBox>(main).Single(c => c.Content as string == "已核对目标企业与员工，确认执行").IsChecked = true;
            Click(main, name); await Wait(() => !Named<Button>(main, "同步员工").IsEnabled);
            Check("Running row task disables row mutation actions", table.Items.Cast<object>().All(r => !(bool)r.GetType().GetProperty("CanManage")!.GetValue(r)!));
            await Wait(() => Named<Button>(main, "同步员工").IsEnabled);
        }
        string target = table.Items.Cast<object>().Select(r => Text(r, "Email")).First(e => e != "qa002@alpha.invalid" && e != "existing@alpha.invalid");
        foreach (var row in table.Items.Cast<object>()) row.GetType().GetProperty("Selected")!.SetValue(row, false);
        table.UpdateLayout();
        Check("Row actions work without selecting employees while bulk actions stay disabled", Action(target, "修改").IsEnabled && !Named<Button>(main, "批量修改资料").IsEnabled && table.Columns.Any(c => c.Header as string == "操作"));
        Row("qa002@alpha.invalid").GetType().GetProperty("Selected")!.SetValue(Row("qa002@alpha.invalid"), true);
        Action(target, "修改").RaiseEvent(new RoutedEventArgs(Button.ClickEvent)); main.UpdateLayout();
        Check("Single employee editor identifies clicked row and preserves unrelated selection", Nodes<TextBlock>(main).Any(t => t.Text.Contains(target)) && (bool)Row("qa002@alpha.invalid").GetType().GetProperty("Selected")!.GetValue(Row("qa002@alpha.invalid"))!);
        Named<TextBox>(main, "部门").Text = "Row action QA"; await Preview();
        Check("Row preview contains only clicked employee despite another checked row", Nodes<TextBlock>(main).Any(t => t.Text.StartsWith("1. " + target)) && !Nodes<TextBlock>(main).Any(t => t.Text.StartsWith("1. qa002@")) && Nodes<TextBlock>(main).Any(t => t.Text.Contains("有效 1 行")));
        await Execute();
        Check("Row edit changes only its own employee", Text(Row(target), "Department") == "Row action QA" && Text(Row("qa002@alpha.invalid"), "Department") != "Row action QA");
        string keptPassword = Text(Row("qa002@alpha.invalid"), "Password");
        Action(target, "重置密码").RaiseEvent(new RoutedEventArgs(Button.ClickEvent)); main.UpdateLayout(); Named<TextBox>(main, "新密码").Text = "fixture-row-reset";
        await Preview(); await Execute();
        Check("Row password reset leaves checked employee password unchanged", Text(Row(target), "Password") == "fixture-row-reset" && Text(Row("qa002@alpha.invalid"), "Password") == keptPassword);
        Action(target, "暂停").RaiseEvent(new RoutedEventArgs(Button.ClickEvent)); main.UpdateLayout(); await Preview(); await Execute();
        Check("Suspended row shows recovery instead of suspension", Text(Row(target), "State") == "已暂停" && Action(target, "恢复").IsEnabled && !Nodes<Button>(table).Any(b => b.Content as string == "暂停" && ReferenceEquals(b.DataContext, Row(target)) && b.IsVisible));
        Action(target, "恢复").RaiseEvent(new RoutedEventArgs(Button.ClickEvent)); main.UpdateLayout(); await Preview(); await Execute();
        Check("Row recovery restores only its own employee", Text(Row(target), "State") == "正常" && Action(target, "暂停").IsEnabled);
        Action(target, "删除").RaiseEvent(new RoutedEventArgs(Button.ClickEvent)); main.UpdateLayout(); await Preview();
        Check("Single employee deletion retains typed tenant confirmation", !Named<Button>(main, "确认删除有效员工").IsEnabled && Named<TextBox>(main, "输入企业名称确认删除").IsVisible);
        using (var companies = JsonDocument.Parse(await client.GetStringAsync("workspaces"))) Named<TextBox>(main, "输入企业名称确认删除").Text = companies.RootElement.GetProperty("workspaces").EnumerateArray().Single(w => w.GetProperty("id").GetString() == alpha).GetProperty("name").GetString()!;
        await Execute("确认删除有效员工");
        Check("Row deletion removes only the clicked employee and preserves checked row", !table.Items.Cast<object>().Any(r => Text(r, "Email") == target) && Text(Row("qa002@alpha.invalid"), "Password") == keptPassword);
        var staleButton = Action("qa002@alpha.invalid", "修改"); var picker = Named<ComboBox>(main, "企业选择"); picker.SelectedValue = beta;
        await Wait(() => table.Items.Count == 1 && Text(table.Items[0], "Email") == "existing@beta.invalid");
        staleButton.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        Check("Stale row action cannot open an editor for another enterprise", !Nodes<Button>(main).Any(b => b.Content as string == "预览员工变更"));
        picker.SelectedValue = alpha; await Wait(() => table.Items.Count > 1); table.UpdateLayout();
        Capture(main, Path.Combine(output, "workspace-row-actions.png"));
        main.Width = 1050; main.Height = 680; await Task.Delay(180); table.ScrollIntoView(table.Items[0], table.Columns.Last()); table.UpdateLayout();
        Capture(main, Path.Combine(output, "workspace-row-actions-min.png"));
        Check("Row actions remain reachable at minimum window size", Action("qa002@alpha.invalid", "修改").IsVisible);
        Check("Email remains pinned while accessing row actions in narrow windows", table.FrozenColumnCount == 2 && table.Columns[1].Header as string == "Google 邮箱");
        main.Width = 1440; main.Height = 900;
    }

    static async Task WorkspaceHumanNames(MainWindow main, HttpClient client, string output, string alpha)
    {
        string Text(object row, string key) => row.GetType().GetProperty(key)!.GetValue(row)?.ToString() ?? "";
        var table = Named<DataGrid>(main, "企业员工列表");
        int beforeCount = table.Items.Count;
        Click(main, "批量创建员工"); main.UpdateLayout();
        var mode = Named<ComboBox>(main, "员工生成方式");
        Named<TextBox>(main, "创建数量").Text = "2"; Named<TextBox>(main, "初始密码").Text = "fixture-human-password";
        mode.SelectedIndex = 2; main.UpdateLayout();
        Check("CSV mode shows only import fields and hides unused generated fields", Named<TextBox>(main, "CSV：email,firstName,lastName,password,orgUnitPath").IsVisible && !Named<TextBox>(main, "初始密码").IsVisible && !Named<TextBox>(main, "创建数量").IsVisible);
        mode.SelectedIndex = 0; main.UpdateLayout();
        Check("Switching generation modes preserves the generated form", Named<TextBox>(main, "初始密码").Text == "fixture-human-password" && Named<TextBox>(main, "创建数量").Text == "2");
        main.Width = 1050; main.Height = 680; await Task.Delay(180);
        Capture(main, Path.Combine(output, "workspace-human-create-min.png"));
        Check("Human generation preview action fits the minimum window", Named<Button>(main, "预览待创建员工").IsVisible);
        main.Width = 1440; main.Height = 900; await Task.Delay(150);
        Click(main, "预览待创建员工"); await Wait(() => Nodes<Button>(main).Any(b => b.Content as string == "确认执行有效行"));
        var names = Nodes<TextBlock>(main).Select(t => System.Text.RegularExpressions.Regex.Match(t.Text, @"^\d+\. ([a-z]+\d{4}@alpha\.invalid)\n([A-Za-z]+) ([A-Za-z]+)\n可执行$")).Where(m => m.Success).ToArray();
        Check("Human preview displays matching names and lowercase mailboxes", names.Length == 2 && names.All(m => m.Groups[1].Value.StartsWith((m.Groups[2].Value + m.Groups[3].Value).ToLowerInvariant())) && names.Select(m => m.Groups[1].Value).Distinct().Count() == 2);
        Check("Human preview never exposes the initial password", !Nodes<TextBlock>(main).Any(t => t.Text.Contains("fixture-human-password")));
        Capture(main, Path.Combine(output, "workspace-human-preview.png"));
        Click(main, "返回修改"); main.UpdateLayout();
        Check("Returning from human preview retains mode count and password", Named<ComboBox>(main, "员工生成方式").SelectedIndex == 0 && Named<TextBox>(main, "创建数量").Text == "2" && Named<TextBox>(main, "初始密码").Text == "fixture-human-password");
        Click(main, "预览待创建员工"); await Wait(() => Nodes<Button>(main).Any(b => b.Content as string == "确认执行有效行"));
        var expected = Nodes<TextBlock>(main).Select(t => System.Text.RegularExpressions.Regex.Match(t.Text, @"^\d+\. ([a-z]+\d{4}@alpha\.invalid)\n([A-Za-z]+) ([A-Za-z]+)\n可执行$")).Where(m => m.Success).Select(m => m.Groups[1].Value).ToArray();
        Nodes<CheckBox>(main).Single(c => c.Content as string == "已核对目标企业与员工，确认执行").IsChecked = true;
        Click(main, "确认执行有效行"); await Wait(() => !Named<Button>(main, "同步员工").IsEnabled); await Wait(() => Named<Button>(main, "同步员工").IsEnabled);
        Check("Human batch creates precisely the previewed mailboxes", expected.Length == 2 && table.Items.Count == beforeCount + 2 && expected.All(email => table.Items.Cast<object>().Any(row => Text(row, "Email") == email && Text(row, "Password") == "fixture-human-password")));
        using var document = JsonDocument.Parse(await client.GetStringAsync($"workspaces/{alpha}/users"));
        var created = document.RootElement.GetProperty("users").EnumerateArray().Where(u => expected.Contains(u.GetProperty("email").GetString())).ToArray();
        Check("Created employee names match their mailbox identities", created.Length == 2 && created.All(u => u.GetProperty("email").GetString()!.StartsWith((u.GetProperty("firstName").GetString() + u.GetProperty("lastName").GetString()).ToLowerInvariant())));
        Capture(main, Path.Combine(output, "workspace-human-result.png"));
    }

    static async Task WorkspaceUsability(MainWindow main, HttpClient client, string output, string alpha)
    {
        string Text(object row, string key) => row.GetType().GetProperty(key)!.GetValue(row)?.ToString() ?? "";
        var table = Named<DataGrid>(main, "企业员工列表");
        var allRows = table.Items.Cast<object>().ToArray();
        foreach (var row in allRows) row.GetType().GetProperty("Selected")!.SetValue(row, Text(row, "Email") == "qa001@alpha.invalid");
        table.Items.Refresh();
        Click(main, "批量修改资料"); main.UpdateLayout();
        bool prefilled = Named<TextBox>(main, "名字").Text == "qa001" && Named<TextBox>(main, "部门").Text == "Engineering";
        Named<TextBox>(main, "部门").Text = "Usability QA";
        bool autoCheck = Nodes<CheckBox>(main).Single(c => c.Content as string == "修改部门").IsChecked == true;
        Capture(main, Path.Combine(output, "workspace-edit-usability.png"));
        Click(main, "关闭企业侧栏");
        Named<TextBox>(main, "搜索企业员工").Text = "qa002"; main.UpdateLayout();
        bool hiddenCleared = allRows.All(r => !(bool)r.GetType().GetProperty("Selected")!.GetValue(r)!);
        bool mutationDisabled = !Named<Button>(main, "批量重置密码").IsEnabled;
        Named<TextBox>(main, "搜索企业员工").Text = "";
        bool inlinePasswords = !Nodes<Button>(main).Any(b => b.Content as string == "查看账号与密码") && Text(table.Items.Cast<object>().Single(r => Text(r, "Email") == "qa001@alpha.invalid"), "Password") == "fixture-reset-password";
        var observed = new { prefilled, autoCheck, hiddenCleared, mutationDisabled, inlinePasswords };
        File.WriteAllText(Path.Combine(output, "workspace-usability-observed.json"), JsonSerializer.Serialize(observed, new JsonSerializerOptions { WriteIndented = true }));
        Check("Single employee editor prefills existing profile", prefilled);
        Check("Editing a profile field automatically selects that change", autoCheck);
        Check("Filtering removes invisible employee selections", hiddenCleared);
        Check("Employee mutation buttons require a checked employee", mutationDisabled);
        Check("Passwords remain inline without a separate account-list shortcut", inlinePasswords);
        table.UpdateLayout();
        var checkbox = Nodes<CheckBox>(table).First(c => c.DataContext != null && Text(c.DataContext, "Email") == "qa001@alpha.invalid");
        checkbox.IsChecked = true;
        Check("Employee checkbox immediately enables maintenance and updates selection count", Named<Button>(main, "批量重置密码").IsEnabled && Nodes<TextBlock>(main).Any(t => t.Text.Contains("已勾选 1 人")));
        Click(main, "批量恢复"); main.UpdateLayout(); Click(main, "预览员工变更"); await Wait(() => Nodes<Button>(main).Any(b => b.Content as string == "确认执行有效行"));
        Nodes<CheckBox>(main).Single(c => c.Content as string == "已核对目标企业与员工，确认执行").IsChecked = true;
        Click(main, "确认执行有效行"); await Wait(() => !Named<Button>(main, "同步员工").IsEnabled); await Wait(() => Named<Button>(main, "同步员工").IsEnabled);
        Check("Resume updates employee state in table", Text(table.Items.Cast<object>().Single(r => Text(r, "Email") == "qa001@alpha.invalid"), "State") == "正常");
        Click(main, "批量创建员工"); main.UpdateLayout();
        Named<ComboBox>(main, "员工生成方式").SelectedIndex = 2;
        var csv = Named<TextBox>(main, "CSV：email,firstName,lastName,password,orgUnitPath");
        csv.Text = "email,firstName,lastName,password,orgUnitPath\nwrong@foreign.invalid,Wrong,User,fixture-csv-pass,/";
        Click(main, "预览待创建员工"); await Wait(() => Nodes<Button>(main).Any(b => b.Content as string == "确认执行有效行"));
        Nodes<CheckBox>(main).Single(c => c.Content as string == "已核对目标企业与员工，确认执行").IsChecked = true;
        Check("Invalid CSV rows cannot execute even after confirmation", !Named<Button>(main, "确认执行有效行").IsEnabled && Nodes<TextBlock>(main).Any(t => t.Text.Contains("邮箱不属于")));
        Click(main, "返回修改"); main.UpdateLayout();
        csv.Text = "email,firstName,lastName,password,orgUnitPath\ncsvqa@alpha.invalid,CSV,User,fixture-csv-pass,/";
        Click(main, "预览待创建员工"); await Wait(() => Nodes<Button>(main).Any(b => b.Content as string == "确认执行有效行"));
        Nodes<CheckBox>(main).Single(c => c.Content as string == "已核对目标企业与员工，确认执行").IsChecked = true;
        Click(main, "确认执行有效行"); await Wait(() => !Named<Button>(main, "同步员工").IsEnabled); await Wait(() => Named<Button>(main, "同步员工").IsEnabled);
        Check("Corrected CSV creates only the intended employee", table.Items.Count == 4 && table.Items.Cast<object>().Any(r => Text(r, "Email") == "csvqa@alpha.invalid"));
        Click(main, "批量创建员工"); main.UpdateLayout();
        Named<ComboBox>(main, "员工生成方式").SelectedIndex = 1;
        Named<TextBox>(main, "账号前缀").Text = "cancelqa"; Named<TextBox>(main, "创建数量").Text = "6"; Named<TextBox>(main, "初始密码").Text = "fixture-cancel-pass";
        Click(main, "预览待创建员工"); await Wait(() => Nodes<Button>(main).Any(b => b.Content as string == "确认执行有效行"));
        Nodes<CheckBox>(main).Single(c => c.Content as string == "已核对目标企业与员工，确认执行").IsChecked = true;
        Click(main, "确认执行有效行"); await Wait(() => Named<Button>(main, "取消企业任务").IsEnabled);
        Click(main, "取消企业任务"); await Wait(() => Named<Button>(main, "同步员工").IsEnabled);
        using var companies = JsonDocument.Parse(await client.GetStringAsync("workspaces"));
        var last = companies.RootElement.GetProperty("workspaces").EnumerateArray().Single(w => w.GetProperty("id").GetString() == alpha).GetProperty("lastJob");
        Check("Cancelling a create batch stops remaining users and restores controls", last.GetProperty("state").GetString() == "cancelled" && last.GetProperty("completed").GetInt32() < 6 && !Named<ProgressBar>(main, "企业任务进度").IsVisible);
        Capture(main, Path.Combine(output, "workspace-usability-final.png"));
        foreach (var row in table.Items.Cast<object>()) row.GetType().GetProperty("Selected")!.SetValue(row, Text(row, "Email") is "qa001@alpha.invalid" or "csvqa@alpha.invalid");
        Click(main, "批量删除"); main.UpdateLayout();
        Check("Delete explains cloud account loss and local preservation inline", main.OwnedWindows.Count == 0 && Nodes<TextBlock>(main).Any(t => t.Text.Contains("本功能不迁移数据")));
        Click(main, "预览员工变更"); await Wait(() => Nodes<Button>(main).Any(b => b.Content as string == "确认删除有效员工"));
        var deleteButton = Named<Button>(main, "确认删除有效员工");
        Nodes<CheckBox>(main).Single(c => c.Content as string == "已核对目标企业与员工，确认执行").IsChecked = true;
        Check("Delete remains disabled until the enterprise name is typed", !deleteButton.IsEnabled);
        var deleteName = Named<TextBox>(main, "输入企业名称确认删除"); deleteName.Text = "Wrong enterprise";
        Check("Wrong enterprise name cannot confirm deletion", !deleteButton.IsEnabled);
        deleteName.Text = companies.RootElement.GetProperty("workspaces").EnumerateArray().Single(w => w.GetProperty("id").GetString() == alpha).GetProperty("name").GetString()!;
        Check("Exact enterprise name enables deliberate deletion", deleteButton.IsEnabled);
        Capture(main, Path.Combine(output, "workspace-delete-confirmation.png"));
        Click(main, "确认删除有效员工"); await Wait(() => !Named<Button>(main, "同步员工").IsEnabled);
        Capture(main, Path.Combine(output, "workspace-delete-progress.png"));
        await Wait(() => Named<Button>(main, "同步员工").IsEnabled);
        Check("Deleted employees leave the active directory and preserve remaining passwords", !table.Items.Cast<object>().Any(r => Text(r, "Email") is "qa001@alpha.invalid" or "csvqa@alpha.invalid") && Text(table.Items.Cast<object>().Single(r => Text(r, "Email") == "qa002@alpha.invalid"), "Password") == "fixture-create-password");
        using var deletedAccounts = JsonDocument.Parse(await client.GetStringAsync("login-accounts"));
        var retainedAccounts = deletedAccounts.RootElement.GetProperty("accounts").EnumerateArray().Where(a => a.GetProperty("email").GetString() is "qa001@alpha.invalid" or "csvqa@alpha.invalid").ToArray();
        Check("Cloud deletion retains local passwords and marks accounts unavailable", retainedAccounts.Length == 2 && retainedAccounts.All(a => a.GetProperty("workspaceMissing").GetBoolean() && a.GetProperty("password").GetString()!.Length > 0));
        Check("Delete finishes by dismissing the overlay", !Named<Grid>(main, "企业任务遮罩").IsVisible);
        Capture(main, Path.Combine(output, "workspace-delete-result.png"));
    }

    static async Task WorkspaceOAuth(MainWindow main, HttpClient client, string output, string alpha)
    {
        bool Done() => Named<Button>(main, "同步员工").IsEnabled;
        async Task<JsonElement> Post(string path, object value) {
            using var response = await client.PostAsync(path, new StringContent(JsonSerializer.Serialize(value), System.Text.Encoding.UTF8, "application/json"));
            response.EnsureSuccessStatusCode(); using var doc = JsonDocument.Parse(await response.Content.ReadAsStringAsync()); return doc.RootElement.Clone();
        }
        Click(main, "企业目录"); await Task.Delay(150); Click(main, "＋ 添加企业"); main.UpdateLayout();
        var method = Named<ComboBox>(main, "企业授权方式");
        Check("New enterprise defaults to administrator OAuth", method.SelectedValue as string == "oauth" && Named<Button>(main, "导入 OAuth 客户端 JSON").IsVisible);
        Check("OAuth setup stays inline and explains test-user expiry", main.OwnedWindows.Count == 0 && Nodes<TextBox>(main).Any(t => t.Text.Contains("7 天")));
        main.Width = 1050; main.Height = 680; await Task.Delay(120); Capture(main, Path.Combine(output, "workspace-oauth-editor-min.png"));
        Check("OAuth save stays accessible at minimum size", Named<Button>(main, "保存企业连接").IsVisible);
        method.SelectedValue = "service_account";
        Check("Advanced service-account mode retains delegation setup", Named<Button>(main, "导入服务账号 JSON").IsVisible && Nodes<TextBox>(main).Any(t => t.Text.Contains("全网域授权")));
        Click(main, "关闭企业侧栏"); main.Width = 1440; main.Height = 900;
        var clientJson = JsonSerializer.Serialize(new { installed = new { client_id = "1234567890-fixture.apps.googleusercontent.com", client_secret = "fixture-client-secret" } });
        await Post("workspaces", new { id = alpha, name = "Alpha 企业 · OAuth 验证", adminEmail = "admin@alpha.invalid", authType = "oauth", oauthClientJson = clientJson });
        await (Task)Method("LoadEnterprises").Invoke(main, null)!;
        string? url = null;
        typeof(MainWindow).GetField("openEnterpriseAuthorization", BindingFlags.NonPublic | BindingFlags.Instance)!.SetValue(main, (Action<string>)(value => url = value));
        Click(main, "Google 管理员授权"); await Wait(() => url != null && Nodes<Button>(main).Any(b => b.Content as string == "取消管理员授权"));
        Check("OAuth opens only the official Google URL", new Uri(url!).Host == "accounts.google.com");
        Check("OAuth wait shows progress and prevents employee mutations", Named<ProgressBar>(main, "企业任务进度").IsVisible && !Named<Button>(main, "批量创建员工").IsEnabled);
        Capture(main, Path.Combine(output, "workspace-oauth-waiting.png"));
        Click(main, "运行诊断"); await Task.Delay(120); Click(main, "企业目录");
        await Wait(() => Named<ProgressBar>(main, "企业任务进度").IsVisible);
        Check("OAuth stays active across page navigation", !Named<Button>(main, "同步员工").IsEnabled);
        Click(main, "取消管理员授权"); await Wait(() => Named<Button>(main, "同步员工").IsEnabled);
        Check("OAuth cancellation restores usable controls", !Named<ProgressBar>(main, "企业任务进度").IsVisible && Named<TextBlock>(main, "企业操作结果").Text.Contains("取消"));
        url = null; Click(main, "Google 管理员授权"); await Wait(() => url != null);
        var parameters = new Uri(url!).Query.TrimStart('?').Split('&').Select(p => p.Split('=', 2)).ToDictionary(p => Uri.UnescapeDataString(p[0]), p => Uri.UnescapeDataString(p[1]));
        using (var callback = new HttpClient()) {
            await callback.GetStringAsync(parameters["redirect_uri"] + "?state=" + Uri.EscapeDataString(parameters["state"]) + "&code=offline-authorization-code");
        }
        await Wait(() => Named<Button>(main, "同步员工").IsEnabled && Named<TextBlock>(main, "企业操作结果").Text.Contains("授权成功"));
        Check("Verified OAuth callback connects the correct enterprise", Nodes<TextBlock>(main).Any(t => t.Text.Contains("已连接") && t.Text.Contains("admin@alpha.invalid")));
        Click(main, "连接设置"); await Wait(() => Nodes<Button>(main).Any(b => b.Content as string == "保存企业连接")); main.UpdateLayout();
        Check("OAuth settings never render client secret or tokens", Nodes<TextBox>(main).All(t => !t.Text.Contains("fixture-client-secret") && !t.Text.Contains("offline-refresh")));
        Capture(main, Path.Combine(output, "workspace-oauth-connected.png"));
        var route = await Post("proxies", new { name = "Offline Google route", proxy = "http://127.0.0.1:12345" });
        var routed = await Post("workspaces", new { id = alpha, name = "Alpha 企业 · OAuth 验证", adminEmail = "admin@alpha.invalid", authType = "oauth", proxyId = route.GetProperty("id").GetString() });
        await (Task)Method("LoadEnterprises").Invoke(main, null)!;
        Check("Stale connection editor cannot overwrite a changed route", !Named<Button>(main, "保存企业连接").IsEnabled && Named<Button>(main, "重新载入最新连接设置").IsVisible && routed.GetProperty("authorized").GetBoolean());
        var oldProxy = Named<ComboBox>(main, "企业管理代理"); Click(main, "重新载入最新连接设置");
        await Wait(() => Named<ComboBox>(main, "企业管理代理") != oldProxy);
        Check("Reloaded editor and header show actual saved management route", Named<ComboBox>(main, "企业管理代理").SelectedValue as string == route.GetProperty("id").GetString() && Nodes<TextBlock>(main).Any(t => t.Text.Contains("网络：Offline Google route")));
        Click(main, "检测企业连接"); await Wait(() => !Done()); await Wait(Done);
        Check("Changed route can reconnect using existing OAuth grant", Nodes<TextBlock>(main).Any(t => t.Text.Contains("已连接") && t.Text.Contains("网络：Offline Google route")));
        Click(main, "关闭企业侧栏"); Click(main, "同步员工");
        await Wait(() => !Named<Button>(main, "同步员工").IsEnabled); await Wait(() => Named<Button>(main, "同步员工").IsEnabled);
        Check("Existing directory sync works after OAuth authorization", Named<DataGrid>(main, "企业员工列表").Items.Count == 3);
    }
}
