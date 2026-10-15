using System.Net.Http;
using System.Reflection;
using System.Text.Json;
using System.Windows.Controls;
using CloakWorkbench;

partial class Program
{
    static async Task EnvironmentSemantics(MainWindow main, HttpClient client, string output)
    {
        object Call(string name, JsonElement profile) => typeof(MainWindow).GetMethod(name, BindingFlags.NonPublic | BindingFlags.Static)!.Invoke(null, new object[] { profile })!;
        JsonElement Profile(string state, bool current, string status = "logged_in", bool binding = true) => JsonSerializer.SerializeToElement(new {
            state, accountCheck = new { status = current ? status : "unknown", isCurrent = current, checkedAt = current ? "2026-10-15T01:00:00Z" : null },
            lastAccountCheck = new { status, checkedAt = "2026-10-14T01:00:00Z", routeMatches = true },
            loginAccount = binding ? new { email = "fixture@example.invalid" } : null,
            networkCheck = new { reachable = true, checkedAt = "2026-10-15T03:00:00Z" }
        });
        var historical = Profile("stopped", false);
        Check("Closed environment exposes historical login instead of online state", (string)Call("LoginObservationLabel", historical) == "上次已登录" && !(bool)Call("IsCurrentLoginObservation", historical));
        var reopened = Profile("running", false);
        Check("Reopened environment cannot inherit a current positive login", !(bool)Call("IsCurrentLoginObservation", reopened) && (string)Call("LoginObservationLabel", reopened) == "上次已登录");
        var active = Profile("running", true);
        Check("Current runtime page evidence can confirm Dola login", (bool)Call("IsCurrentLoginObservation", active) && (string)Call("LoginObservationLabel", active) == "Dola 已登录");
        Check("Confirmed site session never claims the bound Google identity", (string)Call("LoginIdentityLabel", active) == "绑定邮箱未核验");
        Check("Unbound manually logged-in environment remains a valid session", (string)Call("LoginObservationLabel", Profile("running", true, binding: false)) == "Dola 已登录" && (string)Call("LoginIdentityLabel", Profile("running", true, binding: false)) == "网站身份未核验");
        Check("Recent network test cannot change login observation timestamp", (string)Call("AccountObservationTime", historical) == (string)Call("LoginObservationTime", historical) && ((string)Call("AccountObservationTime", historical)).StartsWith("10/14"));
        var empty = JsonSerializer.SerializeToElement(new { state = "running" });
        Check("Missing login evidence stays pending without exceptions", (string)Call("LoginObservationLabel", empty) == "本次待检测" && (string)Call("LoginObservationTime", empty) == "未检测");
        Check("Login-required page remains a distinct actionable state", (string)Call("LoginObservationLabel", Profile("running", true, "login_required")) == "需要登录");
        Check("Region restriction remains distinct from a missing login", (string)Call("LoginObservationLabel", Profile("running", true, "region_blocked")) == "地区受限");

        await (Task)Method("Refresh").Invoke(main, null)!;
        Method("ShowAccountCenter").Invoke(main, null); main.UpdateLayout();
        var table = Named<DataGrid>(main, "环境管理列表");
        Check("Environment list distinguishes login and network observation times", table.Columns.Any(c => Equals(c.Header, "登录检测时间")) && table.Columns.Any(c => Equals(c.Header, "网络检测时间")) && !table.Columns.Any(c => Equals(c.Header, "最近检测")));
        Method("ShowAccountDetails").Invoke(main, new object[] { "ui-browser" });
        await Wait(() => Nodes<Button>(main).Any(b => Equals(b.Content, "检测 Dola 登录")));
        Click(main, "账号"); main.UpdateLayout();
        Check("Unbound environment guides manual or Google login without a failure badge", Nodes<TextBlock>(main).Any(t => t.Text == "手动登录环境") && Nodes<Button>(main).Any(b => Equals(b.Content, "通过 Google 登录 Dola")));
        Click(main, "代理"); main.UpdateLayout();
        Check("Actual environment proxy test remains directly accessible", Named<Button>(main, "检测此环境的代理出口").IsEnabled);
        Click(main, "指纹"); main.UpdateLayout();
        Check("Fingerprint page explains stable saved configuration", Nodes<TextBlock>(main).Any(t => t.Text.Contains("检测代理不会自动更改")));
        Click(main, "登录备份"); main.UpdateLayout();
        Check("Backup recovery explicitly creates a new environment", Nodes<TextBlock>(main).Any(t => t.Text.Contains("使用备份创建新的恢复环境")));
        Click(main, "概览"); main.UpdateLayout();
        Check("Closed environment shows reason login check is unavailable", !Named<Button>(main, "检测 Dola 登录").IsEnabled && Nodes<TextBlock>(main).Any(t => t.Text.Contains("先打开浏览器")));
        Check("Environment details and sections stay inside main window", main.OwnedWindows.Count == 0);
    }
}
