using System.IO;
using System.Text;
using System.Text.Json;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Data;
using System.Windows.Media;
using Microsoft.Win32;

namespace CloakWorkbench;

public sealed partial class MainWindow
{
    readonly ContentControl configurationWorkspace = new() { Visibility = Visibility.Collapsed };
    string? configurationPlan;
    bool configurationBusy;
    string? configurationInputPath;

    void ShowConfigurationTransfer()
    {
        configurationWorkspace.Content ??= BuildConfigurationTransfer();
        ShowManagementPage(configurationWorkspace, "备份与恢复");
    }

    FrameworkElement BuildConfigurationTransfer()
    {
        var root = new Grid();
        var content = new StackPanel { Margin = new Thickness(32, 28, 32, 30), MaxWidth = 1120, HorizontalAlignment = HorizontalAlignment.Stretch };
        root.Children.Add(new ScrollViewer { Content = content, VerticalScrollBarVisibility = ScrollBarVisibility.Auto });
        TextBlock Copy(string value, double size = 12, Brush? color = null) => new() {
            Text = value, FontSize = size, Foreground = color ?? Muted, TextWrapping = TextWrapping.Wrap, LineHeight = size * 1.7
        };
        Border Card(UIElement child) => new() {
            Child = child, Background = Panel, BorderBrush = Line, BorderThickness = new Thickness(1),
            CornerRadius = new CornerRadius(12), Padding = new Thickness(24)
        };
        void Named(DependencyObject control, string name) => AutomationProperties.SetName(control, name);
        PasswordBox Password(string name) {
            var box = new PasswordBox { Background = Hex("#111613"), Foreground = Text, BorderBrush = Line,
                BorderThickness = new Thickness(1), Padding = new Thickness(12, 10, 12, 10), Margin = new Thickness(0, 7, 0, 14), MaxLength = 256 };
            Named(box, name); return box;
        }
        content.Children.Add(Copy("配置迁移", 11, Accent));
        content.Children.Add(new TextBlock { Text = "备份与恢复", FontSize = 28, FontWeight = FontWeights.SemiBold, Foreground = Text, Margin = new Thickness(0, 7, 0, 8) });
        content.Children.Add(Copy("将企业授权和代理带到新电脑，接着使用熟悉的工作配置。"));
        var overview = Copy("默认包含全部企业连接、授权凭据及代理线路。配置备份不含浏览器 Cookie 和下载文件；网站登录态可通过环境中的“登录会话”单独备份。", 12);
        overview.Margin = new Thickness(16, 13, 16, 13);
        content.Children.Add(new Border { Child = overview, Background = Hex("#182C24"), CornerRadius = new CornerRadius(8), Margin = new Thickness(0, 22, 0, 22) });

        var cards = new Grid(); cards.ColumnDefinitions.Add(new ColumnDefinition()); cards.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(20) }); cards.ColumnDefinitions.Add(new ColumnDefinition()); content.Children.Add(cards);
        var exportPanel = new StackPanel();
        exportPanel.Children.Add(Copy("01  /  当前电脑", 11, Accent));
        exportPanel.Children.Add(new TextBlock { Text = "导出配置备份", FontSize = 20, FontWeight = FontWeights.SemiBold, Foreground = Text, Margin = new Thickness(0, 8, 0, 10) });
        exportPanel.Children.Add(Copy("企业 OAuth 授权、服务账号密钥，以及代理账号、密码和上游代理会一并保存。"));
        var accounts = new CheckBox { Content = "同时备份账号与已知密码", Foreground = Text, Margin = new Thickness(0, 18, 0, 10) }; Named(accounts, "备份账号与密码"); exportPanel.Children.Add(accounts);
        var environments = new CheckBox { Content = "同时备份环境设置和账号绑定", Foreground = Text, Margin = new Thickness(0, 0, 0, 18) }; Named(environments, "备份环境设置"); exportPanel.Children.Add(environments);
        environments.Checked += (_, _) => { accounts.IsChecked = true; accounts.IsEnabled = false; };
        environments.Unchecked += (_, _) => accounts.IsEnabled = true;
        exportPanel.Children.Add(Copy("设置备份密码", 12, Text));
        var exportPassword = Password("导出备份密码"); exportPanel.Children.Add(exportPassword);
        exportPanel.Children.Add(Copy("至少 8 位。新电脑导入时需要同一密码，请妥善保存。", 11));
        var feedback = Copy("", 12); feedback.Visibility = Visibility.Collapsed; feedback.Margin = new Thickness(0, 18, 0, 0); Named(feedback, "配置迁移结果");
        var overlayText = Copy("正在准备配置…", 15, Text);
        var overlayPanel = new StackPanel { Width = 320, HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center };
        overlayPanel.Children.Add(overlayText); overlayPanel.Children.Add(new ProgressBar { IsIndeterminate = true, Height = 4, Margin = new Thickness(0, 16, 0, 0) });
        var overlay = new Border { Background = new SolidColorBrush(Color.FromArgb(230, 16, 20, 18)), Child = overlayPanel, Visibility = Visibility.Collapsed };
        Named(overlay, "配置迁移处理中");
        async Task Run(string title, Func<Task> action) {
            if (configurationBusy) return;
            configurationBusy = true; cards.IsEnabled = false; overlayText.Text = title; overlay.Visibility = Visibility.Visible;
            CancelFeedbackDismissal(feedback); feedback.Visibility = Visibility.Collapsed;
            try { await action(); }
            catch (Exception ex) { feedback.Text = ex.Message; feedback.Foreground = Hex("#EFA5A5"); feedback.Visibility = Visibility.Visible; }
            finally { configurationBusy = false; cards.IsEnabled = true; overlay.Visibility = Visibility.Collapsed; }
        }
        void Success(string text) {
            feedback.Text = text; feedback.Foreground = Accent; feedback.Visibility = Visibility.Visible;
            DismissFeedbackLater(feedback, () => feedback.Visibility = Visibility.Collapsed);
        }
        var export = IconButton("download", "导出备份", async () => {
            if (exportPassword.Password.Length < 8) { feedback.Text = "请先设置至少 8 位备份密码。"; feedback.Foreground = Hex("#EFA5A5"); feedback.Visibility = Visibility.Visible; exportPassword.Focus(); return; }
            var dialog = new SaveFileDialog { Title = "保存配置备份", Filter = "栖航配置备份|*.qhbackup", DefaultExt = ".qhbackup", AddExtension = true, FileName = "栖航配置备份-" + DateTime.Now.ToString("yyyyMMdd-HHmmss") + ".qhbackup" };
            if (dialog.ShowDialog(this) != true) return;
            await Run("正在加密并导出配置…", async () => {
                var result = await Api("config-transfer/export", new { password = exportPassword.Password, includeAccounts = accounts.IsChecked == true, includeProfiles = environments.IsChecked == true });
                await File.WriteAllTextAsync(dialog.FileName, Str(result, "text"), new UTF8Encoding(false));
                exportPassword.Clear(); Success("配置备份已导出，可在新电脑导入。请将备份文件与密码分开保管。");
            });
        }, true); export.HorizontalAlignment = HorizontalAlignment.Left; export.Margin = new Thickness(0, 20, 0, 0); Named(export, "导出配置备份"); exportPanel.Children.Add(export);
        cards.Children.Add(Card(exportPanel));

        var importPanel = new StackPanel();
        importPanel.Children.Add(Copy("02  /  新电脑", 11, Accent));
        importPanel.Children.Add(new TextBlock { Text = "导入配置备份", FontSize = 20, FontWeight = FontWeights.SemiBold, Foreground = Text, Margin = new Thickness(0, 8, 0, 10) });
        importPanel.Children.Add(Copy("先查看备份内容与冲突，再确认恢复。导入前会自动备份本机原配置。"));
        var fileLabel = Copy("尚未选择备份文件", 12, Text); fileLabel.Margin = new Thickness(0, 17, 0, 8); Named(fileLabel, "配置备份文件"); importPanel.Children.Add(fileLabel);
        var previewSection = new StackPanel { Margin = new Thickness(0, 22, 0, 0), Visibility = Visibility.Collapsed };
        var previewSummary = Copy("", 14, Text); previewSummary.Margin = new Thickness(0, 0, 0, 14); previewSection.Children.Add(previewSummary);
        var table = new DataGrid { AutoGenerateColumns = false, IsReadOnly = true, CanUserAddRows = false, HeadersVisibility = DataGridHeadersVisibility.Column,
            Height = 250, EnableRowVirtualization = true, SelectionMode = DataGridSelectionMode.Single };
        foreach (var (title, path, width) in new[] { ("类型", "Type", 110d), ("名称 / 邮箱", "Name", 1d), ("导入处理", "Action", 110d), ("授权资料", "Authorization", 150d) })
            table.Columns.Add(new DataGridTextColumn { Header = title, Binding = new Binding(path), Width = width == 1 ? new DataGridLength(1, DataGridLengthUnitType.Star) : new DataGridLength(width) });
        Named(table, "配置导入预览"); previewSection.Children.Add(table);
        var previewNote = Copy("", 11); previewNote.Margin = new Thickness(0, 12, 0, 14); previewSection.Children.Add(previewNote);
        Button? apply = null;
        void Invalidate() { configurationPlan = null; if (apply != null) apply.IsEnabled = false; previewSection.Visibility = Visibility.Collapsed; }
        var choose = Button("选择备份文件", () => {
            var dialog = new OpenFileDialog { Title = "选择配置备份", Filter = "栖航配置备份|*.qhbackup", CheckFileExists = true };
            if (dialog.ShowDialog(this) != true) return;
            configurationInputPath = dialog.FileName; fileLabel.Text = Path.GetFileName(configurationInputPath); fileLabel.ToolTip = configurationInputPath; Invalidate();
        }); choose.HorizontalAlignment = HorizontalAlignment.Left; choose.Margin = new Thickness(0, 0, 0, 16); Named(choose, "选择配置备份文件"); importPanel.Children.Add(choose);
        importPanel.Children.Add(Copy("输入备份密码", 12, Text)); var importPassword = Password("导入备份密码"); importPanel.Children.Add(importPassword); importPassword.PasswordChanged += (_, _) => Invalidate();
        var mode = new ComboBox { ItemsSource = new[] { new ProxyChoice("skip", "保留本机配置，跳过已有项"), new ProxyChoice("replace", "使用备份替换已有项") },
            DisplayMemberPath = "Label", SelectedValuePath = "Id", SelectedValue = "skip", Margin = new Thickness(0, 0, 0, 0) };
        Named(mode, "配置冲突处理"); mode.SelectionChanged += (_, _) => Invalidate(); importPanel.Children.Add(mode);
        var preview = IconButton("search", "读取并预览", async () => await Run("正在解密并检查备份…", async () => {
            Invalidate();
            if (configurationInputPath == null) throw new InvalidOperationException("请先选择配置备份文件。");
            if (new FileInfo(configurationInputPath).Length > 12 * 1024 * 1024) throw new InvalidOperationException("备份文件超过 12 MB 上限。");
            var result = await Api("config-transfer/preview", new { text = await File.ReadAllTextAsync(configurationInputPath, Encoding.UTF8), password = importPassword.Password, mode = mode.SelectedValue as string ?? "skip" });
            configurationPlan = Str(result, "planId"); var counts = result.GetProperty("counts"); table.Visibility = Visibility.Visible;
            previewSummary.Text = $"导入预览   ·   新增 {Str(counts, "add")}   ·   替换 {Str(counts, "replace")}   ·   跳过 {Str(counts, "skip")}";
            table.ItemsSource = result.GetProperty("rows").EnumerateArray().Select(r => new ConfigurationPreviewRow(
                Str(r, "type"), Str(r, "name"), Str(r, "action") switch { "add" => "新增", "replace" => "替换", _ => "跳过" },
                Str(r, "authorization") switch { "portable" => "已包含授权", "missing" => "需要授权", _ => "—" })).ToList();
            previewNote.Text = Str(result, "note") + "\n恢复环境设置不包含浏览器 Cookie。上游为 127.0.0.1 的代理，需要新电脑也有对应代理服务。";
            previewSection.Visibility = Visibility.Visible; apply!.IsEnabled = true;
        })); preview.HorizontalAlignment = HorizontalAlignment.Left; preview.Margin = new Thickness(0, 20, 0, 0); Named(preview, "预览配置备份"); importPanel.Children.Add(preview);
        var importCard = Card(importPanel); Grid.SetColumn(importCard, 2); cards.Children.Add(importCard);
        apply = IconButton("check", "确认导入", async () => await Run("正在备份本机配置并恢复…", async () => {
            if (configurationPlan == null) throw new InvalidOperationException("请先读取并预览备份。");
            var result = await Api("config-transfer/import", new { planId = configurationPlan, password = importPassword.Password });
            Invalidate(); importPassword.Clear();
            await LoadProxyCatalog(); await LoadLoginAccounts(); await Refresh();
            profileSettingsDrafts.Clear(); sessionBackupViews.Clear(); renderedAccounts = null;
            var backup = Str(result, "backupPath");
            Success($"导入完成：恢复 {Str(result, "imported")} 项，跳过 {Str(result, "skipped")} 项。");
            previewSummary.Text = "配置已恢复"; table.ItemsSource = null; table.Visibility = Visibility.Collapsed;
            previewNote.Text = Str(result, "note") + (backup.Length > 0 ? "\n导入前配置已备份（使用本次备份密码）：" + backup : "");
            previewSection.Visibility = Visibility.Visible;
        }), true); apply.HorizontalAlignment = HorizontalAlignment.Left; apply.IsEnabled = false; Named(apply, "确认导入配置"); previewSection.Children.Add(apply);
        content.Children.Add(feedback); content.Children.Add(previewSection); root.Children.Add(overlay);
        return root;
    }

    sealed record ConfigurationPreviewRow(string Type, string Name, string Action, string Authorization);
}
