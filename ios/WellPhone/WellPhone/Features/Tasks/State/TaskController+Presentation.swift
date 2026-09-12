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

    func importTravelCalendar(taskID: UUID) async {
        guard let task = task(id: taskID),
              let calendarImporter,
              let artifact = artifacts(for: task).first(where: {
                  $0.contentType == "application/vnd.wellphone.calendar-events+json"
              }),
              artifact.storageReference?.hasPrefix("eventkit:") != true,
              let payloadJSON = artifact.payloadJSON,
              let data = payloadJSON.data(using: .utf8) else { return }
        do {
            let identifiers = try await calendarImporter.importEvents(payload: data)
            artifact.storageReference = "eventkit:" + identifiers.joined(separator: ",")
            task.detail = "行程已添加到 Apple 日历。"
            task.errorMessage = nil
            touchAndSave(task)
            calendarImportPrompt = nil
        } catch {
            task.errorMessage = error.localizedDescription
            touchAndSave(task)
        }
    }

    func dismissCalendarImportPrompt() {
        calendarImportPrompt = nil
    }

    func showCompletionBanner(
        for task: AgentTask,
        offerCalendarImport: Bool
    ) {
        completionBannerTask?.cancel()
        let banner = CompletionBanner(
            taskID: task.id,
            title: task.title,
            summary: task.resultSummary ?? "任务已经完成。"
        )
        completionBanner = banner
        completionBannerTask = Task { [weak self] in
            try? await Task.sleep(for: .milliseconds(2_500))
            guard !Task.isCancelled, let self else { return }
            if self.completionBanner?.id == banner.id {
                self.completionBanner = nil
            }
            if offerCalendarImport {
                self.calendarImportPrompt = CalendarImportPrompt(
                    taskID: task.id,
                    title: task.title
                )
            }
            self.completionBannerTask = nil
        }
    }
}
