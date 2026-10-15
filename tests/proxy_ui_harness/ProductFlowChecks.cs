using System.IO;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using CloakWorkbench;

internal static partial class Program
{
    static async Task ProductFlow(MainWindow main, HttpClient client, string output)
    {
        async Task<JsonElement[]> Profiles() {
            using var doc = JsonDocument.Parse(await client.GetStringAsync("profiles"));
            return doc.RootElement.GetProperty("profiles").EnumerateArray().Select(p => p.Clone()).ToArray();
        }
        async Task OpenSettings(string? id) {
            Method("EditProfile").Invoke(main, new object?[] { id });
            await Wait(() => Nodes<DockPanel>(main).Any(p => AutomationProperties.GetName(p) == "环境设置页面" && p.IsVisible));
            await Task.Delay(100);
        }
        void Expand(string header) {
            var section = Nodes<Expander>(main).Single(e => e.Header as string == header);
            section.IsExpanded = true; main.UpdateLayout();
        }

        using var response = await client.PostAsync("login-accounts", new StringContent(JsonSerializer.Serialize(new { email = "product-flow@example.invalid", password = "isolated-flow-password", name = "流程测试账号" }), Encoding.UTF8, "application/json"));
        response.EnsureSuccessStatusCode();
        using var account = JsonDocument.Parse(await response.Content.ReadAsStringAsync());
        string aid = account.RootElement.GetProperty("id").GetString()!;
        await (Task)Method("Refresh").Invoke(main, null)!;
        Click(main, "账号列表");
        await (Task)Method("LoadLoginAccounts").Invoke(main, null)!;
        Method("ShowLoginAccountEditor").Invoke(main, new object[] { aid });
        await Wait(() => Nodes<Button>(main).Any(b => b.Content as string == "为此账号创建环境" && b.IsVisible));
        Click(main, "为此账号创建环境");
        await Wait(() => Nodes<DockPanel>(main).Any(p => AutomationProperties.GetName(p) == "环境设置页面" && p.IsVisible));
        Check("Account creates inline environment with its own credential binding selected", main.OwnedWindows.Count == 0 && Named<ComboBox>(main, "浏览器绑定账号").SelectedValue as string == aid);
        Named<TextBox>(main, "环境名称").Text = "绑定账号的独立环境";
        Named<TextBox>(main, "环境分组（可选）").Text = "工作项目";
        Named<TextBox>(main, "环境备注（可选）").Text = "未保存的环境资料";
        Expand("启动页面与窗口尺寸"); Named<TextBox>(main, "启动网址").Text = "about:blank";
        Click(main, "管理代理 / 新建线路");
        await Wait(() => Nodes<DataGrid>(main).Any(g => AutomationProperties.GetName(g) == "代理列表" && g.IsVisible));
        Click(main, "返回环境配置");
        await Wait(() => Nodes<TextBox>(main).Any(t => AutomationProperties.GetName(t) == "环境名称" && t.IsVisible));
        Check("Proxy management detour preserves environment draft and account", Named<TextBox>(main, "环境名称").Text == "绑定账号的独立环境" && Named<TextBox>(main, "环境备注（可选）").Text == "未保存的环境资料" && Named<ComboBox>(main, "浏览器绑定账号").SelectedValue as string == aid);
        Click(main, "代理管理");
        Method("CreateEnvironmentForAccount").Invoke(main, new object[] { aid });
        try { await Wait(() => Nodes<TextBox>(main).Any(t => AutomationProperties.GetName(t) == "环境名称" && t.IsVisible)); }
        catch {
            var messages = (List<string>)typeof(MainWindow).GetField("messages", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance)!.GetValue(main)!;
            File.WriteAllLines(Path.Combine(output, "settings-errors.txt"), messages.Where(m => m.Contains("环境设置"))); throw;
        }
        Check("Ordinary navigation retains unsaved environment draft", Named<TextBox>(main, "环境名称").Text == "绑定账号的独立环境");
        int before = (await Profiles()).Length;
        Click(main, "保存环境");
        await Wait(() => !Nodes<DockPanel>(main).Any(p => AutomationProperties.GetName(p) == "环境设置页面" && p.IsVisible));
        var created = (await Profiles()).Single(p => p.GetProperty("name").GetString() == "绑定账号的独立环境");
        string pid = created.GetProperty("id").GetString()!;
        Check("Save creates one closed environment without running login", (await Profiles()).Length == before + 1 && created.GetProperty("state").GetString() == "closed" && created.GetProperty("loginAccountId").GetString() == aid);
        Check("Environment stores group and notes separately from credential record", created.GetProperty("accountGroup").GetString() == "工作项目" && created.GetProperty("accountNotes").GetString() == "未保存的环境资料");
        var seed = created.GetProperty("seed").GetInt32();
        await OpenSettings(pid);
        Named<TextBox>(main, "环境名称").Text = "修改名称保持指纹";
        Click(main, "保存环境");
        await Wait(() => !Nodes<DockPanel>(main).Any(p => AutomationProperties.GetName(p) == "环境设置页面" && p.IsVisible));
        Check("Ordinary edit retains fixed fingerprint seed", (await Profiles()).Single(p => p.GetProperty("id").GetString() == pid).GetProperty("seed").GetInt32() == seed);

        await (Task)Method("Launch").Invoke(main, new object[] { "ui-browser" })!;
        var live = await BrowserState(client);
        int port = live.GetProperty("cdpPort").GetInt32(), nativePid = live.GetProperty("pid").GetInt32();
        await Evaluate(port, "(()=>{window.__productFlowIdentity=crypto.randomUUID();document.querySelector('#entry').value='draft preserved through inline settings';return true})()");
        var identity = await Evaluate(port, "window.__productFlowIdentity");
        await OpenSettings("ui-browser");
        Check("Running environment cannot edit proxy or account binding", !Named<ComboBox>(main, "环境代理选择").IsEnabled && !Named<ComboBox>(main, "浏览器绑定账号").IsEnabled);
        Named<TextBox>(main, "环境名称").Text = "运行中修改名称";
        Named<TextBox>(main, "环境备注（可选）").Text = "运行中保存备注";
        Click(main, "保存环境");
        await Wait(() => !Nodes<DockPanel>(main).Any(p => AutomationProperties.GetName(p) == "环境设置页面" && p.IsVisible));
        var after = await BrowserState(client);
        Check("Metadata save retains live browser process and fingerprint", after.GetProperty("pid").GetInt32() == nativePid && after.GetProperty("seed").GetInt32() == live.GetProperty("seed").GetInt32());
        Check("Metadata save retains website document and draft", (await Evaluate(port, "window.__productFlowIdentity")).GetString() == identity.GetString() && (await Evaluate(port, "document.querySelector('#entry').value")).GetString() == "draft preserved through inline settings");
        Click(main, "环境工作台"); await Task.Delay(150); Capture(main, Path.Combine(output, "browser-workspace.png"));
        Click(main, "代理管理"); await Task.Delay(200);
        var table = Named<DataGrid>(main, "代理列表");
        Check("Default proxy list prioritizes country and hides technical columns", table.Columns.Single(c => c.Header as string == "国家 / 城市").DisplayIndex < 4 && table.Columns.Single(c => c.Header as string == "ISP").Visibility == Visibility.Collapsed);
        Click(main, "显示详细列");
        Check("Detailed proxy information remains available on demand", table.Columns.Single(c => c.Header as string == "ISP").Visibility == Visibility.Visible);
        Click(main, "显示详细列");
        table.SelectedIndex = 0;
        Click(main, "检测详情"); await Task.Delay(100);
        Check("Proxy diagnostic details are inline without additional windows", main.OwnedWindows.Count == 0 && Named<Button>(main, "返回代理列表").IsVisible);
        Click(main, "返回代理列表"); await Task.Delay(100);
        Click(main, "新建代理"); await Task.Delay(100);
        Check("Proxy editor opens inline without additional windows", main.OwnedWindows.Count == 0 && Named<TextBox>(main, "代理名称").IsVisible);
        Named<TextBox>(main, "代理名称").Text = "页内代理流程验证";
        Named<TextBox>(main, "代理地址").Text = "http://residential-gateway.invalid:3010";
        Named<TextBox>(main, "上游代理地址").Text = "http://127.0.0.1:7897";
        Click(main, "保存代理");
        await Wait(() => Nodes<DataGrid>(main).Any(g => AutomationProperties.GetName(g) == "代理列表" && g.IsVisible && g.Items.Count == 5));
        Check("Inline proxy save returns to updated catalog", Named<DataGrid>(main, "代理列表").Items.Count == 5);
        table.SelectedIndex = 0; Click(main, "检测选中代理");
        await Wait(() => !Named<Button>(main, "检测选中代理").IsEnabled);
        await Wait(() => Named<Button>(main, "检测选中代理").IsEnabled);
        Check("Unified proxy detection accepts highlighted row when nothing is checked", Nodes<TextBlock>(main).Any(t => t.Text.StartsWith("检测完成")));
        main.Width = 1050; main.Height = 680; await Task.Delay(150); Capture(main, Path.Combine(output, "proxy-list-min.png"));
        main.Width = 1440; main.Height = 900;
        await OpenSettings(pid); await Task.Delay(150); Capture(main, Path.Combine(output, "inline-environment-settings.png"));
        Click(main, "取消");
        await (Task)Method("CloseProfile").Invoke(main, new object[] { "ui-browser" })!;
        File.WriteAllText(Path.Combine(output, "product-flow-checks.json"), JsonSerializer.Serialize(new { checks = Checks, passed = Checks.Count }, new JsonSerializerOptions { WriteIndented = true }));
    }
}
