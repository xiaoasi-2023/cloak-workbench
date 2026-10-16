using System.IO;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;

partial class Program
{
    static async Task Feedback(CloakWorkbench.MainWindow main, string output)
    {
        Click(main, "代理管理");
        await Wait(() => Nodes<DataGrid>(main).SingleOrDefault()?.Items.Count == 4);
        var table = Named<DataGrid>(main, "代理列表");
        var banner = Named<Border>(main, "代理检测状态");
        void Choose(string id) {
            foreach (var row in table.Items.Cast<object>()) row.GetType().GetProperty("Selected")!.SetValue(row, (string)row.GetType().GetProperty("Id")!.GetValue(row)! == id);
        }
        async Task Start() {
            Click(main, "检测选中代理");
            await Wait(() => !Named<Button>(main, "检测全部").IsEnabled);
        }
        async Task Finish() => await Wait(() => Named<Button>(main, "检测全部").IsEnabled);
        Choose("ui-standard"); await Start();
        Check("Running diagnostics keep progress visible and cannot be dismissed", banner.IsVisible && !Named<Button>(main, "关闭代理检测提示").IsVisible);
        await Finish();
        Check("Successful completion initially shows confirmation", banner.IsVisible);
        var rows = table.Items.Cast<object>().ToArray();
        await Task.Delay(4500);
        Check("Successful confirmation disappears automatically", !banner.IsVisible);
        Check("Dismissal retains diagnostic rows and results", table.Items.Count == 4 && rows.SequenceEqual(table.Items.Cast<object>()) && RowState(rows.Single(r => (string)r.GetType().GetProperty("Id")!.GetValue(r)! == "ui-standard")) == "completed");
        await Start(); await Finish(); await Task.Delay(2600); await Start(); await Task.Delay(1900);
        Check("Old success timer cannot hide the next running diagnostic", !Named<Button>(main, "检测全部").IsEnabled && banner.IsVisible);
        await Finish(); Click(main, "关闭代理检测提示");
        Check("Successful notice can also be dismissed immediately", !banner.IsVisible);
        Choose("ui-failed"); await Start(); await Finish(); await Task.Delay(4500);
        Check("Failed diagnostic remains visible for review", banner.IsVisible && Nodes<TextBlock>(main).Any(t => t.Text.Contains("失败 1 条")));
        Click(main, "关闭代理检测提示"); Check("Failed notice supports explicit dismissal", !banner.IsVisible);
        Choose("ui-standard"); await Start(); await Finish();
        Click(main, "运行诊断"); await Task.Delay(4500); Click(main, "代理管理"); await Task.Delay(100);
        Check("Returning to a page does not resurrect an expired success notice", !banner.IsVisible && ReferenceEquals(table, Named<DataGrid>(main, "代理列表")));
        Method("ShowAccountCenter").Invoke(main, null); main.UpdateLayout();
        var environmentTable = Named<DataGrid>(main, "环境管理列表");
        var environmentRow = environmentTable.Items.Cast<object>().Single();
        environmentRow.GetType().GetProperty("Chosen")!.SetValue(environmentRow, true);
        Named<ComboBox>(main, "环境批量操作").SelectedIndex = 1;
        await (Task)Method("ExecuteAccountAction").Invoke(main, null)!;
        var environmentBanner = (Border)typeof(CloakWorkbench.MainWindow).GetField("centerActivity", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance)!.GetValue(main)!;
        Check("Successful environment batch initially shows its result", environmentBanner.IsVisible);
        await Task.Delay(4500); Check("Successful environment batch automatically releases list space", !environmentBanner.IsVisible);
        environmentRow.GetType().GetProperty("Chosen")!.SetValue(environmentRow, false);
        await (Task)Method("ExecuteAccountAction").Invoke(main, null)!;
        await Task.Delay(4500); Check("Environment action requiring correction remains visible", environmentBanner.IsVisible);
        Click(main, "关闭环境操作提示"); Check("Environment warning supports manual dismissal", !environmentBanner.IsVisible);
        File.WriteAllText(Path.Combine(output, "feedback-checks.json"), JsonSerializer.Serialize(new { passed = Checks.Count, checks = Checks }, new JsonSerializerOptions { WriteIndented = true }));
    }
}
