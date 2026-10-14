"""Native mouse test of explicit, reversible workbench toolbar actions only."""
import argparse
import ctypes
from ctypes import wintypes as w
import json
from pathlib import Path
import subprocess
import time
from physical_input_probe import Point, Input, Union, Mouse

parser = argparse.ArgumentParser()
parser.add_argument('action', choices=['Editor', 'Inspector', 'Downloads', 'Windows'])
parser.add_argument('--activate', action='store_true', help='Bring only the owned workbench forward for this authorized test')
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
session = json.loads((root / '.local/session.json').read_text(encoding='utf-8-sig'))
name = {'Editor': '新建环境', 'Inspector': '验证当前环境', 'Downloads': '环境下载文件', 'Windows': '浏览器弹窗'}[args.action]
user = ctypes.WinDLL('user32', use_last_error=True)
user.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
user.SetForegroundWindow.argtypes = [w.HWND]
user.SetWindowPos.argtypes = [w.HWND,w.HWND,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,w.UINT]
user.GetWindowLongW.argtypes=[w.HWND,ctypes.c_int]
app_handle=None
original_topmost=False
if args.activate:
    handle_result=subprocess.run(['powershell.exe','-NoProfile','-Command',f'(Get-Process -Id {session["desktopPid"]}).MainWindowHandle.ToInt64()'],check=True,capture_output=True,text=True)
    app_handle=int(handle_result.stdout.strip())
    original_topmost=bool(user.GetWindowLongW(app_handle,-20)&8)
    user.SetWindowPos(app_handle,ctypes.c_void_p(-1),0,0,0,0,0x13)
    user.SetForegroundWindow(app_handle)
    time.sleep(.25)
    user.SetWindowPos(app_handle,ctypes.c_void_p(-1 if original_topmost else -2),0,0,0,0,0x13)
# UTF-8 script text is passed directly as argv, never through a shell pipe.
code = """
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$root=[System.Windows.Automation.AutomationElement]::FromHandle((Get-Process -Id %d).MainWindowHandle)
$items=$root.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::NameProperty,'%s'))
$match=@($items | Where-Object {$_.Current.ProcessId -eq %d -and $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button})
if($match.Count -ne 1){throw 'Unique owned button required'}
$r=$match[0].Current.BoundingRectangle
[pscustomobject]@{x=$r.X+$r.Width/2;y=$r.Y+$r.Height/2;enabled=$match[0].Current.IsEnabled} | ConvertTo-Json -Compress
""" % (session['desktopPid'], name, session['desktopPid'])
result = subprocess.run(['powershell.exe', '-NoProfile', '-Command', code], check=True, capture_output=True, encoding='utf-8-sig', timeout=8)
geometry = json.loads(result.stdout)
if not geometry['enabled']:
    raise RuntimeError('Action disabled')
user = ctypes.WinDLL('user32', use_last_error=True)
user.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
user.GetForegroundWindow.restype = w.HWND
user.WindowFromPoint.argtypes = [Point]
user.WindowFromPoint.restype = w.HWND
user.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user.SendInput.argtypes = [w.UINT, ctypes.POINTER(Input), ctypes.c_int]
def owner(hwnd):
    pid=w.DWORD();user.GetWindowThreadProcessId(hwnd,ctypes.byref(pid));return pid.value
if owner(user.GetForegroundWindow()) != session['desktopPid']:
    raise RuntimeError('Workbench must remain foreground; no click sent')
old=Point();user.GetCursorPos(ctypes.byref(old))
try:
    point=Point(round(geometry['x']),round(geometry['y']))
    user.SetCursorPos(point.x,point.y)
    actual=Point();user.GetCursorPos(ctypes.byref(actual))
    if (point.x,point.y)!=(actual.x,actual.y) or owner(user.WindowFromPoint(actual))!=session['desktopPid']:
        raise RuntimeError('Mouse target not owned; no click sent')
    for flag in (2,4):
        event=Input(0,Union(mi=Mouse(0,0,0,flag,0,0)))
        if user.SendInput(1,ctypes.byref(event),ctypes.sizeof(Input))!=1:raise ctypes.WinError(ctypes.get_last_error())
    time.sleep(.6)
    print(json.dumps({'nativeClick':args.action,'ownedTarget':True}))
finally:
    user.SetCursorPos(old.x,old.y)
