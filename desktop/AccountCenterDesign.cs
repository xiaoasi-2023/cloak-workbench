using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Data;
using System.Windows.Markup;
using System.Windows.Media;

namespace CloakWorkbench;

public sealed partial class MainWindow
{
    readonly TextBlock centerTotal = new(), centerOpened = new(), centerConfirmed = new();
    readonly ScrollViewer centerResults = new() { Visibility = Visibility.Collapsed };
    Button? centerResultToggle;

    FrameworkElement CreateAccountCenter()
    {
        var layout = new DockPanel { Margin = new Thickness(28, 28, 28, 24), LastChildFill = true };
        var heading = new DockPanel { Margin = new Thickness(0, 0, 0, 24) };
        var create = IconButton("plus", "新建环境", () => ShowInlineProfileSettings(null), true);
        create.VerticalAlignment = VerticalAlignment.Center;
        create.Height = 40; create.Padding = new Thickness(16, 0, 16, 0);
        DockPanel.SetDock(create, Dock.Right); heading.Children.Add(create); centerOperations.Add(create);
        var batch = Button("批量创建", ShowAccountImport); batch.Height = 40; batch.Margin = new Thickness(0, 0, 12, 0); batch.VerticalAlignment = VerticalAlignment.Center;
        DockPanel.SetDock(batch, Dock.Right); heading.Children.Add(batch); centerOperations.Add(batch);
        var importSession = Button("导入 CK", () => ShowSessionBackup()); importSession.Height = 40; importSession.Margin = new Thickness(0, 0, 12, 0); importSession.VerticalAlignment = VerticalAlignment.Center;
        DockPanel.SetDock(importSession, Dock.Right); heading.Children.Add(importSession); centerOperations.Add(importSession);
        var title = new StackPanel { Margin = new Thickness(0, 0, 20, 0) };
        title.Children.Add(new TextBlock { Text = "环境管理", Foreground = Text, FontSize = 28, FontWeight = FontWeights.SemiBold });
        title.Children.Add(new TextBlock { Text = "管理浏览器环境、代理和登录状态；邮箱与密码在账号列表维护", Foreground = Muted, FontSize = 12, Margin = new Thickness(0, 8, 0, 0) });
        heading.Children.Add(title); DockPanel.SetDock(heading, Dock.Top); layout.Children.Add(heading);

        var overview = new Grid { Margin = new Thickness(0, 0, 0, 24) };
        var metricLabels = new[] { "全部环境", "浏览器运行中", "本次检测已登录" };
        var metrics = new[] { centerTotal, centerOpened, centerConfirmed };
        for (int i = 0; i < metrics.Length; i++) {
            overview.ColumnDefinitions.Add(new ColumnDefinition());
            var metric = new DockPanel { Margin = new Thickness(i == 0 ? 0 : 24, 0, 24, 0) };
            metrics[i].FontFamily = new FontFamily("Segoe UI"); metrics[i].FontSize = 26;
            metrics[i].FontWeight = FontWeights.SemiBold; metrics[i].Foreground = i == 2 ? WorkbenchTheme.Green : Text;
            metrics[i].Margin = new Thickness(0, 0, 14, 0);
            metric.Children.Add(metrics[i]); metric.Children.Add(Label(metricLabels[i], 12, Muted));
            var shell = new Border { BorderBrush = Line, BorderThickness = new Thickness(i == 0 ? 0 : 1, 0, 0, 0), Child = metric };
            Grid.SetColumn(shell, i); overview.Children.Add(shell);
        }
        DockPanel.SetDock(overview, Dock.Top); layout.Children.Add(overview);

        var content = new Grid(); content.ColumnDefinitions.Add(new ColumnDefinition());
        content.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        var list = new DockPanel();
        var filters = new Grid { Margin = new Thickness(18, 18, 18, 18) };
        filters.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star), MaxWidth = 474 });
        filters.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(160) });
        filters.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(150) });
        filters.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        var searchWrap = new Grid { Margin = new Thickness(0, 0, 14, 0) };
        centerSearch.ToolTip = "搜索名称、邮箱、备注"; centerSearch.Height = 38;
        centerSearch.Padding = new Thickness(34, 9, 10, 9); AutomationProperties.SetName(centerSearch, "环境管理搜索"); searchWrap.Children.Add(centerSearch);
        var searchIcon = (FrameworkElement)WorkbenchTheme.Icon("search", Muted, 14);
        searchIcon.HorizontalAlignment = HorizontalAlignment.Left; searchIcon.Margin = new Thickness(12, 0, 0, 0); searchIcon.IsHitTestVisible = false; searchWrap.Children.Add(searchIcon);
        var searchHint = Label("搜索环境、邮箱或备注", 12, Muted); searchHint.Margin = new Thickness(34, 0, 8, 0); searchHint.IsHitTestVisible = false; searchWrap.Children.Add(searchHint);
        centerSearch.TextChanged += (_, _) => searchHint.Visibility = centerSearch.Text.Length == 0 ? Visibility.Visible : Visibility.Collapsed;
        filters.Children.Add(searchWrap);
        centerGroup.ItemsSource = new[] { "全部分组" }; centerGroup.SelectedIndex = 0; centerGroup.Height = 38;
        centerGroup.Margin = new Thickness(0, 0, 12, 0); AutomationProperties.SetName(centerGroup, "环境分组筛选"); Grid.SetColumn(centerGroup, 1); filters.Children.Add(centerGroup);
        centerState.ItemsSource = new[] { "全部状态", "运行中", "本次已登录", "本次待检测", "上次已登录", "需要登录", "地区受限", "网络异常" }; centerState.SelectedIndex = 0; centerState.Height = 38;
        AutomationProperties.SetName(centerState, "环境状态筛选"); Grid.SetColumn(centerState, 2); filters.Children.Add(centerState);
        var reset = IconButton("reload", "", () => { centerSearch.Clear(); centerGroup.SelectedIndex = 0; centerState.SelectedIndex = 0; });
        reset.Width = 38; reset.Height = 38; reset.Padding = new Thickness(0); reset.Margin = new Thickness(12, 0, 0, 0); reset.ToolTip = "重置筛选";
        AutomationProperties.SetName(reset, "重置环境筛选"); Grid.SetColumn(reset, 3); filters.Children.Add(reset);
        centerSearch.TextChanged += (_, _) => RenderAccountCenter(); centerGroup.SelectionChanged += (_, _) => RenderAccountCenter(); centerState.SelectionChanged += (_, _) => RenderAccountCenter();
        var filterShell = new Border { Background = Panel, BorderBrush = Line, BorderThickness = new Thickness(1), CornerRadius = new CornerRadius(10), Margin = new Thickness(0, 0, 0, 18), Child = filters };
        DockPanel.SetDock(filterShell, Dock.Top); layout.Children.Add(filterShell);

        var toolbar = new DockPanel { Margin = new Thickness(18, 11, 18, 11) };
        var operation = new StackPanel { Orientation = Orientation.Horizontal }; DockPanel.SetDock(operation, Dock.Right); toolbar.Children.Add(operation);
        centerAction.ItemsSource = new[] { "打开浏览器（依次）", "关闭浏览器", "检测 Dola 登录", "检测代理出口", "更改环境分组", "选择代理" }; centerAction.SelectedIndex = 0;
        centerAction.Width = 184; centerAction.Height = 36; centerAction.Margin = new Thickness(0, 0, 10, 0); AutomationProperties.SetName(centerAction, "环境批量操作"); operation.Children.Add(centerAction);
        var execute = Button("执行", async () => await CenterSafe(ExecuteAccountAction), true); execute.MinWidth = 64; operation.Children.Add(execute); centerOperations.Add(execute);
        centerCount.Foreground = Muted; centerCount.FontSize = 12; centerCount.VerticalAlignment = VerticalAlignment.Center;
        centerCount.TextTrimming = TextTrimming.CharacterEllipsis; AutomationProperties.SetName(centerCount, "环境选择统计"); toolbar.Children.Add(centerCount);
        var toolbarShell = new Border { Background = Hex("#1D2521"), BorderBrush = Line, BorderThickness = new Thickness(0, 1, 0, 1), Child = toolbar };
        DockPanel.SetDock(toolbarShell, Dock.Top); list.Children.Add(toolbarShell);

        var activity = new DockPanel { Margin = new Thickness(16, 10, 16, 10) };
        var activityActions = new StackPanel { Orientation = Orientation.Horizontal }; DockPanel.SetDock(activityActions, Dock.Right); activity.Children.Add(activityActions);
        centerCancel = Button("取消剩余", async () => await CenterSafe(CancelAccountOperation)); centerCancel.IsEnabled = false; centerCancel.Padding = new Thickness(10, 6, 10, 6); activityActions.Children.Add(centerCancel);
        centerResultToggle = Button("展开明细", () => {
            bool show = centerResults.Visibility != Visibility.Visible;
            centerResults.Visibility = show ? Visibility.Visible : Visibility.Collapsed; centerResultToggle!.Content = show ? "收起明细" : "展开明细";
        }); centerResultToggle.Visibility = Visibility.Collapsed; centerResultToggle.Padding = new Thickness(10, 6, 10, 6);
        AutomationProperties.SetName(centerResultToggle, "展开账号结果"); activityActions.Children.Add(centerResultToggle);
        var activityText = new StackPanel { VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(0, 0, 16, 0) };
        centerJobText.Foreground = Muted; centerJobText.FontSize = 12; centerJobText.TextWrapping = TextWrapping.Wrap;
        AutomationProperties.SetName(centerJobText, "账号任务进度"); activityText.Children.Add(centerJobText);
        centerProgress.Margin = new Thickness(0, 9, 0, 0); activityText.Children.Add(centerProgress); activity.Children.Add(activityText);
        centerFeedback.Foreground = Muted; centerFeedback.TextWrapping = TextWrapping.Wrap; centerFeedback.FontSize = 12; centerFeedback.LineHeight = 21;
        AutomationProperties.SetName(centerFeedback, "账号操作结果");
        centerResults.Content = centerFeedback; centerResults.MaxHeight = 92; centerResults.VerticalScrollBarVisibility = ScrollBarVisibility.Auto;
        centerResults.HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled; centerResults.Margin = new Thickness(16, 0, 16, 14);
        var activityBody = new StackPanel(); activityBody.Children.Add(activity); activityBody.Children.Add(centerResults);
        centerActivity.Child = activityBody; centerActivity.Background = Hex("#19231F"); centerActivity.BorderBrush = Line; centerActivity.BorderThickness = new Thickness(0, 1, 0, 0);
        DockPanel.SetDock(centerActivity, Dock.Bottom); list.Children.Add(centerActivity);

        centerTable = new DataGrid { AutoGenerateColumns = false, CanUserAddRows = false, CanUserDeleteRows = false, HeadersVisibility = DataGridHeadersVisibility.Column,
            RowHeaderWidth = 0, GridLinesVisibility = DataGridGridLinesVisibility.Horizontal, HorizontalGridLinesBrush = Hex("#29332E"), Background = Panel,
            RowBackground = Panel, AlternatingRowBackground = Panel, Foreground = Text, BorderThickness = new Thickness(0), RowHeight = 64, ColumnHeaderHeight = 40,
            SelectionMode = DataGridSelectionMode.Single, SelectionUnit = DataGridSelectionUnit.FullRow, EnableRowVirtualization = true, FontSize = 12, IsReadOnly = false };
        centerTable.FrozenColumnCount = 2; AutomationProperties.SetName(centerTable, "环境管理列表");
        ScrollViewer.SetHorizontalScrollBarVisibility(centerTable, ScrollBarVisibility.Auto);
        var rowStyle = new Style(typeof(DataGridRow));
        var hoverRow = new Trigger { Property = UIElement.IsMouseOverProperty, Value = true }; hoverRow.Setters.Add(new Setter(Control.BackgroundProperty, Hex("#202C26"))); rowStyle.Triggers.Add(hoverRow);
        var selectedRow = new Trigger { Property = DataGridRow.IsSelectedProperty, Value = true }; selectedRow.Setters.Add(new Setter(Control.BackgroundProperty, Hex("#243C32"))); selectedRow.Setters.Add(new Setter(Control.ForegroundProperty, Text)); rowStyle.Triggers.Add(selectedRow); centerTable.RowStyle = rowStyle;
        var cellStyle = new Style(typeof(DataGridCell)); cellStyle.Setters.Add(new Setter(Control.BorderThicknessProperty, new Thickness(0)));
        var selectedCell = new Trigger { Property = DataGridCell.IsSelectedProperty, Value = true }; selectedCell.Setters.Add(new Setter(Control.BackgroundProperty, Brushes.Transparent)); selectedCell.Setters.Add(new Setter(Control.ForegroundProperty, Text)); cellStyle.Triggers.Add(selectedCell); centerTable.CellStyle = cellStyle;
        var headerStyle = new Style(typeof(DataGridColumnHeader)); headerStyle.Setters.Add(new Setter(Control.BackgroundProperty, Hex("#19201C")));
        headerStyle.Setters.Add(new Setter(Control.ForegroundProperty, Muted)); headerStyle.Setters.Add(new Setter(Control.BorderThicknessProperty, new Thickness(0)));
        headerStyle.Setters.Add(new Setter(Control.PaddingProperty, new Thickness(14, 0, 14, 0))); headerStyle.Setters.Add(new Setter(Control.FontSizeProperty, 11d)); centerTable.ColumnHeaderStyle = headerStyle;
        var checkboxStyle = (Style)XamlReader.Parse("""
        <Style xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml" TargetType="CheckBox">
          <Setter Property="Cursor" Value="Hand"/><Setter Property="MinWidth" Value="24"/><Setter Property="MinHeight" Value="24"/>
          <Setter Property="Template"><Setter.Value><ControlTemplate TargetType="CheckBox">
            <Grid Background="Transparent"><Border x:Name="box" Width="16" Height="16" CornerRadius="4" Background="#202A24" BorderBrush="#65766B" BorderThickness="1"/>
              <Path x:Name="mark" Data="M 0,4 L 3,7 L 9,0" Stroke="#10231B" StrokeThickness="1.8" Width="9" Height="7" Stretch="Uniform" Visibility="Collapsed"/>
              <Border x:Name="dash" Width="8" Height="2" Background="#10231B" Visibility="Collapsed"/>
            </Grid><ControlTemplate.Triggers>
              <Trigger Property="IsChecked" Value="True"><Setter TargetName="box" Property="Background" Value="#28D6AA"/><Setter TargetName="box" Property="BorderBrush" Value="#28D6AA"/><Setter TargetName="mark" Property="Visibility" Value="Visible"/></Trigger>
              <Trigger Property="IsChecked" Value="{x:Null}"><Setter TargetName="box" Property="Background" Value="#28D6AA"/><Setter TargetName="dash" Property="Visibility" Value="Visible"/></Trigger>
              <Trigger Property="IsKeyboardFocused" Value="True"><Setter TargetName="box" Property="BorderBrush" Value="#A0F4D8"/><Setter TargetName="box" Property="BorderThickness" Value="2"/></Trigger>
              <Trigger Property="IsEnabled" Value="False"><Setter Property="Opacity" Value="0.4"/></Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate></Setter.Value></Setter>
        </Style>
        """);
        var template = new DataTemplate(); var check = new FrameworkElementFactory(typeof(CheckBox));
        check.SetValue(FrameworkElement.StyleProperty, checkboxStyle); check.SetBinding(ToggleButton.IsCheckedProperty, new Binding(nameof(AccountCenterRow.Chosen)) { Mode = BindingMode.TwoWay, UpdateSourceTrigger = UpdateSourceTrigger.PropertyChanged });
        check.SetValue(FrameworkElement.HorizontalAlignmentProperty, HorizontalAlignment.Center); check.SetValue(FrameworkElement.VerticalAlignmentProperty, VerticalAlignment.Center);
        check.SetBinding(AutomationProperties.NameProperty, new Binding(nameof(AccountCenterRow.Name)) { StringFormat = "选择环境 {0}" }); template.VisualTree = check;
        centerSelectAll = new CheckBox { Style = checkboxStyle, ToolTip = "选择当前筛选结果", IsThreeState = true, HorizontalAlignment = HorizontalAlignment.Center };
        AutomationProperties.SetName(centerSelectAll, "全选当前环境");
        centerSelectAll.Click += (_, _) => { if (centerSelecting || centerBusy) return; var chosen = centerSelectAll.IsChecked != false; foreach (var row in centerRows) row.Chosen = chosen; UpdateCenterSelection(); };
        centerTable.Columns.Add(new DataGridTemplateColumn { Header = centerSelectAll, Width = 48, MinWidth = 48, CellTemplate = template });
        var textStyle = new Style(typeof(TextBlock)); textStyle.Setters.Add(new Setter(FrameworkElement.VerticalAlignmentProperty, VerticalAlignment.Center));
        textStyle.Setters.Add(new Setter(FrameworkElement.MarginProperty, new Thickness(14, 0, 14, 0))); textStyle.Setters.Add(new Setter(TextBlock.TextTrimmingProperty, TextTrimming.CharacterEllipsis));
        void Column(string label, string key, double width) => centerTable.Columns.Add(new DataGridTextColumn { Header = label, Binding = new Binding(key), IsReadOnly = true, Width = new DataGridLength(width / 120, DataGridLengthUnitType.Star), MinWidth = width, ElementStyle = textStyle });
        var identityTemplate = new DataTemplate(); var identity = new FrameworkElementFactory(typeof(StackPanel)); identity.SetValue(FrameworkElement.VerticalAlignmentProperty, VerticalAlignment.Center); identity.SetValue(FrameworkElement.MarginProperty, new Thickness(14, 0, 14, 0));
        var name = new FrameworkElementFactory(typeof(TextBlock)); name.SetBinding(TextBlock.TextProperty, new Binding("Name")); name.SetValue(TextBlock.FontWeightProperty, FontWeights.SemiBold); name.SetValue(TextBlock.TextTrimmingProperty, TextTrimming.CharacterEllipsis); name.SetBinding(FrameworkElement.ToolTipProperty, new Binding("Name")); identity.AppendChild(name);
        var email = new FrameworkElementFactory(typeof(TextBlock)); email.SetBinding(TextBlock.TextProperty, new Binding("Email")); email.SetValue(TextBlock.ForegroundProperty, Muted); email.SetValue(TextBlock.FontSizeProperty, 11d); email.SetValue(FrameworkElement.MarginProperty, new Thickness(0, 5, 0, 0)); email.SetValue(TextBlock.TextTrimmingProperty, TextTrimming.CharacterEllipsis); email.SetBinding(FrameworkElement.ToolTipProperty, new Binding("Email")); identity.AppendChild(email); identityTemplate.VisualTree = identity;
        centerTable.Columns.Add(new DataGridTemplateColumn { Header = "环境 / Google 账号", CellTemplate = identityTemplate, Width = new DataGridLength(2, DataGridLengthUnitType.Star), MinWidth = 180, IsReadOnly = true });
        var healthTemplate = new DataTemplate(); var badge = new FrameworkElementFactory(typeof(Border)); badge.SetValue(FrameworkElement.HorizontalAlignmentProperty, HorizontalAlignment.Left); badge.SetValue(FrameworkElement.VerticalAlignmentProperty, VerticalAlignment.Center);
        badge.SetValue(Border.CornerRadiusProperty, new CornerRadius(5)); badge.SetValue(Border.BackgroundProperty, Hex("#24312A")); badge.SetValue(Border.PaddingProperty, new Thickness(8, 5, 8, 5)); badge.SetValue(FrameworkElement.MarginProperty, new Thickness(12, 0, 8, 0));
        var health = new FrameworkElementFactory(typeof(TextBlock)); health.SetBinding(TextBlock.TextProperty, new Binding("Health")); health.SetBinding(TextBlock.ForegroundProperty, new Binding("HealthBrush")); health.SetValue(TextBlock.FontSizeProperty, 11d); badge.AppendChild(health); healthTemplate.VisualTree = badge;
        Column("出口国家", "Country", 100); Column("浏览器", "Session", 80);
        centerTable.Columns.Add(new DataGridTemplateColumn { Header = "Dola 登录", CellTemplate = healthTemplate, IsReadOnly = true, Width = new DataGridLength(1, DataGridLengthUnitType.Star), MinWidth = 118 });
        Column("登录检测时间", "Time", 112); Column("网络检测时间", "NetworkTime", 112);
        var rowActions = new FrameworkElementFactory(typeof(StackPanel)); rowActions.SetValue(StackPanel.OrientationProperty, Orientation.Horizontal); rowActions.SetValue(FrameworkElement.VerticalAlignmentProperty, VerticalAlignment.Center);
        var openAction = new FrameworkElementFactory(typeof(Button)); openAction.SetBinding(ContentControl.ContentProperty, new Binding("OpenLabel")); openAction.SetValue(Control.PaddingProperty, new Thickness(10, 7, 10, 7)); openAction.SetValue(FrameworkElement.MarginProperty, new Thickness(5, 0, 4, 0));
        openAction.SetBinding(AutomationProperties.NameProperty, new Binding("Name") { StringFormat = "打开浏览器 {0}" });
        openAction.AddHandler(System.Windows.Controls.Button.ClickEvent, new RoutedEventHandler(async (sender, e) => { e.Handled = true; if (((FrameworkElement)sender).DataContext is AccountCenterRow row) await CenterSafe(() => Launch(row.Id)); })); rowActions.AppendChild(openAction);
        var proxyAction = new FrameworkElementFactory(typeof(Button)); proxyAction.SetValue(ContentControl.ContentProperty, "检测代理"); proxyAction.SetValue(Control.PaddingProperty, new Thickness(8, 7, 8, 7)); proxyAction.SetBinding(AutomationProperties.NameProperty, new Binding("Name") { StringFormat = "检测环境代理 {0}" });
        proxyAction.AddHandler(System.Windows.Controls.Button.ClickEvent, new RoutedEventHandler(async (sender, e) => { e.Handled = true; if (((FrameworkElement)sender).DataContext is AccountCenterRow row) await RunEnvironmentCheck(row.Id, 3); })); rowActions.AppendChild(proxyAction);
        centerTable.Columns.Add(new DataGridTemplateColumn { Header = "操作", CellTemplate = new DataTemplate { VisualTree = rowActions }, Width = 155, MinWidth = 155, IsReadOnly = true });
        centerTable.SelectionChanged += async (_, _) => { if (centerSelecting || centerBusy) return; if (centerTable.SelectedItem is AccountCenterRow row) await CenterSafe(() => LoadAccountDetails(row.Id)); };
        list.Children.Add(centerTable);
        content.Children.Add(new Border { Background = Panel, BorderBrush = Line, BorderThickness = new Thickness(1), CornerRadius = new CornerRadius(10), ClipToBounds = true, Child = list });
        centerSide.Width = 360; centerSide.Margin = new Thickness(20, 0, 0, 0); Grid.SetColumn(centerSide, 1); content.Children.Add(centerSide); layout.Children.Add(content);
        layout.SizeChanged += (_, _) => { bool compact = layout.ActualWidth < 1000; overview.Visibility = compact ? Visibility.Collapsed : Visibility.Visible; centerSide.Width = compact ? 320 : 360; centerSide.Margin = new Thickness(compact ? 14 : 20, 0, 0, 0); };
        centerJobTimer.Tick += async (_, _) => await CenterSafe(PollAccountJob);
        Closed += (_, _) => { centerJobTimer.Stop(); ++centerDetailVersion; };
        return layout;
    }

    void CenterResultPresentation()
    {
        centerCancel!.Visibility = centerBusy ? Visibility.Visible : Visibility.Collapsed;
        centerProgress.Visibility = centerBusy ? Visibility.Visible : Visibility.Collapsed;
        centerJobText.Foreground = centerBusy ? Accent : Muted;
        centerResultToggle!.Visibility = !centerBusy && centerFeedback.Text.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
        if (centerBusy) { centerResults.Visibility = Visibility.Collapsed; centerResultToggle.Content = "展开明细"; }
    }
}
