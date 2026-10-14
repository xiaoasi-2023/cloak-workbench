using System.Globalization;
using System.Security.Cryptography;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Markup;
using System.Windows.Media;

namespace CloakWorkbench;

public sealed partial class MainWindow
{
    sealed record FingerprintChoice(string Value, string Label);
    sealed record FingerprintRegion(string Name, string Locale, string Timezone);
    static readonly FingerprintRegion[] FingerprintRegions = {
        new("中国大陆", "zh-CN", "Asia/Shanghai"), new("中国香港", "zh-HK", "Asia/Hong_Kong"),
        new("中国台湾", "zh-TW", "Asia/Taipei"), new("日本", "ja-JP", "Asia/Tokyo"),
        new("韩国", "ko-KR", "Asia/Seoul"), new("新加坡", "en-SG", "Asia/Singapore"),
        new("美国 · 东部", "en-US", "America/New_York"), new("美国 · 中部", "en-US", "America/Chicago"),
        new("美国 · 山地", "en-US", "America/Denver"), new("美国 · 西部", "en-US", "America/Los_Angeles"),
        new("加拿大", "en-CA", "America/Toronto"), new("英国", "en-GB", "Europe/London"),
        new("德国", "de-DE", "Europe/Berlin"), new("法国", "fr-FR", "Europe/Paris"),
        new("西班牙", "es-ES", "Europe/Madrid"), new("意大利", "it-IT", "Europe/Rome"),
        new("荷兰", "nl-NL", "Europe/Amsterdam"), new("瑞典", "sv-SE", "Europe/Stockholm"),
        new("波兰", "pl-PL", "Europe/Warsaw"), new("俄罗斯", "ru-RU", "Europe/Moscow"),
        new("澳大利亚", "en-AU", "Australia/Sydney"), new("新西兰", "en-NZ", "Pacific/Auckland"),
        new("印度", "en-IN", "Asia/Kolkata"), new("印度尼西亚", "id-ID", "Asia/Jakarta"),
        new("泰国", "th-TH", "Asia/Bangkok"), new("越南", "vi-VN", "Asia/Ho_Chi_Minh"),
        new("马来西亚", "ms-MY", "Asia/Kuala_Lumpur"), new("菲律宾", "en-PH", "Asia/Manila"),
        new("阿联酋", "ar-AE", "Asia/Dubai"), new("土耳其", "tr-TR", "Europe/Istanbul"),
        new("巴西", "pt-BR", "America/Sao_Paulo"), new("墨西哥", "es-MX", "America/Mexico_City"),
        new("阿根廷", "es-AR", "America/Argentina/Buenos_Aires"), new("南非", "en-ZA", "Africa/Johannesburg"),
    };

    Action<Dictionary<string, object?>> BuildFingerprintEditor(StackPanel panel, Func<string, string, string> read)
    {
        bool Supported(string key) => capabilities.ValueKind != JsonValueKind.Object || !capabilities.TryGetProperty("supportedFields", out var fields)
            || !fields.TryGetProperty(key, out var enabled) || enabled.ValueKind != JsonValueKind.False;
        string FormatNumber(string value) => double.TryParse(value, NumberStyles.Float, CultureInfo.InvariantCulture, out var number) ? number.ToString(CultureInfo.InvariantCulture) : value;
        var choiceStyle = (Style)XamlReader.Parse("""
        <Style xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml" TargetType="ComboBox">
          <Setter Property="Background" Value="White"/><Setter Property="Foreground" Value="#172033"/><Setter Property="BorderBrush" Value="#DFE5EE"/>
          <Setter Property="BorderThickness" Value="1"/><Setter Property="MinHeight" Value="38"/><Setter Property="Padding" Value="11,8,30,8"/>
          <Setter Property="ScrollViewer.HorizontalScrollBarVisibility" Value="Disabled"/>
          <Setter Property="Template"><Setter.Value><ControlTemplate TargetType="ComboBox">
            <Grid>
              <ToggleButton Focusable="False" IsChecked="{Binding IsDropDownOpen, Mode=TwoWay, RelativeSource={RelativeSource TemplatedParent}}" ClickMode="Press">
                <ToggleButton.Template><ControlTemplate TargetType="ToggleButton">
                  <Border x:Name="shell" Background="{Binding Background, RelativeSource={RelativeSource AncestorType=ComboBox}}" BorderBrush="{Binding BorderBrush, RelativeSource={RelativeSource AncestorType=ComboBox}}" BorderThickness="1" CornerRadius="6">
                    <Path HorizontalAlignment="Right" VerticalAlignment="Center" Margin="0,0,13,0" Data="M0,0 L4,4 L8,0" Stroke="#657187" StrokeThickness="1.5"/>
                  </Border>
                  <ControlTemplate.Triggers><Trigger Property="IsMouseOver" Value="True"><Setter TargetName="shell" Property="BorderBrush" Value="#93B8F5"/></Trigger></ControlTemplate.Triggers>
                </ControlTemplate></ToggleButton.Template>
              </ToggleButton>
              <TextBlock IsHitTestVisible="False" Margin="{TemplateBinding Padding}" VerticalAlignment="Center" Text="{Binding SelectedItem.Label, RelativeSource={RelativeSource TemplatedParent}}" TextTrimming="CharacterEllipsis"/>
              <Popup x:Name="PART_Popup" Placement="Bottom" IsOpen="{TemplateBinding IsDropDownOpen}" AllowsTransparency="True" Focusable="False">
                <Border Background="White" BorderBrush="#DFE5EE" BorderThickness="1" CornerRadius="6" Padding="4" MinWidth="{Binding ActualWidth, RelativeSource={RelativeSource TemplatedParent}}" MaxHeight="300">
                  <ScrollViewer CanContentScroll="True"><ItemsPresenter KeyboardNavigation.DirectionalNavigation="Contained"/></ScrollViewer>
                </Border>
              </Popup>
            </Grid>
            <ControlTemplate.Triggers>
              <Trigger Property="IsKeyboardFocusWithin" Value="True"><Setter Property="BorderBrush" Value="#2563EB"/></Trigger>
              <Trigger Property="IsEnabled" Value="False"><Setter Property="Opacity" Value="0.5"/></Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate></Setter.Value></Setter>
        </Style>
        """);
        Grid Pair(Panel parent) {
            var grid = new Grid(); grid.ColumnDefinitions.Add(new ColumnDefinition()); grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(16) }); grid.ColumnDefinitions.Add(new ColumnDefinition()); parent.Children.Add(grid); return grid;
        }
        (StackPanel Left, StackPanel Right) Columns(Panel parent) {
            var grid = Pair(parent); var left = new StackPanel(); var right = new StackPanel(); Grid.SetColumn(right, 2); grid.Children.Add(left); grid.Children.Add(right); return (left, right);
        }
        ComboBox Choice(Panel parent, string label, IEnumerable<FingerprintChoice> options, string value, string? supportKey = null) {
            var list = options.ToList(); if (list.All(c => c.Value != value)) list.Add(new(value, value + "  ·  当前自定义值"));
            var wrap = new StackPanel { Margin = new Thickness(0, 0, 0, 12) }; wrap.Children.Add(Label(label, 12));
            var combo = new ComboBox { ItemsSource = list, DisplayMemberPath = "Label", SelectedValuePath = "Value", SelectedValue = value, Style = choiceStyle, Margin = new Thickness(0, 5, 0, 0), MaxDropDownHeight = 300, IsTextSearchEnabled = true };
            System.Windows.Automation.AutomationProperties.SetName(combo, label); TextSearch.SetTextPath(combo, "Label");
            if (supportKey != null && !Supported(supportKey)) { combo.IsEnabled = false; combo.ToolTip = "当前内核不支持此设置"; }
            wrap.Children.Add(combo); parent.Children.Add(wrap); return combo;
        }
        static string Value(ComboBox combo) => combo.SelectedValue as string ?? "";
        TextBox AdvancedField(Panel parent, string label, string value, string? key = null) {
            parent.Children.Add(Label(label, 12)); var input = new TextBox { Text = value, Margin = new Thickness(0, 5, 0, 12) };
            System.Windows.Automation.AutomationProperties.SetName(input, label); if (key != null && !Supported(key)) { input.IsEnabled = false; input.ToolTip = "当前内核不支持此设置"; } parent.Children.Add(input); return input;
        }
        panel.Children.Add(new TextBlock { Text = "当前内核  CloakBrowser " + (capabilities.ValueKind == JsonValueKind.Object ? Str(capabilities, "engineVersion") : "待连接"), Foreground = Accent, FontSize = 11, Margin = new Thickness(0, 0, 0, 12) });
        var banner = new Border { Background = Hex("#EFF5FF"), BorderBrush = Hex("#C9DCF9"), BorderThickness = new Thickness(1), CornerRadius = new CornerRadius(8), Padding = new Thickness(13), Margin = new Thickness(0, 0, 0, 16) };
        var bannerPanel = new StackPanel(); var bannerHeading = new DockPanel { LastChildFill = true }; bannerPanel.Children.Add(bannerHeading);
        var bannerTitle = new TextBlock { Text = "选地区，一键生成", FontSize = 14, FontWeight = FontWeights.SemiBold, Foreground = Text, VerticalAlignment = VerticalAlignment.Center };
        bannerPanel.Children.Add(new TextBlock { Text = "地区方案同时填写语言和时区。随机生成保留地区、平台和代理，替换其余指纹设置。", Foreground = Muted, FontSize = 11, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 5, 0, 0) }); banner.Child = bannerPanel; panel.Children.Add(banner);
        var region = Choice(panel, "地区方案", new[] { new FingerprintChoice("", "自定义地区组合") }.Concat(FingerprintRegions.Select((r, i) => new FingerprintChoice(i.ToString(), r.Name))), "");
        var geographic = Columns(panel);
        var languageOptions = FingerprintRegions.DistinctBy(r => r.Locale).Select(r => new FingerprintChoice(r.Locale, r.Locale + "  ·  " + r.Name)).ToList();
        languageOptions.AddRange(new[] { new FingerprintChoice("fr-CA", "fr-CA  ·  加拿大法语"), new FingerprintChoice("hi-IN", "hi-IN  ·  印地语"), new FingerprintChoice("pt-PT", "pt-PT  ·  葡萄牙语") });
        var locale = Choice(geographic.Left, "语言", languageOptions, read("locale", "zh-CN"));
        var zoneOptions = FingerprintRegions.DistinctBy(r => r.Timezone).Select(r => new FingerprintChoice(r.Timezone, r.Timezone + "  ·  " + r.Name)).ToList();
        var zoneIds = zoneOptions.Select(z => z.Value).ToHashSet();
        foreach (var zone in TimeZoneInfo.GetSystemTimeZones()) {
            var iana = zone.Id.Contains('/') || zone.Id == "UTC" ? zone.Id : TimeZoneInfo.TryConvertWindowsIdToIanaId(zone.Id, out var converted) ? converted : null;
            if (iana != null && zoneIds.Add(iana)) zoneOptions.Add(new(iana, iana));
        }
        var timezone = Choice(geographic.Right, "时区", zoneOptions, read("timezone", "Asia/Shanghai"));
        bool applyingRegion = false;
        void SyncRegion() { if (applyingRegion) return; int index = Array.FindIndex(FingerprintRegions, r => r.Locale == Value(locale) && r.Timezone == Value(timezone)); region.SelectedValue = index < 0 ? "" : index.ToString(); }
        region.SelectionChanged += (_, _) => {
            if (!int.TryParse(Value(region), out var index) || applyingRegion) return; applyingRegion = true;
            locale.SelectedValue = FingerprintRegions[index].Locale; timezone.SelectedValue = FingerprintRegions[index].Timezone; applyingRegion = false;
        };
        locale.SelectionChanged += (_, _) => SyncRegion(); timezone.SelectionChanged += (_, _) => SyncRegion(); SyncRegion();
        var machine = Columns(panel);
        var platform = Choice(machine.Left, "系统平台", new[] { new FingerprintChoice("windows", "Windows"), new FingerprintChoice("macos", "macOS") }, read("fingerprintPlatform", "windows"), "fingerprintPlatform");
        var screenWidth = read("fingerprintScreenWidth", ""); var screenHeight = read("fingerprintScreenHeight", "");
        var screen = Choice(machine.Right, "屏幕分辨率", new[] { new FingerprintChoice("", "自动 · 由内核生成") }.Concat(new[] { "1280x720", "1366x768", "1440x900", "1536x864", "1600x900", "1728x1117", "1920x1080", "1920x1200", "2560x1440", "2560x1600", "3840x2160" }.Select(s => new FingerprintChoice(s, s.Replace("x", " × ")))), screenWidth.Length == 0 && screenHeight.Length == 0 ? "" : screenWidth + "x" + screenHeight, "fingerprintScreenWidth");
        screen.IsEnabled &= Supported("fingerprintScreenHeight");
        var hardware = Columns(panel);
        var cpu = Choice(hardware.Left, "逻辑处理器数", new[] { new FingerprintChoice("", "自动 · 由内核生成") }.Concat(new[] { 2, 4, 6, 8, 10, 12, 16, 24, 32, 64 }.Select(n => new FingerprintChoice(n.ToString(), n + " 核"))), read("hardwareConcurrency", ""), "hardwareConcurrency");
        var memory = Choice(hardware.Right, "内存", new[] { new FingerprintChoice("", "自动 · 由内核生成") }.Concat(new[] { "0.25", "0.5", "1", "2", "4", "8" }.Select(n => new FingerprintChoice(n, n + " GB"))), FormatNumber(read("deviceMemory", "")), "deviceMemory");
        var summary = new TextBlock { Foreground = Muted, FontSize = 11, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 9, 0, 14) }; System.Windows.Automation.AutomationProperties.SetName(summary, "指纹生成反馈");
        var advancedPanel = new StackPanel { Margin = new Thickness(0, 12, 0, 0) };
        var seed = AdvancedField(advancedPanel, "固定指纹种子", read("seed", RandomNumberGenerator.GetInt32(1, int.MaxValue).ToString()));
        var noise = new CheckBox { Content = "启用稳定的 Canvas / WebGL / 音频噪声", IsChecked = !read("fingerprintNoise", "true").Equals("false", StringComparison.OrdinalIgnoreCase), Foreground = Text, Margin = new Thickness(0, 0, 0, 12), IsEnabled = Supported("fingerprintNoise") }; advancedPanel.Children.Add(noise);
        var gpuVendor = AdvancedField(advancedPanel, "WebGL 厂商（留空自动）", read("gpuVendor", ""), "gpuVendor");
        var gpuRenderer = AdvancedField(advancedPanel, "WebGL 渲染器（留空自动）", read("gpuRenderer", ""), "gpuRenderer");
        var taskbar = Choice(advancedPanel, "任务栏高度", new[] { new FingerprintChoice("", "自动 · 由内核生成") }.Concat(new[] { 0, 25, 40, 48, 64, 80 }.Select(n => new FingerprintChoice(n.ToString(), n + " px"))), read("taskbarHeight", ""), "taskbarHeight");
        advancedPanel.Children.Add(new TextBlock { Text = "WebGL 留空时由内核按种子与平台生成。内存为网页可见的分档值，最高 8 GB；屏幕指纹与初始窗口尺寸分别设置。macOS 选项改变浏览器暴露的平台特征。", Foreground = Muted, FontSize = 11, TextWrapping = TextWrapping.Wrap });
        var generate = IconButton("reload", "一键随机指纹", () => {
            var previous = seed.Text; do { seed.Text = RandomNumberGenerator.GetInt32(1, int.MaxValue).ToString(); } while (seed.Text == previous);
            bool mac = Value(platform) == "macos";
            var machines = mac ? new[] { (8, "8"), (10, "8"), (12, "8") } : new[] { (4, "4"), (4, "8"), (8, "8"), (12, "8"), (16, "8") };
            var selectedMachine = machines[RandomNumberGenerator.GetInt32(machines.Length)];
            if (cpu.IsEnabled) cpu.SelectedValue = selectedMachine.Item1.ToString(); if (memory.IsEnabled) memory.SelectedValue = selectedMachine.Item2;
            var screens = mac ? new[] { "1440x900", "1728x1117", "1920x1200", "2560x1600" } : new[] { "1366x768", "1536x864", "1600x900", "1920x1080", "2560x1440" };
            if (screen.IsEnabled) screen.SelectedValue = screens[RandomNumberGenerator.GetInt32(screens.Length)];
            if (taskbar.IsEnabled) taskbar.SelectedValue = mac ? "25" : "40";
            if (noise.IsEnabled) noise.IsChecked = true;
            if (gpuVendor.IsEnabled && gpuRenderer.IsEnabled) { gpuVendor.Text = ""; gpuRenderer.Text = ""; }
            summary.Text = "已生成新指纹 · 种子 " + seed.Text + "\n" + Value(locale) + " · " + Value(timezone) + " · " + (mac ? "macOS" : "Windows") + "。保存后下次启动生效。"; summary.Foreground = WorkbenchTheme.Green;
        }, true);
        generate.HorizontalAlignment = HorizontalAlignment.Right; generate.Margin = new Thickness(12, 0, 0, 0); DockPanel.SetDock(generate, Dock.Right); bannerHeading.Children.Add(generate); bannerHeading.Children.Add(bannerTitle);
        summary.Text = "已准备独立种子 " + seed.Text + "。可直接保存，或一键随机生成硬件与屏幕组合。"; panel.Children.Add(summary);
        var advanced = new Expander { Header = "高级设置 · 种子、噪声与自定义 WebGL", Foreground = Muted, Content = advancedPanel }; panel.Children.Add(advanced);
        return body => {
            int? OptionalInt(string value, string label) => value.Length == 0 ? null : int.TryParse(value, out var n) ? n : throw new InvalidOperationException(label + "必须是整数。");
            if (!int.TryParse(seed.Text.Trim(), out var seedValue) || seedValue < 1) throw new InvalidOperationException("指纹种子必须是 1 至 2147483647 的整数。");
            body["seed"] = seedValue; body["locale"] = Value(locale); body["timezone"] = Value(timezone); body["fingerprintPlatform"] = Value(platform);
            body["hardwareConcurrency"] = OptionalInt(Value(cpu), "逻辑处理器数"); body["deviceMemory"] = Value(memory).Length == 0 ? null : double.Parse(Value(memory), CultureInfo.InvariantCulture);
            var dimensions = Value(screen).Split('x'); body["fingerprintScreenWidth"] = OptionalInt(dimensions[0], "屏幕宽度"); body["fingerprintScreenHeight"] = dimensions.Length == 1 ? null : OptionalInt(dimensions[1], "屏幕高度");
            body["taskbarHeight"] = OptionalInt(Value(taskbar), "任务栏高度"); body["fingerprintNoise"] = noise.IsChecked == true;
            body["gpuVendor"] = gpuVendor.Text.Trim(); body["gpuRenderer"] = gpuRenderer.Text.Trim();
        };
    }
}
