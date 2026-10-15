using System.Net.Http;
using System.IO;
using System.Reflection;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;

internal static partial class Program
{
    static async Task ProxyGeography(CloakWorkbench.MainWindow main,HttpClient client,string output)
    {
        var editor=(Window)Method("CreateProfileDialog").Invoke(main,new object?[]{null})!;editor.Show();await Task.Delay(100);
        Named<TextBox>(editor,"环境名称").Text="Isolated geography fixture";
        Named<ComboBox>(editor,"环境代理选择").SelectedValue="ui-standard";await Task.Delay(100);
        Named<ComboBox>(editor,"环境代理选择").SelectedValue="ui-residential";
        await Wait(()=>!Named<Button>(editor,"检测出口国家与网络").IsEnabled);
        Check("Proxy selection shows country lookup activity",Named<Border>(editor,"环境网络检测中").IsVisible&&!Named<Button>(editor,"保存环境").IsEnabled);
        Capture(editor,Path.Combine(output,"environment-country-checking.png"));
        await Wait(()=>Named<Button>(editor,"检测出口国家与网络").IsEnabled);
        Check("Country card shows readable country and actual city",Named<TextBlock>(editor,"出口国家").Text=="美国"&&Nodes<TextBlock>(editor).Any(t=>t.Text.Contains("Dallas")));
        Check("Proxy geography automatically applies correct US time zone",Choice(editor,"语言")=="en-US"&&Choice(editor,"时区")=="America/Chicago"&&Choice(editor,"地区方案")=="7");
        Check("Region mode stays automatic after applying network",Named<CheckBox>(editor,"自动匹配出口地区").IsChecked==true&&Named<TextBlock>(editor,"出口地区推荐").Text.StartsWith("已按出口匹配"));
        await Task.Delay(1500);
        Check("Late result for previous proxy never overwrites selected route geography",Named<TextBlock>(editor,"出口国家").Text=="美国"&&Choice(editor,"时区")=="America/Chicago");
        Named<ComboBox>(editor,"地区方案").SelectedValue="3";
        Check("Manual region choice disables automatic overwrite",Named<CheckBox>(editor,"自动匹配出口地区").IsChecked==false);
        Click(editor,"检测出口国家与网络");await Wait(()=>Named<Button>(editor,"检测出口国家与网络").IsEnabled);
        Check("Network recheck respects manually selected geography",Choice(editor,"语言")=="ja-JP"&&Choice(editor,"时区")=="Asia/Tokyo");
        Named<CheckBox>(editor,"自动匹配出口地区").IsChecked=true;
        Check("Reenabling automatic mode applies latest proven exit",Choice(editor,"语言")=="en-US"&&Choice(editor,"时区")=="America/Chicago");
        // Feed country-only evidence through the production binding to verify
        // that multiple US time zones are never guessed from country alone.
        var networkField=typeof(CloakWorkbench.MainWindow).GetMethod("BuildFingerprintEditor",BindingFlags.NonPublic|BindingFlags.Instance)!;
        var detachedPanel=new StackPanel();Func<string,string,string> read=(key,fallback)=>fallback;
        var binding=networkField.Invoke(main,new object[]{detachedPanel,read})!;
        var recommend=(Action<JsonElement,bool>)binding.GetType().GetProperty("Recommend")!.GetValue(binding)!;
        using(var incomplete=JsonDocument.Parse("{\"reachable\":true,\"country\":\"US\"}")) recommend(incomplete.RootElement,true);
        var detachedZone=Nodes<ComboBox>(detachedPanel).Single(c=>System.Windows.Automation.AutomationProperties.GetName(c)=="时区");
        Check("Country-only evidence does not guess multi-zone country time zone",detachedZone.SelectedValue as string=="Asia/Shanghai"&&Nodes<TextBlock>(detachedPanel).Any(t=>t.Text.Contains("多个时区")));
        using(var unsupported=JsonDocument.Parse("{\"reachable\":true,\"country\":\"ZZ\",\"timezone\":\"UTC\"}")) recommend(unsupported.RootElement,true);
        Check("Unsupported country retains existing geography",detachedZone.SelectedValue as string=="Asia/Shanghai");
        var scroll=Nodes<ScrollViewer>(editor).Single(s=>s.Content is StackPanel);scroll.ScrollToVerticalOffset(180);await Task.Delay(80);Capture(editor,Path.Combine(output,"environment-country-result.png"));
        Click(editor,"保存环境");await Wait(()=>!editor.IsVisible);
        using var saved=JsonDocument.Parse(await client.GetStringAsync("profiles"));
        var profile=saved.RootElement.GetProperty("profiles").EnumerateArray().Single(p=>p.GetProperty("name").GetString()=="Isolated geography fixture");
        Check("Automatic geography and source mode persist together",profile.GetProperty("locale").GetString()=="en-US"&&profile.GetProperty("timezone").GetString()=="America/Chicago"&&profile.GetProperty("regionMode").GetString()=="proxy");
        var id=profile.GetProperty("id").GetString()!;
        Method("ShowAccountDetails").Invoke(main,new object[]{id});
        await Wait(()=>Nodes<Button>(main).Any(b=>b.Content as string=="检测 Dola 登录"));
        Click(main,"代理");main.UpdateLayout();
        Click(main,"检测此环境的代理出口");
        await Wait(()=>(bool)typeof(CloakWorkbench.MainWindow).GetField("centerBusy",BindingFlags.NonPublic|BindingFlags.Instance)!.GetValue(main)!);
        Check("Environment list exposes inline diagnostic loading",main.OwnedWindows.Count==0&&Nodes<ProgressBar>(main).Any(p=>p.IsVisible));
        await Wait(()=>!(bool)typeof(CloakWorkbench.MainWindow).GetField("centerBusy",BindingFlags.NonPublic|BindingFlags.Instance)!.GetValue(main)!);
        await Task.Delay(150);Click(main,"代理");main.UpdateLayout();
        Check("Environment list exposes country without opening editor",Nodes<TextBlock>(main).Any(t=>t.Text=="美国")&&Nodes<TextBlock>(main).Any(t=>t.Text=="Dallas"));
        Capture(main,Path.Combine(output,"environment-list-country.png"));
    }
}
