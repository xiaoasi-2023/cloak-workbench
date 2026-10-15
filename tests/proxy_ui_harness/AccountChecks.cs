using System.Net.Http;
using System.IO;
using System.Reflection;
using System.Text;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using CloakWorkbench;

partial class Program
{
    static async Task Accounts(MainWindow main, HttpClient client, string output)
    {
        async Task<JsonElement> Post(string path, object payload) {
            using var response = await client.PostAsync(path, new StringContent(JsonSerializer.Serialize(payload), Encoding.UTF8, "application/json")); response.EnsureSuccessStatusCode();
            using var doc = JsonDocument.Parse(await response.Content.ReadAsStringAsync()); return doc.RootElement.Clone();
        }
        var initial = await BrowserState(client);
        await Post("profiles", new { id = "account-search-fixture", name = "搜索账号", startUrl = initial.GetProperty("startUrl").GetString(), accountEmail = "unique@example.invalid", accountGroup = "项目甲", accountNotes = "回归备注" });
        await (Task)Method("Refresh").Invoke(main, null)!;
        Check("Closed environments are managed outside the browser running list", !Nodes<TextBlock>(main).Any(t => t.Text == "搜索账号"));
        await (Task)Method("Launch").Invoke(main, new object[] { "account-search-fixture" })!;
        await Wait(() => Nodes<TextBlock>(main).Any(t => t.Text == "搜索账号"));
        Check("Running rows distinguish manual login from saved credentials", Nodes<TextBlock>(main).Any(t => t.Text == "手动登录环境") && Nodes<TextBlock>(main).Any(t => t.Text.Contains("本次待检测")));
        var search = Named<TextBox>(main, "搜索运行环境"); search.Text = "UNIQUE@"; await Task.Delay(100);
        Check("Case-insensitive email search filters accounts", Nodes<TextBlock>(main).Any(t => t.Text == "搜索账号") && !Nodes<TextBlock>(main).Any(t => t.Text == "隔离页面切换验证"));
        search.Text = "回归备注";
        main.UpdateLayout();
        Check("Account search includes notes", Nodes<TextBlock>(main).Any(t => t.Text == "搜索账号"));
        search.Text = "missing-account";
        main.UpdateLayout();
        Check("Empty search has useful feedback", Nodes<TextBlock>(main).Any(t => t.Text.StartsWith("没有匹配的运行环境")));
        search.Text = "";
        await (Task)Method("CloseProfile").Invoke(main, new object[] { "account-search-fixture" })!;
        main.UpdateLayout();
        Check("Closing an environment removes it from the running list", !Nodes<TextBlock>(main).Any(t => t.Text == "搜索账号"));
        var add = (Window)Method("CreateAccountDialog").Invoke(main, new object?[] { null })!; add.Show(); await Task.Delay(100);
        Check("Simple account form exposes proxy inventory", Named<ComboBox>(add, "账号代理选择").Items.Count == 5);
        Named<TextBox>(add, "环境名称").Text = ""; Click(add, "创建浏览器"); await Task.Delay(120);
        Check("Invalid account stays open with inline error", add.IsVisible && Nodes<TextBlock>(add).Any(t => t.Text.Contains("请填写环境名称")));
        Named<TextBox>(add, "环境名称").Text = "新增账号回归";
        Named<TextBox>(add, "旧版标识（仅备注，可选）").Text = "new@example.invalid";
        Named<TextBox>(add, "分组（可选）").Text = "工作账号";
        Named<TextBox>(add, "备注（可选）").Text = "中文备注\n独立会话";
        Named<ComboBox>(add, "账号代理选择").SelectedValue = "ui-residential";
        await Wait(() => !Named<Button>(add, "创建浏览器").IsEnabled);
        Check("Adding account shows pending geography feedback", Named<TextBlock>(add, "账号出口地区").Text.Contains("正在检测"));
        await Wait(() => Named<Button>(add, "创建浏览器").IsEnabled);
        Check("Adding account uses actual proxy country and time zone", Named<TextBlock>(add, "账号出口地区").Text.Contains("美国") && Named<TextBlock>(add, "账号出口地区").Text.Contains("America/Chicago"));
        Capture(add, Path.Combine(output, "account-add.png")); Click(add, "创建浏览器"); await Wait(() => !add.IsVisible);
        using (var saved = JsonDocument.Parse(await client.GetStringAsync("profiles"))) {
            var account = saved.RootElement.GetProperty("profiles").EnumerateArray().Single(p => p.GetProperty("name").GetString() == "新增账号回归");
            Check("Account creation persists metadata, proxy and automatic geography", account.GetProperty("accountGroup").GetString() == "工作账号" && account.GetProperty("accountNotes").GetString() == "中文备注\n独立会话" && account.GetProperty("proxyId").GetString() == "ui-residential" && account.GetProperty("timezone").GetString() == "America/Chicago");
        }
        await (Task)Method("Launch").Invoke(main, new object[] { "ui-browser" })!; await Task.Delay(500);
        var before = await BrowserState(client); var port = before.GetProperty("cdpPort").GetInt32();
        await Evaluate(port, "(()=>{window.__accountToken=crypto.randomUUID();document.querySelector('#entry').value='account editing draft';return true})()");
        var token = await Evaluate(port, "window.__accountToken");
        var edit = (Window)Method("CreateAccountDialog").Invoke(main, new object?[] { "ui-browser" })!; edit.Show(); await Task.Delay(100);
        Named<TextBox>(edit, "环境名称").Text = "运行中账号"; Named<TextBox>(edit, "分组（可选）").Text = "工作账号"; Click(edit, "保存环境资料"); await Wait(() => !edit.IsVisible);
        Check("Editing running account preserves same browser process", (await BrowserState(client)).GetProperty("pid").GetInt32() == before.GetProperty("pid").GetInt32());
        Check("Editing running account preserves native document and draft", (await Evaluate(port, "window.__accountToken")).ToString() == token.ToString() && (await Evaluate(port, "document.querySelector('#entry').value")).GetString() == "account editing draft");
        await (Task)Method("CheckAccount").Invoke(main, new object[] { "ui-browser" })!;
        Check("Real non-Dola page never fabricates logged-in account", (await BrowserState(client)).GetProperty("accountCheck").GetProperty("reason").GetString() == "not_dola_page");
        await Post("profiles", new { id = "account-two", name = "第二个独立会话", startUrl = before.GetProperty("url").GetString() });
        await (Task)Method("Refresh").Invoke(main, null)!; await (Task)Method("Launch").Invoke(main, new object[] { "account-two" })!;
        Method("Select").Invoke(main, new object[] { "ui-browser" }); await Task.Delay(200);
        Check("Switching two account sessions preserves document identity", (await Evaluate(port, "window.__accountToken")).ToString() == token.ToString());
        main.Width = 1050; main.Height = 680; await Task.Delay(100); Capture(main, Path.Combine(output, "accounts-min.png"));
        Check("Minimum window keeps environment search and browser address usable", Named<TextBox>(main, "搜索运行环境").ActualWidth > 180 && Named<TextBox>(main, "AddressBar").ActualWidth > 200);
        main.Width = 1440; main.Height = 900; await Task.Delay(100); Capture(main, Path.Combine(output, "accounts-desktop.png"));
        await (Task)Method("CloseProfile").Invoke(main, new object[] { "account-two" })!;
        Check("Closing another account preserves selected session", (await BrowserState(client)).GetProperty("pid").GetInt32() == before.GetProperty("pid").GetInt32());
    }
}
