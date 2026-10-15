"""Gracefully restart only this workbench, retaining its open environment list."""
from pathlib import Path
import argparse
import ctypes
from ctypes import wintypes
import json
import subprocess
import time
from urllib.request import Request, ProxyHandler, build_opener
from urllib.parse import urlsplit
from websockets.sync.client import connect


def restart_snapshot(snapshot, profiles):
    """Retain only live environments and their embedded/external presentation."""
    reopen = []
    seen = set()
    for item in snapshot['browsers']:
        profile = profiles.get(item['id'])
        if not profile or profile['state'] not in ('running', 'starting') or profile['id'] in seen:
            continue
        seen.add(profile['id'])
        reopen.append({'id': profile['id'], 'url': profile['url'],
                       'displayMode': 'external' if item.get('displayMode') == 'external' else 'embedded'})
    selected = snapshot.get('selected')
    if selected not in seen:
        selected = reopen[0]['id'] if reopen else None
    return {'browsers': reopen, 'selected': selected,
            'width': snapshot['width'], 'height': snapshot['height']}


def probe_drafts(opener, profiles, saved=None):
    """Retain unsaved local test-page drafts; never inspect third-party forms."""
    drafts = {} if saved is None else saved
    restored = 0
    for profile_id, profile in profiles.items():
        if not profile.get('cdpPort') or (saved is not None and profile_id not in saved):
            continue
        with opener.open(f"http://127.0.0.1:{profile['cdpPort']}/json/list", timeout=5) as response:
            pages = json.load(response)
        page = next((p for p in pages if p.get('type') == 'page'
                     and urlsplit(p.get('url', '')).hostname == '127.0.0.1'
                     and urlsplit(p['url']).port == 8766
                     and urlsplit(p['url']).path == '/probe'), None)
        if not page:
            if saved is not None:
                raise RuntimeError('Saved local probe page is missing; draft recovery file retained')
            continue
        with connect(page['webSocketDebuggerUrl'], open_timeout=5, close_timeout=1, proxy=None) as ws:
            if saved is None:
                expression = "(()=>{const e=document.querySelector('#entry');return e?{url:location.href,value:e.value,start:e.selectionStart,end:e.selectionEnd}:null})()"
            else:
                draft = saved[profile_id]
                expression = "(()=>{const d=" + json.dumps(draft) + ";const e=document.querySelector('#entry');if(!e||location.href!==d.url||(e.value&&e.value!==d.value))return false;e.value=d.value;e.setSelectionRange(d.start,d.end);return e.value===d.value})()"
            ws.send(json.dumps({'id': 1, 'method': 'Runtime.evaluate', 'params': {
                'expression': expression, 'returnByValue': True}}))
            while True:
                result = json.loads(ws.recv(timeout=5))
                if result.get('id') == 1:
                    value = result.get('result', {}).get('result', {}).get('value')
                    break
        if saved is None:
            if value is not None:
                drafts[profile_id] = value
        elif value is True:
            restored += 1
        else:
            raise RuntimeError('Local draft restoration refused; new text preserved and recovery file retained')
    return drafts if saved is None else restored


def main(before_launch=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--build', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    local = root / '.local'
    session = json.loads((local / 'session.json').read_text(encoding='utf-8-sig'))
    snapshot = json.loads((local / 'ui-state.json').read_text(encoding='utf-8-sig'))
    opener = build_opener(ProxyHandler({}))
    req = Request('http://127.0.0.1:8766/profiles', headers={'Authorization': 'Bearer ' + session['token']})
    with opener.open(req, timeout=10) as response:
        profiles = {p['id']: p for p in json.load(response)['profiles']}
    saved = restart_snapshot(snapshot, profiles)
    reopen = saved['browsers']
    drafts = probe_drafts(opener, {p['id']: profiles[p['id']] for p in reopen})
    draft_file = local / 'restart-drafts.json'
    draft_file.write_text(json.dumps(drafts, ensure_ascii=False), encoding='utf-8')
    (local / 'restart-state.json').write_text(json.dumps(saved, ensure_ascii=False), encoding='utf-8')
    (local / 'resume-result.json').unlink(missing_ok=True)
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    windows = []

    @callback_type
    def callback(hwnd, _):
        pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == session['desktopPid']:
            title = ctypes.create_unicode_buffer(512)
            user.GetWindowTextW(hwnd, title, len(title))
            if title.value.startswith('Cloak Workbench'):
                windows.append(hwnd)
        return True

    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user.EnumWindows(callback, 0)
    if len(windows) != 1:
        raise RuntimeError('Cannot uniquely identify the owned desktop window')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    pids = [session['desktopPid'], session['workerPid']] + [p['pid'] for p in profiles.values() if p.get('pid')]
    handles = [kernel.OpenProcess(0x100000, False, pid) for pid in pids]
    try:
        if not user.PostMessageW(windows[0], 0x10, 0, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        deadline = time.monotonic() + 35
        while any(h and kernel.WaitForSingleObject(h, 0) == 0x102 for h in handles):
            if time.monotonic() > deadline:
                raise RuntimeError('Owned processes did not exit gracefully; rebuild was not attempted')
            time.sleep(.1)
    finally:
        for handle in handles:
            if handle:
                kernel.CloseHandle(handle)
    if before_launch is not None:
        before_launch()
    if args.build:
        subprocess.run([str(root / '.tools/dotnet/dotnet.exe'), 'build',
                        str(root / 'desktop/CloakWorkbench.csproj'), '-c', 'Release', '--nologo'], cwd=root, check=True)
    subprocess.Popen([str(root / '.venv/Scripts/pythonw.exe'), str(root / 'launch.py'), '--resume'],
                     cwd=root, creationflags=subprocess.CREATE_NO_WINDOW)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        result = local / 'resume-result.json'
        if result.exists():
            resumed = json.loads(result.read_text(encoding='utf-8-sig'))
            if not resumed['ok'] or resumed['profiles'] != len(reopen) or resumed['selected'] != saved['selected']:
                raise RuntimeError('Not all previously running environments resumed')
            restored_snapshot = json.loads((local / 'ui-state.json').read_text(encoding='utf-8-sig'))
            modes = {p['id']: p.get('displayMode', 'embedded') for p in restored_snapshot['browsers']}
            if any(modes.get(p['id']) != p['displayMode'] for p in reopen):
                raise RuntimeError('Browser presentation mode was not restored')
            current = json.loads((local / 'session.json').read_text(encoding='utf-8-sig'))
            request = Request('http://127.0.0.1:8766/profiles', headers={'Authorization': 'Bearer ' + current['token']})
            with opener.open(request, timeout=10) as response:
                running = {p['id']: p for p in json.load(response)['profiles']}
            restored_drafts = probe_drafts(opener, running, drafts)
            if restored_drafts != len(drafts):
                raise RuntimeError('Not all saved local drafts restored; recovery file retained')
            draft_file.unlink(missing_ok=True)
            print(json.dumps({'ok': True, 'resumedProfiles': len(reopen), 'selectedRestored': True,
                              'ownedProcessesClosed': len(pids), 'localDraftsRestored': restored_drafts}))
            return
        time.sleep(.15)
    raise RuntimeError('Restart did not finish; inspect the local launcher and desktop logs')


if __name__ == '__main__':
    main()
