using System.IO;
using System.Net.Http;
using System.Reflection;
using System.Text;
using System.Text.Json;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using CloakWorkbench;

internal static partial class Program
{
    static string Choice(Window window, string label) => (string)Named<ComboBox>(window,label).SelectedValue;
    static async Task ScrollFingerprint(Window window,TextBlock heading) {
        var scroll=Nodes<ScrollViewer>(window).Single(s=>s.Content is StackPanel);
        scroll.ScrollToVerticalOffset(heading.TransformToAncestor((System.Windows.Media.Visual)scroll.Content).Transform(new Point()).Y-18);await Task.Delay(120);
    }
    static async Task Fingerprints(MainWindow main,HttpClient client,string output)
    {
        var editor=(Window)Method("CreateProfileDialog").Invoke(main,new object?[]{null})!;editor.Show();await Task.Delay(120);
        var fingerprint=Nodes<TextBlock>(editor).Single(t=>t.Text=="浏览器指纹");await ScrollFingerprint(editor,fingerprint);
        Check("Common fingerprint settings use fixed dropdowns",new[]{"地区方案","语言","时区","系统平台","逻辑处理器数","内存","屏幕分辨率"}.All(label=>Named<ComboBox>(editor,label).Items.Count>1&&!Named<ComboBox>(editor,label).IsEditable));
        Check("Timezone dropdown includes global options",Named<ComboBox>(editor,"时区").Items.Count>80);
        Named<ComboBox>(editor,"地区方案").SelectedValue="3";
        Check("Japan preset applies paired locale and timezone",Choice(editor,"语言")=="ja-JP"&&Choice(editor,"时区")=="Asia/Tokyo");
        Named<ComboBox>(editor,"时区").SelectedValue="Europe/London";
        Check("Independent timezone choice marks custom region",Choice(editor,"地区方案")=="");
        Named<ComboBox>(editor,"地区方案").SelectedValue="3";
        var advanced=Nodes<Expander>(editor).Single(e=>e.Header is string h&&h.StartsWith("高级设置"));advanced.IsExpanded=true;editor.UpdateLayout();
        var seed=Named<TextBox>(editor,"固定指纹种子");var initialSeed=seed.Text;
        Named<TextBox>(editor,"WebGL 厂商（留空自动）").Text="Previous custom vendor";
        Named<TextBox>(editor,"WebGL 渲染器（留空自动）").Text="Previous custom renderer";
        var expectedProxy=Named<ComboBox>(editor,"环境代理选择").SelectedValue;
        Click(editor,"一键随机指纹");
        Check("Random action creates a fresh valid seed",seed.Text!=initialSeed&&int.TryParse(seed.Text,out var n)&&n>0);
        Check("Random action retains region and platform",Choice(editor,"语言")=="ja-JP"&&Choice(editor,"时区")=="Asia/Tokyo"&&Choice(editor,"系统平台")=="windows");
        Check("Random action retains environment and proxy fields",Equals(expectedProxy,Named<ComboBox>(editor,"环境代理选择").SelectedValue)&&Named<TextBox>(editor,"环境名称").Text=="新环境"&&Named<TextBox>(editor,"启动网址").Text=="https://www.dola.com/");
        Check("Random GPU uses kernel and new seed",Named<TextBox>(editor,"WebGL 厂商（留空自动）").Text==""&&Named<TextBox>(editor,"WebGL 渲染器（留空自动）").Text=="");
        var seeds=new HashSet<string>();
        for(int i=0;i<50;i++) {
            Click(editor,"一键随机指纹");
            CheckRandom(seeds.Add(seed.Text),"Duplicate generated seed");
            CheckRandom(new[]{"4","8","12","16"}.Contains(Choice(editor,"逻辑处理器数")),"Invalid generated CPU");
            CheckRandom(new[]{"4","8"}.Contains(Choice(editor,"内存")),"Invalid generated memory");
            CheckRandom(Choice(editor,"屏幕分辨率").Split('x').Length==2,"Unpaired generated screen");
            CheckRandom(Choice(editor,"语言")=="ja-JP"&&Choice(editor,"时区")=="Asia/Tokyo","Random changed selected geography");
        }
        Check("Repeated generation keeps valid paired options",true);
        advanced.IsExpanded=false;editor.UpdateLayout();await ScrollFingerprint(editor,fingerprint);Capture(editor,Path.Combine(output,"fingerprint-dropdowns.png"));
        Check("Dropdown selection displays readable label",Nodes<TextBlock>(Named<ComboBox>(editor,"语言")).Any(t=>t.Text=="ja-JP  ·  日本")&&!Nodes<TextBlock>(editor).Any(t=>t.Text.Contains("FingerprintChoice")));
        editor.Width=710;editor.Height=600;editor.UpdateLayout();await ScrollFingerprint(editor,fingerprint);Capture(editor,Path.Combine(output,"fingerprint-dropdowns-min.png"));editor.Width=820;editor.Height=790;
        var language=Named<ComboBox>(editor,"语言");language.BringIntoView();language.IsDropDownOpen=true;await Task.Delay(120);
        Check("Custom rounded dropdown opens real selectable items",language.IsDropDownOpen&&language.ItemContainerGenerator.ContainerFromIndex(0) is ComboBoxItem);
        language.IsDropDownOpen=false;
        string expectedSeed=seed.Text, expectedCpu=Choice(editor,"逻辑处理器数"),expectedMemory=Choice(editor,"内存"),expectedScreen=Choice(editor,"屏幕分辨率");
        Named<TextBox>(editor,"环境名称").Text="Isolated generated fingerprint";Named<TextBox>(editor,"启动网址").Text="about:blank";
        Click(editor,"保存环境");await Wait(()=>!editor.IsVisible);
        using var catalog=JsonDocument.Parse(await client.GetStringAsync("profiles"));var saved=catalog.RootElement.GetProperty("profiles").EnumerateArray().Single(p=>p.GetProperty("name").GetString()=="Isolated generated fingerprint").Clone();
        string id=saved.GetProperty("id").GetString()!;
        Check("Generated options persist as native typed profile fields",saved.GetProperty("seed").GetInt32().ToString()==expectedSeed&&saved.GetProperty("hardwareConcurrency").GetInt32().ToString()==expectedCpu&&saved.GetProperty("deviceMemory").GetDouble().ToString()==expectedMemory&&saved.GetProperty("fingerprintScreenWidth").GetInt32().ToString()+"x"+saved.GetProperty("fingerprintScreenHeight").GetInt32().ToString()==expectedScreen);
        await (Task)Method("Launch").Invoke(main,new object[]{id})!;
        using var report=JsonDocument.Parse(await client.GetStringAsync($"profiles/{id}/environment"));
        var fields=new[]{"locale","timezone","fingerprintPlatform","hardwareConcurrency","deviceMemory","fingerprintScreenWidth","fingerprintScreenHeight","taskbarHeight"};
        var verified=report.RootElement.GetProperty("checks").EnumerateArray().Where(c=>fields.Contains(c.GetProperty("field").GetString())).ToArray();
        Check("Generated fingerprint reaches real browser page",verified.Length==fields.Length&&verified.All(c=>c.GetProperty("status").GetString()=="match"));
        var canvas=report.RootElement.GetProperty("actual").GetProperty("canvasSHA256").GetString();
        await (Task)Method("CloseProfile").Invoke(main,new object[]{id})!;
        await (Task)Method("Launch").Invoke(main,new object[]{id})!;
        using var reopened=JsonDocument.Parse(await client.GetStringAsync($"profiles/{id}/environment"));
        Check("Generated seed remains stable after browser restart",canvas==reopened.RootElement.GetProperty("actual").GetProperty("canvasSHA256").GetString());
        await (Task)Method("CloseProfile").Invoke(main,new object[]{id})!;
        await (Task)Method("Refresh").Invoke(main,null)!;
        var edit=(Window)Method("CreateProfileDialog").Invoke(main,new object?[]{id})!;edit.Show();await Task.Delay(120);
        Check("Editing reopens saved dropdown values without regeneration",Choice(edit,"语言")=="ja-JP"&&Choice(edit,"时区")=="Asia/Tokyo"&&Choice(edit,"屏幕分辨率")==expectedScreen);
        var adv=Nodes<Expander>(edit).Single(e=>e.Header is string h&&h.StartsWith("高级设置"));adv.IsExpanded=true;edit.UpdateLayout();
        Check("Editing retains stable saved seed",Named<TextBox>(edit,"固定指纹种子").Text==expectedSeed);
        Named<ComboBox>(edit,"系统平台").SelectedValue="macos";Click(edit,"一键随机指纹");
        Check("macOS randomization uses platform-specific options",new[]{"8","10","12"}.Contains(Choice(edit,"逻辑处理器数"))&&Choice(edit,"内存")=="8"&&Choice(edit,"任务栏高度")=="25");edit.Close();
        var capField=typeof(MainWindow).GetField("capabilities",BindingFlags.NonPublic|BindingFlags.Instance)!;var original=capField.GetValue(main);
        using var unsupported=JsonDocument.Parse("{\"supportedFields\":{\"hardwareConcurrency\":false,\"deviceMemory\":false,\"fingerprintScreenWidth\":false,\"fingerprintScreenHeight\":false,\"fingerprintPlatform\":false,\"fingerprintNoise\":false,\"gpuVendor\":false,\"gpuRenderer\":false,\"taskbarHeight\":false}}");
        capField.SetValue(main,unsupported.RootElement.Clone());
        try {
            var disabled=(Window)Method("CreateProfileDialog").Invoke(main,new object?[]{null})!;disabled.Show();await Task.Delay(80);Click(disabled,"一键随机指纹");
            Check("Randomization respects unavailable kernel capabilities",!Named<ComboBox>(disabled,"逻辑处理器数").IsEnabled&&Choice(disabled,"逻辑处理器数")==""&&!Named<ComboBox>(disabled,"屏幕分辨率").IsEnabled&&Choice(disabled,"屏幕分辨率")=="");disabled.Close();
        } finally {capField.SetValue(main,original);}
        using var changed=await client.PostAsync("profiles",new StringContent(JsonSerializer.Serialize(new{id,locale="fr-CH",timezone="Europe/Zurich",hardwareConcurrency=7,deviceMemory=.5,fingerprintScreenWidth=1700,fingerprintScreenHeight=1000,taskbarHeight=33,gpuVendor="Custom preserved vendor",gpuRenderer="Custom preserved renderer"}),Encoding.UTF8,"application/json"));changed.EnsureSuccessStatusCode();
        await (Task)Method("Refresh").Invoke(main,null)!;
        var custom=(Window)Method("CreateProfileDialog").Invoke(main,new object?[]{id})!;custom.Show();await Task.Delay(80);
        Check("Unlisted saved options remain selectable",Choice(custom,"语言")=="fr-CH"&&Choice(custom,"时区")=="Europe/Zurich"&&Choice(custom,"逻辑处理器数")=="7"&&Choice(custom,"内存")=="0.5"&&Choice(custom,"屏幕分辨率")=="1700x1000");
        Click(custom,"保存环境");await Wait(()=>!custom.IsVisible);
        using var customSaved=JsonDocument.Parse(await client.GetStringAsync("profiles"));var preserved=customSaved.RootElement.GetProperty("profiles").EnumerateArray().Single(p=>p.GetProperty("id").GetString()==id);
        Check("Saving unchanged custom options preserves values",preserved.GetProperty("locale").GetString()=="fr-CH"&&preserved.GetProperty("hardwareConcurrency").GetInt32()==7&&preserved.GetProperty("taskbarHeight").GetInt32()==33&&preserved.GetProperty("gpuRenderer").GetString()=="Custom preserved renderer");
    }
    static void CheckRandom(bool condition,string error){if(!condition)throw new Exception(error);}
}
