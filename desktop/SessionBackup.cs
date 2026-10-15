using System.IO;
using System.Text;
using System.Text.Json;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Media;
using Microsoft.Win32;

namespace CloakWorkbench;

public sealed partial class MainWindow
{
    readonly Dictionary<string, SessionBackupView> sessionBackupViews = new();

    sealed class SessionBackupView
    {
        internal required StackPanel Panel, Footer;
        internal required StackPanel BackupSection, ImportSection;
        internal required TextBlock Summary, Preview, Status;
        internal required TextBox Name;
        internal required ComboBox Proxy, Format;
        internal required Button Backup, Restore, Read, Export, Import, Open, Check;
        internal required Button BackupTab, ImportTab;
        internal required ProgressBar Progress;
        internal string? DraftId, ImportedId;
        internal bool Busy, Available;
    }

    async void ShowSessionBackup(string? id = null)
    {
        try {
            ShowAccountCenter();
            await LoadProxyCatalog();
            var container = CenterPanel("登录会话", id != null && profiles.TryGetValue(id, out var p) ? Str(p, "name") + " · 备份与恢复" : "导入 CK，创建独立账号环境");
            string key = id ?? ":import";
            if (sessionBackupViews.TryGetValue(key, out var cached)) {
                if (cached.Panel.Parent is Panel old) old.Children.Remove(cached.Panel);
                container.Children.Add(cached.Panel); AttachGoogleLoginFooter(container, cached.Footer);
                await ReloadSessionBackup(id, cached); return;
            }
            var panel = new StackPanel(); container.Children.Add(panel);
            TextBlock Note(string text) => new() { Text = text, Foreground = Muted, FontSize = 11, LineHeight = 19, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 0, 0, 12) };
            panel.Children.Add(Note("日常使用请继续原环境。恢复会用备份创建新的环境，不覆盖原环境；Cookie 是否仍有效需由 Dola 确认。"));
            SessionBackupView? view = null;
            var tabs = new Grid { Margin = new Thickness(0, 0, 0, 16) }; tabs.ColumnDefinitions.Add(new ColumnDefinition()); tabs.ColumnDefinitions.Add(new ColumnDefinition()); panel.Children.Add(tabs);
            var backupTab = Button("本机备份", () => SelectSessionTab(view!, false)); backupTab.Margin = new Thickness(0, 0, 4, 0); backupTab.Visibility = id == null ? Visibility.Collapsed : Visibility.Visible; tabs.Children.Add(backupTab);
            var importTab = Button("导入 / 恢复", () => SelectSessionTab(view!, true)); importTab.Margin = new Thickness(4, 0, 0, 0); Grid.SetColumn(importTab, 1); tabs.Children.Add(importTab);
            var backupSection = new StackPanel(); var importSection = new StackPanel(); panel.Children.Add(backupSection); panel.Children.Add(importSection);
            var summary = Note(id == null ? "可导入 Cookie JSON、完整会话包或本机加密备份。" : "正在读取本机备份…"); AutomationProperties.SetName(summary, "会话备份摘要"); backupSection.Children.Add(summary);
            var backup = Button("备份当前登录", async () => await SessionAction(view!, async () => {
                await Api($"profiles/{id}/session-backup", new { }); await ReloadSessionBackup(id, view!);
                view!.Status.Text = "本机加密备份已更新。";
            }), true); backup.Visibility = id == null ? Visibility.Collapsed : Visibility.Visible; backupSection.Children.Add(backup);
            var format = new ComboBox { ItemsSource = new[] { new ProxyChoice("encrypted", "加密备份 · 当前 Windows 用户"), new ProxyChoice("json", "CK JSON · 可迁移，包含登录凭据") }, DisplayMemberPath = "Label", SelectedValuePath = "Id", SelectedValue = "encrypted", Margin = new Thickness(0, 12, 0, 8) };
            AutomationProperties.SetName(format, "会话导出格式"); format.Visibility = id == null ? Visibility.Collapsed : Visibility.Visible; backupSection.Children.Add(format);
            var export = Button("导出备份文件", async () => {
                var selectedFormat = format.SelectedValue as string ?? "encrypted";
                var chooser = new SaveFileDialog { Title = selectedFormat == "json" ? "导出 CK JSON（包含登录凭据）" : "导出本机加密备份", Filter = selectedFormat == "json" ? "CK JSON|*.json" : "加密会话备份|*.ckbackup", FileName = (id ?? "session") + (selectedFormat == "json" ? ".json" : ".ckbackup"), AddExtension = true };
                if (chooser.ShowDialog(this) != true) return;
                await SessionAction(view!, async () => {
                    var result = await Api($"profiles/{id}/session-backup/export", new { format = selectedFormat });
                    await File.WriteAllTextAsync(chooser.FileName, Str(result, "text"), new UTF8Encoding(false));
                    view!.Status.Text = selectedFormat == "json" ? "CK JSON 已导出，请像密码一样保管。" : "加密文件已导出，仅原 Windows 用户可解密。";
                });
            }); export.Visibility = id == null ? Visibility.Collapsed : Visibility.Visible; backupSection.Children.Add(export);
            var restore = Button("从本机备份准备恢复", async () => await SessionAction(view!, async () => {
                BeginSessionPreview(view!);
                var result = await Api($"profiles/{id}/session-backup/preview", new { }); UpdateSessionPreview(view!, result);
            })); restore.Margin = new Thickness(0, 8, 0, 0); restore.Visibility = id == null ? Visibility.Collapsed : Visibility.Visible; backupSection.Children.Add(restore);
            importSection.Children.Add(Note("可导入 Cookie JSON、完整会话包或本机加密备份。"));
            var read = Button("选择 CK JSON / 加密备份", async () => {
                var chooser = new OpenFileDialog { Title = "选择 Dola 登录会话", Filter = "登录会话|*.json;*.ckbackup|所有文件|*.*", Multiselect = false };
                if (chooser.ShowDialog(this) != true) return;
                await SessionAction(view!, async () => {
                    BeginSessionPreview(view!);
                    if (new FileInfo(chooser.FileName).Length > 16 * 1024 * 1024) throw new InvalidOperationException("文件过大，请选择 16 MiB 以内的备份。");
                    var text = await File.ReadAllTextAsync(chooser.FileName, new UTF8Encoding(false, true));
                    var result = await Api("sessions/preview", new { text }); UpdateSessionPreview(view!, result);
                });
            }); importSection.Children.Add(read);
            var preview = Note("先读取文件或本机备份，再确认创建恢复环境。原环境保持不变，同一份 CK 不是两个独立账号。"); preview.Margin = new Thickness(0, 10, 0, 14); AutomationProperties.SetName(preview, "CK 导入预览"); importSection.Children.Add(preview);
            var name = CenterField(importSection, "恢复环境名称", id != null && profiles.TryGetValue(id, out p) ? Str(p, "name") + " · 恢复" : "Dola 恢复环境"); name.MaxLength = 120;
            var proxy = CenterProxyPicker(importSection, "恢复环境代理");
            proxy.SelectedValue = id != null && profiles.TryGetValue(id, out p) ? Str(p, "proxyId") : "";
            importSection.Children.Add(Note("选择可用的代理出口；完整备份保留原指纹，普通 CK 使用新环境默认配置。导入后先检测登录，不因存在 Cookie 就显示已登录。"));
            var footer = new StackPanel { Margin = new Thickness(20, 10, 20, 18) };
            var status = Note("等待操作"); status.Foreground = Text; status.Margin = new Thickness(0);
            AutomationProperties.SetName(status, "会话操作结果");
            var progress = new ProgressBar { Height = 3, IsIndeterminate = true, Foreground = Accent, Background = Line, Visibility = Visibility.Collapsed, Margin = new Thickness(0, 10, 0, 0) };
            var activity = new StackPanel(); activity.Children.Add(status); activity.Children.Add(progress);
            footer.Children.Add(new Border { Background = Hex("#202D26"), CornerRadius = new CornerRadius(8), Padding = new Thickness(12), Child = activity, Margin = new Thickness(0, 0, 0, 10) });
            var import = Button("确认创建并恢复", async () => await SessionAction(view!, async () => {
                if (view!.DraftId == null) return;
                view.Status.Text = "正在创建独立环境、恢复会话并检测登录…";
                var result = await Api("sessions/import", new { draftId = view.DraftId, name = name.Text.Trim(), proxyId = proxy.SelectedValue as string ?? "" });
                view.ImportedId = Str(result, "id"); view.DraftId = null; await Refresh();
                if (Str(result, "restored").Equals("true", StringComparison.OrdinalIgnoreCase)) {
                    await LaunchQueued(view.ImportedId, selectBrowser: false);
                    var check = AccountObject(result, "check"); string state = Str(check, "status");
                    view.Status.Text = state == "logged_in" ? "会话已导入，Dola 登录已确认。" : state == "login_required" ? "会话已导入，但已失效或需要验证；请打开新环境重新登录。" : state == "region_blocked" ? "会话已导入，Dola 提示地区受限；请检查代理出口。" : "会话已导入，登录仍待确认；可打开新环境查看或再次检测。";
                } else view.Status.Text = "恢复未完成，新环境已保留。原账号未修改，可手动登录。";
                view.Open.Visibility = view.Check.Visibility = Visibility.Visible;
            }), true); import.IsEnabled = false; footer.Children.Add(import);
            var followup = new Grid { Margin = new Thickness(0, 8, 0, 0) }; followup.ColumnDefinitions.Add(new ColumnDefinition()); followup.ColumnDefinitions.Add(new ColumnDefinition()); footer.Children.Add(followup);
            var open = Button("打开新环境", async () => await SessionAction(view!, () => Launch(view!.ImportedId!))); open.Margin = new Thickness(0, 0, 6, 0); open.Visibility = Visibility.Collapsed; followup.Children.Add(open);
            var checkButton = Button("检测登录", async () => await SessionAction(view!, async () => {
                var result = await Api($"profiles/{view!.ImportedId}/account-check", new { });
                view.Status.Text = AccountStateLabel(Str(result, "status")) + " · " + AccountReason(Str(result, "reason")); await Refresh();
            })); AutomationProperties.SetName(checkButton, "检测恢复后的登录"); checkButton.Margin = new Thickness(6, 0, 0, 0); checkButton.Visibility = Visibility.Collapsed; Grid.SetColumn(checkButton, 1); followup.Children.Add(checkButton);
            view = new SessionBackupView { Panel = panel, Footer = footer, BackupSection = backupSection, ImportSection = importSection, BackupTab = backupTab, ImportTab = importTab, Summary = summary, Preview = preview, Status = status, Name = name, Proxy = proxy, Format = format, Backup = backup, Restore = restore, Read = read, Export = export, Import = import, Open = open, Check = checkButton, Progress = progress };
            sessionBackupViews[key] = view; AttachGoogleLoginFooter(container, footer);
            SelectSessionTab(view, id == null);
            name.TextChanged += (_, _) => SetSessionControls(view); await ReloadSessionBackup(id, view);
        } catch { Log("会话页面打开失败，请重新进入账号详情。没有提交登录数据。"); }
    }

    async Task ReloadSessionBackup(string? id, SessionBackupView view)
    {
        if (id == null) return;
        try {
            var info = await Api($"profiles/{id}/session-backup"); view.Available = Str(info, "available").Equals("true", StringComparison.OrdinalIgnoreCase);
            view.Summary.Text = view.Available ? "本机加密备份 · " + SessionTime(Str(info, "savedAt")) + "\n" + Str(info, "cookieCount") + " 个 Cookie · " + Str(info, "originCount") + " 个网站来源 · " + Str(info, "databaseCount") + " 个数据库" : "暂无本机备份。确认登录后自动备份，也可手动备份。";
            if (info.TryGetProperty("warnings", out var warnings) && warnings.GetArrayLength() > 0) view.Summary.Text += "\n部分数据库含非 JSON 数据，未纳入快照。";
            if (view.DraftId == null && view.ImportedId == null && Str(info, "message").Length > 0) view.Status.Text = Str(info, "message");
        } catch { view.Summary.Text = "本机备份暂时无法读取，可使用 CK 文件导入。"; view.Available = false; }
        SetSessionControls(view);
    }

    static string SessionTime(string value) => double.TryParse(value, System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var seconds) ? DateTimeOffset.FromUnixTimeSeconds((long)seconds).ToLocalTime().ToString("MM/dd HH:mm") : "时间未知";

    void UpdateSessionPreview(SessionBackupView view, JsonElement result)
    {
        view.DraftId = Str(result, "draftId");
        SelectSessionTab(view, true);
        view.Preview.Text = Str(result, "cookieCount") + " 个 Dola Cookie · " + Str(result, "originCount") + " 个网站来源\n已过期 " + Str(result, "expiredCount") + " 个 · 忽略其他网站 " + Str(result, "skippedCount") + " 个\n" + (Str(result, "hasSessionCookie").Equals("true", StringComparison.OrdinalIgnoreCase) ? "存在会话 Cookie，仍需真实登录检测。" : "未发现常见会话 Cookie，可能需要重新登录。");
        view.Status.Text = "已读取会话。确认名称与代理后创建新环境。"; SetSessionControls(view);
    }

    void BeginSessionPreview(SessionBackupView view)
    {
        view.DraftId = view.ImportedId = null;
        view.Open.Visibility = view.Check.Visibility = Visibility.Collapsed;
        view.Preview.Text = "正在读取会话文件…";
        SelectSessionTab(view, true);
    }

    void SetSessionControls(SessionBackupView view)
    {
        foreach (var control in new Control[] { view.Read, view.Backup, view.Name, view.Proxy, view.Format, view.Open, view.Check, view.BackupTab, view.ImportTab }) control.IsEnabled = !view.Busy;
        view.Restore.IsEnabled = view.Export.IsEnabled = !view.Busy && view.Available;
        view.Import.IsEnabled = !view.Busy && view.DraftId != null && view.Name.Text.Trim().Length > 0;
        view.Progress.Visibility = view.Busy ? Visibility.Visible : Visibility.Collapsed;
    }

    void SelectSessionTab(SessionBackupView view, bool import)
    {
        view.BackupSection.Visibility = import ? Visibility.Collapsed : Visibility.Visible;
        view.ImportSection.Visibility = view.Import.Visibility = import ? Visibility.Visible : Visibility.Collapsed;
        view.BackupTab.Background = import ? Brushes.Transparent : Hex("#243C32");
        view.ImportTab.Background = import ? Hex("#243C32") : Brushes.Transparent;
    }

    async Task SessionAction(SessionBackupView view, Func<Task> action)
    {
        if (view.Busy) return; view.Busy = true; view.Status.Text = "正在处理会话数据…"; SetSessionControls(view);
        try { await action(); }
        catch (Exception ex) { view.Status.Text = ex is InvalidOperationException && ex.Message.Length < 180 ? ex.Message : "会话操作未完成，原账号保持不变；请检查浏览器或文件后重试。"; }
        finally { view.Busy = false; SetSessionControls(view); }
    }
}
