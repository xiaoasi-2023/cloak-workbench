param([ValidateSet('A','B')][string]$Demo = 'B')
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$projectRoot = Split-Path -Parent $PSScriptRoot
$sessionState = Get-Content -Raw -Encoding UTF8 (Join-Path $projectRoot '.local\session.json') | ConvertFrom-Json
$desktopProcess = Get-Process -Id $sessionState.desktopPid
$appRoot = [System.Windows.Automation.AutomationElement]::FromHandle($desktopProcess.MainWindowHandle)
if ($appRoot.Current.ProcessId -ne $sessionState.desktopPid) { throw 'Wrong desktop owner' }
$buttons = $appRoot.FindAll([System.Windows.Automation.TreeScope]::Descendants,
    [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ControlTypeProperty,
        [System.Windows.Automation.ControlType]::Button))
$matchesFound = @($buttons | Where-Object { $_.Current.Name -match ('\u6D4B\u8BD5\u73AF\u5883 ' + $Demo) })
if ($matchesFound.Count -ne 1) { throw 'Demo tab is not uniquely available; no user profile selected' }
$matchesFound[0].GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
Write-Output ('Selected local demo-' + $Demo.ToLower())
