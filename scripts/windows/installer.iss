#ifndef ReleaseDir
  #define ReleaseDir "..\..\dist\perdix-windows-x64"
#endif
#ifdef TestRoot
  #define ConfigDir TestRoot + "\profile\.perdix"
  #define DesktopDir TestRoot + "\desktop"
  #define ProgramsDir TestRoot + "\start-menu"
#else
  #define ConfigDir "{%USERPROFILE}\.perdix"
  #define DesktopDir "{userdesktop}"
  #define ProgramsDir "{userprograms}\Perdix"
#endif
[Setup]
#ifdef TestRoot
AppId=PerdixInstallerSmokeTest
#else
AppId={{8E97F0E1-E69E-4CC4-9D63-6252A389EC7C}
#endif
AppName=Perdix
AppVersion=0.0.0
#ifdef TestRoot
DefaultDirName={#TestRoot}\app
#else
DefaultDirName={localappdata}\Programs\Perdix
#endif
DefaultGroupName=Perdix
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
DisableProgramGroupPage=yes
DisableWelcomePage=no
OutputDir=..\..\dist
#ifdef TestRoot
OutputBaseFilename=Perdix-Setup-test
#else
OutputBaseFilename=Perdix-Setup-windows-x64
#endif
Compression=lzma2/fast
SolidCompression=yes
UninstallDisplayIcon={app}\perdix.exe
CloseApplications=no

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Files]
Source: "{#ReleaseDir}\perdix.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ReleaseDir}\server\*"; DestDir: "{app}\server"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#ReleaseDir}\README.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ReleaseDir}\credentials.example.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ReleaseDir}\perdix-vscode-0.0.1.vsix"; DestDir: "{app}"; Flags: ignoreversion
Source: "apply-install-settings.ps1"; Flags: dontcopy

[Icons]
Name: "{#DesktopDir}\Perdix"; Filename: "{app}\perdix.exe"; WorkingDir: "{userdocs}"
Name: "{#DesktopDir}\Perdix 키 설정"; Filename: "{sys}\notepad.exe"; Parameters: """{#ConfigDir}\credentials.json"""
Name: "{#DesktopDir}\Perdix 시작 안내"; Filename: "{sys}\notepad.exe"; Parameters: """{app}\README.txt"""
Name: "{#ProgramsDir}\Perdix"; Filename: "{app}\perdix.exe"; WorkingDir: "{userdocs}"

[Run]
#ifdef TestRoot
Filename: "{sys}\cmd.exe"; Parameters: "/C echo installed>""{#TestRoot}\vscode-installed.txt"""; StatusMsg: "VS Code 확장을 설정하는 중입니다..."; Flags: runhidden waituntilterminated
#else
Filename: "{code:GetVSCodeExecutable}"; Parameters: "--install-extension ""{app}\perdix-vscode-0.0.1.vsix"" --force"; StatusMsg: "Perdix VS Code 확장을 설치하는 중입니다..."; Flags: runhidden waituntilterminated; Check: HasVSCode
#endif
Filename: "{app}\perdix.exe"; WorkingDir: "{userdocs}"; Description: "Perdix 실행"; Flags: nowait postinstall skipifsilent

[Code]
var
  ProviderPage: TInputOptionWizardPage;
  KeyPage: TInputQueryWizardPage;
  SettingsFailed: Boolean;

function FindVSCodeExecutable(var Path: String): Boolean;
begin
  Path := ExpandConstant('{localappdata}\Programs\Microsoft VS Code\Code.exe');
  if FileExists(Path) then begin Result := True; Exit; end;
  Path := ExpandConstant('{autopf}\Microsoft VS Code\Code.exe');
  if FileExists(Path) then begin Result := True; Exit; end;
  if RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\App Paths\Code.exe', '', Path) and FileExists(Path) then begin Result := True; Exit; end;
  if RegQueryStringValue(HKLM, 'Software\Microsoft\Windows\CurrentVersion\App Paths\Code.exe', '', Path) and FileExists(Path) then begin Result := True; Exit; end;
  Path := '';
  Result := False;
end;

function HasVSCode: Boolean;
var
  Path: String;
begin
  Result := FindVSCodeExecutable(Path);
end;

function GetVSCodeExecutable(Param: String): String;
begin
  if not FindVSCodeExecutable(Result) then Result := '';
end;

function JsonEscape(Value: String): String;
begin
  StringChangeEx(Value, '\', '\\', True);
  StringChangeEx(Value, '"', '\"', True);
  StringChangeEx(Value, #13, '\r', True);
  StringChangeEx(Value, #10, '\n', True);
  StringChangeEx(Value, #9, '\t', True);
  Result := Value;
end;

procedure InitializeWizard;
begin
  ProviderPage := CreateInputOptionPage(wpSelectDir, '사용할 AI 선택',
    '기본으로 사용할 공급자를 선택하세요.', '설치 후 바로 사용할 기본 모델도 함께 설정합니다.', True, False);
  ProviderPage.Add('Claude (Anthropic)');
  ProviderPage.Add('OpenAI');
  ProviderPage.SelectedValueIndex := 0;
  KeyPage := CreateInputQueryPage(ProviderPage.ID, 'API 키 설정', '키를 붙여 넣으면 설정이 끝납니다.',
    '선택한 공급자의 API 키를 붙여 넣으세요. 로그인 비밀번호가 아닙니다.'#13#10 +
    '이미 저장된 키를 유지하려면 빈 칸으로 두세요. 키는 이 PC에만 저장됩니다.');
  KeyPage.Add('API 키 (입력 내용은 가려집니다):', True);
end;

function ApplySettings(ValidateOnly: Boolean): Boolean;
var
  InputFile, ExternalConfig, Provider, Params: String;
  Lines: TArrayOfString;
  ResultCode: Integer;
begin
  ExtractTemporaryFile('apply-install-settings.ps1');
  InputFile := ExpandConstant('{tmp}\perdix-settings-input.json');
  ExternalConfig := ExpandConstant('{param:CONFIG|}');
  if ExternalConfig <> '' then begin
    if not CopyFile(ExternalConfig, InputFile, False) then begin Result := False; Exit; end;
  end else begin
    Provider := 'anthropic';
    if ProviderPage.SelectedValueIndex = 1 then Provider := 'openai';
    SetArrayLength(Lines, 1);
    Lines[0] := '{"provider":"' + Provider + '","api_key":"' + JsonEscape(Trim(KeyPage.Values[0])) + '"}';
    if not SaveStringsToUTF8FileWithoutBOM(InputFile, Lines, False) then begin Result := False; Exit; end;
  end;
  try
    Params := '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + ExpandConstant('{tmp}\apply-install-settings.ps1') + '" -InputPath "' + InputFile + '"';
    Params := Params + ' -ConfigDirectory "' + ExpandConstant('{#ConfigDir}') + '"';
    if ValidateOnly then Params := Params + ' -ValidateOnly';
    Result := Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), Params, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Result := Result and (ResultCode = 0);
  finally
    DeleteFile(InputFile);
  end;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = KeyPage.ID) and not WizardSilent then begin
    Result := ApplySettings(True);
    if not Result then MsgBox('선택한 공급자의 API 키를 입력해 주세요. 기존 키가 있다면 키 파일의 형식과 쓰기 권한을 확인하세요.', mbError, MB_OK);
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if not ApplySettings(True) then Result := 'API 키 설정을 확인해 주세요. 키가 없으면 설치를 진행할 수 없습니다.';
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then begin
    if not ApplySettings(False) then begin
      SettingsFailed := True;
      RaiseException('API 키 설정 저장에 실패했습니다. 설정을 확인하고 설치를 다시 실행하세요.');
    end;
    KeyPage.Values[0] := '';
  end;
end;

function GetCustomSetupExitCode: Integer;
begin
  Result := 0;
  if SettingsFailed then Result := 1;
end;
