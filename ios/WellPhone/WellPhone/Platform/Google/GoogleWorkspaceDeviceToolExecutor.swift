import Foundation
@preconcurrency import GoogleSignIn
import UIKit

@MainActor
final class GoogleWorkspaceDeviceToolExecutor {
    private let session: URLSession
    private let configuredClientID: String?

    private let gmailScope = "https://www.googleapis.com/auth/gmail.readonly"
    private let driveFileScope = "https://www.googleapis.com/auth/drive.file"

    init(
        session: URLSession = .shared,
        clientIDOverride: String? = nil
    ) {
        self.session = session
        configuredClientID = clientIDOverride
            ?? Bundle.main.object(forInfoDictionaryKey: "GIDClientID") as? String
    }

    func execute(_ request: DeviceToolRequest) async -> DeviceToolExecutionResult {
        do {
            switch request.toolName {
            case "google.gmail.search":
                return .completed(try await searchGmail(request.arguments))
            case "google.drive.upload-text":
                return .completed(try await uploadDriveText(request.arguments))
            default:
                return .failed(
                    code: "unsupported_google_tool",
                    message: "Google Workspace 不支持设备工具：\(request.toolName)"
                )
            }
        } catch let error as GoogleWorkspaceError {
            return .failed(code: error.code, message: error.localizedDescription)
        } catch {
            return .failed(
                code: "google_workspace_failed",
                message: "Google Workspace 操作失败：\(error.localizedDescription)"
            )
        }
    }

    private func searchGmail(
        _ arguments: [String: JSONValue]
    ) async throws -> [String: JSONValue] {
        guard case .string(let query) = arguments["query"],
              !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw GoogleWorkspaceError.invalidArguments("Gmail 搜索缺少 query。")
        }
        let maxResults: Int
        if case .number(let value) = arguments["maxResults"] {
            maxResults = min(max(Int(value), 1), 20)
        } else {
            maxResults = 12
        }
        let token = try await accessToken(scopes: [gmailScope])
        var components = URLComponents(
            string: "https://gmail.googleapis.com/gmail/v1/users/me/messages"
        )!
        components.queryItems = [
            URLQueryItem(name: "q", value: query),
            URLQueryItem(name: "maxResults", value: String(maxResults)),
        ]
        let listing: GmailMessageList = try await apiRequest(
            components.url!, token: token
        )
        var messages: [JSONValue] = []
        for reference in listing.messages ?? [] {
            let url = URL(
                string: "https://gmail.googleapis.com/gmail/v1/users/me/messages/\(reference.id)?format=full"
            )!
            let message: GmailMessageResource = try await apiRequest(url, token: token)
            messages.append(.object(message.normalized))
        }
        return [
            "messages": .array(messages),
            "resultCount": .number(Double(messages.count)),
            "source": .string("gmail-api-device"),
        ]
    }

    private func uploadDriveText(
        _ arguments: [String: JSONValue]
    ) async throws -> [String: JSONValue] {
        guard case .string(let name) = arguments["name"], !name.isEmpty,
              case .string(let content) = arguments["content"],
              case .string(let mimeType) = arguments["mimeType"], !mimeType.isEmpty,
              case .string(let idempotencyKey) = arguments["idempotencyKey"],
              !idempotencyKey.isEmpty else {
            throw GoogleWorkspaceError.invalidArguments("Drive 上传参数不完整。")
        }
        let folderID: String?
        if case .string(let value) = arguments["folderId"], !value.isEmpty {
            folderID = value
        } else {
            folderID = nil
        }
        let token = try await accessToken(scopes: [driveFileScope])
        let bytes = if mimeType == "application/pdf" {
            try BusinessTripPDFRenderer.render(markdown: content)
        } else {
            Data(content.utf8)
        }

        if let existing = try await existingDriveFile(
            idempotencyKey: idempotencyKey,
            folderID: folderID,
            token: token
        ), try await driveFileMatches(existing.id, data: bytes, token: token) {
            return existing.result(verified: true, reused: true)
        }

        let boundary = "wellphone-\(UUID().uuidString.lowercased())"
        var metadata: [String: Any] = [
            "name": name,
            "mimeType": mimeType,
            "appProperties": ["wellphoneTaskId": idempotencyKey],
        ]
        if let folderID { metadata["parents"] = [folderID] }
        let metadataData = try JSONSerialization.data(withJSONObject: metadata)
        var body = Data()
        body.append(Data("--\(boundary)\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".utf8))
        body.append(metadataData)
        body.append(Data("\r\n--\(boundary)\r\nContent-Type: \(mimeType)\r\n\r\n".utf8))
        body.append(bytes)
        body.append(Data("\r\n--\(boundary)--\r\n".utf8))

        let url = URL(
            string: "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id,name,mimeType,webViewLink"
        )!
        var request = authorizedRequest(url, token: token)
        request.httpMethod = "POST"
        request.setValue(
            "multipart/related; boundary=\(boundary)",
            forHTTPHeaderField: "Content-Type"
        )
        request.httpBody = body
        let file: DriveFile = try await response(for: request)
        let verified = try await driveFileMatches(file.id, data: bytes, token: token)
        guard verified else { throw GoogleWorkspaceError.verificationFailed }
        return file.result(verified: true, reused: false)
    }

    private func existingDriveFile(
        idempotencyKey: String,
        folderID: String?,
        token: String
    ) async throws -> DriveFile? {
        let escaped = idempotencyKey.replacingOccurrences(of: "'", with: "\\'")
        var clauses = [
            "trashed = false",
            "appProperties has { key='wellphoneTaskId' and value='\(escaped)' }",
        ]
        if let folderID {
            let escapedFolder = folderID.replacingOccurrences(of: "'", with: "\\'")
            clauses.append("'\(escapedFolder)' in parents")
        }
        var components = URLComponents(string: "https://www.googleapis.com/drive/v3/files")!
        components.queryItems = [
            URLQueryItem(name: "q", value: clauses.joined(separator: " and ")),
            URLQueryItem(name: "spaces", value: "drive"),
            URLQueryItem(name: "fields", value: "files(id,name,mimeType,webViewLink)"),
            URLQueryItem(name: "pageSize", value: "1"),
        ]
        let result: DriveFileList = try await apiRequest(components.url!, token: token)
        return result.files.first
    }

    private func driveFileMatches(
        _ fileID: String,
        data: Data,
        token: String
    ) async throws -> Bool {
        let url = URL(
            string: "https://www.googleapis.com/drive/v3/files/\(fileID)?alt=media"
        )!
        var request = authorizedRequest(url, token: token)
        request.httpMethod = "GET"
        let (downloaded, response) = try await session.data(for: request)
        try validate(response: response, data: downloaded)
        return downloaded == data
    }

    private func accessToken(scopes: [String]) async throws -> String {
        guard let clientID = configuredClientID,
              !clientID.isEmpty,
              !clientID.contains("$(") else {
            throw GoogleWorkspaceError.notConfigured
        }
        GIDSignIn.sharedInstance.configuration = GIDConfiguration(clientID: clientID)
        var user = GIDSignIn.sharedInstance.currentUser
        if user == nil {
            user = try? await restorePreviousSignIn()
        }
        guard let presenting = UIApplication.shared.wellPhoneTopViewController else {
            throw GoogleWorkspaceError.presentationUnavailable
        }
        if user == nil {
            user = try await signIn(presenting: presenting, scopes: scopes)
        }
        guard var resolvedUser = user else { throw GoogleWorkspaceError.authorizationRequired }
        let granted = Set(resolvedUser.grantedScopes ?? [])
        let missing = scopes.filter { !granted.contains($0) }
        if !missing.isEmpty {
            resolvedUser = try await addScopes(missing, user: resolvedUser, presenting: presenting)
        }
        resolvedUser = try await refresh(resolvedUser)
        return resolvedUser.accessToken.tokenString
    }

    private func restorePreviousSignIn() async throws -> GIDGoogleUser {
        try await withCheckedThrowingContinuation { continuation in
            GIDSignIn.sharedInstance.restorePreviousSignIn { user, error in
                if let user { continuation.resume(returning: user) }
                else { continuation.resume(throwing: error ?? GoogleWorkspaceError.authorizationRequired) }
            }
        }
    }

    private func signIn(
        presenting: UIViewController,
        scopes: [String]
    ) async throws -> GIDGoogleUser {
        try await withCheckedThrowingContinuation { continuation in
            GIDSignIn.sharedInstance.signIn(
                withPresenting: presenting,
                hint: nil,
                additionalScopes: scopes
            ) { result, error in
                if let result { continuation.resume(returning: result.user) }
                else { continuation.resume(throwing: error ?? GoogleWorkspaceError.authorizationRequired) }
            }
        }
    }

    private func addScopes(
        _ scopes: [String],
        user: GIDGoogleUser,
        presenting: UIViewController
    ) async throws -> GIDGoogleUser {
        try await withCheckedThrowingContinuation { continuation in
            user.addScopes(scopes, presenting: presenting) { result, error in
                if let result { continuation.resume(returning: result.user) }
                else { continuation.resume(throwing: error ?? GoogleWorkspaceError.authorizationRequired) }
            }
        }
    }

    private func refresh(_ user: GIDGoogleUser) async throws -> GIDGoogleUser {
        try await withCheckedThrowingContinuation { continuation in
            user.refreshTokensIfNeeded { refreshed, error in
                if let refreshed { continuation.resume(returning: refreshed) }
                else { continuation.resume(throwing: error ?? GoogleWorkspaceError.authorizationRequired) }
            }
        }
    }

    private func apiRequest<Response: Decodable>(
        _ url: URL,
        token: String
    ) async throws -> Response {
        var request = authorizedRequest(url, token: token)
        request.httpMethod = "GET"
        return try await response(for: request)
    }

    private func response<Response: Decodable>(
        for request: URLRequest
    ) async throws -> Response {
        let (data, response) = try await session.data(for: request)
        try validate(response: response, data: data)
        return try JSONDecoder().decode(Response.self, from: data)
    }

    private func authorizedRequest(_ url: URL, token: String) -> URLRequest {
        var request = URLRequest(url: url)
        request.timeoutInterval = 45
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        return request
    }

    private func validate(response: URLResponse, data: Data) throws {
        guard let http = response as? HTTPURLResponse,
              (200..<300).contains(http.statusCode) else {
            let detail = String(data: data.prefix(1_000), encoding: .utf8) ?? ""
            throw GoogleWorkspaceError.apiRejected(detail)
        }
    }
}

private struct GmailMessageList: Decodable {
    struct Reference: Decodable { let id: String }
    let messages: [Reference]?
}

private struct GmailMessageResource: Decodable {
    struct Payload: Decodable {
        struct Header: Decodable { let name: String; let value: String }
        struct Body: Decodable { let data: String? }
        let mimeType: String?
        let headers: [Header]?
        let body: Body?
        let parts: [Payload]?
    }

    let id: String
    let threadId: String?
    let snippet: String?
    let payload: Payload?

    var normalized: [String: JSONValue] {
        var headers: [String: String] = [:]
        for header in payload?.headers ?? [] {
            headers[header.name.lowercased()] = header.value
        }
        return [
            "id": .string(id),
            "threadId": threadId.map(JSONValue.string) ?? .null,
            "subject": .string(headers["subject"] ?? "（无主题）"),
            "sender": headers["from"].map(JSONValue.string) ?? .null,
            "date": headers["date"].map(JSONValue.string) ?? .null,
            "snippet": .string(String((snippet ?? "").prefix(2_000))),
            "bodyText": .string(String((payload?.plainText ?? "").prefix(4_000))),
        ]
    }
}

private extension GmailMessageResource.Payload {
    var plainText: String? {
        if mimeType == "text/plain", let encoded = body?.data {
            return Data(base64URLEncoded: encoded).flatMap { String(data: $0, encoding: .utf8) }
        }
        for part in parts ?? [] {
            if let text = part.plainText, !text.isEmpty { return text }
        }
        return nil
    }
}

private struct DriveFileList: Decodable { let files: [DriveFile] }

private struct DriveFile: Decodable {
    let id: String
    let name: String
    let mimeType: String?
    let webViewLink: String?

    func result(verified: Bool, reused: Bool) -> [String: JSONValue] {
        [
            "fileId": .string(id),
            "name": .string(name),
            "mimeType": mimeType.map(JSONValue.string) ?? .null,
            "webViewLink": webViewLink.map(JSONValue.string) ?? .null,
            "verified": .bool(verified),
            "reused": .bool(reused),
            "source": .string("google-drive-api-device"),
        ]
    }
}

private extension Data {
    init?(base64URLEncoded value: String) {
        var normalized = value.replacingOccurrences(of: "-", with: "+")
            .replacingOccurrences(of: "_", with: "/")
        normalized += String(repeating: "=", count: (4 - normalized.count % 4) % 4)
        self.init(base64Encoded: normalized)
    }
}

private extension UIApplication {
    var wellPhoneTopViewController: UIViewController? {
        let root = connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .flatMap(\.windows)
            .first(where: \.isKeyWindow)?
            .rootViewController
        var current = root
        while let presented = current?.presentedViewController { current = presented }
        if let navigation = current as? UINavigationController {
            return navigation.visibleViewController
        }
        return current
    }
}

private enum GoogleWorkspaceError: LocalizedError {
    case notConfigured
    case presentationUnavailable
    case authorizationRequired
    case invalidArguments(String)
    case apiRejected(String)
    case verificationFailed

    var code: String {
        switch self {
        case .notConfigured: "google_oauth_not_configured"
        case .presentationUnavailable: "google_oauth_presentation_unavailable"
        case .authorizationRequired: "google_authorization_required"
        case .invalidArguments: "invalid_google_arguments"
        case .apiRejected: "google_api_rejected"
        case .verificationFailed: "google_drive_verification_failed"
        }
    }

    var errorDescription: String? {
        switch self {
        case .notConfigured:
            "尚未配置 WellPhone 的 Google iOS OAuth 客户端。"
        case .presentationUnavailable:
            "当前无法显示 Google 授权页面，请保持 WellPhone 在前台。"
        case .authorizationRequired:
            "需要连接 Google 账号才能继续此任务。"
        case .invalidArguments(let detail): detail
        case .apiRejected(let detail): "Google API 拒绝了请求：\(detail)"
        case .verificationFailed: "Drive 文件上传后无法回读验证。"
        }
    }
}
