@preconcurrency import AVFoundation
import Foundation
import Observation
import Speech
import Synchronization

enum VoiceSessionPhase: Equatable {
    case idle
    case preparing
    case listening
    case prompting
    case paused
    case finalizing
    case ready
    case failed
}

@MainActor
@Observable
final class VoiceSessionController {
    private(set) var phase: VoiceSessionPhase = .idle
    private(set) var transcript = ""
    private(set) var errorMessage: String?

    var canFinish: Bool { phase == .listening }
    var canSubmit: Bool {
        phase == .ready && !transcript.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    private let audioEngine = AVAudioEngine()
    private var audioBufferContinuation: AsyncStream<CapturedAudioBuffer>.Continuation?
    private var analyzerInputContinuation: AsyncStream<AnalyzerInput>.Continuation?
    private var analyzer: SpeechAnalyzer?
    private var transcriber: SpeechTranscriber?
    private var converter: VoiceAudioBufferConverter?
    private var audioFeedTask: Task<Void, Never>?
    private var resultsTask: Task<Void, Never>?
    private var endpointMonitorTask: Task<Void, Never>?
    private var hasInstalledAudioTap = false
    private var finalizedTranscript = ""
    private var volatileTranscript = ""
    private var endpointDetector = VoiceEndpointDetector()
    private let promptSpeaker = VoicePromptSpeaker()

    func start() async {
        guard phase == .idle || phase == .failed || phase == .ready else { return }
        resetForNewSession()
        phase = .preparing

        do {
            guard await requestMicrophonePermission() else {
                throw VoiceSessionError.microphonePermissionDenied
            }
            guard SpeechTranscriber.isAvailable else {
                throw VoiceSessionError.transcriptionUnavailable
            }
            guard let locale = await preferredRecognitionLocale() else {
                throw VoiceSessionError.localeNotSupported
            }

            let transcriber = SpeechTranscriber(
                locale: locale,
                preset: .progressiveTranscription
            )
            try await ensureModel(for: transcriber, locale: locale)

            let analyzer = SpeechAnalyzer(modules: [transcriber])
            guard let analyzerFormat = await SpeechAnalyzer.bestAvailableAudioFormat(
                compatibleWith: [transcriber]
            ) else {
                throw VoiceSessionError.transcriptionUnavailable
            }

            self.transcriber = transcriber
            self.analyzer = analyzer
            startReadingResults(from: transcriber)

            let (inputSequence, inputContinuation) = AsyncStream<AnalyzerInput>.makeStream()
            analyzerInputContinuation = inputContinuation
            try await analyzer.start(inputSequence: inputSequence)

            try configureAudioSession()
            let audioStream = try startAudioCapture()
            let inputFormat = audioEngine.inputNode.outputFormat(forBus: 0)
            guard let converter = VoiceAudioBufferConverter(
                inputFormat: inputFormat,
                outputFormat: analyzerFormat
            ) else {
                throw VoiceSessionError.audioConversionFailed
            }
            self.converter = converter
            startFeedingAudio(audioStream, converter: converter)
            phase = .listening
            startEndpointMonitoring()
        } catch {
            fail(with: error)
        }
    }

    func finish() async {
        guard phase == .listening else { return }
        phase = .finalizing
        stopEndpointMonitoring()

        stopAudioCapture()
        await audioFeedTask?.value
        analyzerInputContinuation?.finish()

        do {
            try await analyzer?.finalizeAndFinishThroughEndOfInput()
            await resultsTask?.value
            deactivateAudioSession()
            phase = transcript.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                ? .idle
                : .ready
        } catch {
            fail(with: error)
        }
    }

    func pauseForBackground() {
        guard phase == .listening || phase == .prompting else { return }
        promptSpeaker.stop()
        audioEngine.pause()
        phase = .paused
    }

    func resumeAfterBackground() async {
        guard phase == .paused else { return }

        do {
            try configureAudioSession()
            audioEngine.prepare()
            try audioEngine.start()
            endpointDetector.resume(at: Self.uptime)
            phase = .listening
        } catch {
            fail(with: error)
        }
    }

    func cancel() {
        stopEndpointMonitoring()
        promptSpeaker.stop()
        stopAudioCapture()
        analyzerInputContinuation?.finish()
        audioFeedTask?.cancel()
        resultsTask?.cancel()
        if let analyzer {
            Task { await analyzer.cancelAndFinishNow() }
        }
        deactivateAudioSession()
        phase = .idle
    }

    private func resetForNewSession() {
        cancel()
        transcript = ""
        finalizedTranscript = ""
        volatileTranscript = ""
        errorMessage = nil
        phase = .idle
    }

    private func requestMicrophonePermission() async -> Bool {
        await withCheckedContinuation { continuation in
            AVAudioApplication.requestRecordPermission { granted in
                continuation.resume(returning: granted)
            }
        }
    }

    private func preferredRecognitionLocale() async -> Locale? {
        let candidates = [
            Locale(identifier: "zh-Hans-CN"),
            Locale(identifier: "zh-CN"),
            Locale.current,
        ]

        for candidate in candidates {
            if let locale = await SpeechTranscriber.supportedLocale(equivalentTo: candidate) {
                return locale
            }
        }
        return nil
    }

    private func ensureModel(
        for transcriber: SpeechTranscriber,
        locale: Locale
    ) async throws {
        if let installationRequest = try await AssetInventory.assetInstallationRequest(
            supporting: [transcriber]
        ) {
            try await installationRequest.downloadAndInstall()
        }

        let reservedLocales = await AssetInventory.reservedLocales
        if !reservedLocales.contains(locale) {
            if reservedLocales.count >= AssetInventory.maximumReservedLocales,
               let localeToRelease = reservedLocales.last {
                await AssetInventory.release(reservedLocale: localeToRelease)
            }
            _ = try await AssetInventory.reserve(locale: locale)
        }
    }

    private func configureAudioSession() throws {
        let audioSession = AVAudioSession.sharedInstance()
        try audioSession.setCategory(
            .playAndRecord,
            mode: .spokenAudio,
            options: [.defaultToSpeaker]
        )
        try audioSession.setActive(true)
    }

    private func startAudioCapture() throws -> AsyncStream<CapturedAudioBuffer> {
        let (stream, continuation) = AsyncStream<CapturedAudioBuffer>.makeStream(
            bufferingPolicy: .bufferingNewest(4)
        )
        audioBufferContinuation = continuation

        let inputNode = audioEngine.inputNode
        let inputFormat = inputNode.outputFormat(forBus: 0)
        inputNode.installTap(
            onBus: 0,
            bufferSize: 4096,
            format: inputFormat
        ) { @Sendable buffer, _ in
            continuation.yield(CapturedAudioBuffer(buffer))
        }
        hasInstalledAudioTap = true
        audioEngine.prepare()
        try audioEngine.start()
        return stream
    }

    private func startFeedingAudio(
        _ audioStream: AsyncStream<CapturedAudioBuffer>,
        converter: VoiceAudioBufferConverter
    ) {
        let inputContinuation = analyzerInputContinuation
        audioFeedTask = Task.detached(priority: .userInitiated) { [weak self] in
            do {
                for await buffer in audioStream {
                    try Task.checkCancellation()
                    let converted = try await converter.convert(buffer)
                    inputContinuation?.yield(AnalyzerInput(buffer: converted.value))
                    await self?.observeAudioLevel(converted.levelDecibels)
                }
            } catch is CancellationError {
                return
            } catch {
                await self?.fail(with: error)
            }
        }
    }

    private func startReadingResults(from transcriber: SpeechTranscriber) {
        resultsTask = Task { [weak self] in
            do {
                for try await result in transcriber.results {
                    guard let self, !Task.isCancelled else { return }
                    self.apply(result)
                }
            } catch is CancellationError {
                return
            } catch {
                self?.fail(with: error)
            }
        }
    }

    private func apply(_ result: SpeechTranscriber.Result) {
        let text = String(result.text.characters)
        if result.isFinal {
            finalizedTranscript += text
            volatileTranscript = ""
        } else {
            volatileTranscript = text
        }
        transcript = finalizedTranscript + volatileTranscript
        if !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            endpointDetector.observeTranscript(at: Self.uptime)
        }
    }

    private func observeAudioLevel(_ levelDecibels: Float) {
        guard phase == .listening else { return }
        endpointDetector.observeAudio(
            at: Self.uptime,
            isVoice: levelDecibels >= VoiceEndpointDetector.speechThresholdDecibels
        )
    }

    private func startEndpointMonitoring() {
        endpointMonitorTask?.cancel()
        endpointDetector.start(at: Self.uptime)
        endpointMonitorTask = Task { [weak self] in
            while !Task.isCancelled {
                do {
                    try await Task.sleep(for: .milliseconds(100))
                } catch {
                    return
                }
                guard let self, self.phase == .listening else { continue }

                switch self.endpointDetector.nextAction(
                    at: Self.uptime,
                    hasTranscript: !self.transcript
                        .trimmingCharacters(in: .whitespacesAndNewlines)
                        .isEmpty
                ) {
                case .none:
                    continue
                case .prompt:
                    await self.playNoSpeechPrompt()
                case .finish:
                    self.endpointMonitorTask = nil
                    Task { [weak self] in
                        await self?.finish()
                    }
                    return
                }
            }
        }
    }

    private func playNoSpeechPrompt() async {
        guard phase == .listening else { return }
        audioEngine.pause()
        phase = .prompting
        await promptSpeaker.speak("我在听，请说出你的指令。")
        guard phase == .prompting else { return }

        do {
            audioEngine.prepare()
            try audioEngine.start()
            endpointDetector.promptDidFinish(at: Self.uptime)
            phase = .listening
        } catch {
            fail(with: error)
        }
    }

    private func stopEndpointMonitoring() {
        endpointMonitorTask?.cancel()
        endpointMonitorTask = nil
    }

    private func stopAudioCapture() {
        if audioEngine.isRunning {
            audioEngine.stop()
        }
        if hasInstalledAudioTap {
            audioEngine.inputNode.removeTap(onBus: 0)
            hasInstalledAudioTap = false
        }
        audioBufferContinuation?.finish()
        audioBufferContinuation = nil
    }

    private func deactivateAudioSession() {
        try? AVAudioSession.sharedInstance().setActive(
            false,
            options: .notifyOthersOnDeactivation
        )
    }

    private func fail(with error: Error) {
        stopEndpointMonitoring()
        promptSpeaker.stop()
        stopAudioCapture()
        analyzerInputContinuation?.finish()
        audioFeedTask?.cancel()
        resultsTask?.cancel()
        deactivateAudioSession()
        errorMessage = (error as? LocalizedError)?.errorDescription
            ?? "语音识别暂时不可用，请稍后重试。"
        phase = .failed
    }

    private static var uptime: TimeInterval {
        ProcessInfo.processInfo.systemUptime
    }
}

nonisolated private struct CapturedAudioBuffer: @unchecked Sendable {
    let value: AVAudioPCMBuffer

    init(_ value: AVAudioPCMBuffer) {
        self.value = value
    }
}

nonisolated private struct ConvertedAudioBuffer: @unchecked Sendable {
    let value: AVAudioPCMBuffer
    let levelDecibels: Float
}

private actor VoiceAudioBufferConverter {
    private let converter: AVAudioConverter
    private let outputFormat: AVAudioFormat

    init?(inputFormat: AVAudioFormat, outputFormat: AVAudioFormat) {
        guard let converter = AVAudioConverter(from: inputFormat, to: outputFormat) else {
            return nil
        }
        self.converter = converter
        self.outputFormat = outputFormat
    }

    func convert(_ capturedBuffer: CapturedAudioBuffer) throws -> ConvertedAudioBuffer {
        let buffer = capturedBuffer.value
        let levelDecibels = VoiceAudioLevel.decibels(in: buffer)
        if buffer.format == outputFormat {
            return ConvertedAudioBuffer(
                value: buffer,
                levelDecibels: levelDecibels
            )
        }

        let ratio = outputFormat.sampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount(ceil(Double(buffer.frameLength) * ratio)) + 1
        guard let convertedBuffer = AVAudioPCMBuffer(
            pcmFormat: outputFormat,
            frameCapacity: capacity
        ) else {
            throw VoiceSessionError.audioConversionFailed
        }

        var conversionError: NSError?
        let pendingInput = Mutex<AVAudioPCMBuffer?>(buffer)
        let status = converter.convert(to: convertedBuffer, error: &conversionError) {
            _, inputStatus in
            return pendingInput.withLock { pendingBuffer in
                guard let inputBuffer = pendingBuffer else {
                    inputStatus.pointee = .noDataNow
                    return nil
                }
                pendingBuffer = nil
                inputStatus.pointee = .haveData
                return inputBuffer
            }
        }

        if let conversionError {
            throw conversionError
        }
        guard status != .error else {
            throw VoiceSessionError.audioConversionFailed
        }
        return ConvertedAudioBuffer(
            value: convertedBuffer,
            levelDecibels: levelDecibels
        )
    }
}

nonisolated private enum VoiceAudioLevel {
    static func decibels(in buffer: AVAudioPCMBuffer) -> Float {
        let frameCount = Int(buffer.frameLength)
        guard frameCount > 0 else { return -.infinity }

        let meanSquare: Double
        if let channel = buffer.floatChannelData?.pointee {
            var sum = 0.0
            for index in 0..<frameCount {
                let sample = Double(channel[index])
                sum += sample * sample
            }
            meanSquare = sum / Double(frameCount)
        } else if let channel = buffer.int16ChannelData?.pointee {
            var sum = 0.0
            for index in 0..<frameCount {
                let sample = Double(channel[index]) / Double(Int16.max)
                sum += sample * sample
            }
            meanSquare = sum / Double(frameCount)
        } else {
            return -.infinity
        }

        let rootMeanSquare = sqrt(meanSquare)
        return 20 * log10(Float(max(rootMeanSquare, 0.000_000_1)))
    }
}

nonisolated struct VoiceEndpointDetector {
    enum Action: Equatable {
        case none
        case prompt
        case finish
    }

    static let speechThresholdDecibels: Float = -45
    static let initialSilenceDuration: TimeInterval = 3
    static let endSilenceDuration: TimeInterval = 1.5
    static let minimumVoiceDuration: TimeInterval = 0.15

    private var listeningStartedAt: TimeInterval = 0
    private var candidateVoiceStartedAt: TimeInterval?
    private var lastActivityAt: TimeInterval?
    private var hasDetectedSpeech = false
    private var hasPrompted = false
    private var hasFinished = false

    mutating func start(at time: TimeInterval) {
        listeningStartedAt = time
        candidateVoiceStartedAt = nil
        lastActivityAt = nil
        hasDetectedSpeech = false
        hasPrompted = false
        hasFinished = false
    }

    mutating func observeAudio(at time: TimeInterval, isVoice: Bool) {
        guard isVoice else {
            candidateVoiceStartedAt = nil
            return
        }

        if hasDetectedSpeech {
            lastActivityAt = time
            return
        }

        guard let candidateVoiceStartedAt else {
            self.candidateVoiceStartedAt = time
            return
        }
        guard time - candidateVoiceStartedAt >= Self.minimumVoiceDuration else { return }
        hasDetectedSpeech = true
        lastActivityAt = time
    }

    mutating func observeTranscript(at time: TimeInterval) {
        hasDetectedSpeech = true
        lastActivityAt = time
    }

    mutating func nextAction(
        at time: TimeInterval,
        hasTranscript: Bool
    ) -> Action {
        guard !hasFinished else { return .none }

        if !hasDetectedSpeech,
           !hasPrompted,
           time - listeningStartedAt >= Self.initialSilenceDuration {
            hasPrompted = true
            return .prompt
        }

        if hasDetectedSpeech,
           hasTranscript,
           let lastActivityAt,
           time - lastActivityAt >= Self.endSilenceDuration {
            hasFinished = true
            return .finish
        }

        return .none
    }

    mutating func promptDidFinish(at time: TimeInterval) {
        listeningStartedAt = time
        candidateVoiceStartedAt = nil
    }

    mutating func resume(at time: TimeInterval) {
        candidateVoiceStartedAt = nil
        if hasDetectedSpeech {
            lastActivityAt = time
        } else {
            listeningStartedAt = time
        }
    }
}

@MainActor
private final class VoicePromptSpeaker: NSObject, AVSpeechSynthesizerDelegate {
    private let synthesizer = AVSpeechSynthesizer()
    private var continuation: CheckedContinuation<Void, Never>?

    override init() {
        super.init()
        synthesizer.delegate = self
    }

    func speak(_ text: String) async {
        stop()
        let utterance = AVSpeechUtterance(string: text)
        utterance.voice = AVSpeechSynthesisVoice(language: "zh-CN")
        utterance.rate = 0.48

        await withCheckedContinuation { continuation in
            self.continuation = continuation
            synthesizer.speak(utterance)
        }
    }

    func stop() {
        if synthesizer.isSpeaking {
            synthesizer.stopSpeaking(at: .immediate)
        }
        completeSpeech()
    }

    nonisolated func speechSynthesizer(
        _ synthesizer: AVSpeechSynthesizer,
        didFinish utterance: AVSpeechUtterance
    ) {
        Task { @MainActor [weak self] in
            self?.completeSpeech()
        }
    }

    nonisolated func speechSynthesizer(
        _ synthesizer: AVSpeechSynthesizer,
        didCancel utterance: AVSpeechUtterance
    ) {
        Task { @MainActor [weak self] in
            self?.completeSpeech()
        }
    }

    private func completeSpeech() {
        let continuation = continuation
        self.continuation = nil
        continuation?.resume()
    }
}

enum VoiceSessionError: LocalizedError {
    case microphonePermissionDenied
    case transcriptionUnavailable
    case localeNotSupported
    case audioConversionFailed

    var errorDescription: String? {
        switch self {
        case .microphonePermissionDenied:
            "需要麦克风权限才能接收语音指令。"
        case .transcriptionUnavailable:
            "当前设备暂不支持本地语音转写。"
        case .localeNotSupported:
            "当前系统语言暂不支持语音转写。"
        case .audioConversionFailed:
            "麦克风音频无法转换为语音识别格式。"
        }
    }
}
