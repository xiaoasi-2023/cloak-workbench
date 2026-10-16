namespace CloakWorkbench;

public sealed partial class MainWindow
{
    string? proxyActivitySummary;
    bool showingActivity;

    void UpdateGlobalActivity()
    {
        var activity = new List<string>();
        if (activeVideoCount > 0) activity.Add($"视频生成 · {activeVideoCount} 个任务，进度见视频生成");
        if (enterpriseJobs.Count > 0) activity.Add($"企业管理任务 · {enterpriseJobs.Count} 个企业，进度见企业目录");
        if (enterpriseOAuth.Count > 0) activity.Add($"管理员授权 · {enterpriseOAuth.Count} 个企业，进度见企业目录");
        if (centerBusy) activity.Add("环境任务 · " + centerJobText.Text);
        if (proxyActivitySummary != null) activity.Add(proxyActivitySummary);
        int loginCount = googleLoginViews.Values.Count(view => view.Active || view.Starting);
        if (loginCount > 0) activity.Add($"Google 登录进行中 · {loginCount} 个环境，浏览器上方可查看进度");
        if (checkingAccounts.Count > 0) activity.Add($"Dola 登录检测 · {checkingAccounts.Count} 个环境");
        if (checkingEnvironmentNetworks.Count > 0) activity.Add($"代理出口检测 · {checkingEnvironmentNetworks.Count} 个环境");
        if (activity.Count > 0) { CancelFeedbackDismissal(status); status.Text = string.Join("   |   ", activity); showingActivity = true; }
        else if (showingActivity) {
            const string finished = "任务已结束，可返回对应页面查看结果。";
            status.Text = finished; showingActivity = false;
            DismissFeedbackLater(status, () => { if (status.Text == finished) { status.Text = "就绪"; status.ToolTip = status.Text; } });
        }
        status.ToolTip = status.Text;
    }
}
