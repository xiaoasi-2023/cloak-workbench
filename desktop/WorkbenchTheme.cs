using System.Windows;
using System.Windows.Controls;
using System.Windows.Markup;
using System.Windows.Media;
using System.Windows.Shapes;

namespace CloakWorkbench;

internal static class WorkbenchTheme
{
    internal static Brush Color(string value) => new SolidColorBrush((System.Windows.Media.Color)ColorConverter.ConvertFromString(value));
    internal static readonly Brush Ink = Color("#172033"), Muted = Color("#657187"), Border = Color("#DFE5EE"), Blue = Color("#2563EB"), Navy = Color("#111827"), Green = Color("#0F766E");
    internal static void Apply(Window window)
    {
        window.FontFamily = new FontFamily("Microsoft YaHei UI");
        window.FontSize = 13;
        window.Resources.MergedDictionaries.Add((ResourceDictionary)XamlReader.Parse("""
        <ResourceDictionary xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml">
          <Style TargetType="Button">
            <Setter Property="Padding" Value="12,8"/><Setter Property="Background" Value="White"/><Setter Property="Foreground" Value="#172033"/>
            <Setter Property="BorderBrush" Value="#DFE5EE"/><Setter Property="BorderThickness" Value="1"/><Setter Property="Cursor" Value="Hand"/>
            <Setter Property="HorizontalContentAlignment" Value="Center"/><Setter Property="VerticalContentAlignment" Value="Center"/>
            <Setter Property="Template"><Setter.Value><ControlTemplate TargetType="Button">
              <Border x:Name="shell" Background="{TemplateBinding Background}" BorderBrush="{TemplateBinding BorderBrush}" BorderThickness="{TemplateBinding BorderThickness}" CornerRadius="7" Padding="{TemplateBinding Padding}">
                <ContentPresenter HorizontalAlignment="{TemplateBinding HorizontalContentAlignment}" VerticalAlignment="Center" RecognizesAccessKey="True"/>
              </Border><ControlTemplate.Triggers>
                <Trigger Property="IsMouseOver" Value="True"><Setter TargetName="shell" Property="Opacity" Value="0.82"/></Trigger>
                <Trigger Property="IsPressed" Value="True"><Setter TargetName="shell" Property="Opacity" Value="0.65"/></Trigger>
                <Trigger Property="IsKeyboardFocused" Value="True"><Setter TargetName="shell" Property="BorderBrush" Value="#2563EB"/><Setter TargetName="shell" Property="BorderThickness" Value="2"/></Trigger>
                <Trigger Property="IsEnabled" Value="False"><Setter TargetName="shell" Property="Opacity" Value="0.4"/></Trigger>
              </ControlTemplate.Triggers>
            </ControlTemplate></Setter.Value></Setter>
          </Style>
          <Style TargetType="TextBox">
            <Setter Property="Background" Value="White"/><Setter Property="Foreground" Value="#172033"/><Setter Property="CaretBrush" Value="#2563EB"/>
            <Setter Property="BorderBrush" Value="#DFE5EE"/><Setter Property="BorderThickness" Value="1"/><Setter Property="Padding" Value="11,9"/>
            <Setter Property="VerticalContentAlignment" Value="Center"/><Setter Property="Template"><Setter.Value><ControlTemplate TargetType="TextBox">
              <Border x:Name="shell" Background="{TemplateBinding Background}" BorderBrush="{TemplateBinding BorderBrush}" BorderThickness="{TemplateBinding BorderThickness}" CornerRadius="6">
                <ScrollViewer x:Name="PART_ContentHost"/>
              </Border><ControlTemplate.Triggers><Trigger Property="IsKeyboardFocusWithin" Value="True"><Setter TargetName="shell" Property="BorderBrush" Value="#2563EB"/></Trigger>
              <Trigger Property="IsReadOnly" Value="True"><Setter TargetName="shell" Property="Background" Value="#F4F6FB"/></Trigger></ControlTemplate.Triggers>
            </ControlTemplate></Setter.Value></Setter>
          </Style>
          <Style TargetType="PasswordBox">
            <Setter Property="Background" Value="White"/><Setter Property="Foreground" Value="#172033"/><Setter Property="CaretBrush" Value="#2563EB"/>
            <Setter Property="BorderBrush" Value="#DFE5EE"/><Setter Property="BorderThickness" Value="1"/><Setter Property="Padding" Value="11,9"/>
            <Setter Property="Template"><Setter.Value><ControlTemplate TargetType="PasswordBox">
              <Border x:Name="shell" Background="{TemplateBinding Background}" BorderBrush="{TemplateBinding BorderBrush}" BorderThickness="{TemplateBinding BorderThickness}" CornerRadius="6">
                <ScrollViewer x:Name="PART_ContentHost"/>
              </Border><ControlTemplate.Triggers><Trigger Property="IsKeyboardFocusWithin" Value="True"><Setter TargetName="shell" Property="BorderBrush" Value="#2563EB"/></Trigger></ControlTemplate.Triggers>
            </ControlTemplate></Setter.Value></Setter>
          </Style>
        </ResourceDictionary>
        """));
    }

    internal static UIElement Icon(string name, Brush? color = null, double size = 17)
    {
        var data = name switch {
            "plus" => "M12,5 L12,19 M5,12 L19,12",
            "back" => "M14,5 L7,12 L14,19 M7,12 L21,12",
            "forward" => "M10,5 L17,12 L10,19 M3,12 L17,12",
            "reload" => "M20,8 A8,8 0 1 0 20,16 M20,3 L20,8 L15,8",
            "external" => "M13,4 L20,4 L20,11 M20,4 L10,14 M9,5 L4,5 L4,20 L19,20 L19,15",
            "close" => "M6,6 L18,18 M18,6 L6,18",
            "browser" => "M4,4 L20,4 L20,20 L4,20 Z M4,9 L20,9 M7,6 L8,6 M11,6 L12,6",
            "shield" => "M12,3 L20,6 L20,12 C20,17 15,20 12,22 C9,20 4,17 4,12 L4,6 Z M8,12 L11,15 L16,9",
            "settings" => "M4,6 L20,6 M4,12 L20,12 M4,18 L20,18 M8,3 L8,9 M16,9 L16,15 M10,15 L10,21",
            "diagnostic" => "M4,4 L20,4 L20,20 L4,20 Z M7,9 L10,12 L7,15 M13,16 L17,16",
            "download" => "M12,3 L12,16 M7,11 L12,16 L17,11 M4,17 L4,21 L20,21 L20,17",
            "play" => "M8,4 L20,12 L8,20 Z",
            _ => "M12,3 A9,9 0 1 1 11.99,3 M12,7 L12,13 M12,17 L12,17.1"
        };
        return new Viewbox { Width = size, Height = size, Child = new Path { Data = Geometry.Parse(data), Stroke = color ?? Ink, StrokeThickness = 1.7, StrokeStartLineCap = PenLineCap.Round, StrokeEndLineCap = PenLineCap.Round, StrokeLineJoin = PenLineJoin.Round, Width = 24, Height = 24 } };
    }
}
