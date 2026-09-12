import Testing
@testable import WellPhone

struct VoiceEndpointDetectorTests {
    @Test
    func promptsOnceAfterThreeSecondsWithoutSpeech() {
        var detector = VoiceEndpointDetector()
        detector.start(at: 10)

        #expect(detector.nextAction(at: 12.9, hasTranscript: false) == .none)
        #expect(detector.nextAction(at: 13, hasTranscript: false) == .prompt)
        detector.promptDidFinish(at: 14)
        #expect(detector.nextAction(at: 20, hasTranscript: false) == .none)
    }

    @Test
    func finishesAfterSpeechAndOnePointFiveSecondsOfSilence() {
        var detector = VoiceEndpointDetector()
        detector.start(at: 0)
        detector.observeAudio(at: 0.2, isVoice: true)
        detector.observeAudio(at: 0.4, isVoice: true)
        detector.observeTranscript(at: 0.5)

        #expect(detector.nextAction(at: 1.99, hasTranscript: true) == .none)
        #expect(detector.nextAction(at: 2, hasTranscript: true) == .finish)
        #expect(detector.nextAction(at: 4, hasTranscript: true) == .none)
    }

    @Test
    func doesNotFinishUntilThereIsAUsableTranscript() {
        var detector = VoiceEndpointDetector()
        detector.start(at: 0)
        detector.observeAudio(at: 0.2, isVoice: true)
        detector.observeAudio(at: 0.4, isVoice: true)

        #expect(detector.nextAction(at: 3, hasTranscript: false) == .none)

        detector.observeTranscript(at: 3)
        #expect(detector.nextAction(at: 4.49, hasTranscript: true) == .none)
        #expect(detector.nextAction(at: 4.5, hasTranscript: true) == .finish)
    }

    @Test
    func backgroundResumeRestartsTheSilenceWindow() {
        var detector = VoiceEndpointDetector()
        detector.start(at: 0)
        detector.observeTranscript(at: 1)
        detector.resume(at: 20)

        #expect(detector.nextAction(at: 21.49, hasTranscript: true) == .none)
        #expect(detector.nextAction(at: 21.5, hasTranscript: true) == .finish)
    }
}
