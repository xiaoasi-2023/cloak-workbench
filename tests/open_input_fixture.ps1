$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$projectRoot = Split-Path -Parent $PSScriptRoot
$sessionState = Get-Content -Raw -Encoding UTF8 (Join-Path $projectRoot '.local\session.json') | ConvertFrom-Json
$fixture = Get-Content -Raw -Encoding UTF8 (Join-Path $projectRoot '.local\input-fixture.json') | ConvertFrom-Json
$desktopProcess = Get-Process -Id $sessionState.desktopPid
$appRoot = [System.Windows.Automation.AutomationElement]::FromHandle($desktopProcess.MainWindowHandle)
if ($appRoot.Current.ProcessId -ne $sessionState.desktopPid) { throw 'Wrong desktop owner' }
$elements = $appRoot.FindAll([System.Windows.Automation.TreeScope]::Descendants,
    [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $sessionState.desktopPid))
$label = @($elements | Where-Object { $_.Current.Name -eq $fixture.name -and $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Text })
if ($label.Count -ne 1) { throw 'Fixture card is not uniquely present; wait for polling' }
$walker = [System.Windows.Automation.TreeWalker]::RawViewWalker
$container = $walker.GetParent($label[0])
$openButton = $null
for ($depth = 0; $depth -lt 5 -and $container; $depth++) {
    $children = $container.FindAll([System.Windows.Automation.TreeScope]::Descendants,
        [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ControlTypeProperty,
            [System.Windows.Automation.ControlType]::Button))
    $candidates = @($children | Where-Object { $_.Current.Name -eq ([string][char]0x6253 + [string][char]0x5F00) })
    if ($candidates.Count -eq 1) { $openButton = $candidates[0]; break }
    if ($candidates.Count -gt 1) { throw 'Container covers other profiles; no button invoked' }
    $container = $walker.GetParent($container)
}
if (-not $openButton) { throw 'Could not uniquely identify fixture open button' }
$openButton.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
Write-Output 'Opened isolated offline input fixture'
