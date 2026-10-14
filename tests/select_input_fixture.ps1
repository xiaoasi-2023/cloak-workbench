$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskSession = Get-Content -Raw -Encoding UTF8 (Join-Path $taskRoot '.local\session.json') | ConvertFrom-Json
$taskFixture = Get-Content -Raw -Encoding UTF8 (Join-Path $taskRoot '.local\input-fixture-fresh.json') | ConvertFrom-Json
$taskApp = [System.Windows.Automation.AutomationElement]::FromHandle((Get-Process -Id $taskSession.desktopPid).MainWindowHandle)
if ($taskApp.Current.ProcessId -ne $taskSession.desktopPid) { throw 'Wrong desktop owner' }
$taskButtons = $taskApp.FindAll([System.Windows.Automation.TreeScope]::Descendants,
    [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::Button))
$taskTabs = @($taskButtons | Where-Object {
    $_.Current.Name -eq $taskFixture.name -and $_.Current.ProcessId -eq $taskSession.desktopPid
})
if ($taskTabs.Count -ne 1) { throw 'Fresh fixture tab is not uniquely available' }
$taskTabs[0].GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
Write-Output 'Selected owned fresh fixture tab'
