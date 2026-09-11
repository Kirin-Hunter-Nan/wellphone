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

        XCTAssertTrue(app.otherElements["chat.sidebar"].waitForExistence(timeout: 2))
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
}
