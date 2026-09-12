import Foundation
import SwiftData

extension TaskController {
    func artifacts(for task: AgentTask) -> [TaskArtifact] {
        let taskID = task.id
        let descriptor = FetchDescriptor<TaskArtifact>(
            predicate: #Predicate { $0.taskID == taskID },
            sortBy: [SortDescriptor(\.createdAt)]
        )
        return (try? modelContext.fetch(descriptor)) ?? []
    }

    @discardableResult
    func importTravelCalendar(
        taskID: UUID,
        includeOutcomeInSummary: Bool = false
    ) async -> Bool {
        guard let task = task(id: taskID), let calendarImporter else { return false }
        guard let artifact = artifacts(for: task).first(where: {
            $0.contentType == "application/vnd.wellphone.calendar-events+json"
        }) else { return false }
        if artifact.storageReference?.hasPrefix("eventkit:") == true {
            return true
        }
        guard
              let payloadJSON = artifact.payloadJSON,
              let data = payloadJSON.data(using: .utf8) else { return false }
        do {
            let identifiers = try await calendarImporter.importEvents(payload: data)
            artifact.storageReference = "eventkit:" + identifiers.joined(separator: ",")
            task.detail = "行程已添加到 Apple 日历。"
            task.errorMessage = nil
            if includeOutcomeInSummary,
               task.resultSummary?.contains("Apple 日历") != true {
                task.resultSummary = (task.resultSummary ?? "旅行规划已经完成。")
                    + " 行程已添加到 Apple 日历。"
            }
            touchAndSave(task)
            calendarImportPrompt = nil
            return true
        } catch {
            task.errorMessage = error.localizedDescription
            if includeOutcomeInSummary {
                task.resultSummary = (task.resultSummary ?? "旅行规划已经完成。")
                    + " 但未能添加到 Apple 日历：\(error.localizedDescription)"
            }
            touchAndSave(task)
            return false
        }
    }

    func dismissCalendarImportPrompt() {
        calendarImportPrompt = nil
    }

    func calendarWasExplicitlyRequested(for task: AgentTask) -> Bool {
        guard task.capability == "travel.plan",
              let argumentsJSON = task.argumentsJSON,
              let data = argumentsJSON.data(using: .utf8),
              let arguments = try? JSONDecoder().decode(
                  [String: JSONValue].self,
                  from: data
              ),
              case .bool(true) = arguments["addToCalendar"] else { return false }
        return true
    }

    func offerCalendarImport(for task: AgentTask) {
        calendarImportPrompt = CalendarImportPrompt(
            taskID: task.id,
            title: task.title
        )
    }
}
