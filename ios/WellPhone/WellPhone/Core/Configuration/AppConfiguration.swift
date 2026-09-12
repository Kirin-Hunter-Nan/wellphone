import Foundation

enum AppConfiguration {
    static var modelProxyBaseURL: URL {
        if let environmentValue = ProcessInfo.processInfo.environment["MODEL_PROXY_BASE_URL"],
           let url = validURL(from: environmentValue) {
            return url
        }

        if let bundleValue = Bundle.main.object(forInfoDictionaryKey: "MODEL_PROXY_BASE_URL") as? String,
           let url = validURL(from: bundleValue) {
            return url
        }

        if let configurationURL = Bundle.main.url(forResource: "AppConfig", withExtension: "json"),
           let data = try? Data(contentsOf: configurationURL),
           let configuration = try? JSONDecoder().decode(BundledConfiguration.self, from: data),
           let url = validURL(from: configuration.modelProxyBaseURL) {
            return url
        }

        // Works with the iOS Simulator when the proxy is running on this Mac.
        return URL(string: "http://127.0.0.1:8787")!
    }

    private static func validURL(from value: String) -> URL? {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty,
              !trimmed.contains("$("),
              let url = URL(string: trimmed),
              ["http", "https"].contains(url.scheme?.lowercased() ?? ""),
              url.host != nil else {
            return nil
        }
        return url
    }
}

private struct BundledConfiguration: Decodable {
    let modelProxyBaseURL: String
}

enum ModelGatewayFactory {
    static func make() -> any ModelGateway {
        URLSessionModelGateway(baseURL: AppConfiguration.modelProxyBaseURL)
    }
}
