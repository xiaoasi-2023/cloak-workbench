using System.IO;
using System.Net.Http;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;

internal static partial class Program
{
    static async Task Videos(CloakWorkbench.MainWindow main, HttpClient client, string output)
    {
        Click(main, "视频生成");
        await Wait(() => Nodes<ComboBox>(main).Any(c => System.Windows.Automation.AutomationProperties.GetName(c) == "浏览器环境"));
        Check("Video creator is inline in main window", main.OwnedWindows.Count == 0);
        var environment = Named<ComboBox>(main, "浏览器环境");
        Check("Saved browser environments are selectable", environment.Items.Count == 1);
        Click(main, "提交视频生成");
        await Wait(() => Named<TextBlock>(main, "视频操作反馈").Text.Contains("提示词"));
        Check("Empty prompt is validated before creation", (await client.GetStringAsync("video-tasks")).Contains("\"tasks\": []"));
        var prompt = Named<TextBox>(main, "视频提示词"); prompt.Text = "A green bird";
        var duration = Named<ComboBox>(main, "视频时长 / 秒"); duration.SelectedItem = 30;
        Check("30 seconds visibly selects Seedance 2.5", Named<ComboBox>(main, "生成模型").SelectedIndex == 1);
        Named<ComboBox>(main, "生成模型").SelectedIndex = 0;
        Check("Switching explicitly to 2.0 changes unsupported 30 seconds to 15", (int)duration.SelectedItem == 15);
        duration.SelectedItem = 30;
        main.Width = 1050; main.Height = 680; await Task.Delay(150);
        Capture(main, Path.Combine(output, "video-min.png"));
        main.Width = 1440; main.Height = 900;
        Click(main, "打开环境");
        for (int i = 0; i < 120; i++) { if ((await BrowserState(client)).GetProperty("state").GetString() == "running") break; await Task.Delay(100); }
        Click(main, "提交视频生成");
        await Wait(() => Nodes<ProgressBar>(main).Any(p => p.IsIndeterminate));
        Check("Active task shows progress without leaving the creator", Nodes<ProgressBar>(main).Any(p => p.IsIndeterminate));
        Capture(main, Path.Combine(output, "video-running.png"));
        Click(main, "账号列表"); await Task.Delay(150); Click(main, "视频生成");
        Check("Page navigation preserves unsent prompt and selected duration", prompt.Text == "A green bird" && (int)duration.SelectedItem == 30);
        bool completed = false;
        for (int i = 0; i < 200; i++) {
            using var doc = JsonDocument.Parse(await client.GetStringAsync("video-tasks"));
            var job = doc.RootElement.GetProperty("tasks").EnumerateArray().FirstOrDefault();
            if (job.ValueKind == JsonValueKind.Object && job.GetProperty("state").GetString() == "completed") { completed = true; break; }
            await Task.Delay(100);
        }
        Check("Real browser fixture finishes once without extra submission", completed);
        await (Task)Method("LoadVideos").Invoke(main, null)!;
        Check("Verified output duration is visible on task card", Nodes<TextBlock>(main).Any(t => t.Text.StartsWith("实际成片") && t.Text.Contains("30")));
        Capture(main, Path.Combine(output, "video-completed.png"));
        Click(main, "打开任务页面"); await Task.Delay(300);
        Check("Opening result switches to browser page", !Named<ComboBox>(main, "浏览器环境").IsVisible);
        Click(main, "视频生成");
        Check("Task remains available after viewing browser", Nodes<TextBlock>(main).Any(t => t.Text.StartsWith("实际成片")));
        File.WriteAllText(Path.Combine(output, "video-checks.json"), JsonSerializer.Serialize(new { passed = Checks.Count, checks = Checks }, new JsonSerializerOptions { WriteIndented = true }));
    }
}
