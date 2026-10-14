using System.Windows;

namespace CloakWorkbench;

public static class Program
{
    [STAThread]
    public static void Main(string[] args)
    {
        var app = new Application();
        app.DispatcherUnhandledException += (_, e) => {
            MessageBox.Show(e.Exception.Message, "Cloak Workbench");
            e.Handled = true;
        };
        app.Run(new MainWindow(args.Contains("--smoke"), args.Contains("--resume")));
    }
}
