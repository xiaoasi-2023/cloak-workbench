using System.Net.Http;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Reflection;
using CloakWorkbench;

internal static partial class Program
{
    [DllImport("user32.dll")] static extern IntPtr GetParent(IntPtr hwnd);
    [DllImport("user32.dll", EntryPoint="GetWindowLongW")] static extern int WindowStyle(IntPtr hwnd, int index);

    sealed class ExternalFlashWatch : IDisposable
    {
        readonly CancellationTokenSource stop = new();
        readonly Task observer;
        public int Flashes;
        public ExternalFlashWatch(IntPtr hwnd) {
            // Observe during the asynchronous operation, not just its final state.
            observer=Task.Run(()=> {
                while (!stop.IsCancellationRequested) {
                    if (IsWindowVisible(hwnd) && (WindowStyle(hwnd,-16)&0x40000000)==0)
                        Interlocked.Increment(ref Flashes);
                    Thread.Sleep(1);
                }
            });
        }
        public void Dispose() { stop.Cancel(); observer.GetAwaiter().GetResult(); stop.Dispose(); }
    }

    static async Task BrowserLifecycle(MainWindow main, HttpClient client)
    {
        var hosts=(Dictionary<string,NativeBrowserHost>)typeof(MainWindow).GetField("hosts",BindingFlags.NonPublic|BindingFlags.Instance)!.GetValue(main)!;
        for (int i=0;i<3;i++) {
            await (Task)Method("Launch").Invoke(main,new object[]{"ui-browser"})!;
            await Task.Delay(250);
            var state=await BrowserState(client);int pid=state.GetProperty("pid").GetInt32(),port=state.GetProperty("cdpPort").GetInt32();
            var hwnd=hosts["ui-browser"].AttachedHandle;
            Check($"Hidden startup {i+1} becomes a visible embedded child",IsWindowVisible(hwnd)&&GetParent(hwnd)!=IntPtr.Zero&&(WindowStyle(hwnd,-16)&0x40000000)!=0);
            await Evaluate(port,"(()=>{window.__lifecycleToken='retained-document';document.querySelector('#entry').value='unsaved lifecycle draft';return true})()");
            if(i==0) {
                // The fixture rejects only the first close; the original host,
                // page, and visibility must survive a service error.
                bool failed=false;
                using(var watch=new ExternalFlashWatch(hwnd)) {
                    try { await (Task)Method("CloseProfile").Invoke(main,new object[]{"ui-browser"})!; }
                    catch(InvalidOperationException ex) when(ex.Message.Contains("Synthetic close failure")) { failed=true; }
                    Check("Failed close keeps original embedded host visible",failed&&hosts["ui-browser"].AttachedHandle==hwnd&&IsWindowVisible(hwnd)&&GetParent(hwnd)!=IntPtr.Zero);
                    Check("Failed close has no standalone flash",watch.Flashes==0);
                }
                Check("Failed close retains unsaved page input",(await Evaluate(port,"document.querySelector('#entry').value")).GetString()=="unsaved lifecycle draft");
                Method("ToggleExternal").Invoke(main,null);
                Check("Explicit external action still shows a normal window",!hosts.ContainsKey("ui-browser")&&IsWindowVisible(hwnd)&&(WindowStyle(hwnd,-16)&0x40000000)==0);
                await (Task)Method("Launch").Invoke(main,new object[]{"ui-browser"})!;
                var again=await BrowserState(client);
                Check("External window reattaches with same native handle and process",hosts["ui-browser"].AttachedHandle==hwnd&&again.GetProperty("pid").GetInt32()==pid);
                Check("Reattachment preserves existing document and input",(await Evaluate(port,"window.__lifecycleToken+':'+document.querySelector('#entry').value")).GetString()=="retained-document:unsaved lifecycle draft");
            }
            using(var watch=new ExternalFlashWatch(hwnd)) {
                var close=(Task)Method("CloseProfile").Invoke(main,new object[]{"ui-browser"})!;
                var duplicate=(Task)Method("CloseProfile").Invoke(main,new object[]{"ui-browser"})!;
                await Task.WhenAll(close,duplicate);await Task.Delay(60);
                Check($"Close {i+1} never shows a standalone browser",watch.Flashes==0&&!NativeBrowserHost.IsWindowAlive(hwnd)&&!hosts.ContainsKey("ui-browser"));
            }
        }
        await (Task)Method("Launch").Invoke(main,new object[]{"ui-browser"})!;
        var last=hosts["ui-browser"].AttachedHandle;
        using(var watch=new ExternalFlashWatch(last)) {
            main.Close();await Wait(()=>!main.IsVisible);await Task.Delay(80);
            Check("Workbench shutdown closes children without external flash",watch.Flashes==0&&!NativeBrowserHost.IsWindowAlive(last));
        }
    }
}
