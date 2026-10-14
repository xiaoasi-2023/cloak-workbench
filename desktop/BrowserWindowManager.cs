using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;

namespace CloakWorkbench;

/// <summary>
/// Read-only inventory plus explicit user actions for this browser's owned
/// top-level dialogs and popup windows. Never reparents, auto-hides or redirects
/// focus from a dialog; Chromium and Windows keep their normal modal behavior.
/// </summary>
public sealed class BrowserWindowManager
{
    private readonly IntPtr _browser;
    private readonly uint _pid;

    public BrowserWindowManager(IntPtr browser)
    {
        _browser = browser;
        GetWindowThreadProcessId(browser, out _pid);
    }

    public sealed record OwnedWindow(long Handle, string Title, string ClassName,
        string Kind, bool Enabled, bool Minimized, bool Foreground);

    public IReadOnlyList<OwnedWindow> Snapshot()
    {
        var result = new List<OwnedWindow>();
        if (!RootAlive()) return result;
        EnumWindows((hwnd, _) =>
        {
            if (!IsOwnedPopup(hwnd)) return true;
            var className = ReadClass(hwnd);
            var title = new StringBuilder(512);
            GetWindowText(hwnd, title, title.Capacity);
            result.Add(new(hwnd.ToInt64(), title.ToString(), className,
                className == "#32770" ? "dialog" : "popup", IsWindowEnabled(hwnd),
                IsIconic(hwnd), GetForegroundWindow() == hwnd));
            return true;
        }, IntPtr.Zero);
        return result;
    }

    public bool Activate(long handle, IntPtr workbench)
    {
        var hwnd = new IntPtr(handle);
        // A stale UI snapshot or background refresh cannot commandeer another
        // application. Recheck ownership at the point of the explicit action.
        var foreground = GetForegroundWindow();
        if (!IsOwnedPopup(hwnd) || (foreground != workbench && !IsOwnedPopup(foreground))) return false;
        if (IsIconic(hwnd)) ShowWindow(hwnd, 9);
        return SetForegroundWindow(hwnd);
    }

    public bool RequestClose(long handle)
    {
        var hwnd = new IntPtr(handle);
        // WM_CLOSE lets the browser/dialog handle cancellation and unsaved state.
        // Do not TerminateProcess or close the profile's embedded primary HWND.
        return IsOwnedPopup(hwnd) && PostMessage(hwnd, 0x0010, IntPtr.Zero, IntPtr.Zero);
    }

    private bool RootAlive()
    {
        if (!IsWindow(_browser)) return false;
        GetWindowThreadProcessId(_browser, out var pid);
        return pid == _pid && pid != 0;
    }

    private bool IsOwnedPopup(IntPtr hwnd)
    {
        if (!RootAlive() || hwnd == IntPtr.Zero || hwnd == _browser
            || !IsWindow(hwnd) || !IsWindowVisible(hwnd) || GetAncestor(hwnd, 2) != hwnd) return false;
        var className = ReadClass(hwnd);
        // IME/candidate and tooltips are native transient UI, not user-closeable
        // application popups. They remain untouched and above the child browser.
        if (className.Contains("IME", StringComparison.OrdinalIgnoreCase)
            || className.Contains("Candidate", StringComparison.OrdinalIgnoreCase)
            || className.Contains("Tooltip", StringComparison.OrdinalIgnoreCase)) return false;
        if (className is not ("#32770" or "Chrome_WidgetWin_1")) return false;
        GetWindowThreadProcessId(hwnd, out var pid);
        if (pid == _pid) return true;
        // Windows file choosers can execute in Chromium's utility process and
        // use our WPF root as owner after SetParent. Accept those only with BOTH
        // a native ownership chain and a live browser-descendant process chain.
        if (className != "#32770" || !IsBrowserDescendant(pid)) return false;
        var shell = GetAncestor(_browser, 2);
        for (var owner = GetWindow(hwnd, 4); owner != IntPtr.Zero; owner = GetWindow(owner, 4))
            if (owner == _browser || owner == shell) return true;
        return false;
    }

    private bool IsBrowserDescendant(uint pid)
    {
        var snapshot = CreateToolhelp32Snapshot(2, 0);
        if (snapshot == new IntPtr(-1)) return false;
        try
        {
            var parents = new Dictionary<uint, uint>();
            var entry = new ProcessEntry { Size = (uint)Marshal.SizeOf<ProcessEntry>() };
            if (!Process32First(snapshot, ref entry)) return false;
            do { parents[entry.ProcessId] = entry.ParentProcessId; }
            while (Process32Next(snapshot, ref entry));
            var seen = new HashSet<uint>();
            while (pid != 0 && seen.Add(pid) && parents.TryGetValue(pid, out var parent))
            {
                if (parent == _pid) return true;
                pid = parent;
            }
            return false;
        }
        finally { CloseHandle(snapshot); }
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct ProcessEntry
    {
        public uint Size, Usage, ProcessId;
        public UIntPtr DefaultHeapId;
        public uint ModuleId, Threads, ParentProcessId;
        public int BasePriority;
        public uint Flags;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 260)] public string Executable;
    }

    private static string ReadClass(IntPtr hwnd)
    {
        var value = new StringBuilder(256);
        GetClassName(hwnd, value, value.Capacity);
        return value.ToString();
    }

    private delegate bool EnumWindowsProc(IntPtr hwnd, IntPtr parameter);
    [DllImport("user32.dll")] private static extern bool EnumWindows(EnumWindowsProc callback, IntPtr parameter);
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);
    [DllImport("user32.dll")] private static extern bool IsWindow(IntPtr hwnd);
    [DllImport("user32.dll")] private static extern bool IsWindowVisible(IntPtr hwnd);
    [DllImport("user32.dll")] private static extern bool IsWindowEnabled(IntPtr hwnd);
    [DllImport("user32.dll")] private static extern bool IsIconic(IntPtr hwnd);
    [DllImport("user32.dll")] private static extern IntPtr GetAncestor(IntPtr hwnd, uint flags);
    [DllImport("user32.dll")] private static extern IntPtr GetWindow(IntPtr hwnd, uint command);
    [DllImport("user32.dll")] private static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] private static extern bool SetForegroundWindow(IntPtr hwnd);
    [DllImport("user32.dll")] private static extern bool ShowWindow(IntPtr hwnd, int command);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern int GetClassName(IntPtr hwnd, StringBuilder value, int capacity);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern int GetWindowText(IntPtr hwnd, StringBuilder value, int capacity);
    [DllImport("user32.dll", SetLastError = true)] private static extern bool PostMessage(IntPtr hwnd, uint message, IntPtr wParam, IntPtr lParam);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern IntPtr CreateToolhelp32Snapshot(uint flags, uint processId);
    [DllImport("kernel32.dll", EntryPoint = "Process32FirstW", CharSet = CharSet.Unicode)] private static extern bool Process32First(IntPtr snapshot, ref ProcessEntry entry);
    [DllImport("kernel32.dll", EntryPoint = "Process32NextW", CharSet = CharSet.Unicode)] private static extern bool Process32Next(IntPtr snapshot, ref ProcessEntry entry);
    [DllImport("kernel32.dll")] private static extern bool CloseHandle(IntPtr handle);
}
