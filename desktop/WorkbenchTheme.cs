using System.Windows;
using System.Windows.Controls;
using System.Windows.Markup;
using System.Windows.Media;
using System.Windows.Shapes;
using System.Runtime.InteropServices;
using System.Windows.Interop;

namespace CloakWorkbench;

internal static class WorkbenchTheme
{
    [DllImport("dwmapi.dll")] static extern int DwmSetWindowAttribute(IntPtr hwnd, int attribute, ref int value, int size);
    internal static Brush Color(string value) => new SolidColorBrush((System.Windows.Media.Color)ColorConverter.ConvertFromString(value));
    internal static readonly Brush Ink = Color("#E7ECE9"), Muted = Color("#97A39D"), Border = Color("#303A35"), Blue = Color("#28D6AA"), Navy = Color("#101412"), Green = Color("#63DDB4");
    internal static void Apply(Window window)
    {
        window.FontFamily = new FontFamily("Microsoft YaHei UI");
        window.FontSize = 13;
        window.SourceInitialized += (_, _) => {
            var hwnd = new WindowInteropHelper(window).Handle;
            int dark = 1, caption = 0x00161714, ink = 0x00E9ECE7;
            DwmSetWindowAttribute(hwnd, 20, ref dark, sizeof(int));
            DwmSetWindowAttribute(hwnd, 35, ref caption, sizeof(int));
            DwmSetWindowAttribute(hwnd, 36, ref ink, sizeof(int));
        };
        window.Resources.MergedDictionaries.Add((ResourceDictionary)XamlReader.Parse("""
        <ResourceDictionary xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml">
          <Style x:Key="AccountActionMenu" TargetType="ContextMenu">
            <Setter Property="OverridesDefaultStyle" Value="True"/>
            <Setter Property="Foreground" Value="#E7ECE9"/><Setter Property="Background" Value="#202622"/>
            <Setter Property="BorderBrush" Value="#3B4740"/><Setter Property="BorderThickness" Value="1"/>
            <Setter Property="FontFamily" Value="Microsoft YaHei UI"/><Setter Property="FontSize" Value="12"/>
            <Setter Property="MinWidth" Value="184"/><Setter Property="Padding" Value="5"/>
            <Setter Property="SnapsToDevicePixels" Value="True"/><Setter Property="HasDropShadow" Value="False"/>
            <Setter Property="Template"><Setter.Value><ControlTemplate TargetType="ContextMenu">
              <Border Margin="6" Background="{TemplateBinding Background}" BorderBrush="{TemplateBinding BorderBrush}" BorderThickness="{TemplateBinding BorderThickness}" CornerRadius="9" Padding="{TemplateBinding Padding}">
                <Border.Effect><DropShadowEffect Color="#000000" BlurRadius="10" ShadowDepth="2" Opacity="0.32"/></Border.Effect>
                <ItemsPresenter KeyboardNavigation.DirectionalNavigation="Cycle"/>
              </Border>
            </ControlTemplate></Setter.Value></Setter>
          </Style>
          <Style x:Key="AccountActionItem" TargetType="MenuItem">
            <Setter Property="OverridesDefaultStyle" Value="True"/><Setter Property="Foreground" Value="#E7ECE9"/>
            <Setter Property="Background" Value="Transparent"/><Setter Property="Padding" Value="11,0"/>
            <Setter Property="MinHeight" Value="37"/><Setter Property="Margin" Value="0,1"/>
            <Setter Property="HorizontalContentAlignment" Value="Left"/><Setter Property="Cursor" Value="Hand"/>
            <Setter Property="Template"><Setter.Value><ControlTemplate TargetType="MenuItem">
              <Border x:Name="row" Background="{TemplateBinding Background}" Padding="{TemplateBinding Padding}" CornerRadius="5">
                <Grid><Grid.ColumnDefinitions><ColumnDefinition Width="25"/><ColumnDefinition Width="*"/></Grid.ColumnDefinitions>
                  <ContentPresenter Content="{TemplateBinding Icon}" VerticalAlignment="Center"/>
                  <ContentPresenter Grid.Column="1" ContentSource="Header" RecognizesAccessKey="True" VerticalAlignment="Center"/>
                </Grid>
              </Border>
              <ControlTemplate.Triggers>
                <Trigger Property="IsHighlighted" Value="True"><Setter TargetName="row" Property="Background" Value="#30463A"/><Setter Property="Foreground" Value="#81E9C5"/></Trigger>
                <Trigger Property="Tag" Value="danger"><Setter Property="Foreground" Value="#EFA5A5"/></Trigger>
                <MultiTrigger><MultiTrigger.Conditions><Condition Property="Tag" Value="danger"/><Condition Property="IsHighlighted" Value="True"/></MultiTrigger.Conditions><Setter TargetName="row" Property="Background" Value="#493032"/><Setter Property="Foreground" Value="#FFC0C0"/></MultiTrigger>
                <Trigger Property="IsEnabled" Value="False"><Setter TargetName="row" Property="Opacity" Value="0.4"/></Trigger>
              </ControlTemplate.Triggers>
            </ControlTemplate></Setter.Value></Setter>
          </Style>
          <Style x:Key="AccountActionSeparator" TargetType="Separator">
            <Setter Property="Template"><Setter.Value><ControlTemplate TargetType="Separator"><Border Height="1" Background="#36413A" Margin="11,5"/></ControlTemplate></Setter.Value></Setter>
          </Style>
          <Style TargetType="ComboBoxItem">
            <Setter Property="Foreground" Value="#E7ECE9"/><Setter Property="Background" Value="#202622"/>
            <Setter Property="Padding" Value="10,8"/>
            <Setter Property="Template"><Setter.Value><ControlTemplate TargetType="ComboBoxItem">
              <Border x:Name="item" Background="{TemplateBinding Background}" Padding="{TemplateBinding Padding}" CornerRadius="4"><ContentPresenter/></Border>
              <ControlTemplate.Triggers><Trigger Property="IsHighlighted" Value="True"><Setter TargetName="item" Property="Background" Value="#345344"/></Trigger>
              <Trigger Property="IsSelected" Value="True"><Setter TargetName="item" Property="Background" Value="#2B4639"/></Trigger></ControlTemplate.Triggers>
            </ControlTemplate></Setter.Value></Setter>
          </Style>
          <Style TargetType="ComboBox">
            <Setter Property="Background" Value="#202622"/><Setter Property="Foreground" Value="#E7ECE9"/><Setter Property="BorderBrush" Value="#303A35"/><Setter Property="MinHeight" Value="36"/>
            <Setter Property="Template"><Setter.Value><ControlTemplate TargetType="ComboBox">
              <Grid>
                <ToggleButton Focusable="False" IsChecked="{Binding IsDropDownOpen, Mode=TwoWay, RelativeSource={RelativeSource TemplatedParent}}" ClickMode="Press">
                  <ToggleButton.Template><ControlTemplate TargetType="ToggleButton"><Border Background="{Binding Background, RelativeSource={RelativeSource AncestorType=ComboBox}}" BorderBrush="#303A35" BorderThickness="1" CornerRadius="6">
                    <TextBlock Text="&#xE70D;" FontFamily="Segoe MDL2 Assets" FontSize="10" Foreground="#97A39D" HorizontalAlignment="Right" VerticalAlignment="Center" Margin="0,0,12,0"/>
                  </Border></ControlTemplate></ToggleButton.Template>
                </ToggleButton>
                <ContentPresenter Content="{TemplateBinding SelectionBoxItem}" ContentTemplate="{TemplateBinding SelectionBoxItemTemplate}" ContentTemplateSelector="{TemplateBinding ItemTemplateSelector}" Margin="11,7,30,7" VerticalAlignment="Center" IsHitTestVisible="False"/>
                <Popup x:Name="PART_Popup" Placement="Bottom" IsOpen="{TemplateBinding IsDropDownOpen}" AllowsTransparency="True" Focusable="False">
                  <Border Background="#202622" BorderBrush="#303A35" BorderThickness="1" CornerRadius="6" Padding="4" MinWidth="{Binding ActualWidth, RelativeSource={RelativeSource TemplatedParent}}" MaxHeight="300"><ScrollViewer><ItemsPresenter/></ScrollViewer></Border>
                </Popup>
              </Grid>
              <ControlTemplate.Triggers><Trigger Property="IsEnabled" Value="False"><Setter Property="Opacity" Value="0.45"/></Trigger></ControlTemplate.Triggers>
            </ControlTemplate></Setter.Value></Setter>
          </Style>
          <Style TargetType="DataGridColumnHeader"><Setter Property="Background" Value="#242C26"/><Setter Property="Foreground" Value="#AAB8AF"/><Setter Property="BorderBrush" Value="#303A35"/><Setter Property="BorderThickness" Value="0,0,1,1"/><Setter Property="Padding" Value="10,8"/></Style>
          <Style TargetType="CheckBox"><Setter Property="Foreground" Value="#E7ECE9"/></Style>
          <Style TargetType="ToolTip"><Setter Property="Background" Value="#2B352E"/><Setter Property="Foreground" Value="#E7ECE9"/></Style>
          <Style TargetType="ScrollBar">
            <Setter Property="Background" Value="Transparent"/><Setter Property="Width" Value="10"/>
            <Setter Property="Template"><Setter.Value><ControlTemplate TargetType="ScrollBar">
              <Track x:Name="PART_Track" Orientation="{TemplateBinding Orientation}" IsDirectionReversed="True">
                <Track.DecreaseRepeatButton><RepeatButton x:Name="decrease" Command="ScrollBar.PageUpCommand" Opacity="0" Focusable="False"/></Track.DecreaseRepeatButton>
                <Track.Thumb><Thumb><Thumb.Template><ControlTemplate TargetType="Thumb"><Border Background="#44534A" CornerRadius="3" Margin="2"/></ControlTemplate></Thumb.Template></Thumb></Track.Thumb>
                <Track.IncreaseRepeatButton><RepeatButton x:Name="increase" Command="ScrollBar.PageDownCommand" Opacity="0" Focusable="False"/></Track.IncreaseRepeatButton>
              </Track>
              <ControlTemplate.Triggers><Trigger Property="Orientation" Value="Horizontal"><Setter Property="Width" Value="Auto"/><Setter Property="Height" Value="10"/><Setter TargetName="PART_Track" Property="IsDirectionReversed" Value="False"/><Setter TargetName="decrease" Property="Command" Value="ScrollBar.PageLeftCommand"/><Setter TargetName="increase" Property="Command" Value="ScrollBar.PageRightCommand"/></Trigger></ControlTemplate.Triggers>
            </ControlTemplate></Setter.Value></Setter>
          </Style>
          <Style TargetType="Button">
            <Setter Property="Padding" Value="12,8"/><Setter Property="Background" Value="#202622"/><Setter Property="Foreground" Value="#E7ECE9"/>
            <Setter Property="BorderBrush" Value="#303A35"/><Setter Property="BorderThickness" Value="1"/><Setter Property="Cursor" Value="Hand"/>
            <Setter Property="HorizontalContentAlignment" Value="Center"/><Setter Property="VerticalContentAlignment" Value="Center"/>
            <Setter Property="Template"><Setter.Value><ControlTemplate TargetType="Button">
              <Border x:Name="shell" Background="{TemplateBinding Background}" BorderBrush="{TemplateBinding BorderBrush}" BorderThickness="{TemplateBinding BorderThickness}" CornerRadius="7" Padding="{TemplateBinding Padding}">
                <ContentPresenter HorizontalAlignment="{TemplateBinding HorizontalContentAlignment}" VerticalAlignment="Center" RecognizesAccessKey="True"/>
              </Border><ControlTemplate.Triggers>
                <Trigger Property="IsMouseOver" Value="True"><Setter TargetName="shell" Property="Opacity" Value="0.82"/></Trigger>
                <Trigger Property="IsPressed" Value="True"><Setter TargetName="shell" Property="Opacity" Value="0.65"/></Trigger>
                <Trigger Property="IsKeyboardFocused" Value="True"><Setter TargetName="shell" Property="BorderBrush" Value="#28D6AA"/><Setter TargetName="shell" Property="BorderThickness" Value="2"/></Trigger>
                <Trigger Property="IsEnabled" Value="False"><Setter TargetName="shell" Property="Opacity" Value="0.4"/></Trigger>
              </ControlTemplate.Triggers>
            </ControlTemplate></Setter.Value></Setter>
          </Style>
          <Style TargetType="TextBox">
            <Setter Property="Background" Value="#202622"/><Setter Property="Foreground" Value="#E7ECE9"/><Setter Property="CaretBrush" Value="#28D6AA"/>
            <Setter Property="BorderBrush" Value="#303A35"/><Setter Property="BorderThickness" Value="1"/><Setter Property="Padding" Value="11,9"/>
            <Setter Property="VerticalContentAlignment" Value="Center"/><Setter Property="Template"><Setter.Value><ControlTemplate TargetType="TextBox">
              <Border x:Name="shell" Background="{TemplateBinding Background}" BorderBrush="{TemplateBinding BorderBrush}" BorderThickness="{TemplateBinding BorderThickness}" CornerRadius="6">
                <ScrollViewer x:Name="PART_ContentHost"/>
              </Border><ControlTemplate.Triggers><Trigger Property="IsKeyboardFocusWithin" Value="True"><Setter TargetName="shell" Property="BorderBrush" Value="#28D6AA"/></Trigger>
              <Trigger Property="IsReadOnly" Value="True"><Setter TargetName="shell" Property="Background" Value="#161B19"/></Trigger></ControlTemplate.Triggers>
            </ControlTemplate></Setter.Value></Setter>
          </Style>
          <Style TargetType="PasswordBox">
            <Setter Property="Background" Value="#202622"/><Setter Property="Foreground" Value="#E7ECE9"/><Setter Property="CaretBrush" Value="#28D6AA"/>
            <Setter Property="BorderBrush" Value="#303A35"/><Setter Property="BorderThickness" Value="1"/><Setter Property="Padding" Value="11,9"/>
            <Setter Property="Template"><Setter.Value><ControlTemplate TargetType="PasswordBox">
              <Border x:Name="shell" Background="{TemplateBinding Background}" BorderBrush="{TemplateBinding BorderBrush}" BorderThickness="{TemplateBinding BorderThickness}" CornerRadius="6">
                <ScrollViewer x:Name="PART_ContentHost"/>
              </Border><ControlTemplate.Triggers><Trigger Property="IsKeyboardFocusWithin" Value="True"><Setter TargetName="shell" Property="BorderBrush" Value="#28D6AA"/></Trigger></ControlTemplate.Triggers>
            </ControlTemplate></Setter.Value></Setter>
          </Style>
        </ResourceDictionary>
        """));
    }

    internal static UIElement Icon(string name, Brush? color = null, double size = 17)
    {
        var glyph = name switch {
            "plus" => "\uE710", "back" => "\uE72B", "forward" => "\uE72A",
            "reload" => "\uE72C", "external" => "\uE8A7", "close" => "\uE711",
            "browser" => "\uE774", "shield" => "\uE83D", "settings" => "\uE713",
            "diagnostic" => "\uE9D9", "download" => "\uE896", "play" => "\uE768",
            "accounts" => "\uE716", "search" => "\uE721", "more" => "\uE712",
            "edit" => "\uE70F", "home" => "\uE80F", _ => "\uE946"
        };
        return new TextBlock { Text = glyph, FontFamily = new FontFamily("Segoe Fluent Icons, Segoe MDL2 Assets"),
            FontSize = size, Width = size + 2, Height = size + 2, TextAlignment = TextAlignment.Center,
            VerticalAlignment = VerticalAlignment.Center, Foreground = color ?? Ink };
    }
}
