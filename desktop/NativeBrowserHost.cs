using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Input;
using System.Windows.Interop;
using System.Windows.Threading;

namespace CloakWorkbench;

/// <summary>
/// Experimental Windows-only host for an external browser window. SetParent is
/// not a supported Chromium embedding API; DPI, dialogs and IME need live testing.
/// Use one host instance per browser profile and keep that host alive while its
/// browser is attached. Never attach a window belonging to an unrelated process.
/// </summary>
public sealed class NativeBrowserHost : HwndHost
{
    private const int GwlStyle = -16;
    private const int GwlExStyle = -20;
    private const long WsChild = 0x40000000L;
    private const long WsVisible = 0x10000000L;
    private const long WsClipChildren = 0x02000000L;
    private const long WsClipSiblings = 0x04000000L;
    private const long WsPopup = 0x80000000L;
    private const long WsOverlappedWindow = 0x00CF0000L;
    private const long WsExAppWindow = 0x00040000L;
    private const long WsExNoActivate = 0x08000000L;
    private const uint SwpNoZOrder = 0x0004;
    private const uint SwpNoActivate = 0x0010;
    private const uint SwpFrameChanged = 0x0020;
    private const uint SwpNoOwnerZOrder = 0x0200;
    private const int SwHide = 0;
    private const int SwShow = 5;
    private const int SwRestore = 9;
    private const uint GwOwner = 4;

    private IntPtr _container;
    private WindowSnapshot? _original;
    private bool _browserShown = true;
    private uint _linkedBrowserThread;
    private IntPtr _lastFocusedChild;
    private IntPtr _focusHook;
    private IntPtr _captureHook;
    private IntPtr _minimizeHook;
    private WinEventProc? _focusCallback;
    private bool _synchronizingFocus;
    private DispatcherTimer? _pendingContentFocus;
    private BrowserContentInsets _contentInsets = new(0, 0, 0, 0, false);
    private uint _insetDpi;
    private int _insetAttempts;
    private int _presentationVersion;
    private DispatcherTimer? _pendingPresentation;
    private bool _needsPresentation = true;

    public NativeBrowserHost()
    {
        Focusable = true;
        KeyboardNavigation.SetIsTabStop(this, true);
    }

    public IntPtr AttachedHandle => _original?.Handle ?? IntPtr.Zero;
    // Physical native pixels, never WPF DIPs or the browser's spoofed screen/DPR.
    public BrowserContentInsets ContentInsets => _contentInsets;
    public BrowserWindowManager? OwnedWindows { get; private set; }
    public event Action<string>? Diagnostic;

    protected override HandleRef BuildWindowCore(HandleRef hwndParent)
    {
        _container = CreateWindowEx(0, "STATIC", "CloakWorkbenchBrowserHost",
            (uint)(WsChild | WsVisible | WsClipChildren | WsClipSiblings),
            0, 0, 1, 1, hwndParent.Handle, IntPtr.Zero, GetModuleHandle(null), IntPtr.Zero);
        if (_container == IntPtr.Zero)
            throw LastError("Create browser host window");
        return new HandleRef(this, _container);
    }

    protected override void DestroyWindowCore(HandleRef hwnd)
    {
        Detach();
        if (IsWindow(hwnd.Handle)) DestroyWindow(hwnd.Handle);
        _container = IntPtr.Zero;
    }

    public void Attach(IntPtr hwnd)
    {
        Dispatcher.VerifyAccess();
        if (_container == IntPtr.Zero) throw new InvalidOperationException("Wait for the browser host Loaded event before attaching.");
        if (!IsWindowAlive(hwnd)) throw new ArgumentException("Browser window no longer exists.", nameof(hwnd));
        if (AttachedHandle == hwnd) { RefreshBounds(); return; }
        if (_original is not null) throw new InvalidOperationException("Detach this host's existing browser before attaching another one.");

        if (!GetWindowRect(hwnd, out var rect)) throw LastError("Read browser bounds");
        var parent = GetParent(hwnd);
        var style = ReadWindowLong(hwnd, GwlStyle);
        // GetParent also reports the owner of a top-level window. Only a real
        // child window has parent-relative geometry that needs coordinate mapping.
        var actualParent = (style.ToInt64() & WsChild) != 0 ? parent : IntPtr.Zero;
        if (actualParent != IntPtr.Zero)
        {
            var points = new[] { new NativePoint(rect.Left, rect.Top), new NativePoint(rect.Right, rect.Bottom) };
            MapWindowPoints(IntPtr.Zero, actualParent, points, 2);
            rect = new NativeRect(points[0].X, points[0].Y, points[1].X, points[1].Y);
        }
        _original = new WindowSnapshot(hwnd, parent, style, ReadWindowLong(hwnd, GwlExStyle), rect, IsWindowVisible(hwnd));
        OwnedWindows = new BrowserWindowManager(hwnd);
        _contentInsets = new(0, 0, 0, 0, false);
        _insetAttempts = 0;
        _insetDpi = 0;
        _needsPresentation = true;
        try
        {
            Diagnostic?.Invoke($"Attach HWND 0x{hwnd.ToInt64():X}; browser DPI {GetDpiForWindow(hwnd)}, host DPI {GetDpiForWindow(_container)}. Cross-process SetParent remains experimental.");
            ShowWindow(hwnd, SwHide);
            const long windowState = 0x21000000L; // WS_MINIMIZE | WS_MAXIMIZE
            var embeddedStyle = (style.ToInt64() & ~(WsPopup | WsOverlappedWindow | WsVisible | windowState)) | WsChild | WsClipChildren | WsClipSiblings;
            WriteWindowLong(hwnd, GwlStyle, new IntPtr(embeddedStyle));
            WriteWindowLong(hwnd, GwlExStyle, new IntPtr(_original.ExStyle.ToInt64() & ~(WsExAppWindow | WsExNoActivate)));
            ChangeParent(hwnd, _container);
            ApplyFrame(hwnd);
            InstallFocusObserver(hwnd);
            RefreshBounds();
            ShowBrowser(_browserShown);
        }
        catch
        {
            Detach();
            throw;
        }
    }

    public void Detach()
    {
        Dispatcher.VerifyAccess();
        ++_presentationVersion;
        CancelPendingPresentation();
        RemoveFocusObserver();
        CancelPendingContentFocus();
        ReleaseInputQueue();
        _lastFocusedChild = IntPtr.Zero;
        var original = _original;
        if (original is null) return;
        if (!IsWindowAlive(original.Handle)) { _original = null; return; }
        // Attempt every restoration step even when an earlier one fails. Keep the
        // snapshot on failure so a later explicit Detach can retry safely.
        bool restored = true;
        void RestoreStep(Action action)
        {
            try { action(); }
            catch (Exception ex) { restored = false; Diagnostic?.Invoke($"Browser restoration failed: {ex.Message}"); }
        }
        ShowWindow(original.Handle, SwHide);
        RestoreStep(() => ChangeParent(original.Handle, original.Parent));
        RestoreStep(() => WriteWindowLong(original.Handle, GwlStyle, original.Style));
        RestoreStep(() => WriteWindowLong(original.Handle, GwlExStyle, original.ExStyle));
        RestoreStep(() =>
        {
            var rect = original.Rect;
            if (!SetWindowPos(original.Handle, IntPtr.Zero, rect.Left, rect.Top,
                rect.Right - rect.Left, rect.Bottom - rect.Top,
                SwpNoZOrder | SwpNoActivate | SwpFrameChanged | SwpNoOwnerZOrder))
                throw LastError("Restore browser bounds");
        });
        if (original.WasVisible) ShowWindow(original.Handle, SwShow);
        if (restored)
        {
            _original = null;
            OwnedWindows = null;
            _contentInsets = new(0, 0, 0, 0, false);
        }
    }

    public void ShowBrowser(bool visible)
    {
        Dispatcher.VerifyAccess();
        _browserShown = visible;
        if (!visible) _needsPresentation = true;
        int version = ++_presentationVersion;
        CancelPendingPresentation();
        if (!visible) CancelPendingContentFocus();
        // Keep the selected browser's input queue connected for the complete
        // interaction, not just during SetFocus. Detaching immediately makes
        // native mouse focus/key state diverge between WPF and Chromium; DOM
        // WM_CHAR tests bypass that failure and cannot validate Ctrl+V or IME.
        if (visible && IsWindowAlive(AttachedHandle)) EnsureInputQueue();
        else ReleaseInputQueue();
        if (IsWindowAlive(AttachedHandle))
            ShowWindow(AttachedHandle, visible ? IsIconic(AttachedHandle) ? SwRestore : SwShow : SwHide);
        if (visible)
        {
            RefreshBounds();
            // HwndHost changes its native parent's visibility during layout.
            // Chromium may miss the ancestor-show transition and keep WebContents
            // hidden even though every HWND reports visible. Reassert the browser
            // visibility after layout, once per selection, without reloading it.
            var hwnd = AttachedHandle;
            if (_needsPresentation || !IsWindowVisible(_container))
                Dispatcher.BeginInvoke(new Action(() =>
                {
                    if (version != _presentationVersion || !_browserShown || AttachedHandle != hwnd) return;
                    // Native ancestor visibility/position notifications are handled
                    // on Chromium's other thread after WPF layout completes. One
                    // bounded delay lets those queued notifications settle before
                    // reasserting WebContents visibility. Never repeat on polling.
                    var pending = new DispatcherTimer(DispatcherPriority.Background, Dispatcher) {
                        Interval = TimeSpan.FromMilliseconds(100)
                    };
                    _pendingPresentation = pending;
                    pending.Tick += (_, _) =>
                    {
                        pending.Stop();
                        if (_pendingPresentation == pending) _pendingPresentation = null;
                        if (version != _presentationVersion || !_browserShown || AttachedHandle != hwnd
                            || !IsWindowAlive(hwnd) || !IsVisible || !IsWindowVisible(_container)) return;
                        bool restoreContentFocus = IsBrowserChild(GetFocus())
                            && GetForegroundWindow() == GetAncestor(_container, 2);
                        ShowWindow(hwnd, SwHide);
                        ShowWindow(hwnd, IsIconic(hwnd) ? SwRestore : SwShow);
                        RefreshBounds();
                        _needsPresentation = false;
                        if (restoreContentFocus) FocusBrowser();
                    };
                    pending.Start();
                }), DispatcherPriority.Loaded);
        }
    }

    private void CancelPendingPresentation()
    {
        _pendingPresentation?.Stop();
        _pendingPresentation = null;
    }

    public void RefreshBounds()
    {
        Dispatcher.VerifyAccess();
        if (_container == IntPtr.Zero || !IsWindowAlive(AttachedHandle)) return;
        // Chromium's own caption/system command can still minimize the foreign
        // child even after its Win32 frame is removed. Keep the selected browser
        // restored inside its host; never restore an inactive profile or WPF root.
        if (_browserShown && IsIconic(AttachedHandle)) ShowWindow(AttachedHandle, SwRestore);
        // Native client coordinates already reflect the host's DPI. Do not feed
        // WPF device-independent units to MoveWindow or multiply DPI a second time.
        if (!GetClientRect(_container, out var rect)) { ReportLastError("Read host client bounds"); return; }
        int width = Math.Max(1, rect.Right - rect.Left);
        int height = Math.Max(1, rect.Bottom - rect.Top);
        MeasureContentInsets();
        var insets = _contentInsets;
        int x = -insets.Left, y = -insets.Top;
        int browserWidth = width + insets.Left + insets.Right;
        int browserHeight = height + insets.Top + insets.Bottom;
        if (GetWindowRect(AttachedHandle, out var browserRect))
        {
            var corners = new[] { new NativePoint(browserRect.Left, browserRect.Top), new NativePoint(browserRect.Right, browserRect.Bottom) };
            Marshal.SetLastPInvokeError(0);
            var mapped = MapWindowPoints(IntPtr.Zero, _container, corners, 2);
            if ((mapped != 0 || Marshal.GetLastWin32Error() == 0)
                && corners[0].X == x && corners[0].Y == y
                && corners[1].X == x + browserWidth && corners[1].Y == y + browserHeight)
                return;
        }
        // Polling can call this every two seconds. A no-op MoveWindow still sends
        // native positioning/size messages to Chromium and can disturb its input
        // proxy or focus. Only send those messages when physical geometry changes.
        if (!MoveWindow(AttachedHandle, x, y, browserWidth, browserHeight, true))
            ReportLastError("Resize embedded browser");
    }

    private void MeasureContentInsets()
    {
        if (!_browserShown || !IsWindowVisible(AttachedHandle)) return;
        uint dpi = GetDpiForWindow(AttachedHandle);
        if (dpi != _insetDpi)
        {
            _insetDpi = dpi;
            _insetAttempts = 0;
            _contentInsets = new(0, 0, 0, 0, false);
        }
        if (_contentInsets.Measured || ++_insetAttempts > 5) return;
        try
        {
            // Chromium draws its caption inside the native client area, so
            // removing WS_CAPTION alone cannot remove it. UIA's native Views
            // provider exposes the content container in physical screen pixels.
            // Read only that container, never DOM text or guessed OS metrics.
            var frame = AutomationElement.FromHandle(AttachedHandle);
            var content = frame.FindFirst(TreeScope.Descendants,
                new PropertyCondition(AutomationElement.ClassNameProperty, "MultiContentsView"));
            if (content is null || !GetWindowRect(AttachedHandle, out var outer)) return;
            var bounds = content.Current.BoundingRectangle;
            if (bounds.IsEmpty || bounds.Width < 1 || bounds.Height < 1) return;
            GetWindowThreadProcessId(AttachedHandle, out var pid);
            if (content.Current.ProcessId != (int)pid) return;
            int left = (int)Math.Round(bounds.Left - outer.Left);
            int top = (int)Math.Round(bounds.Top - outer.Top);
            int right = (int)Math.Round(outer.Right - bounds.Right);
            int bottom = (int)Math.Round(outer.Bottom - bounds.Bottom);
            double scale = Math.Max(1, dpi) / 96d;
            if (left < 0 || right < 0 || top < 0 || bottom < 0
                || left > 48 * scale || right > 48 * scale || bottom > 48 * scale || top > 180 * scale
                || bounds.Width < (outer.Right - outer.Left) * .75
                || bounds.Height < (outer.Bottom - outer.Top) * .5) return;
            _contentInsets = new(left, top, right, bottom, true);
            Diagnostic?.Invoke($"Native content insets measured: left {left}, top {top}, right {right}, bottom {bottom}, DPI {dpi}.");
        }
        catch (Exception ex) when (ex is ElementNotAvailableException or InvalidOperationException or COMException)
        {
            Diagnostic?.Invoke($"Native content bounds not yet available: {ex.GetType().Name}.");
        }
    }

    protected override void OnWindowPositionChanged(Rect rcBoundingBox)
    {
        base.OnWindowPositionChanged(rcBoundingBox);
        RefreshBounds();
    }

    protected override void OnGotKeyboardFocus(KeyboardFocusChangedEventArgs e)
    {
        base.OnGotKeyboardFocus(e);
        if (!_synchronizingFocus) FocusBrowser();
    }

    public void FocusBrowser()
    {
        Dispatcher.VerifyAccess();
        var hwnd = AttachedHandle;
        if (!IsWindowAlive(hwnd) || !_browserShown) return;
        EnsureInputQueue();
        // Chrome_WidgetWin_1 is the app frame, not the web content input HWND.
        // Preserve a live native focused descendant, otherwise find the visible
        // renderer widget. Never redirect keys by synthesizing WM_CHAR/CDP input.
        var focused = GetFocus();
        var target = FindRenderWidget(hwnd);
        if (target == IntPtr.Zero && focused != hwnd && IsBrowserChild(focused)) target = focused;
        if (target == IntPtr.Zero && _lastFocusedChild != hwnd && IsBrowserChild(_lastFocusedChild)
            && IsWindowVisible(_lastFocusedChild)) target = _lastFocusedChild;
        if (target == IntPtr.Zero) target = hwnd;
        SetFocus(target);
        if (GetFocus() != hwnd && IsBrowserChild(GetFocus())) _lastFocusedChild = GetFocus();
    }

    protected override bool HasFocusWithinCore() => IsBrowserChild(GetFocus()) || base.HasFocusWithinCore();

    protected override bool TabIntoCore(TraversalRequest request)
    {
        FocusBrowser();
        return IsBrowserChild(GetFocus());
    }

    private bool IsBrowserChild(IntPtr hwnd) => IsWindowAlive(hwnd) && IsWindowAlive(AttachedHandle)
        && (hwnd == AttachedHandle || IsChild(AttachedHandle, hwnd));

    private void EnsureInputQueue()
    {
        if (!_browserShown || !IsWindowAlive(AttachedHandle)) return;
        var browserThread = GetWindowThreadProcessId(AttachedHandle, out _);
        if (_linkedBrowserThread == browserThread || browserThread == GetCurrentThreadId()) return;
        ReleaseInputQueue();
        if (!AttachThreadInput(GetCurrentThreadId(), browserThread, true))
            throw LastError("Connect selected browser keyboard input queue");
        _linkedBrowserThread = browserThread;
        Diagnostic?.Invoke($"Browser input queue connected: UI thread {GetCurrentThreadId()}, browser thread {browserThread}.");
    }

    private void ReleaseInputQueue()
    {
        if (_linkedBrowserThread == 0) return;
        // A hidden/closing browser must not retain OS focus. Focusing the shell
        // root does not commandeer its address textbox or any WPF dialog.
        if (IsBrowserChild(GetFocus()) && _container != IntPtr.Zero)
            SetFocus(GetAncestor(_container, 2));
        if (!AttachThreadInput(GetCurrentThreadId(), _linkedBrowserThread, false))
            ReportLastError("Disconnect browser keyboard input queue");
        _linkedBrowserThread = 0;
    }

    private void InstallFocusObserver(IntPtr hwnd)
    {
        GetWindowThreadProcessId(hwnd, out var pid);
        _focusCallback = (_, eventType, focusedHwnd, _, _, _, eventTime) =>
        {
            void Observe()
            {
                if (eventType == 0x0008) FocusFromNativeMouseCapture(focusedHwnd, eventTime);
                else if (eventType is 0x0016 or 0x0017)
                {
                    // Run after native minimize processing, then restore only our
                    // selected attached browser and recompute physical bounds.
                    Dispatcher.BeginInvoke(new Action(() =>
                    {
                        if (_browserShown && focusedHwnd == AttachedHandle) RefreshBounds();
                    }), System.Windows.Threading.DispatcherPriority.Background);
                }
                else SynchronizeNativeFocus(focusedHwnd);
            }
            if (Dispatcher.CheckAccess()) Observe();
            else Dispatcher.BeginInvoke(new Action(Observe));
        };
        // Observe only this browser process; no global keyboard/mouse hooks or
        // input interception. A real native click bypasses WPF routed mouse events.
        _focusHook = SetWinEventHook(0x8005, 0x8005, IntPtr.Zero, _focusCallback, pid, 0, 0);
        if (_focusHook == IntPtr.Zero) ReportLastError("Observe browser native focus");
        _captureHook = SetWinEventHook(0x0008, 0x0008, IntPtr.Zero, _focusCallback, pid, 0, 0);
        if (_captureHook == IntPtr.Zero) ReportLastError("Observe browser native mouse capture");
        _minimizeHook = SetWinEventHook(0x0016, 0x0017, IntPtr.Zero, _focusCallback, pid, 0, 0);
        if (_minimizeHook == IntPtr.Zero) ReportLastError("Observe attached browser minimize state");
    }

    private void FocusFromNativeMouseCapture(IntPtr eventHwnd, uint eventTime)
    {
        // Chromium can focus the DOM input while retaining WPF's native HWND as
        // OS keyboard focus after SetParent. A focus observer alone never fixes
        // that state. Browser-owned mouse capture is a native click signal even
        // though the foreign child does not raise a WPF routed MouseDown event.
        if (!_browserShown || !IsBrowserChild(eventHwnd) || _synchronizingFocus) return;
        if (unchecked(GetTickCount() - eventTime) > 250) return;
        if (GetForegroundWindow() != GetAncestor(_container, 2)) return;
        if (!GetCursorPos(out var pointer) || !IsBrowserChild(WindowFromPoint(pointer))) return;
        EnsureInputQueue();
        _synchronizingFocus = true;
        try
        {
            Keyboard.Focus(this);
            FocusBrowser();
        }
        finally { _synchronizingFocus = false; }
        Diagnostic?.Invoke($"Browser native mouse capture focused HWND 0x{GetFocus().ToInt64():X}.");
        ScheduleContentFocus();
    }

    private void ScheduleContentFocus()
    {
        CancelPendingContentFocus();
        // Chrome's native renderer/IME proxy can be created lazily by the click.
        // CaptureStart arrives before that HWND exists: focusing the frame alone
        // leaves DOM focus correct but loses real keyboard and clipboard input.
        int attempts = 0;
        _pendingContentFocus = new DispatcherTimer(DispatcherPriority.Input, Dispatcher)
        {
            Interval = TimeSpan.FromMilliseconds(20)
        };
        _pendingContentFocus.Tick += (_, _) =>
        {
            if (++attempts >= 15 || !_browserShown || !IsWindowAlive(AttachedHandle)
                || Keyboard.FocusedElement != this || !IsBrowserChild(GetFocus())
                || GetForegroundWindow() != GetAncestor(_container, 2)
                || !GetCursorPos(out var pointer) || !IsBrowserChild(WindowFromPoint(pointer)))
            {
                CancelPendingContentFocus();
                return;
            }
            var renderer = FindRenderWidget(AttachedHandle);
            if (renderer == IntPtr.Zero) return;
            SetFocus(renderer);
            if (GetFocus() == renderer)
            {
                _lastFocusedChild = renderer;
                Diagnostic?.Invoke($"Browser content keyboard focus confirmed HWND 0x{renderer.ToInt64():X}.");
                CancelPendingContentFocus();
            }
        };
        _pendingContentFocus.Start();
    }

    private void CancelPendingContentFocus()
    {
        _pendingContentFocus?.Stop();
        _pendingContentFocus = null;
    }

    private void SynchronizeNativeFocus(IntPtr eventHwnd)
    {
        if (!_browserShown || _synchronizingFocus || !IsBrowserChild(eventHwnd)) return;
        // Out-of-context notifications can be delayed. Never steal focus after
        // the user has already clicked the address bar, settings or another app.
        var current = GetFocus();
        if (!IsBrowserChild(current) || GetForegroundWindow() != GetAncestor(_container, 2)) return;
        if (current != AttachedHandle) _lastFocusedChild = current;
        if (Keyboard.FocusedElement == this) return;
        _synchronizingFocus = true;
        try
        {
            Keyboard.Focus(this);
            if (IsBrowserChild(current) && _browserShown) SetFocus(current);
        }
        finally { _synchronizingFocus = false; }
    }

    private void RemoveFocusObserver()
    {
        if (_focusHook != IntPtr.Zero) UnhookWinEvent(_focusHook);
        if (_captureHook != IntPtr.Zero) UnhookWinEvent(_captureHook);
        if (_minimizeHook != IntPtr.Zero) UnhookWinEvent(_minimizeHook);
        _focusHook = IntPtr.Zero;
        _captureHook = IntPtr.Zero;
        _minimizeHook = IntPtr.Zero;
        _focusCallback = null;
    }

    private static IntPtr FindRenderWidget(IntPtr browser)
    {
        IntPtr result = IntPtr.Zero;
        EnumChildWindows(browser, (child, _) =>
        {
            if (!IsWindowVisible(child)) return true;
            var className = new StringBuilder(256);
            GetClassName(child, className, className.Capacity);
            if (className.ToString() != "Chrome_RenderWidgetHostHWND") return true;
            result = child;
            return false;
        }, IntPtr.Zero);
        return result;
    }

    public static bool IsWindowAlive(IntPtr hwnd) => hwnd != IntPtr.Zero && IsWindow(hwnd);

    /// <summary>Read-only discovery; call again while a launched browser starts.</summary>
    public static IntPtr FindBrowserWindow(int pid)
    {
        if (pid <= 0) return IntPtr.Zero;
        IntPtr found = IntPtr.Zero;
        EnumWindows((hwnd, _) =>
        {
            GetWindowThreadProcessId(hwnd, out var windowPid);
            if (windowPid != (uint)pid || !IsWindowVisible(hwnd) || GetWindow(hwnd, GwOwner) != IntPtr.Zero) return true;
            var className = new StringBuilder(256);
            GetClassName(hwnd, className, className.Capacity);
            if (className.ToString() != "Chrome_WidgetWin_1") return true;
            found = hwnd;
            return false;
        }, IntPtr.Zero);
        return found;
    }

    private void ReportLastError(string operation) => Diagnostic?.Invoke(LastError(operation).Message);
    private static Win32Exception LastError(string operation) => new(Marshal.GetLastWin32Error(), operation);

    private static IntPtr ReadWindowLong(IntPtr hwnd, int index)
    {
        Marshal.SetLastPInvokeError(0);
        var value = IntPtr.Size == 8 ? GetWindowLongPtr64(hwnd, index) : new IntPtr(GetWindowLong32(hwnd, index));
        var error = Marshal.GetLastWin32Error();
        if (value == IntPtr.Zero && error != 0) throw new Win32Exception(error, "Read browser window style");
        return value;
    }

    private static void WriteWindowLong(IntPtr hwnd, int index, IntPtr value)
    {
        Marshal.SetLastPInvokeError(0);
        var previous = IntPtr.Size == 8 ? SetWindowLongPtr64(hwnd, index, value) : new IntPtr(SetWindowLong32(hwnd, index, value.ToInt32()));
        var error = Marshal.GetLastWin32Error();
        if (previous == IntPtr.Zero && error != 0) throw new Win32Exception(error, "Set browser window style");
    }

    private static void ChangeParent(IntPtr hwnd, IntPtr parent)
    {
        Marshal.SetLastPInvokeError(0);
        var previous = SetParent(hwnd, parent);
        var error = Marshal.GetLastWin32Error();
        if (previous == IntPtr.Zero && error != 0) throw new Win32Exception(error, "Attach/restore browser parent");
    }

    private static void ApplyFrame(IntPtr hwnd)
    {
        const uint noMove = 0x0002;
        const uint noSize = 0x0001;
        if (!SetWindowPos(hwnd, IntPtr.Zero, 0, 0, 0, 0, SwpNoZOrder | SwpNoActivate | SwpFrameChanged | noMove | noSize))
            throw LastError("Apply embedded browser frame");
    }

    private sealed record WindowSnapshot(IntPtr Handle, IntPtr Parent, IntPtr Style, IntPtr ExStyle, NativeRect Rect, bool WasVisible);
    public sealed record BrowserContentInsets(int Left, int Top, int Right, int Bottom, bool Measured);
    private delegate bool EnumWindowsProc(IntPtr hwnd, IntPtr lParam);
    private delegate void WinEventProc(IntPtr hook, uint eventType, IntPtr hwnd, int objectId, int childId, uint threadId, uint time);
    [StructLayout(LayoutKind.Sequential)] private struct NativeRect
    {
        public int Left, Top, Right, Bottom;
        public NativeRect(int left, int top, int right, int bottom) { Left = left; Top = top; Right = right; Bottom = bottom; }
    }
    [StructLayout(LayoutKind.Sequential)] private struct NativePoint
    {
        public int X, Y;
        public NativePoint(int x, int y) { X = x; Y = y; }
    }

    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)] private static extern IntPtr CreateWindowEx(uint exStyle, string className, string windowName, uint style, int x, int y, int width, int height, IntPtr parent, IntPtr menu, IntPtr instance, IntPtr parameter);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)] private static extern IntPtr GetModuleHandle(string? moduleName);
    [DllImport("user32.dll", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool DestroyWindow(IntPtr hwnd);
    [DllImport("user32.dll")] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool IsWindow(IntPtr hwnd);
    [DllImport("user32.dll")] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool IsWindowVisible(IntPtr hwnd);
    [DllImport("user32.dll")] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool IsIconic(IntPtr hwnd);
    [DllImport("user32.dll")] private static extern IntPtr GetParent(IntPtr hwnd);
    [DllImport("user32.dll", SetLastError = true)] private static extern IntPtr SetParent(IntPtr hwnd, IntPtr parent);
    [DllImport("user32.dll", EntryPoint = "GetWindowLongPtrW", SetLastError = true)] private static extern IntPtr GetWindowLongPtr64(IntPtr hwnd, int index);
    [DllImport("user32.dll", EntryPoint = "SetWindowLongPtrW", SetLastError = true)] private static extern IntPtr SetWindowLongPtr64(IntPtr hwnd, int index, IntPtr value);
    [DllImport("user32.dll", EntryPoint = "GetWindowLongW", SetLastError = true)] private static extern int GetWindowLong32(IntPtr hwnd, int index);
    [DllImport("user32.dll", EntryPoint = "SetWindowLongW", SetLastError = true)] private static extern int SetWindowLong32(IntPtr hwnd, int index, int value);
    [DllImport("user32.dll", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool GetWindowRect(IntPtr hwnd, out NativeRect rect);
    [DllImport("user32.dll", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool GetClientRect(IntPtr hwnd, out NativeRect rect);
    [DllImport("user32.dll", SetLastError = true)] private static extern int MapWindowPoints(IntPtr from, IntPtr to, [In, Out] NativePoint[] points, uint count);
    [DllImport("user32.dll", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool MoveWindow(IntPtr hwnd, int x, int y, int width, int height, [MarshalAs(UnmanagedType.Bool)] bool repaint);
    [DllImport("user32.dll", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool SetWindowPos(IntPtr hwnd, IntPtr after, int x, int y, int width, int height, uint flags);
    [DllImport("user32.dll")] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool ShowWindow(IntPtr hwnd, int command);
    [DllImport("user32.dll")] private static extern IntPtr SetFocus(IntPtr hwnd);
    [DllImport("user32.dll")] private static extern IntPtr GetFocus();
    [DllImport("user32.dll")] private static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool GetCursorPos(out NativePoint point);
    [DllImport("user32.dll")] private static extern IntPtr WindowFromPoint(NativePoint point);
    [DllImport("kernel32.dll")] private static extern uint GetTickCount();
    [DllImport("user32.dll")] private static extern IntPtr GetAncestor(IntPtr hwnd, uint flags);
    [DllImport("user32.dll")] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool IsChild(IntPtr parent, IntPtr child);
    [DllImport("user32.dll")] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool EnumChildWindows(IntPtr parent, EnumWindowsProc callback, IntPtr parameter);
    [DllImport("user32.dll", SetLastError = true)] private static extern IntPtr SetWinEventHook(uint eventMin, uint eventMax, IntPtr module, WinEventProc callback, uint processId, uint threadId, uint flags);
    [DllImport("user32.dll")] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool UnhookWinEvent(IntPtr hook);
    [DllImport("user32.dll", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool AttachThreadInput(uint thread, uint attachedTo, [MarshalAs(UnmanagedType.Bool)] bool attach);
    [DllImport("kernel32.dll")] private static extern uint GetCurrentThreadId();
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);
    [DllImport("user32.dll")] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool EnumWindows(EnumWindowsProc callback, IntPtr parameter);
    [DllImport("user32.dll")] private static extern IntPtr GetWindow(IntPtr hwnd, uint command);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern int GetClassName(IntPtr hwnd, StringBuilder className, int capacity);
    [DllImport("user32.dll")] private static extern uint GetDpiForWindow(IntPtr hwnd);
}
