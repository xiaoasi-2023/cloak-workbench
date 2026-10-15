using System.Windows;
using System.Windows.Interop;
using System.Windows.Media;

namespace CloakWorkbench;

public static class Program
{
    [STAThread]
    public static void Main(string[] args)
    {
        // The management shell is small; software composition also survives
        // display-device changes during lock/unlock and remote desktop sessions.
        // Embedded Chromium keeps its own independent rendering pipeline.
        RenderOptions.ProcessRenderMode = RenderMode.SoftwareOnly;
        var app = new Application();
        app.DispatcherUnhandledException += (_, e) => {
            MessageBox.Show(e.Exception.Message, "Cloak Workbench");
            e.Handled = true;
        };
        app.Run(new MainWindow(args.Contains("--smoke"), args.Contains("--resume")));
    }
}
