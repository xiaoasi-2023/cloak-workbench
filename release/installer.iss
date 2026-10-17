; Internal offline distribution. Values are generated from branding.json.
#include "build-config.iss"

[Setup]
AppId={{0B6F0C77-5B51-485D-8F42-4990B894E766}
AppName={#ProductName}
AppVersion={#ProductVersion}
AppVerName={#ProductName} {#DisplayVersion}
AppPublisher={#Publisher}
AppPublisherURL={#Homepage}
AppSupportURL={#SupportURL}
DefaultDirName={localappdata}\Programs\QihangWorkbench
DefaultGroupName={#ProductName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
SetupIconFile={#BrandIcon}
UninstallDisplayIcon={app}\Qihang.exe
OutputDir={#OutputPath}
OutputBaseFilename=Qihang-Setup-{#DisplayVersion}-win-x64
Compression=lzma2/normal
SolidCompression=yes
LZMABlockSize=32768
WizardStyle=modern
DisableWelcomePage=no
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
VersionInfoVersion={#FileVersion}
VersionInfoCompany={#Publisher}
VersionInfoProductName={#ProductName}
VersionInfoDescription={#ProductName} 安装程序

[Languages]
Name: "zhcn"; MessagesFile: "ChineseSimplified.isl"

[Messages]
WelcomeLabel1=欢迎安装 [name]
WelcomeLabel2=集中管理账号、代理与独立浏览器环境。%n%n安装包已包含所需运行组件，安装完成即可启动，无需额外配置运行环境。%n%n账号及浏览器资料保存在当前 Windows 用户目录中，卸载时保留。%n%n点击“下一步”继续。
FinishedHeadingLabel=[name] 安装完成
FinishedLabel=您现在可以启动 [name]，添加账号、配置代理并创建浏览器环境。%n%n后续也可通过桌面或开始菜单中的快捷方式打开工作台。

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："

[Files]
Source: "{#PayloadPath}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#ProductName}"; Filename: "{app}\Qihang.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\{#ProductName}"; Filename: "{app}\Qihang.exe"; WorkingDir: "{app}"; Tasks: desktopicon
Name: "{group}\卸载{#ProductName}"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\Qihang.exe"; Description: "启动{#ProductName}"; Flags: nowait postinstall skipifsilent

; Deliberately no UninstallDelete: user data lives outside {app} and is retained.
