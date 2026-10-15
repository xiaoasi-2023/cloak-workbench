using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace CloakWorkbench;

public sealed partial class MainWindow
{
    readonly Dictionary<string, JsonElement> environmentNetworkChecks = new();
    readonly HashSet<string> checkingEnvironmentNetworks = new();
    static string NetworkCountryCode(JsonElement result) {
        var code = Str(result, "countryCode"); if (code.Length == 0) code = Str(result, "country"); code = code.ToUpperInvariant();
        return code.Length == 2 ? code : code switch { "UNITED STATES" => "US", "UNITED KINGDOM" => "GB", "JAPAN" => "JP", "CANADA" => "CA", "SINGAPORE" => "SG", "AUSTRALIA" => "AU", "GERMANY" => "DE", "FRANCE" => "FR", "CHINA" => "CN", _ => "" };
    }
    static string NetworkCountryName(JsonElement result) {
        var code = NetworkCountryCode(result);
        var region = FingerprintRegions.FirstOrDefault(r => r.Locale.Split('-').Last() == code);
        return region?.Name.Split('·')[0].Trim() ?? (Str(result, "country").Length > 0 ? Str(result, "country") : "国家未识别");
    }
    JsonElement CatalogCheck(string id) {
        var entry = proxyCatalog.FirstOrDefault(p => Str(p, "id") == id);
        return entry.ValueKind == JsonValueKind.Object && entry.TryGetProperty("check", out var check) && check.ValueKind == JsonValueKind.Object ? check.Clone() : default;
    }
    JsonElement EnvironmentCheck(string id, JsonElement profile)
    {
        // Independent residential sessions can have a different exit from the
        // proxy inventory's temporary test. Prefer the actual account snapshot.
        if (profile.TryGetProperty("networkCheck", out var saved) && saved.ValueKind == JsonValueKind.Object) return saved;
        return default;
    }

    static void RenderNetworkResult(StackPanel panel, JsonElement result)
    {
        panel.Children.Clear();
        bool reachable = Str(result, "reachable").Equals("true", StringComparison.OrdinalIgnoreCase);
        var country = new TextBlock { Text = reachable ? NetworkCountryName(result) : "线路连接失败", FontSize = 21, FontWeight = FontWeights.SemiBold, Foreground = reachable ? Text : Hex("#E8B578") };
        System.Windows.Automation.AutomationProperties.SetName(country, "出口国家"); panel.Children.Add(country);
        var place = string.Join(" · ", new[] { Str(result, "region"), Str(result, "city") }.Where(s => s.Length > 0).Distinct());
        panel.Children.Add(new TextBlock { Text = reachable ? place.Length > 0 ? place : "区域 / 城市暂未查到" : "请检查代理认证、上游连接或稍后重试。", Foreground = Muted, FontSize = 12, Margin = new Thickness(0, 4, 0, 12), TextWrapping = TextWrapping.Wrap });
        if (!reachable) return;
        var facts = new Grid(); facts.ColumnDefinitions.Add(new ColumnDefinition()); facts.ColumnDefinitions.Add(new ColumnDefinition());
        var first = new StackPanel(); var second = new StackPanel(); Grid.SetColumn(second, 1); facts.Children.Add(first); facts.Children.Add(second); panel.Children.Add(facts);
        void Fact(Panel parent, string label, string value) { parent.Children.Add(new TextBlock { Text = label + "  " + (value.Length > 0 ? value : "暂未查到"), Foreground = Muted, FontSize = 11, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 0, 8, 5) }); }
        Fact(first, "出口时区", Str(result, "timezone")); Fact(first, "网络运营商", Str(result, "isp"));
        Fact(second, "出口 IP", Str(result, "egressIp")); Fact(second, "连接延迟", Str(result, "latencyMs") + " ms");
        panel.Children.Add(new TextBlock { Text = "检测时间  " + CheckTime(result) + (Str(result, "rotatingExit").Equals("true", StringComparison.OrdinalIgnoreCase) ? " · 出口有变化，展示本次快照" : ""), Foreground = Muted, FontSize = 10, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 7, 0, 0) });
    }

    async Task CheckEnvironmentNetwork(string id)
    {
        if (!checkingEnvironmentNetworks.Add(id)) return;
        environmentNetworkChecks.Remove(id); RenderAccounts(); Log("正在检测环境的实际出口国家…");
        try {
            var result = await Api($"profiles/{id}/proxy-test", new { });
            environmentNetworkChecks[id] = result.Clone();
            await LoadProxyCatalog();
            await Refresh();
            Log(Str(result, "reachable").Equals("true", StringComparison.OrdinalIgnoreCase) ? "出口国家：" + NetworkCountryName(result) + "。检测仅查询线路，地区配置可在环境配置中匹配。" : "代理连接失败，请检查线路与上游代理。");
        } catch (Exception ex) {
            environmentNetworkChecks[id] = JsonSerializer.SerializeToElement(new { reachable = false });
            Log("代理检测未完成：" + ex.Message);
        }
        finally { checkingEnvironmentNetworks.Remove(id); RenderAccounts(); }
    }
}
