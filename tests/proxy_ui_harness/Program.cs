using System.IO;
using System.Net.Http;
using System.Net.WebSockets;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using CloakWorkbench;

internal static partial class Program
{
    static readonly List<string> Checks = new();
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr hwnd);
    static MethodInfo Method(string name) => typeof(MainWindow).GetMethod(name, BindingFlags.NonPublic | BindingFlags.Instance)!;
    static IEnumerable<T> Nodes<T>(DependencyObject root) where T : DependencyObject {
        if (root is T found) yield return found;
        for (int i = 0; i < VisualTreeHelper.GetChildrenCount(root); i++)
            foreach (var item in Nodes<T>(VisualTreeHelper.GetChild(root, i))) yield return item;
    }
    static T Named<T>(Window window, string name) where T : DependencyObject => Nodes<T>(window).Single(n => AutomationProperties.GetName(n) == name || n is Button b && b.Content is string s && s == name);
    static void Check(string label, bool condition) { if (!condition) throw new Exception(label); Checks.Add(label); }
    static async Task Wait(Func<bool> condition) { for(int i=0;i<100;i++) { if(condition()) return; await Task.Delay(80); } throw new Exception("UI did not reach expected state"); }
    static void Click(Window window, string name) => Named<Button>(window, name).RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
    static string RowState(object row) => (string)row.GetType().GetProperty("CheckState")!.GetValue(row)!;
    static void Capture(Window window, string path) {
        window.UpdateLayout(); var image = new RenderTargetBitmap((int)window.ActualWidth, (int)window.ActualHeight, 96, 96, PixelFormats.Pbgra32);
        image.Render(window); var encoder = new PngBitmapEncoder(); encoder.Frames.Add(BitmapFrame.Create(image)); using var stream=File.Create(path); encoder.Save(stream);
    }
    static async Task<JsonElement> BrowserState(HttpClient client) {
        using var doc=JsonDocument.Parse(await client.GetStringAsync("profiles"));
        return doc.RootElement.GetProperty("profiles").EnumerateArray().Single(p=>p.GetProperty("id").GetString()=="ui-browser").Clone();
    }
    static async Task<JsonElement> Evaluate(int port,string expression) {
        using var browser=new HttpClient(); using var targets=JsonDocument.Parse(await browser.GetStringAsync($"http://127.0.0.1:{port}/json/list"));
        var target=targets.RootElement.EnumerateArray().Single(t=>t.GetProperty("type").GetString()=="page");
        using var socket=new ClientWebSocket();await socket.ConnectAsync(new Uri(target.GetProperty("webSocketDebuggerUrl").GetString()!),CancellationToken.None);
        await socket.SendAsync(Encoding.UTF8.GetBytes(JsonSerializer.Serialize(new{id=1,method="Runtime.evaluate",@params=new{expression,returnByValue=true}})),WebSocketMessageType.Text,true,CancellationToken.None);
        var buffer=new byte[65536];using var assembled=new MemoryStream();
        for(;;) { var received=await socket.ReceiveAsync(buffer,CancellationToken.None);assembled.Write(buffer,0,received.Count);if(!received.EndOfMessage)continue;
            using var result=JsonDocument.Parse(assembled.ToArray());if(result.RootElement.TryGetProperty("id",out var id)&&id.GetInt32()==1)return result.RootElement.GetProperty("result").GetProperty("result").GetProperty("value").Clone();assembled.SetLength(0); }
    }
    [STAThread]
    static int Main(string[] args) {
        var app = new Application { ShutdownMode = ShutdownMode.OnExplicitShutdown }; int exit=1;
        string output=args[1]; Directory.CreateDirectory(output);File.Delete(Path.Combine(output,"ui-error.txt"));
        var main = new MainWindow(false);
        var client=(HttpClient)typeof(MainWindow).GetField("api",BindingFlags.NonPublic|BindingFlags.Instance)!.GetValue(main)!;
        client.BaseAddress=new Uri(args[0]);
        main.Loaded += async (_,_) => {
            try {
                if (args.Contains("--configuration-transfer")) { await ConfigurationTransfer(main,client,output); exit=0; return; }
                if (args.Contains("--videos")) { await Videos(main,client,output); exit=0; return; }
                if (args.Contains("--feedback")) { await Feedback(main,output); exit=0; return; }
                if (args.Contains("--workspaces")) { await Workspaces(main,client,output); exit=0; return; }
                if (args.Contains("--lifecycle")) { await BrowserLifecycle(main,client); exit=0; return; }
                if (args.Contains("--browser-workspace")) { await BrowserWorkspace(main,client,output); exit=0; return; }
                if (args.Contains("--product-flow")) { await ProductFlow(main,client,output); exit=0; return; }
                if (args.Contains("--environment-semantics")) { await EnvironmentSemantics(main,client,output); exit=0; return; }
                if (args.Contains("--login-accounts")) { await LoginAccounts(main,client,output); exit=0; return; }
                if (args.Contains("--accounts")) { await Accounts(main,client,output); exit=0; return; }
                if (args.Contains("--account-center")) { await AccountCenter(main,client,output); exit=0; return; }
                if (args.Contains("--google-login")) { await GoogleLogin(main,client,output); exit=0; return; }
                if (args.Contains("--session-backup")) { await SessionBackup(main,client,output); exit=0; return; }
                if (args.Contains("--proxy-sessions")) { await ProxySessions(main,client,output); exit=0; return; }
                if (args.Contains("--diagnostics")) { await Diagnostics(main,client,output); exit=0; return; }
                await (Task)Method("LoadProxyCatalog").Invoke(main, null)!;
                await (Task)Method("Launch").Invoke(main,new object[]{"ui-browser"})!;await Task.Delay(600);
                var before=await BrowserState(client);int pid=before.GetProperty("pid").GetInt32(),port=before.GetProperty("cdpPort").GetInt32();
                var hosts=(Dictionary<string,NativeBrowserHost>)typeof(MainWindow).GetField("hosts",BindingFlags.NonPublic|BindingFlags.Instance)!.GetValue(main)!;
                var native=hosts["ui-browser"].AttachedHandle;Check("Isolated browser attached before navigation",native!=IntPtr.Zero&&IsWindowVisible(native));
                const string snapshot="({token:window.__proxyUiToken,entry:document.querySelector('#entry').value,storage:localStorage.getItem('proxy-ui-storage'),visibility:document.visibilityState})";
                await Evaluate(port,"(()=>{window.__proxyUiToken=crypto.randomUUID();document.querySelector('#entry').value='isolated proxy UI draft';localStorage.setItem('proxy-ui-storage','isolated-state');return true})()");
                var documentBefore=await Evaluate(port,snapshot);
                Click(main,"代理管理");await Wait(()=>Nodes<DataGrid>(main).SingleOrDefault()?.Items.Count==4);
                var manager=main;var table=Nodes<DataGrid>(manager).Single();
                var pageField=typeof(MainWindow).GetField("proxyWorkspace",BindingFlags.NonPublic|BindingFlags.Instance)!;var page=((ContentControl)pageField.GetValue(main)!).Content;
                Check("Proxy management is inside main page",main.OwnedWindows.Cast<Window>().All(w=>!Nodes<DataGrid>(w).Any()));
                Check("Native browser hidden on proxy page",!IsWindowVisible(native));
                Check("Inventory contains four isolated fixtures",table.Items.Count==4);
                manager.Width=1050;manager.Height=680; await Task.Delay(120); Capture(manager,Path.Combine(output,"proxy-manager-min.png"));
                manager.Width=1440;manager.Height=900; await Task.Delay(120); Capture(manager,Path.Combine(output,"proxy-manager.png"));
                Check("Batch action available",Named<Button>(manager,"检测全部").IsEnabled);
                Click(manager,"检测全部");
                await Wait(()=>!Named<Button>(main,"检测全部").IsEnabled);
                var rowBefore=table.Items[0];
                await Wait(()=>table.Items.Cast<object>().Any(r=>RowState(r)=="running")&&table.Items.Cast<object>().Any(r=>RowState(r)=="queued"));
                Check("Visible top activity card during diagnostics",Named<Border>(main,"代理检测状态").IsVisible);
                Check("Real running and queued row badges",Nodes<TextBlock>(main).Any(t=>t.Text=="等待检测")&&Nodes<TextBlock>(main).Any(t=>t.Text=="检测中…"));
                Check("No fabricated progress before first result",Named<TextBlock>(main,"代理检测进度").Text.StartsWith("0 / 4")&&Named<ProgressBar>(main,"代理检测进度条").IsIndeterminate);
                Capture(manager,Path.Combine(output,"proxy-manager-checking.png"));
                manager.Width=1050;manager.Height=680;await Task.Delay(100);Capture(manager,Path.Combine(output,"proxy-manager-checking-min.png"));manager.Width=1440;manager.Height=900;
                Click(main,"环境工作台");await Task.Delay(250);
                Check("Browser view restored during diagnostic job",IsWindowVisible(native));
                Click(main,"代理管理");await Task.Delay(120);
                Check("Returning proxy page preserves same controls",ReferenceEquals(page,((ContentControl)pageField.GetValue(main)!).Content)&&ReferenceEquals(table,Nodes<DataGrid>(main).Single()));
                Check("Job remains active after page switch",!Named<Button>(main,"检测全部").IsEnabled);
                await Wait(()=>table.Items.Cast<object>().Any(r=>RowState(r)=="completed")&&!Named<Button>(manager,"检测全部").IsEnabled);
                Check("Partial results update without recreating rows",ReferenceEquals(rowBefore,table.Items[0])&&table.Items.Cast<object>().Any(r=>RowState(r)=="running"));
                Check("Progress becomes determinate for real partial results",!Named<ProgressBar>(main,"代理检测进度条").IsIndeterminate);
                Capture(manager,Path.Combine(output,"proxy-manager-partial.png"));
                await Wait(()=>Named<Button>(manager,"检测全部").IsEnabled && Nodes<TextBlock>(manager).Any(x=>x.Text.StartsWith("检测完成")));
                Check("Batch progress completed across inpage switches", true);
                Check("Completion summary includes successes and failures",Nodes<TextBlock>(main).Any(t=>t.Text.Contains("成功 3 条")&&t.Text.Contains("失败 1 条")));
                Check("Completion removes spinning progress",!Named<ProgressBar>(main,"代理检测进度条").IsVisible&&table.Items.Cast<object>().All(r=>RowState(r) is not ("running" or "queued")));
                Capture(manager,Path.Combine(output,"proxy-manager-results.png"));
                Click(manager,"检测全部");await Wait(()=>Named<Button>(manager,"取消检测").IsEnabled&&table.Items.Cast<object>().Any(r=>RowState(r)=="running"));
                Click(manager,"取消检测");await Wait(()=>Nodes<TextBlock>(main).Any(t=>t.Text=="正在取消检测…"));
                Check("Cancellation shows pending connection feedback",!Named<Button>(manager,"取消检测").IsEnabled);
                await Wait(()=>Named<Button>(manager,"检测全部").IsEnabled&&Nodes<TextBlock>(main).Any(t=>t.Text=="检测已取消"));
                Check("Cancellation stops loading and keeps previous results",!Named<ProgressBar>(main,"代理检测进度条").IsVisible&&table.Items.Cast<object>().Any(r=>RowState(r)=="cancelled")&&table.Items.Cast<object>().All(r=>RowState(r) is not ("running" or "queued")));
                var profile=(Window)Method("CreateProfileDialog").Invoke(main,new object?[]{null})!;
                profile.Owner=manager;profile.Show();await Task.Delay(120);
                var picker=Named<ComboBox>(profile,"环境代理选择");
                Check("Environment picker includes direct and all proxies",picker.Items.Count==5);
                picker.SelectedValue="ui-residential";
                Check("Selected proxy exposes complete upstream route",Nodes<TextBlock>(profile).Any(t=>t.Text.Contains("上游")&&t.Text.Contains("residential-gateway.invalid")));
                var section=Nodes<TextBlock>(profile).First(t=>t.Text=="网络代理");section.BringIntoView();await Task.Delay(120);
                Capture(profile,Path.Combine(output,"environment-proxy-picker.png"));
                Named<TextBox>(profile,"环境名称").Text="Isolated UI bound environment";
                var nameInput=Named<TextBox>(profile,"环境名称");var originalContent=profile.Content;
                Click(profile,"管理代理 / 新建线路");await Wait(()=>Nodes<DataGrid>(profile).SingleOrDefault()?.Items.Count==4);
                Check("Nested management uses same environment window",Nodes<DataGrid>(profile).Count()==1&&main.OwnedWindows.Cast<Window>().Where(w=>Nodes<DataGrid>(w).Any()).All(w=>ReferenceEquals(w,profile)));
                Click(profile,"返回环境配置");await Wait(()=>ReferenceEquals(profile.Content,originalContent)&&Nodes<TextBox>(profile).Any(n=>AutomationProperties.GetName(n)=="环境名称"));
                Check("Returning editor preserves original unsaved controls",ReferenceEquals(nameInput,Named<TextBox>(profile,"环境名称"))&&nameInput.Text=="Isolated UI bound environment");
                await Task.Delay(160);Check("Returning editor preserves selected proxy",Named<ComboBox>(profile,"环境代理选择").SelectedValue as string=="ui-residential");
                Click(profile,"保存环境");await Wait(()=>!profile.IsVisible);
                var saved=await client.GetStringAsync("profiles");
                using(var doc=JsonDocument.Parse(saved)) Check("Profile save persists selected proxyId",doc.RootElement.GetProperty("profiles").EnumerateArray().Any(p=>p.GetProperty("proxyId").GetString()=="ui-residential"));
                var editor=(Window)Method("CreateProxyEditor").Invoke(main,new object?[]{manager,null})!;
                editor.Show();await Task.Delay(120);
                Named<TextBox>(editor,"代理名称").Text="UI synthetic proxy";
                Named<TextBox>(editor,"代理地址").Text="http://new-gateway.invalid:3010";
                Named<TextBox>(editor,"上游代理地址").Text="http://127.0.0.1:7897";
                var up=Named<TextBox>(editor,"上游代理地址");up.BringIntoView();await Task.Delay(120);
                Capture(editor,Path.Combine(output,"proxy-editor.png"));
                Click(editor,"检测线路与出口信息");await Wait(()=>Named<Border>(editor,"线路检测中").IsVisible);
                Check("Draft test shows loading and prevents duplicate actions",!Named<Button>(editor,"保存代理").IsEnabled&&!Named<Button>(editor,"检测线路与出口信息").IsEnabled);
                Capture(editor,Path.Combine(output,"proxy-editor-checking.png"));
                await Wait(()=>Named<Button>(editor,"保存代理").IsEnabled);
                Check("Draft test removes loading after completion",!Named<Border>(editor,"线路检测中").IsVisible&&Nodes<TextBlock>(editor).Any(t=>t.Text.StartsWith("出口国家 美国")));
                Click(editor,"保存代理");await Wait(()=>!editor.IsVisible);
                Check("Proxy editor persisted residential upstream",(await client.GetStringAsync("proxies")).Contains("new-gateway.invalid"));
                Click(main,"环境工作台");await Task.Delay(600);var after=await BrowserState(client);var documentAfter=await Evaluate(port,snapshot);
                Check("Browser page restores original native handle",hosts["ui-browser"].AttachedHandle==native&&IsWindowVisible(native));
                Check("Navigation preserves browser PID",after.GetProperty("pid").GetInt32()==pid);
                foreach(var key in new[]{"token","entry","storage"})Check("Navigation preserves document "+key,documentBefore.GetProperty(key).GetString()==documentAfter.GetProperty(key).GetString());
                Check("Returned document is visible",documentAfter.GetProperty("visibility").GetString()=="visible");
                await ProxyGeography(main,client,output);
                await Fingerprints(main,client,output);exit=0;
            } catch(Exception ex) { File.WriteAllText(Path.Combine(output,"ui-error.txt"),ex.ToString()); }
            finally {
                File.WriteAllText(Path.Combine(output,"proxy-ui-checks.json"),JsonSerializer.Serialize(new {ok=exit==0,checks=Checks},new JsonSerializerOptions{WriteIndented=true}));
                main.Close();app.Shutdown();
            }
        };
        main.Show();app.Run();return exit;
    }
}
