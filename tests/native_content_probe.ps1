param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot),
    [string]$OutPath = ''
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class ContentProbeNative {
  [StructLayout(LayoutKind.Sequential)] public struct R { public int L,T,Right,B; }
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h,out R r);
  [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr h,out R r);
  [DllImport("user32.dll")] public static extern int MapWindowPoints(IntPtr f,IntPtr t,ref R r,int n);
  [DllImport("user32.dll")] public static extern IntPtr GetParent(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr GetAncestor(IntPtr h,uint flags);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h,out uint pid);
  [DllImport("user32.dll")] public static extern uint GetDpiForWindow(IntPtr h);
}
'@
$state = Get-Content -LiteralPath (Join-Path $Root '.local/ui-state.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$selected = @($state.browsers | Where-Object { $_.id -eq $state.selected -and $_.visible })
if ($selected.Count -ne 1) { throw 'Expected one selected embedded browser' }
$browser = [IntPtr]$selected[0].hwnd
if ([ContentProbeNative]::IsIconic([ContentProbeNative]::GetAncestor($browser,2))) { throw 'Workbench is minimized; visible content geometry cannot be verified' }
$parent = [ContentProbeNative]::GetParent($browser)
if ($parent.ToInt64() -ne $selected[0].parent) { throw 'Owned browser parent changed' }
[uint32]$browserPid = 0
[ContentProbeNative]::GetWindowThreadProcessId($browser,[ref]$browserPid) | Out-Null
$frame = [System.Windows.Automation.AutomationElement]::FromHandle($browser)
$content = $frame.FindFirst([System.Windows.Automation.TreeScope]::Descendants,
    [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ClassNameProperty,'MultiContentsView'))
if ($null -eq $content -or $content.Current.ProcessId -ne $browserPid) { throw 'Owned Chromium content view unavailable' }
$hostRect = [ContentProbeNative+R]::new()
[ContentProbeNative]::GetClientRect($parent,[ref]$hostRect) | Out-Null
[ContentProbeNative]::MapWindowPoints($parent,[IntPtr]::Zero,[ref]$hostRect,2) | Out-Null
$outer = [ContentProbeNative+R]::new()
[ContentProbeNative]::GetWindowRect($browser,[ref]$outer) | Out-Null
$bounds = $content.Current.BoundingRectangle
$hostBounds = @($hostRect.L,$hostRect.T,$hostRect.Right,$hostRect.B)
$contentBounds = @($bounds.Left,$bounds.Top,$bounds.Right,$bounds.Bottom)
$maxDifference = 0
for ($i=0; $i -lt 4; $i++) { $maxDifference = [Math]::Max($maxDifference,[Math]::Abs($hostBounds[$i]-$contentBounds[$i])) }
$report = [ordered]@{
    ok = $maxDifference -le 2
    selected = $state.selected
    dpi = [ContentProbeNative]::GetDpiForWindow($browser)
    hostBounds = $hostBounds
    contentBounds = $contentBounds
    browserBounds = @($outer.L,$outer.T,$outer.Right,$outer.B)
    measuredInsets = @{left=$bounds.Left-$outer.L;top=$bounds.Top-$outer.T;right=$outer.Right-$bounds.Right;bottom=$outer.B-$bounds.Bottom}
    maxEdgeDifference = $maxDifference
    checks = @('Native content fills entire clipped host','Chromium caption and borders outside host','Native physical geometry without spoofed browser screen or DPR')
    notVerified = @('Other monitor DPI scales','Native IME candidate positioning')
}
$json = $report | ConvertTo-Json -Depth 5
if ($OutPath) { [System.IO.File]::WriteAllText($OutPath,$json,[System.Text.UTF8Encoding]::new($false)) }
$json
if (-not $report.ok) { exit 1 }
