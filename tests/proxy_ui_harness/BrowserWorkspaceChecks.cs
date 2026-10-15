using System.IO;
using System.Net.Http;
using System.Reflection;
using System.Text.Json;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Media;
using CloakWorkbench;

internal static partial class Program
{
    static async Task BrowserWorkspace(MainWindow main, HttpClient client, string output)
    {
        T Field<T>(string name) => (T)typeof(MainWindow).GetField(name, BindingFlags.NonPublic | BindingFlags.Instance)!.GetValue(main)!;
        await (Task)Method("Launch").Invoke(main, new object[] { "ui-browser" })!;
        await Wait(() => Field<StackPanel>("accountList").Children.Count > 0);
        var profile = await BrowserState(client); var port = profile.GetProperty("cdpPort").GetInt32(); var pid = profile.GetProperty("pid").GetInt32();
        await Evaluate(port, "(()=>{window.__workspaceDraft='preserved';document.querySelector('#entry').value='workspace draft';return true})()");
        var hosts = Field<Dictionary<string, NativeBrowserHost>>("hosts"); var hwnd = hosts["ui-browser"].AttachedHandle;
        Check("Runtime sidebar excludes saved closed environments", Field<StackPanel>("accountList").Children.Count == ((IEnumerable<string>)Method("RuntimeIds").Invoke(main, null)!).Count());
        main.UpdateLayout();
        Check("Running environments use compact rows", Field<StackPanel>("accountList").Children.OfType<Border>().All(row => row.ActualHeight <= 88));
        Check("Sidebar has no repeated launch or login check buttons", !Nodes<Button>(Field<StackPanel>("accountList")).Any(b => b.Content is string label && (label == "当前会话" || label == "检测登录")));
        var width = Named<TextBox>(main, "AddressBar").ActualWidth;
        Method("ToggleRunningSidebar").Invoke(main, null); main.UpdateLayout();
        Check("Running sidebar collapses without hiding active native browser", Field<ColumnDefinition>("runningSidebarColumn").Width.Value == 0 && IsWindowVisible(hwnd));
        Check("Collapsing sidebar gives width to website toolbar", Named<TextBox>(main, "AddressBar").ActualWidth > width + 150);
        Method("ToggleRunningSidebar").Invoke(main, null);
        Method("ToggleExternal").Invoke(main, null); main.UpdateLayout();
        Check("External runtime stays in sidebar and tabs", !hosts.ContainsKey("ui-browser") && Field<StackPanel>("tabs").Children.Count == ((IEnumerable<string>)Method("RuntimeIds").Invoke(main, null)!).Count() && Field<StackPanel>("tabs").Children.Count > 0);
        Check("External runtime has explicit reembed and focus choices", Nodes<Button>(main).Any(b => b.Content is string s && s == "嵌回工作台") && Nodes<Button>(main).Any(b => b.Content is string s && s == "切到独立窗口"));
        Check("Runtime count does not drop after detaching", Field<TextBlock>("counts").Text.StartsWith("1 个运行环境"));
        var localDir = Field<string>("localDir");
        using (var snapshot = JsonDocument.Parse(File.ReadAllText(Path.Combine(localDir, "ui-state.json")))) {
            Check("Restart snapshot retains external running environment", snapshot.RootElement.GetProperty("browsers").EnumerateArray().Any(p => p.GetProperty("id").GetString() == "ui-browser" && p.GetProperty("displayMode").GetString() == "external"));
        }
        Capture(main, Path.Combine(output, "browser-workspace-external.png"));
        File.WriteAllText(Path.Combine(localDir, "restart-state.json"), JsonSerializer.Serialize(new {
            selected = "ui-browser", width = 1440, height = 900,
            browsers = new[] { new { id = "ui-browser", url = "http://127.0.0.1:1/must-not-navigate-live-page", displayMode = "external" } }
        }));
        await (Task)Method("RestoreSession").Invoke(main, null)!;
        Check("Repeated resume preserves external mode and existing process", !hosts.ContainsKey("ui-browser") && (await BrowserState(client)).GetProperty("pid").GetInt32() == pid);
        Check("Repeated resume never navigates away from unsaved live page", (await Evaluate(port, "window.__workspaceDraft+':'+document.querySelector('#entry').value")).GetString() == "preserved:workspace draft");
        await (Task)Method("Launch").Invoke(main, new object[] { "ui-browser" })!;
        Check("Explicit reembed uses same native window and browser process", hosts["ui-browser"].AttachedHandle == hwnd && (await BrowserState(client)).GetProperty("pid").GetInt32() == pid);
        Check("Reembed preserves existing webpage draft", (await Evaluate(port, "window.__workspaceDraft+':'+document.querySelector('#entry').value")).GetString() == "preserved:workspace draft");
        main.Width = 1050; main.Height = 680; main.UpdateLayout();
        Capture(main, Path.Combine(output, "browser-workspace-min.png"));
        Check("Minimum width keeps browser address usable", Named<TextBox>(main, "AddressBar").ActualWidth > 200);
        main.Width = 1440; main.Height = 900; main.UpdateLayout(); Capture(main, Path.Combine(output, "browser-workspace-desktop.png"));
        using (var created = await client.PostAsync("profiles", new StringContent(JsonSerializer.Serialize(new {
            id = "workspace-next", name = "Workspace fallback", startUrl = profile.GetProperty("url").GetString()
        }), System.Text.Encoding.UTF8, "application/json"))) created.EnsureSuccessStatusCode();
        await (Task)Method("Refresh").Invoke(main, null)!;
        await (Task)Method("Launch").Invoke(main, new object[] { "workspace-next" })!;
        Method("ToggleExternal").Invoke(main, null);
        using (var closed = await client.PostAsync("profiles/workspace-next/close", new StringContent("{}", System.Text.Encoding.UTF8, "application/json"))) closed.EnsureSuccessStatusCode();
        await (Task)Method("Refresh").Invoke(main, null)!;
        await Wait(() => Field<string>("selected") == "ui-browser" && IsWindowVisible(hosts["ui-browser"].AttachedHandle));
        Check("External process exit selects and reveals the remaining embedded runtime", Field<string>("selected") == "ui-browser" && IsWindowVisible(hosts["ui-browser"].AttachedHandle));
        Check("External process exit leaves other webpage draft unchanged", (await Evaluate(port, "window.__workspaceDraft+':'+document.querySelector('#entry').value")).GetString() == "preserved:workspace draft");
        await (Task)Method("CloseProfile").Invoke(main, new object[] { "ui-browser" })!;
        Check("Closed environment leaves running-only sidebar and tabs", Field<StackPanel>("tabs").Children.Count == 0 && Field<TextBlock>("counts").Text.StartsWith("0 个运行环境"));
    }
}
