using System.IO;
using System.Net.Http;
using System.Reflection;
using System.Text;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using CloakWorkbench;

partial class Program
{
    static async Task AccountCenter(MainWindow main, HttpClient client, string output)
    {
        async Task<JsonElement> Post(string path, object payload) {
            using var response = await client.PostAsync(path, new StringContent(JsonSerializer.Serialize(payload), Encoding.UTF8, "application/json"));
            response.EnsureSuccessStatusCode(); using var doc = JsonDocument.Parse(await response.Content.ReadAsStringAsync()); return doc.RootElement.Clone();
        }
        async Task<JsonElement[]> Inventory() {
            using var doc = JsonDocument.Parse(await client.GetStringAsync("profiles")); return doc.RootElement.GetProperty("profiles").EnumerateArray().Select(p => p.Clone()).ToArray();
        }
        bool Busy() => (bool)typeof(MainWindow).GetField("centerBusy", BindingFlags.NonPublic | BindingFlags.Instance)!.GetValue(main)!;
        void Choose(DataGrid table, string id, bool chosen) {
            var row = table.Items.Cast<object>().Single(r => (string)r.GetType().GetProperty("Id")!.GetValue(r)! == id);
            row.GetType().GetProperty("Chosen")!.SetValue(row, chosen);
        }
        await Post("profiles", new { id = "center-second", name = "批量账号二", accountEmail = "two@example.invalid", accountGroup = "项目乙", startUrl = new Uri(client.BaseAddress!, "probe").ToString() });
        var account = await Post("login-accounts", new { email = "bound-account@example.invalid", password = "isolated-binding-secret" });
        await Post("profiles/center-second/login-account", new { accountId = account.GetProperty("id").GetString() });
        await (Task)Method("LoadProxyCatalog").Invoke(main, null)!;
        await (Task)Method("Launch").Invoke(main, new object[] { "ui-browser" })!;
        var before = await BrowserState(client); var port = before.GetProperty("cdpPort").GetInt32();
        await Evaluate(port, "(()=>{window.__centerToken=crypto.randomUUID();document.querySelector('#entry').value='batch page preservation';return true})()");
        var token = (await Evaluate(port, "window.__centerToken")).GetString();
        var hosts = (Dictionary<string, NativeBrowserHost>)typeof(MainWindow).GetField("hosts", BindingFlags.NonPublic | BindingFlags.Instance)!.GetValue(main)!;
        var native = hosts["ui-browser"].AttachedHandle;
        Method("ShowAccountCenter").Invoke(main, null); await Task.Delay(150);
        var table = Named<DataGrid>(main, "环境管理列表");
        Check("Account center is in main window and hides native browser", !IsWindowVisible(native) && main.OwnedWindows.Count == 0 && table.Items.Count == 2);
        Check("Environment list has a dedicated frozen bound-account column", table.Columns.Any(c => Equals(c.Header, "关联 Google 账号")) && table.FrozenColumnCount == 3);
        var accountRow = table.Items.Cast<object>().Single(r => (string)r.GetType().GetProperty("Id")!.GetValue(r)! == "center-second");
        Check("Environment shows actual binding instead of metadata email or website identity", (string)accountRow.GetType().GetProperty("Email")!.GetValue(accountRow)! == "bound-account@example.invalid");
        Named<TextBox>(main, "环境管理搜索").Text = "bound-account@";
        Check("Environment search finds associated Google account", table.Items.Count == 1);
        Named<TextBox>(main, "环境管理搜索").Clear();
        Choose(table, "ui-browser", true); Choose(table, "center-second", true);
        Check("Account center selection has real count", Named<TextBlock>(main, "环境选择统计").Text.Contains("已选 2"));
        Named<TextBox>(main, "环境管理搜索").Text = "two@example";
        Check("Account center filters while preserving hidden selections", table.Items.Count == 1 && Named<TextBlock>(main, "环境选择统计").Text.Contains("已选 2"));
        Named<TextBox>(main, "环境管理搜索").Text = "";
        await (Task)Method("Refresh").Invoke(main, null)!;
        Check("Polling preserves account checkbox selection", table.Items.Cast<object>().All(r => (bool)r.GetType().GetProperty("Chosen")!.GetValue(r)!));
        Click(main, "代理管理"); await Task.Delay(80); Method("ShowAccountCenter").Invoke(main, null);
        Check("Management navigation restores same center controls", ReferenceEquals(table, Named<DataGrid>(main, "环境管理列表")) && main.OwnedWindows.Count == 0);
        var action = Named<ComboBox>(main, "环境批量操作"); action.SelectedIndex = 4;
        await (Task)Method("ExecuteAccountAction").Invoke(main, null)!;
        main.UpdateLayout();
        Named<TextBox>(main, "目标分组").Text = "批量分组"; Click(main, "应用到所选环境");
        await Wait(() => !Busy() && Nodes<TextBlock>(main).Any(t => t.Text.Contains("成功 2，失败 0")));
        Check("Batch grouping applies to opened and closed accounts", (await Inventory()).All(p => p.GetProperty("accountGroup").GetString() == "批量分组"));
        action.SelectedIndex = 5; await (Task)Method("ExecuteAccountAction").Invoke(main, null)!;
        main.UpdateLayout();
        Named<ComboBox>(main, "目标代理").SelectedValue = "ui-standard"; Click(main, "应用到所选环境");
        await Wait(() => !Busy() && Nodes<TextBlock>(main).Any(t => t.Text.Contains("成功 1，失败 1")));
        var bound = await Inventory();
        Check("Batch proxy assignment reports partial failures and protects running account", bound.Single(p => p.GetProperty("id").GetString() == "ui-browser").GetProperty("proxyId").GetString() == "" && bound.Single(p => p.GetProperty("id").GetString() == "center-second").GetProperty("proxyId").GetString() == "ui-standard");
        Method("ShowAccountImport").Invoke(main, null);
        main.UpdateLayout();
        var mode = Named<ComboBox>(main, "环境导入方式"); mode.SelectedIndex = 1;
        var input = Named<TextBox>(main, "CSV / JSON 环境资料");
        input.Text = "name,accountEmail,accountGroup,accountNotes\n导入一,one@example.invalid,导入分组,中文备注\n重复,ONE@example.invalid,,\n无效,broken,,";
        Click(main, "预览并检查"); await Wait(() => Named<Button>(main, "确认创建可用行").IsEnabled);
        Check("Import preview reports duplicate and invalid rows without creating", (await Inventory()).Length == 2 && Named<TextBlock>(main, "环境导入预览结果").Text.Contains("可创建 1"));
        input.Text += "\n导入二,second@example.invalid,导入分组,";
        Check("Editing preview data invalidates confirmation", !Named<Button>(main, "确认创建可用行").IsEnabled);
        Click(main, "预览并检查"); await Wait(() => Named<Button>(main, "确认创建可用行").IsEnabled);
        Check("Import form remains inline", main.OwnedWindows.Count == 0);
        main.Width = 1050; main.Height = 680; await Task.Delay(100); Capture(main, Path.Combine(output, "account-center-import-min.png"));
        Click(main, "确认创建可用行"); await Wait(() => Named<TextBlock>(main, "环境导入预览结果").Text.Contains("本次已创建 2"));
        var imported = await Inventory();
        Check("Import confirms valid rows only with independent fingerprint seeds", imported.Length == 4 && imported.Select(p => p.GetProperty("seed").GetInt32()).Distinct().Count() == 4);
        Method("ShowAccountDetails").Invoke(main, new object[] { "ui-browser" });
        await Wait(() => Nodes<Button>(main).Any(b => b.Content is string s && s == "检测 Dola 登录"));
        Check("Details separate site login and identity without modal", main.OwnedWindows.Count == 0 && Nodes<TextBlock>(main).Any(t => t.Text == "身份核验"));
        foreach (var section in new[] { "账号", "代理", "指纹", "登录备份", "概览" }) { Click(main, section); main.UpdateLayout(); }
        Check("Environment details expose five inline sections", main.OwnedWindows.Count == 0 && Nodes<Button>(main).Any(b => b.Content is string s && s == "环境设置"));
        main.UpdateLayout();
        var search = Named<TextBox>(main, "环境管理搜索");
        var groupFilter = Named<ComboBox>(main, "环境分组筛选");
        var searchEnd = search.TransformToAncestor(main).Transform(new Point(search.ActualWidth, 0)).X;
        var groupStart = groupFilter.TransformToAncestor(main).Transform(new Point()).X;
        Check("Minimum window keeps usable search and separate filters", search.ActualWidth >= 200 && searchEnd < groupStart && Named<Button>(main, "执行").IsVisible);
        var reset = Named<Button>(main, "重置环境筛选");
        Check("Minimum window keeps rightmost state filter and reset inside viewport", reset.TransformToAncestor(main).Transform(new Point(reset.ActualWidth, 0)).X <= main.ActualWidth - 20);
        var tableScroll = Nodes<ScrollViewer>(table).First(s => s.Name == "DG_ScrollViewer");
        Check("Minimum window scrolls columns instead of squeezing status text", tableScroll.ExtentWidth > tableScroll.ViewportWidth && table.ActualHeight >= 250);
        Capture(main, Path.Combine(output, "account-center-details-min.png"));
        main.Width = 1440; main.Height = 900; await Task.Delay(100); Capture(main, Path.Combine(output, "account-center-details-desktop.png"));
        action.SelectedIndex = 2; await (Task)Method("ExecuteAccountAction").Invoke(main, null)!;
        await Wait(() => !Busy());
        Check("Batch login check never opens closed account", hosts.Count == 1 && (await BrowserState(client)).GetProperty("accountCheck").GetProperty("reason").GetString() == "not_dola_page");
        var feedback = (TextBlock)typeof(MainWindow).GetField("centerFeedback", BindingFlags.NonPublic | BindingFlags.Instance)!.GetValue(main)!;
        Check("Completed results are compact and can be expanded", !feedback.IsVisible && Named<Button>(main, "展开账号结果").IsVisible);
        Click(main, "展开账号结果"); main.UpdateLayout();
        Check("Result expansion exposes actual per-account outcomes", feedback.IsVisible && feedback.Text.Contains("批量账号二"));
        Click(main, "展开账号结果"); main.UpdateLayout();
        Check("Result collapse restores space for account list", !feedback.IsVisible);
        Click(main, "关闭环境侧栏"); main.Width = 1440; main.Height = 900; await Task.Delay(100);
        Capture(main, Path.Combine(output, "account-center-desktop.png"));
        Click(main, "环境工作台"); await Task.Delay(400);
        Check("Account center preserves original browser HWND and PID", hosts["ui-browser"].AttachedHandle == native && IsWindowVisible(native) && (await BrowserState(client)).GetProperty("pid").GetInt32() == before.GetProperty("pid").GetInt32());
        Check("Account operations preserve document identity and draft", (await Evaluate(port, "window.__centerToken")).GetString() == token && (await Evaluate(port, "document.querySelector('#entry').value")).GetString() == "batch page preservation");
        Method("ShowAccountCenter").Invoke(main, null);
        await Post("accounts/batch", new { ids = new[] { "center-second" }, action = "proxy", proxyId = "" });
        await (Task)Method("Refresh").Invoke(main, null)!;
        Choose(table, "ui-browser", false); Choose(table, "center-second", true);
        action.SelectedIndex = 0; await (Task)Method("ExecuteAccountAction").Invoke(main, null)!;
        Check("Batch launch attaches hidden native host without leaving management page", hosts.ContainsKey("center-second") && hosts["center-second"].AttachedHandle != IntPtr.Zero && !IsWindowVisible(hosts["center-second"].AttachedHandle) && table.IsVisible && !IsWindowVisible(native));
        Method("Select").Invoke(main, new object[] { "center-second" }); await Task.Delay(350);
        var secondNative = hosts["center-second"].AttachedHandle;
        Check("Batch-launched native browser becomes visible and shows local page", IsWindowVisible(secondNative) && (await Inventory()).Single(p => p.GetProperty("id").GetString() == "center-second").GetProperty("url").GetString()!.Contains("/probe"));
        Method("ShowAccountCenter").Invoke(main, null);
        action.SelectedIndex = 1; await (Task)Method("ExecuteAccountAction").Invoke(main, null)!;
        Check("Batch close selected account keeps management page and hides remaining browser", !hosts.ContainsKey("center-second") && hosts.ContainsKey("ui-browser") && table.IsVisible && !IsWindowVisible(native));
        action.SelectedIndex = 3; await (Task)Method("ExecuteAccountAction").Invoke(main, null)!;
        Check("Account network job shows cancellable progress", Busy() && Named<Button>(main, "取消剩余").IsEnabled);
        Click(main, "取消剩余");
        await Wait(() => !Busy());
        Check("Account network cancellation finishes with honest terminal status", Named<TextBlock>(main, "账号任务进度").Text.Contains("检测已取消"));
        Click(main, "环境工作台"); await Task.Delay(350);
        Check("Return from batch close restores untouched remaining browser", IsWindowVisible(native) && (await Evaluate(port, "window.__centerToken")).GetString() == token);
    }
}
