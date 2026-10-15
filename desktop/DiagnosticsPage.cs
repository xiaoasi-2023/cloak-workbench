using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Media;

namespace CloakWorkbench;

public sealed partial class MainWindow
{
    readonly TextBox diagnosticLog = new();
    readonly TextBlock diagnosticSummary = new();
    readonly CheckBox followDiagnostics = new() { Content = "自动滚动到最新", IsChecked = true };

    void ShowDiagnostics()
    {
        diagnosticsWorkspace.Content ??= CreateDiagnosticsPage();
        ShowManagementPage(diagnosticsWorkspace, "运行诊断");
    }

    FrameworkElement CreateDiagnosticsPage()
    {
        var layout = new DockPanel { Margin = new Thickness(26) };
        var header = new StackPanel { Margin = new Thickness(0, 0, 0, 18) };
        header.Children.Add(new TextBlock { Text = "运行诊断", FontSize = 25, FontWeight = FontWeights.SemiBold, Foreground = Text });
        header.Children.Add(new TextBlock { Text = "查看浏览器启动、窗口连接和操作记录，定位运行问题。", Foreground = Muted, FontSize = 12, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 7, 0, 0) });
        DockPanel.SetDock(header, Dock.Top); layout.Children.Add(header);

        var tools = new DockPanel { Margin = new Thickness(0, 0, 0, 14) };
        followDiagnostics.Foreground = Muted; followDiagnostics.FontSize = 12;
        followDiagnostics.VerticalAlignment = VerticalAlignment.Center;
        AutomationProperties.SetName(followDiagnostics, "诊断自动滚动");
        followDiagnostics.Checked += (_, _) => diagnosticLog.ScrollToEnd();
        DockPanel.SetDock(followDiagnostics, Dock.Right); tools.Children.Add(followDiagnostics);
        diagnosticSummary.Foreground = Accent; diagnosticSummary.FontSize = 12;
        diagnosticSummary.VerticalAlignment = VerticalAlignment.Center;
        AutomationProperties.SetName(diagnosticSummary, "诊断记录摘要"); tools.Children.Add(diagnosticSummary);
        DockPanel.SetDock(tools, Dock.Top); layout.Children.Add(tools);

        var hint = new TextBlock { Text = "仅显示本次运行的最近 200 条记录。取消自动滚动，可停留查看历史；日志可选中复制。", Foreground = Muted, FontSize = 11, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 12, 0, 0) };
        DockPanel.SetDock(hint, Dock.Bottom); layout.Children.Add(hint);
        diagnosticLog.IsReadOnly = true; diagnosticLog.TextWrapping = TextWrapping.Wrap;
        diagnosticLog.Background = WorkbenchTheme.Navy; diagnosticLog.Foreground = Text;
        diagnosticLog.FontFamily = new FontFamily("Consolas, Microsoft YaHei UI"); diagnosticLog.FontSize = 12;
        diagnosticLog.Padding = new Thickness(18); diagnosticLog.BorderThickness = new Thickness(0);
        diagnosticLog.VerticalContentAlignment = VerticalAlignment.Top;
        diagnosticLog.VerticalScrollBarVisibility = ScrollBarVisibility.Auto;
        AutomationProperties.SetName(diagnosticLog, "运行诊断日志");
        layout.Children.Add(new Border { Child = diagnosticLog, Background = WorkbenchTheme.Navy, BorderBrush = Line, BorderThickness = new Thickness(1), CornerRadius = new CornerRadius(10), Padding = new Thickness(1) });
        UpdateDiagnostics(); return layout;
    }

    void UpdateDiagnostics()
    {
        if (diagnosticsWorkspace.Content == null && diagnosticLog.Parent == null) return;
        var offset = diagnosticLog.VerticalOffset;
        int selectionStart = diagnosticLog.SelectionStart, selectionLength = diagnosticLog.SelectionLength;
        // Append new records without replacing the user's selection or viewport.
        var text = string.Join(Environment.NewLine, messages);
        if (text.StartsWith(diagnosticLog.Text, StringComparison.Ordinal)) {
            diagnosticLog.AppendText(text[diagnosticLog.Text.Length..]);
            diagnosticLog.Select(selectionStart, selectionLength);
        }
        else {
            int removed = diagnosticLog.Text.IndexOf(Environment.NewLine, StringComparison.Ordinal) + Environment.NewLine.Length;
            diagnosticLog.Text = text;
            int start = Math.Clamp(selectionStart - removed, 0, text.Length);
            int end = Math.Clamp(selectionStart + selectionLength - removed, start, text.Length);
            diagnosticLog.Select(start, end - start);
        }
        if (followDiagnostics.IsChecked == true) diagnosticLog.ScrollToEnd();
        else diagnosticLog.ScrollToVerticalOffset(offset);
        diagnosticSummary.Text = messages.Count == 0 ? "暂无记录 · 等待浏览器操作" : $"{messages.Count} 条记录 · 最新 {messages[^1][..8]} · 实时更新";
    }
}
