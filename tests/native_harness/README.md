# Native browser acceptance checks

Run these commands from the project root on Windows:

```powershell
.\.tools\dotnet\dotnet.exe build tests\native_harness\NativeHarness.csproj -o .local\native-harness-build
.\.tools\dotnet\dotnet.exe .local\native-harness-build\NativeHarness.dll .local\native-harness-report.json
.\.tools\dotnet\dotnet.exe .local\native-harness-build\NativeHarness.dll --switch .local\native-switch-report.json --rapid
```

The first run checks native content geometry, popup ownership, the actual file
chooser's visibility/cancellation, and restoration of standalone window geometry
and styles after detaching.

The switch run creates two temporary browser profiles in two permanent native
hosts. It uses the production hide/layout/show sequence and verifies 16 actual
screen pixel samples, page visibility, input drafts, storage, URLs and unchanged
document identities. `--rapid` additionally performs 64 fast switches and 64
same-tab reselections before the presentation timer expires. Omit `--rapid` for
the ordinary switching regression.

The test links the production native host directly. It contains no synthetic
visibility, resize, repaint or browser-flag workaround. It briefly displays an
owned test window above other windows, without global keyboard/mouse input, and
refuses to read pixels if an unrelated app covers the sample point. It cleans up
only its temporary browser processes and profiles. CDP is used for read-only
page inspection in the switch run.

Native keyboard, IME candidate placement and other monitor DPI scales require
separate verification.
