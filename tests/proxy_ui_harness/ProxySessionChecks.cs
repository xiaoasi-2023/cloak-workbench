using System.IO;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using CloakWorkbench;

internal static partial class Program
{
    static async Task ProxySessions(MainWindow main, HttpClient client, string output)
    {
        async Task<JsonElement> Post(string path, object payload) {
            using var response = await client.PostAsync(path, new StringContent(JsonSerializer.Serialize(payload), Encoding.UTF8, "application/json"));
            response.EnsureSuccessStatusCode(); using var doc = JsonDocument.Parse(await response.Content.ReadAsStringAsync()); return doc.RootElement.Clone();
        }
        var saved = await Post("proxies", new { id = "ui-session-route", name = "住宅会话回归", kind = "residential", proxy = "http://gateway.invalid:443", proxyUsername = "fixture-region-US-asn-AS64500", proxyPassword = "isolated-terminal-secret", upstreamProxy = "http://127.0.0.1:7897", upstreamProxyUsername = "upstream-fixture", upstreamProxyPassword = "isolated-upstream-secret" });
        var editor = (Window)Method("CreateProxyEditor").Invoke(main, new object?[] { main, saved })!;
        editor.Show(); await Task.Delay(100);
        var mode = Named<ComboBox>(editor, "住宅会话模式");
        var template = Named<TextBox>(editor, "供应商用户名模板");
        Check("Residential sessions default to fixed original provider authentication", (string)mode.SelectedValue == "fixed");
        Check("Template is hidden until explicit independent-session opt-in", !template.IsVisible);
        var originalUser = Named<TextBox>(editor, "代理用户名 / 会话用户名").Text;
        var originalUpstream = Named<TextBox>(editor, "上游代理地址").Text;
        mode.SelectedValue = "per_profile"; editor.UpdateLayout();
        Check("Opt-in reveals provider template without inventing a provider format", template.IsVisible && template.Text == "");
        Check("Changing session mode never changes base username or upstream", Named<TextBox>(editor, "代理用户名 / 会话用户名").Text == originalUser && Named<TextBox>(editor, "上游代理地址").Text == originalUpstream);
        Check("Session lifetime defaults to no automatic rotation", (string)Named<ComboBox>(editor, "住宅会话有效期").SelectedValue == "0");
        template.Text = "{user}-session-{session}";
        Named<ComboBox>(editor, "住宅会话有效期").SelectedValue = "60";
        template.BringIntoView(); await Task.Delay(100); Capture(editor, Path.Combine(output, "proxy-session-editor.png"));
        Click(editor, "保存代理"); await Wait(() => !editor.IsVisible);
        using (var all = JsonDocument.Parse(await client.GetStringAsync("proxies"))) {
            var entry = all.RootElement.GetProperty("proxies").EnumerateArray().Single(p => p.GetProperty("id").GetString() == "ui-session-route");
            Check("Editor persists explicit template and lifetime", entry.GetProperty("sessionTemplate").GetString() == "{user}-session-{session}" && entry.GetProperty("sessionLifetimeMinutes").GetInt32() == 60);
            Check("Saving session policy preserves original terminal username", entry.GetProperty("proxyUsername").GetString() == originalUser);
            Check("Saving session policy retains authenticated upstream unchanged", entry.GetProperty("upstreamProxy").GetString() == originalUpstream && entry.GetProperty("upstreamProxyUsername").GetString() == "upstream-fixture" && entry.GetProperty("upstreamProxyPasswordSet").GetBoolean());
            Check("Session policy response does not expose proxy passwords", !entry.GetRawText().Contains("isolated-terminal-secret") && !entry.GetRawText().Contains("isolated-upstream-secret"));
        }
        await Post("profiles", new { id = "ui-sticky-one", name = "会话甲", proxyId = "ui-session-route" });
        await Post("profiles", new { id = "ui-sticky-two", name = "会话乙", proxyId = "ui-session-route" });
        var first = await Post("profiles/ui-sticky-one/proxy-session/rotate", new { });
        var second = await Post("profiles/ui-sticky-two/proxy-session/rotate", new { });
        Check("Each environment receives an independent residential session", first.GetProperty("session").GetString() != second.GetProperty("session").GetString());
        var rotated = await Post("profiles/ui-sticky-one/proxy-session/rotate", new { });
        Check("Manual rotation replaces only the chosen environment session", rotated.GetProperty("session").GetString() != first.GetProperty("session").GetString());
        using (var details = JsonDocument.Parse(await client.GetStringAsync("profiles/ui-sticky-two/details"))) {
            Check("Second environment session survives first environment rotation", details.RootElement.GetProperty("proxySession").GetProperty("session").GetString() == second.GetProperty("session").GetString());
        }
        var fresh = (Window)Method("CreateProxyEditor").Invoke(main, new object?[] { main, null })!;
        fresh.Show(); await Task.Delay(100);
        Named<ComboBox>(fresh, "代理类型").SelectedValue = "standard";
        Check("Ordinary proxy cannot opt into residential sessions", !Named<ComboBox>(fresh, "住宅会话模式").IsEnabled && (string)Named<ComboBox>(fresh, "住宅会话模式").SelectedValue == "fixed");
        fresh.Close();
    }
}
