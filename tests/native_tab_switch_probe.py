"""Exercise only the workbench's already-open local fixtures using native clicks.

No navigation, keyboard, clipboard, or input changes. Inspect page state through
CDP and measure actual pixels only while the workbench owns the sampled region.
"""
import ctypes
from ctypes import wintypes as w
import json
from pathlib import Path
import subprocess
import time
from urllib.request import Request, ProxyHandler, build_opener
from urllib.parse import urlsplit
from websockets.sync.client import connect
from physical_input_probe import Point, Rect, Input, Union, Mouse

root = Path(__file__).resolve().parents[1]
session = json.loads((root / '.local/session.json').read_text(encoding='utf-8-sig'))
state_path = root / '.local/ui-state.json'
state = json.loads(state_path.read_text(encoding='utf-8-sig'))
opener = build_opener(ProxyHandler({}))
with opener.open(Request('http://127.0.0.1:8766/profiles', headers={'Authorization': 'Bearer ' + session['token']}), timeout=8) as response:
    profiles = {p['id']: p for p in json.load(response)['profiles']}
ids = [pid for pid in ('demo-a', 'input-regression-2', 'demo-b', 'input-regression') if any(x['id'] == pid for x in state['browsers'])][:2]
if len(ids) != 2:
    raise RuntimeError('Two already-open owned local fixtures required')
saved_id = state['selected']
user = ctypes.WinDLL('user32', use_last_error=True)
user.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
user.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
user.GetAncestor.argtypes = [w.HWND, w.UINT];user.GetAncestor.restype = w.HWND
user.GetWindowThreadProcessId.argtypes = [w.HWND,ctypes.POINTER(w.DWORD)]
user.GetWindowRect.argtypes = [w.HWND,ctypes.POINTER(Rect)]
user.GetClientRect.argtypes = [w.HWND,ctypes.POINTER(Rect)]
user.MapWindowPoints.argtypes = [w.HWND,w.HWND,ctypes.c_void_p,w.UINT]
user.GetForegroundWindow.restype = w.HWND
user.WindowFromPoint.argtypes = [Point];user.WindowFromPoint.restype = w.HWND
user.SetForegroundWindow.argtypes = [w.HWND];user.ShowWindow.argtypes = [w.HWND,ctypes.c_int]
user.SetWindowPos.argtypes = [w.HWND,w.HWND,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,w.UINT]
user.GetWindowLongW.argtypes = [w.HWND,ctypes.c_int]
user.GetDC.argtypes = [w.HWND];user.GetDC.restype = w.HDC
user.ReleaseDC.argtypes = [w.HWND,w.HDC]
user.SendInput.argtypes = [w.UINT,ctypes.POINTER(Input),ctypes.c_int]
gdi=ctypes.WinDLL('gdi32');gdi.GetPixel.argtypes=[w.HDC,ctypes.c_int,ctypes.c_int];gdi.GetPixel.restype=w.DWORD
def owner(hwnd):
    pid=w.DWORD();user.GetWindowThreadProcessId(hwnd,ctypes.byref(pid));return pid.value
app=user.GetAncestor(state['browsers'][0]['parent'],2)
if owner(app)!=session['desktopPid']:raise RuntimeError('Owner changed')
def tab_point(pid):
    name=profiles[pid]['name'].replace("'","''")
    code="""
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$r=[System.Windows.Automation.AutomationElement]::FromHandle([intptr]%d)
$a=$r.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::NameProperty,'%s'))
$b=@($a | Where-Object {$_.Current.ProcessId -eq %d -and $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button})
if($b.Count -ne 1){throw 'Unique tab required'}
$p=$b[0].Current.BoundingRectangle
[pscustomobject]@{x=$p.X+$p.Width/2;y=$p.Y+$p.Height/2} | ConvertTo-Json -Compress
""" % (app,name,session['desktopPid'])
    result=subprocess.run(['powershell.exe','-NoProfile','-Command',code],check=True,capture_output=True,encoding='utf-8-sig',timeout=8)
    return json.loads(result.stdout)
def select(pid):
    if owner(user.GetForegroundWindow())!=session['desktopPid']:raise RuntimeError('Foreground changed; no click sent')
    p=tab_point(pid);point=Point(round(p['x']),round(p['y']))
    user.SetCursorPos(point.x,point.y)
    if owner(user.WindowFromPoint(point))!=session['desktopPid']:raise RuntimeError('Tab obscured; no click sent')
    for flag in (2,4):
        event=Input(0,Union(mi=Mouse(0,0,0,flag,0,0)))
        if user.SendInput(1,ctypes.byref(event),ctypes.sizeof(Input))!=1:raise ctypes.WinError(ctypes.get_last_error())
    time.sleep(.7)
def page_state(pid):
    profile=profiles[pid]
    with opener.open(f"http://127.0.0.1:{profile['cdpPort']}/json/list",timeout=5) as response:targets=json.load(response)
    target=next(t for t in targets if t.get('type')=='page' and urlsplit(t.get('url','')).hostname=='127.0.0.1' and urlsplit(t['url']).path=='/probe')
    with connect(target['webSocketDebuggerUrl'],proxy=None,open_timeout=5,close_timeout=1) as socket:
        socket.send(json.dumps({'id':1,'method':'Runtime.evaluate','params':{'expression':"({entry:document.querySelector('#entry').value,marker:window.probeResult?.persistentMarker,url:location.href,visibility:document.visibilityState})",'returnByValue':True}}))
        while True:
            result=json.loads(socket.recv(timeout=5))
            if result.get('id')==1:return result['result']['result']['value']
before={pid:page_state(pid) for pid in ids}
observations=[];report={'ok':False,'scope':'Already-open local fixtures; guarded native mouse and owned pixels only','observations':observations}
cursor=Point();user.GetCursorPos(ctypes.byref(cursor))
original_topmost=bool(user.GetWindowLongW(app,-20)&8)
try:
    user.ShowWindow(app,9)
    user.SetWindowPos(app,ctypes.c_void_p(-1),0,0,0,0,0x13)
    user.SetForegroundWindow(app);time.sleep(.2)
    # Native activation is allowed only if this precise WPF header owns the
    # mouse hit. Lock screens, overlays and other apps abort before any click.
    app_bounds=Rect();user.GetWindowRect(app,ctypes.byref(app_bounds))
    activation=Point(app_bounds.left+240,app_bounds.top+60)
    user.SetCursorPos(activation.x,activation.y)
    actual=Point();user.GetCursorPos(ctypes.byref(actual))
    if (actual.x,actual.y)!=(activation.x,activation.y) or owner(user.WindowFromPoint(actual))!=session['desktopPid']:
        raise RuntimeError('Owned activation header obscured; no click sent')
    for flag in (2,4):
        event=Input(0,Union(mi=Mouse(0,0,0,flag,0,0)))
        if user.SendInput(1,ctypes.byref(event),ctypes.sizeof(Input))!=1:raise ctypes.WinError(ctypes.get_last_error())
    time.sleep(.2)
    for round_index in range(12):
        pid=ids[round_index%2];select(pid)
        snapshot=json.loads(state_path.read_text(encoding='utf-8-sig'))
        if snapshot['selected']!=pid:raise RuntimeError('Tab selection did not complete')
        host=next(h for h in snapshot['browsers'] if h['id']==pid)
        bounds=Rect();user.GetClientRect(host['parent'],ctypes.byref(bounds));user.MapWindowPoints(host['parent'],None,ctypes.byref(bounds),2)
        point=Point((bounds.left+bounds.right)//2,bounds.top+18)
        if user.GetAncestor(user.WindowFromPoint(point),2)!=app:raise RuntimeError('Owned pixel region obscured; no capture')
        dc=user.GetDC(None)
        try:pixel=int(gdi.GetPixel(dc,point.x,point.y))
        finally:user.ReleaseDC(None,dc)
        current=page_state(pid)
        stable=all(current[k]==before[pid][k] for k in ('entry','marker','url'))
        painted=pixel==0x282b13
        observations.append({'round':round_index,'id':pid,'headerPixel':f'{pixel:06x}','painted':painted,'draftStorageUrlPreserved':stable,'documentVisibility':current['visibility']})
        if not painted or not stable:raise RuntimeError('Visible page paint or persistence regression')
    report['ok']=True
except Exception as error:report['error']=str(error)
finally:
    try:
        if json.loads(state_path.read_text(encoding='utf-8-sig'))['selected']!=saved_id:select(saved_id)
        report['selectionRestored']=True
    except Exception:report['selectionRestored']=False
    user.SetWindowPos(app,ctypes.c_void_p(-1 if original_topmost else -2),0,0,0,0,0x13)
    user.SetCursorPos(cursor.x,cursor.y)
    (root/'.local/native-tab-switch-live.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report))
raise SystemExit(0 if report['ok'] else 1)
