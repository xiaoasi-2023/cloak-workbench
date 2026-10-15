namespace CloakWorkbench;

public sealed partial class MainWindow
{
    string? proxyActivitySummary;
    bool showingActivity;

    void UpdateGlobalActivity()
    {
        var activity = new List<string>();
        if (centerBusy) activity.Add("环境任务 · " + centerJobText.Text);
        if (proxyActivitySummary != null) activity.Add(proxyActivitySummary);
        int loginCount = googleLoginViews.Values.Count(view => view.Active || view.Starting);
        if (loginCount > 0) activity.Add($"Google 登录进行中 · {loginCount} 个环境，进度见环境管理");
        if (checkingAccounts.Count > 0) activity.Add($"Dola 登录检测 · {checkingAccounts.Count} 个环境");
        if (checkingEnvironmentNetworks.Count > 0) activity.Add($"代理出口检测 · {checkingEnvironmentNetworks.Count} 个环境");
        if (activity.Count > 0) { status.Text = string.Join("   |   ", activity); showingActivity = true; }
        else if (showingActivity) { status.Text = "任务已结束，可返回对应页面查看结果。"; showingActivity = false; }
        status.ToolTip = status.Text;
    }
}
