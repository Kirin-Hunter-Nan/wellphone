import Foundation
import MapKit

@MainActor
protocol DeviceToolExecuting: Sendable {
    func execute(_ request: DeviceToolRequest) async -> DeviceToolExecutionResult
}

@MainActor
final class MapKitDeviceToolExecutor: DeviceToolExecuting {
    func execute(_ request: DeviceToolRequest) async -> DeviceToolExecutionResult {
        guard request.toolName == "mapkit.local-search" else {
            return .failed(
                code: "unsupported_device_tool",
                message: "手机不支持设备工具：\(request.toolName)"
            )
        }
        guard case .string(let query) = request.arguments["query"],
              case .string(let destination) = request.arguments["destination"],
              !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              !destination.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return .failed(
                code: "invalid_mapkit_arguments",
                message: "MapKit 地点回查缺少地点名称或目的地。"
            )
        }

        do {
            let items = try await search(query: query, destination: destination)
            return .completed(resolve(items, query: query, destination: destination))
        } catch {
            return .failed(
                code: mapKitErrorCode(error),
                message: "系统地图回查失败：\(error.localizedDescription)"
            )
        }
    }

    private func search(query: String, destination: String) async throws -> [MKMapItem] {
        var lastError: (any Error)?
        for attempt in 0..<3 {
            let request = MKLocalSearch.Request(
                naturalLanguageQuery: "\(query) \(destination)"
            )
            request.resultTypes = [.pointOfInterest, .address]
            do {
                return try await MKLocalSearch(request: request).start().mapItems
            } catch {
                lastError = error
                guard isThrottled(error), attempt < 2 else { throw error }
                try await Task<Never, Never>.sleep(
                    for: .milliseconds(500 * (1 << attempt))
                )
            }
        }
        throw lastError ?? DeviceMapKitLocalError.searchFailed
    }

    private func resolve(
        _ items: [MKMapItem],
        query: String,
        destination: String
    ) -> [String: JSONValue] {
        let candidates = items.map {
            candidate(for: $0, query: query, destination: destination)
        }.sorted { lhs, rhs in
            if lhs.score == rhs.score { return lhs.name < rhs.name }
            return lhs.score > rhs.score
        }
        guard let best = candidates.first else {
            return unverifiedResult(
                name: query,
                mapURL: searchURL(query: query, destination: destination),
                error: "Apple 地图中没有找到可核对的地点。",
                candidateCount: 0
            )
        }

        let runnerUpScore = candidates.dropFirst().first?.score ?? 0
        let isUnambiguous = candidates.count == 1 || best.score - runnerUpScore >= 0.08
        let hasCanonicalIdentity = best.nameScore >= 0.97
            && best.placeID != nil
            && best.formattedAddress?.isEmpty == false
        let verified = best.destinationMatched
            && best.score >= 0.72
            && (isUnambiguous || hasCanonicalIdentity)
        var result: [String: JSONValue] = [
            "name": .string(best.name),
            "latitude": .number(best.latitude),
            "longitude": .number(best.longitude),
            "map_url": .string(best.mapURL.absoluteString),
            "verified": .bool(verified),
            "confidence": .number(min(max(best.score, 0), 1)),
            "source": .string("mapkit-native"),
            "candidate_count": .number(Double(candidates.count)),
        ]
        if let address = best.formattedAddress, !address.isEmpty {
            result["formatted_address"] = .string(address)
        }
        if let placeID = best.placeID {
            result["place_id"] = .string(placeID)
        }
        if let category = best.category {
            result["category"] = .string(category)
        }
        if !verified {
            result["verification_error"] = .string(
                best.destinationMatched
                    ? "Apple 地图返回了多个相近候选，无法唯一确认该地点。"
                    : "Apple 地图结果不在目标目的地内。"
            )
        }
        return result
    }

    private func candidate(
        for item: MKMapItem,
        query: String,
        destination: String
    ) -> MapKitCandidate {
        let name = item.name?.trimmingCharacters(in: .whitespacesAndNewlines)
        let resolvedName = name.flatMap { $0.isEmpty ? nil : $0 } ?? query
        let address = item.address?.fullAddress
        let destinationKey = normalized(destination)
        let locationText = [
            address,
            item.addressRepresentations?.cityName,
            item.addressRepresentations?.cityWithContext,
            item.addressRepresentations?.regionName,
        ].compactMap { $0 }.joined(separator: " ")
        let destinationMatched = !destinationKey.isEmpty
            && normalized(locationText).contains(destinationKey)
        let nameScore = canonicalNameSimilarity(query: query, candidate: resolvedName)
        let placeID = item.identifier?.rawValue
        let score = min(
            nameScore * 0.68
                + (destinationMatched ? 0.27 : 0)
                + (placeID == nil ? 0 : 0.05),
            1
        )
        let coordinate = item.location.coordinate
        return MapKitCandidate(
            name: resolvedName,
            formattedAddress: address,
            latitude: coordinate.latitude,
            longitude: coordinate.longitude,
            placeID: placeID,
            category: item.pointOfInterestCategory?.rawValue,
            mapURL: placeURL(
                name: resolvedName,
                latitude: coordinate.latitude,
                longitude: coordinate.longitude,
                placeID: placeID
            ),
            destinationMatched: destinationMatched,
            nameScore: nameScore,
            score: score
        )
    }

    func canonicalNameSimilarity(query: String, candidate: String) -> Double {
        similarity(normalized(query), normalized(candidate))
    }

    private func similarity(_ query: String, _ candidate: String) -> Double {
        guard !query.isEmpty, !candidate.isEmpty else { return 0 }
        if query == candidate { return 1 }
        if candidate.contains(query) || query.contains(candidate) {
            let lengthRatio = Double(min(query.count, candidate.count))
                / Double(max(query.count, candidate.count))
            return min(0.82 + 0.18 * lengthRatio, 1)
        }
        let queryPairs = bigrams(query)
        let candidatePairs = bigrams(candidate)
        guard !queryPairs.isEmpty, !candidatePairs.isEmpty else { return 0 }
        let overlap = queryPairs.intersection(candidatePairs).count
        return (2 * Double(overlap)) / Double(queryPairs.count + candidatePairs.count)
    }

    private func bigrams(_ value: String) -> Set<String> {
        let characters = Array(value)
        guard characters.count > 1 else { return Set([value]) }
        return Set((0..<(characters.count - 1)).map {
            String(characters[$0...($0 + 1)])
        })
    }

    private func normalized(_ value: String) -> String {
        value.folding(options: [.caseInsensitive, .diacriticInsensitive], locale: .current)
            .unicodeScalars
            .filter { CharacterSet.alphanumerics.contains($0) }
            .map(String.init)
            .joined()
    }

    private func unverifiedResult(
        name: String,
        mapURL: URL,
        error: String,
        candidateCount: Int
    ) -> [String: JSONValue] {
        [
            "name": .string(name),
            "map_url": .string(mapURL.absoluteString),
            "verified": .bool(false),
            "source": .string("mapkit-native"),
            "confidence": .number(0),
            "candidate_count": .number(Double(candidateCount)),
            "verification_error": .string(error),
        ]
    }

    private func searchURL(query: String, destination: String) -> URL {
        var components = URLComponents(string: "https://maps.apple.com/search")!
        components.queryItems = [
            URLQueryItem(name: "query", value: "\(query) \(destination)"),
        ]
        return components.url!
    }

    private func placeURL(
        name: String,
        latitude: Double,
        longitude: Double,
        placeID: String?
    ) -> URL {
        var components = URLComponents(string: "https://maps.apple.com/place")!
        if let placeID {
            components.queryItems = [URLQueryItem(name: "place-id", value: placeID)]
        } else {
            components.queryItems = [
                URLQueryItem(name: "coordinate", value: "\(latitude),\(longitude)"),
                URLQueryItem(name: "name", value: name),
            ]
        }
        return components.url!
    }

    private func isThrottled(_ error: any Error) -> Bool {
        let error = error as NSError
        return error.domain == MKErrorDomain
            && error.code == Int(MKError.Code.loadingThrottled.rawValue)
    }

    private func mapKitErrorCode(_ error: any Error) -> String {
        isThrottled(error) ? "mapkit_throttled" : "mapkit_search_failed"
    }
}

@MainActor
final class DisabledDeviceToolExecutor: DeviceToolExecuting {
    func execute(_ request: DeviceToolRequest) async -> DeviceToolExecutionResult {
        .failed(
            code: "device_tool_unavailable",
            message: "当前运行环境没有可用的设备工具执行器。"
        )
    }
}

private struct MapKitCandidate {
    let name: String
    let formattedAddress: String?
    let latitude: Double
    let longitude: Double
    let placeID: String?
    let category: String?
    let mapURL: URL
    let destinationMatched: Bool
    let nameScore: Double
    let score: Double
}

private enum DeviceMapKitLocalError: LocalizedError {
    case searchFailed

    var errorDescription: String? { "系统地图没有返回搜索结果。" }
}
