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
    static async Task ConfigurationTransfer(MainWindow main, HttpClient client, string output)
    {
        async Task<JsonElement> Post(string path, object body) {
            using var response = await client.PostAsync(path, new StringContent(JsonSerializer.Serialize(body), Encoding.UTF8, "application/json"));
            response.EnsureSuccessStatusCode(); using var json = JsonDocument.Parse(await response.Content.ReadAsStringAsync()); return json.RootElement.Clone();
        }
        Click(main, "备份与恢复"); await Task.Delay(120);
        Check("Configuration transfer opens inside main page", main.OwnedWindows.Count == 0 && Named<Button>(main, "导出配置备份").IsVisible);
        Check("Import requires a preview", !Named<Button>(main, "确认导入配置").IsEnabled);
        var accounts = Named<CheckBox>(main, "备份账号与密码"); var profiles = Named<CheckBox>(main, "备份环境设置");
        Check("Default backup excludes accounts and browser settings", accounts.IsChecked != true && profiles.IsChecked != true);
        profiles.IsChecked = true;
        Check("Environment settings include account binding dependencies", accounts.IsChecked == true && !accounts.IsEnabled);
        profiles.IsChecked = false;
        Check("Account option becomes editable again", accounts.IsEnabled);
        main.Width = 1050; main.Height = 680; await Task.Delay(100); Capture(main, Path.Combine(output, "configuration-min.png"));
        main.Width = 1440; main.Height = 900; await Task.Delay(100); Capture(main, Path.Combine(output, "configuration.png"));
        Click(main, "预览配置备份"); await Wait(() => !Named<Border>(main, "配置迁移处理中").IsVisible);
        Check("Missing file gives an inline error", Named<TextBlock>(main, "配置迁移结果").Text.Contains("选择配置备份"));
        const string password = "offline-ui-backup-password";
        var exported = await Post("config-transfer/export", new { password });
        string file = Path.Combine(output, "synthetic-config.qhbackup"); await File.WriteAllTextAsync(file, exported.GetProperty("text").GetString()!);
        typeof(MainWindow).GetField("configurationInputPath", BindingFlags.NonPublic | BindingFlags.Instance)!.SetValue(main, file);
        var input = Named<PasswordBox>(main, "导入备份密码"); input.Password = "wrong-password";
        Click(main, "预览配置备份");
        Check("Decryption displays an interaction-blocking progress layer", Named<Border>(main, "配置迁移处理中").IsVisible);
        await Wait(() => !Named<Border>(main, "配置迁移处理中").IsVisible);
        Check("Wrong password leaves import disabled", !Named<Button>(main, "确认导入配置").IsEnabled && Named<TextBlock>(main, "配置迁移结果").Text.Contains("密码"));
        input.Password = password;
        Click(main, "预览配置备份"); await Wait(() => Named<Button>(main, "确认导入配置").IsEnabled);
        Check("Real API preview lists fixture configurations", Named<DataGrid>(main, "配置导入预览").Items.Count == 2);
        Capture(main, Path.Combine(output, "configuration-preview.png"));
        input.Password = password + "changed";
        Check("Password changes invalidate preview", !Named<Button>(main, "确认导入配置").IsEnabled);
        input.Password = password;
        Click(main, "预览配置备份"); await Wait(() => Named<Button>(main, "确认导入配置").IsEnabled);
        Named<ComboBox>(main, "配置冲突处理").SelectedValue = "replace";
        Check("Conflict policy changes invalidate preview", !Named<Button>(main, "确认导入配置").IsEnabled);
        Click(main, "预览配置备份"); await Wait(() => Named<Button>(main, "确认导入配置").IsEnabled);
        Click(main, "确认导入配置");
        await Wait(() => Named<TextBlock>(main, "配置迁移结果").Text.StartsWith("导入完成"));
        Check("Import completes through real page and API", !Named<Button>(main, "确认导入配置").IsEnabled && input.Password.Length == 0);
        Check("Import records preimport backup location", Nodes<TextBlock>(main).Any(t => t.Text.Contains("before-import-")));
        await Task.Delay(4500);
        Check("Success notice dismisses automatically", !Named<TextBlock>(main, "配置迁移结果").IsVisible);
        Click(main, "环境工作台");
        Check("Configuration page hides when returning to browser", !Named<Button>(main, "导出配置备份").IsVisible);
        Click(main, "备份与恢复");
        Check("Returning preserves controls", ReferenceEquals(input, Named<PasswordBox>(main, "导入备份密码")));
        File.WriteAllText(Path.Combine(output, "configuration-checks.json"), JsonSerializer.Serialize(new { ok = true, checks = Checks }, new JsonSerializerOptions { WriteIndented = true }));
    }
}
