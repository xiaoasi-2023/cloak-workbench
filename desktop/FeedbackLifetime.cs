using System.Windows;
using System.Windows.Controls;
using System.Windows.Threading;

namespace CloakWorkbench;

public sealed partial class MainWindow
{
    readonly Dictionary<FrameworkElement, DispatcherTimer> feedbackDismissTimers = new();

    void CancelFeedbackDismissal(FrameworkElement target)
    {
        if (feedbackDismissTimers.Remove(target, out var timer)) timer.Stop();
    }

    void DismissFeedbackLater(FrameworkElement target, Action dismiss)
    {
        CancelFeedbackDismissal(target);
        var timer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(4) };
        feedbackDismissTimers[target] = timer;
        timer.Tick += (_, _) => {
            timer.Stop();
            if (!feedbackDismissTimers.TryGetValue(target, out var current) || current != timer) return;
            feedbackDismissTimers.Remove(target);
            if (!closing) dismiss();
        };
        timer.Start();
    }

    void ShowTemporaryFeedback(TextBlock target, string message)
    {
        target.Text = message;
        DismissFeedbackLater(target, () => { if (target.Text == message) target.Text = ""; });
    }

    void StopFeedbackTimers()
    {
        foreach (var timer in feedbackDismissTimers.Values) timer.Stop();
        feedbackDismissTimers.Clear();
    }
}
