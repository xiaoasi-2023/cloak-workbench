using System.IO;
using System.Net.Http;
using System.Reflection;
using System.Text;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using CloakWorkbench;

partial class Program
{
    static async Task SessionBackup(MainWindow main, HttpClient client, string output)
    {
        await (Task)Method("LoadProxyCatalog").Invoke(main,null)!;
        await (Task)Method("Refresh").Invoke(main,null)!;
        await (Task)Method("Launch").Invoke(main,new object[]{"ui-browser"})!;
        var before=await BrowserState(client);
        Method("ShowSessionBackup").Invoke(main,new object?[]{"ui-browser"});
        await Wait(()=>Nodes<TextBlock>(main).Any(t=>t.Text.StartsWith("暂无本机备份")));
        Check("Session backup page stays inside main window",main.OwnedWindows.Count==0 && Named<Button>(main,"备份当前登录").IsVisible);
        var proxy=Named<ComboBox>(main,"恢复环境代理");
        Check("Restore proxy picker includes managed proxies",proxy.Items.Count==5);
        Check("Session export defaults to Windows encrypted backup",Named<ComboBox>(main,"会话导出格式").SelectedValue as string=="encrypted");
        Check("Import requires a prepared session preview",!Named<Button>(main,"确认创建并恢复").IsEnabled);
        Click(main,"备份当前登录");
        await Wait(()=>Named<Button>(main,"导出备份文件").IsEnabled);
        Check("Real browser backup reports cookie metadata without displaying secrets",Named<TextBlock>(main,"会话备份摘要").Text.Contains("1 个 Cookie") && !Nodes<TextBlock>(main).Any(t=>t.Text.Contains("isolated-ui-session-token")));
        var root=Environment.GetEnvironmentVariable("CLOAK_WORKBENCH_ROOT")!;
        var encrypted=File.ReadAllText(Path.Combine(root,"data","session-backups","ui-browser.json"));
        Check("On-disk UI backup encrypts Cookie and website storage",!encrypted.Contains("isolated-ui-session-token")&&!encrypted.Contains("isolated-ui-storage"));
        Click(main,"从本机备份准备恢复"); await Wait(()=>Named<Button>(main,"确认创建并恢复").IsEnabled);
        Check("Local restore previews actual Cookie count and validation requirement",Named<TextBlock>(main,"CK 导入预览").Text.Contains("1 个 Dola Cookie")&&Named<TextBlock>(main,"CK 导入预览").Text.Contains("仍需真实"));
        Named<TextBox>(main,"恢复环境名称").Text="Restored isolated session";
        proxy.SelectedValue="ui-standard";
        Click(main,"代理管理"); Method("ShowSessionBackup").Invoke(main,new object?[]{"ui-browser"}); await Task.Delay(150);
        Check("Page navigation retains session preview and proxy choice",Named<TextBox>(main,"恢复环境名称").Text=="Restored isolated session"&&ReferenceEquals(proxy,Named<ComboBox>(main,"恢复环境代理"))&&proxy.SelectedValue as string=="ui-standard");
        main.Width=1050;main.Height=680;await Task.Delay(120);main.UpdateLayout();
        var confirm=Named<Button>(main,"确认创建并恢复");var bottom=confirm.TransformToAncestor(main).Transform(new Point(confirm.ActualWidth,confirm.ActualHeight));
        Check("Minimum window pins session import action inside viewport",bottom.Y<=main.ActualHeight&&bottom.X<=main.ActualWidth);
        Capture(main,Path.Combine(output,"session-backup-min.png"));
        Click(main,"确认创建并恢复");
        Check("Pending restore disables duplicate submission",!confirm.IsEnabled&&!Named<TextBox>(main,"恢复环境名称").IsEnabled);
        await Wait(()=>Named<Button>(main,"打开新环境").IsVisible && Named<Button>(main,"打开新环境").IsEnabled);
        Check("Restore distinguishes applied session from confirmed Dola login",Named<TextBlock>(main,"会话操作结果").Text.Contains("登录已确认"));
        using var inventory=JsonDocument.Parse(await client.GetStringAsync("profiles"));
        var restored=inventory.RootElement.GetProperty("profiles").EnumerateArray().Single(p=>p.GetProperty("name").GetString()=="Restored isolated session");
        string restoredId=restored.GetProperty("id").GetString()!;
        Check("Import creates new managed profile with explicitly selected proxy",restoredId!="ui-browser"&&restored.GetProperty("proxyId").GetString()=="ui-standard");
        Check("Original account process remains unchanged after restore",(await BrowserState(client)).GetProperty("pid").GetInt32()==before.GetProperty("pid").GetInt32());
        Click(main,"检测恢复后的登录");await Wait(()=>Named<Button>(main,"检测恢复后的登录").IsEnabled);
        Check("Restored login can be checked again without submitting chat",Named<TextBlock>(main,"会话操作结果").Text.Contains("已确认登录"));
        main.Width=1440;main.Height=900;await Task.Delay(120);Capture(main,Path.Combine(output,"session-backup-desktop.png"));
        Click(main,"打开新环境");await Task.Delay(300);
        var hosts=(Dictionary<string,NativeBrowserHost>)typeof(MainWindow).GetField("hosts",BindingFlags.NonPublic|BindingFlags.Instance)!.GetValue(main)!;
        Check("Open restored account uses real embedded browser",hosts.ContainsKey(restoredId)&&IsWindowVisible(hosts[restoredId].AttachedHandle));
        Click(main,"批量管理");Click(main,"导入 CK");await Wait(()=>Nodes<TextBlock>(main).Any(t=>t.Text.Contains("可导入 Cookie JSON")));
        Check("Account center offers independent CK import without modal",main.OwnedWindows.Count==0&&!Named<Button>(main,"备份当前登录").IsVisible);
        Check("Public account inventory and desktop log contain no session token",!(await client.GetStringAsync("profiles")).Contains("isolated-ui-session-token")&&!File.ReadAllText(Path.Combine(root,".local","desktop.log")).Contains("isolated-ui-session-token"));
    }
}
