//
//  WellPhoneUITests.swift
//  WellPhoneUITests
//
//  Created by 南佳琪 on 2026/9/10.
//

import XCTest

final class WellPhoneUITests: XCTestCase {

    override func setUpWithError() throws {
        // Put setup code here. This method is called before the invocation of each test method in the class.

        // In UI tests it is usually best to stop immediately when a failure occurs.
        continueAfterFailure = false

        // In UI tests it’s important to set the initial state - such as interface orientation - required for your tests before they run. The setUp method is a good place to do this.
    }

    override func tearDownWithError() throws {
        // Put teardown code here. This method is called after the invocation of each test method in the class.
    }

    @MainActor
    func testSwipeRightRevealsSidebar() throws {
        let app = XCUIApplication()
        app.launch()

        app.swipeRight()

        XCTAssertTrue(app.buttons["新聊天"].waitForExistence(timeout: 3))
        XCTAssertTrue(app.buttons["任务"].exists)
    }

    @MainActor
    func testTappingConversationDismissesComposerKeyboard() throws {
        let app = XCUIApplication()
        app.launch()

        let composer = app.textFields["chat.composer"]
        XCTAssertTrue(composer.waitForExistence(timeout: 2))
        composer.tap()
        XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 2))

        let conversation = app.scrollViews["chat.conversation"]
        XCTAssertTrue(conversation.waitForExistence(timeout: 2))
        conversation.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.25)).tap()

        XCTAssertFalse(app.keyboards.firstMatch.waitForExistence(timeout: 1))
    }

    @MainActor
    func testLaunchPerformance() throws {
        // This measures how long it takes to launch your application.
        measure(metrics: [XCTApplicationLaunchMetric()]) {
            XCUIApplication().launch()
        }
    }

    @MainActor
    func testLiveTravelTaskEndToEndOnDevice() throws {
        try requireLiveTravelE2E()
        let app = XCUIApplication()
        app.launch()
        startNewConversation(in: app)
        capture("01-new-conversation", app: app)

        let composer = app.textFields["chat.composer"]
        XCTAssertTrue(composer.waitForExistence(timeout: 10))
        composer.tap()
        composer.typeText("帮我规划2026年10月20日上海一日旅行，节奏轻松，喜欢博物馆和咖啡")
        app.buttons["arrow.up"].tap()

        let confirm = app.buttons["确认开始"]
        XCTAssertTrue(confirm.waitForExistence(timeout: 120), "没有收到旅行任务确认卡片")
        XCTAssertTrue(app.staticTexts["后台任务"].exists)
        capture("02-confirmation-card", app: app)
        confirm.tap()

        let boundedProgress = app.staticTexts.matching(
            NSPredicate(format: "label MATCHES %@", ".*第 [0-9]+/12 轮.*")
        ).firstMatch
        XCTAssertTrue(boundedProgress.waitForExistence(timeout: 90), "没有显示带 12 轮上限的进度")
        capture("03-running-progress", app: app)

        XCUIDevice.shared.press(.home)
        sleep(5)
        app.activate()
        XCTAssertTrue(app.staticTexts["后台任务"].waitForExistence(timeout: 15))

        let completion = app.staticTexts["任务完成"]
        XCTAssertTrue(completion.waitForExistence(timeout: 300), "旅行任务没有在五分钟内完成")
        capture("04-completion-banner", app: app)

        let calendarAlert = app.alerts["是否添加到 Apple 日历？"]
        XCTAssertTrue(calendarAlert.waitForExistence(timeout: 15), "完成后没有显示日历选择弹窗")
        XCTAssertTrue(calendarAlert.buttons["暂不"].exists)
        XCTAssertTrue(calendarAlert.buttons["添加到日历"].exists)
        capture("05-calendar-choice", app: app)
        calendarAlert.buttons["暂不"].tap()

        XCTAssertFalse(app.staticTexts["后台任务"].exists, "完成后聊天中的后台任务卡片仍然存在")
        XCTAssertTrue(
            app.staticTexts.matching(
                NSPredicate(format: "label CONTAINS %@", "已完成，共规划 1 天")
            ).firstMatch.waitForExistence(timeout: 30),
            "聊天中没有出现自然语言任务完成回复"
        )

        app.terminate()
        app.launch()
        XCTAssertFalse(
            app.alerts["是否添加到 Apple 日历？"].waitForExistence(timeout: 8),
            "选择暂不后，重启 App 又重复显示了日历弹窗"
        )

        app.buttons["打开侧边栏"].tap()
        XCTAssertTrue(app.buttons["任务"].waitForExistence(timeout: 5))
        app.buttons["任务"].tap()
        XCTAssertTrue(app.navigationBars["任务"].waitForExistence(timeout: 10))
        let taskTitle = app.staticTexts["规划 上海 旅行"].firstMatch
        XCTAssertTrue(taskTitle.waitForExistence(timeout: 15))
        taskTitle.tap()

        XCTAssertTrue(app.navigationBars["规划 上海 旅行"].waitForExistence(timeout: 10))
        let resultSection = app.staticTexts["结果"]
        scrollToElement(resultSection, in: app)
        XCTAssertTrue(resultSection.isHittable)
        let artifactsSection = app.staticTexts["任务产物"]
        scrollToElement(artifactsSection, in: app)
        XCTAssertTrue(artifactsSection.isHittable)
        let addFromDetail = app.buttons["添加到 Apple 日历"]
        scrollToElement(addFromDetail, in: app)
        XCTAssertTrue(addFromDetail.isHittable, "任务详情没有提供再次添加日历的入口")
        capture("06-task-detail", app: app)

        addFromDetail.tap()
        allowCalendarAccessIfNeeded(in: app)
        let imported = app.staticTexts["已添加到 Apple 日历"]
        XCTAssertTrue(imported.waitForExistence(timeout: 30), "行程没有成功写入 Apple 日历")
        capture("07-calendar-imported", app: app)
    }

    @MainActor
    func testLiveExistingTravelTaskDetailAndCalendarImportOnDevice() throws {
        try requireLiveTravelE2E()
        let app = XCUIApplication()
        app.launch()

        XCTAssertTrue(app.buttons["打开侧边栏"].waitForExistence(timeout: 10))
        app.buttons["打开侧边栏"].tap()
        XCTAssertTrue(app.buttons["任务"].waitForExistence(timeout: 5))
        app.buttons["任务"].tap()
        XCTAssertTrue(app.navigationBars["任务"].waitForExistence(timeout: 10))

        let taskTitle = app.staticTexts["规划 上海 旅行"].firstMatch
        XCTAssertTrue(taskTitle.waitForExistence(timeout: 15))
        taskTitle.tap()
        XCTAssertTrue(app.navigationBars["规划 上海 旅行"].waitForExistence(timeout: 10))

        let resultSection = app.staticTexts["结果"]
        scrollToElement(resultSection, in: app)
        XCTAssertTrue(resultSection.isHittable)
        let artifactsSection = app.staticTexts["任务产物"]
        scrollToElement(artifactsSection, in: app)
        XCTAssertTrue(artifactsSection.isHittable)

        let addFromDetail = app.buttons["添加到 Apple 日历"]
        scrollToElement(addFromDetail, in: app)
        XCTAssertTrue(addFromDetail.isHittable, "任务详情没有提供再次添加日历的入口")
        capture("08-existing-task-detail", app: app)
        addFromDetail.tap()
        allowCalendarAccessIfNeeded(in: app)

        let imported = app.staticTexts["已添加到 Apple 日历"]
        XCTAssertTrue(imported.waitForExistence(timeout: 30), "行程没有成功写入 Apple 日历")
        capture("09-existing-task-calendar-imported", app: app)
    }

    @MainActor
    private func startNewConversation(in app: XCUIApplication) {
        XCTAssertTrue(app.buttons["打开侧边栏"].waitForExistence(timeout: 10))
        app.buttons["打开侧边栏"].tap()
        XCTAssertTrue(app.buttons["新聊天"].waitForExistence(timeout: 5))
        app.buttons["新聊天"].tap()
    }

    private func requireLiveTravelE2E() throws {
        guard ProcessInfo.processInfo.environment["WELLPHONE_LIVE_TRAVEL_E2E"] == "1" else {
            throw XCTSkip("设置 WELLPHONE_LIVE_TRAVEL_E2E=1 后才运行真实 Qwen 与日历写入测试")
        }
    }

    @MainActor
    private func scrollToElement(_ element: XCUIElement, in app: XCUIApplication) {
        for _ in 0..<8 where !element.isHittable {
            app.swipeUp()
        }
    }

    @MainActor
    private func allowCalendarAccessIfNeeded(in app: XCUIApplication) {
        let alert = app.alerts.firstMatch
        guard alert.waitForExistence(timeout: 3) else { return }
        for title in ["允许完全访问", "Allow Full Access", "允许", "OK"] {
            let button = alert.buttons[title]
            if button.exists {
                button.tap()
                return
            }
        }
        XCTFail("出现了无法识别的日历权限弹窗：\(alert.debugDescription)")
    }

    @MainActor
    private func capture(_ name: String, app: XCUIApplication) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
