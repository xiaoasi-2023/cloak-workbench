using System.IO;
using System.Net.Http;
using System.Reflection;
using System.Windows;
using System.Windows.Controls;
using CloakWorkbench;

partial class Program
{
    static async Task Diagnostics(MainWindow main, HttpClient client, string output)
    {
        await (Task)Method("Launch").Invoke(main, new object[] { "ui-browser" })!;
        await Task.Delay(500);
        var before = await BrowserState(client); int port = before.GetProperty("cdpPort").GetInt32();
        await Evaluate(port, "(()=>{window.__diagnosticToken=crypto.randomUUID();document.querySelector('#entry').value='diagnostic navigation draft';return true})()");
        var token = await Evaluate(port, "window.__diagnosticToken");
        var hosts = (Dictionary<string, NativeBrowserHost>)typeof(MainWindow).GetField("hosts", BindingFlags.NonPublic | BindingFlags.Instance)!.GetValue(main)!;
        var native = hosts["ui-browser"].AttachedHandle;
        Click(main, "运行诊断"); main.UpdateLayout();
        var log = Named<TextBox>(main, "运行诊断日志");
        Check("Diagnostics stays inside main window without modal", main.OwnedWindows.Count == 0 && log.IsVisible);
        Check("Diagnostics hides native browser", !IsWindowVisible(native));
        Check("Diagnostics retains previous startup records", log.IsReadOnly && log.Text.Contains("正在打开浏览器环境"));
        var follow = Named<CheckBox>(main, "诊断自动滚动"); follow.IsChecked = false;
        Method("Log").Invoke(main, new object[] { "diagnostic-selection-marker" });
        log.Select(log.Text.IndexOf("diagnostic-selection-marker", StringComparison.Ordinal), "diagnostic-selection-marker".Length);
        Method("Log").Invoke(main, new object[] { "diagnostic-live-update" });
        Check("Live updates preserve selected history text", log.Text.Contains("diagnostic-live-update") && log.SelectedText == "diagnostic-selection-marker");
        for (int i = 0; i < 205; i++) Method("Log").Invoke(main, new object[] { "diagnostic-limit-" + i });
        Check("Diagnostics caps recent records at 200", log.Text.Split(Environment.NewLine).Length == 200 && !log.Text.Contains("diagnostic-limit-4" + Environment.NewLine) && log.Text.EndsWith("diagnostic-limit-204"));
        log.Select(log.Text.IndexOf("diagnostic-limit-150", StringComparison.Ordinal), "diagnostic-limit-150".Length);
        Method("Log").Invoke(main, new object[] { "diagnostic-cap-update" });
        Check("Trimming old records preserves surviving selection", log.SelectedText == "diagnostic-limit-150");
        log.ScrollToHome(); await Task.Delay(80); var offset = log.VerticalOffset;
        Method("Log").Invoke(main, new object[] { "diagnostic-paused-update" }); await Task.Delay(80);
        Check("Disabling follow preserves history scroll position", Math.Abs(log.VerticalOffset - offset) < 1);
        main.Width = 1050; main.Height = 680; await Task.Delay(100); Capture(main, Path.Combine(output, "diagnostics-min.png"));
        Check("Minimum window keeps diagnostic log readable", log.ActualWidth > 700 && log.ActualHeight > 350);
        Click(main, "代理管理"); await Task.Delay(160);
        Check("Proxy navigation hides diagnostics and native browser", !log.IsVisible && !IsWindowVisible(native));
        Click(main, "运行诊断"); main.UpdateLayout();
        Check("Returning diagnostics preserves controls and follow preference", ReferenceEquals(log, Named<TextBox>(main, "运行诊断日志")) && follow.IsChecked == false);
        follow.IsChecked = true; main.Width = 1440; main.Height = 900; await Task.Delay(120);
        Capture(main, Path.Combine(output, "diagnostics-desktop.png"));
        Click(main, "环境工作台"); await Task.Delay(200);
        Check("Returning account page restores same native handle", hosts["ui-browser"].AttachedHandle == native && IsWindowVisible(native) && !log.IsVisible);
        Check("Page navigation preserves browser process", (await BrowserState(client)).GetProperty("pid").GetInt32() == before.GetProperty("pid").GetInt32());
        Check("Page navigation preserves document and draft", (await Evaluate(port, "window.__diagnosticToken")).ToString() == token.ToString() && (await Evaluate(port, "document.querySelector('#entry').value")).GetString() == "diagnostic navigation draft");
    }
}
