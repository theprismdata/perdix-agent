import AppKit
import Foundation

enum InstallError: Error {
    case invalidSettings
    case missingPayload
    case invalidArguments
}

struct InstallOptions {
    let payload: URL
    let destination: URL
    let config: URL
    let home: URL
    let provider: String
    let key: String
    let integrations: Bool
}

func readSettings(_ url: URL) throws -> [String: Any] {
    if !FileManager.default.fileExists(atPath: url.path) { return [:] }
    guard let value = try JSONSerialization.jsonObject(with: Data(contentsOf: url)) as? [String: Any] else {
        throw InstallError.invalidSettings
    }
    return value
}

func saveSettings(_ value: [String: Any], to url: URL) throws {
    let data = try JSONSerialization.data(withJSONObject: value, options: [.prettyPrinted, .sortedKeys])
    try data.write(to: url, options: .atomic)
    try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
}

func shellQuote(_ value: String) -> String {
    return "'" + value.replacingOccurrences(of: "'", with: "'\\''") + "'"
}

func replaceLink(_ name: String, target: URL, directory: URL) throws {
    let fm = FileManager.default
    let link = directory.appendingPathComponent(name)
    let temporary = directory.appendingPathComponent(".perdix-link-" + UUID().uuidString)
    try fm.createSymbolicLink(at: temporary, withDestinationURL: target)
    defer { try? fm.removeItem(at: temporary) }
    if fm.fileExists(atPath: link.path) || (try? fm.destinationOfSymbolicLink(atPath: link.path)) != nil {
        try fm.removeItem(at: link)
    }
    try fm.moveItem(at: temporary, to: link)
}

func install(_ options: InstallOptions) throws -> URL {
    let fm = FileManager.default
    guard ["anthropic", "openai"].contains(options.provider),
          !options.key.contains(where: { $0.isWhitespace }) else { throw InstallError.invalidSettings }
    guard fm.isExecutableFile(atPath: options.payload.appendingPathComponent("perdix").path),
          fm.isExecutableFile(atPath: options.payload.appendingPathComponent("server/perdix-server").path) else {
        throw InstallError.missingPayload
    }
    let credentialsURL = options.config.appendingPathComponent("credentials.json")
    var credentials = try readSettings(credentialsURL)
    if !options.key.isEmpty {
        var entry = credentials[options.provider] as? [String: Any] ?? [:]
        entry["api_key"] = options.key
        credentials[options.provider] = entry
    }
    try fm.createDirectory(at: options.destination, withIntermediateDirectories: true)
    let version = options.destination.appendingPathComponent("versions/" + UUID().uuidString)
    try fm.createDirectory(at: version.deletingLastPathComponent(), withIntermediateDirectories: true)
    try fm.copyItem(at: options.payload, to: version)
    try fm.createDirectory(at: options.config, withIntermediateDirectories: true)
    if !options.key.isEmpty { try saveSettings(credentials, to: credentialsURL) }
    let bin = options.home.appendingPathComponent(".local/bin")
    try fm.createDirectory(at: bin, withIntermediateDirectories: true)
    try replaceLink("perdix", target: version.appendingPathComponent("perdix"), directory: bin)
    try replaceLink("perdix-server", target: version.appendingPathComponent("server/perdix-server"), directory: bin)
    if options.integrations {
        let profile = options.home.appendingPathComponent(".zprofile")
        let pathLine = "export PATH=\"$HOME/.local/bin:$PATH\""
        let existing = fm.fileExists(atPath: profile.path) ? try String(contentsOf: profile, encoding: .utf8) : ""
        if !existing.contains(pathLine) {
            try (existing + "\n" + pathLine + "\n").write(to: profile, atomically: true, encoding: .utf8)
        }
        let applications = options.home.appendingPathComponent("Applications")
        try fm.createDirectory(at: applications, withIntermediateDirectories: true)
        let launcher = applications.appendingPathComponent("Perdix.command")
        try ("#!/bin/zsh\nexec " + shellQuote(bin.appendingPathComponent("perdix").path) + " \"$@\"\n")
            .write(to: launcher, atomically: true, encoding: .utf8)
        try fm.setAttributes([.posixPermissions: 0o755], ofItemAtPath: launcher.path)
        let code = URL(fileURLWithPath: "/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code")
        if fm.isExecutableFile(atPath: code.path) {
            let process = Process()
            process.executableURL = code
            process.arguments = ["--install-extension", version.appendingPathComponent("perdix.vsix").path, "--force"]
            process.standardOutput = FileHandle.nullDevice
            process.standardError = FileHandle.nullDevice
            try process.run()
            process.waitUntilExit()
            if process.terminationStatus != 0 {
                throw InstallError.invalidSettings
            }
        }
    }
    return version
}

func headlessInstall() throws {
    let args = Array(CommandLine.arguments.dropFirst())
    guard args.count == 6, args[0] == "--test-install" else { throw InstallError.invalidArguments }
    let key = String(data: FileHandle.standardInput.readDataToEndOfFile(), encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    let installed = try install(InstallOptions(
        payload: URL(fileURLWithPath: args[1]), destination: URL(fileURLWithPath: args[2]),
        config: URL(fileURLWithPath: args[3]), home: URL(fileURLWithPath: args[4]),
        provider: args[5], key: key, integrations: false
    ))
    print(installed.path)
}

func installerAlert() -> (NSAlert, NSPopUpButton, NSSecureTextField) {
    let alert = NSAlert()
    alert.messageText = "Perdix 설치"
    alert.informativeText = "CLI·서버와 VS Code 확장을 설치합니다. API 키는 비워 두면 기존 키나 환경변수를 사용합니다."
    alert.addButton(withTitle: "설치")
    alert.addButton(withTitle: "취소")
    let view = NSView(frame: NSRect(x: 0, y: 0, width: 420, height: 145))
    let provider = NSPopUpButton(frame: NSRect(x: 120, y: 105, width: 280, height: 26))
    provider.addItems(withTitles: ["Anthropic", "OpenAI"])
    let key = NSSecureTextField(frame: NSRect(x: 120, y: 62, width: 280, height: 26))
    provider.setAccessibilityLabel("공급자")
    key.setAccessibilityLabel("API 키")
    provider.nextKeyView = key
    for (text, y) in [("공급자", 110.0), ("API 키", 67.0)] {
        let label = NSTextField(labelWithString: text)
        label.frame = NSRect(x: 0, y: y, width: 110, height: 20)
        view.addSubview(label)
    }
    for field in [provider, key] as [NSView] { view.addSubview(field) }
    alert.accessoryView = view
    return (alert, provider, key)
}

func showInstaller() throws {
    let app = NSApplication.shared
    app.setActivationPolicy(.regular)
    app.finishLaunching()
    app.activate(ignoringOtherApps: true)
    let (alert, provider, key) = installerAlert()
    if alert.runModal() != .alertFirstButtonReturn { return }
    guard let resources = Bundle.main.resourceURL else { throw InstallError.missingPayload }
    let home = FileManager.default.homeDirectoryForCurrentUser
    let name = provider.indexOfSelectedItem == 0 ? "anthropic" : "openai"
    _ = try install(InstallOptions(
        payload: resources.appendingPathComponent("payload"),
        destination: home.appendingPathComponent("Library/Application Support/Perdix"),
        config: home.appendingPathComponent(".perdix"), home: home, provider: name,
        key: key.stringValue.trimmingCharacters(in: .whitespacesAndNewlines),
        integrations: true
    ))
    let done = NSAlert()
    done.messageText = "설치 완료"
    done.informativeText = "새 터미널에서 perdix를 실행하세요. Finder의 사용자 Applications/Perdix.command로도 실행할 수 있습니다. VS Code는 Developer: Reload Window 후 Perdix를 여세요."
    done.addButton(withTitle: "확인")
    done.runModal()
}

do {
    if CommandLine.arguments.count > 1 { try headlessInstall() }
    else { try showInstaller() }
} catch {
    if CommandLine.arguments.count > 1 {
        FileHandle.standardError.write(Data("설치 실패: 파일 권한·설정 형식·배포 파일을 확인하세요.\n".utf8))
        exit(1)
    }
    let alert = NSAlert()
    alert.messageText = "설치하지 못했습니다"
    alert.informativeText = "파일 권한·설정 형식·배포 파일을 확인하세요. 키나 설정 파일을 삭제하지 말고 다시 실행해 주세요."
    alert.runModal()
    exit(1)
}
