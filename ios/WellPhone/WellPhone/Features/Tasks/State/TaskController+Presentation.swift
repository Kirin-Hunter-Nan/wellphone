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
        let isRequiredForCompletion = includeOutcomeInSummary
            || calendarWasExplicitlyRequested(for: task)
        guard let artifact = artifacts(for: task).first(where: {
            $0.contentType == "application/vnd.wellphone.calendar-events+json"
        }) else {
            if isRequiredForCompletion {
                failRequiredCalendarImport(
                    task,
                    error: TravelCalendarFinalizationError.missingArtifact
                )
            }
            return false
        }
        if artifact.storageReference?.hasPrefix("eventkit:") == true {
            if isRequiredForCompletion {
                completeRequiredCalendarImport(task)
            }
            return true
        }
        guard
              let payloadJSON = artifact.payloadJSON,
              let data = payloadJSON.data(using: .utf8) else {
            if isRequiredForCompletion {
                failRequiredCalendarImport(
                    task,
                    error: TravelCalendarFinalizationError.invalidArtifact
                )
            }
            return false
        }

        if isRequiredForCompletion {
            task.status = .running
            task.phase = .verifying
            task.progress = 0.96
            task.detail = "正在写入并验证 Apple 日历"
            task.errorMessage = nil
            touchAndSave(task)
            queueCheckpoint(for: task)
        }

        do {
            let identifiers = try await calendarImporter.importEvents(
                payload: data,
                idempotencyKey: task.id.uuidString.lowercased()
            )
            artifact.storageReference = "eventkit:" + identifiers.joined(separator: ",")
            task.detail = "行程已添加到 Apple 日历。"
            task.errorMessage = nil
            if isRequiredForCompletion,
               task.resultSummary?.contains("Apple 日历") != true {
                task.resultSummary = (task.resultSummary ?? "旅行规划已经完成。")
                    + " 行程已添加到 Apple 日历。"
            }
            if isRequiredForCompletion {
                task.status = .completed
                task.phase = .completed
                task.progress = 1
            }
            touchAndSave(task)
            if isRequiredForCompletion {
                queueCheckpoint(for: task)
            }
            calendarImportPrompt = nil
            return true
        } catch {
            if isRequiredForCompletion {
                failRequiredCalendarImport(task, error: error)
            } else {
                task.errorMessage = error.localizedDescription
                touchAndSave(task)
            }
            return false
        }
    }

    private func completeRequiredCalendarImport(_ task: AgentTask) {
        task.status = .completed
        task.phase = .completed
        task.progress = 1
        task.detail = "行程已添加到 Apple 日历。"
        task.errorMessage = nil
        if task.resultSummary?.contains("Apple 日历") != true {
            task.resultSummary = (task.resultSummary ?? "旅行规划已经完成。")
                + " 行程已添加到 Apple 日历。"
        }
        touchAndSave(task)
        queueCheckpoint(for: task)
    }

    private func failRequiredCalendarImport(_ task: AgentTask, error: any Error) {
        let message = "旅行规划已完成，但未能写入并验证 Apple 日历：\(error.localizedDescription)"
        task.status = .failed
        task.phase = .failed
        task.progress = nil
        task.detail = "Apple 日历写入或验证失败"
        task.errorMessage = message
        task.resultSummary = message
        touchAndSave(task)
        queueCheckpoint(for: task)
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

private enum TravelCalendarFinalizationError: LocalizedError {
    case missingArtifact
    case invalidArtifact

    var errorDescription: String? {
        switch self {
        case .missingArtifact: "服务端没有返回可写入的日历事件产物。"
        case .invalidArtifact: "日历事件产物无法读取。"
        }
    }
}
