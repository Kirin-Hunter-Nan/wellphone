@preconcurrency import AVFoundation
import Foundation
import Observation
import Speech
import Synchronization

enum VoiceSessionPhase: Equatable {
    case idle
    case preparing
    case listening
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
    private var audioBufferContinuation: AsyncStream<AVAudioPCMBuffer>.Continuation?
    private var analyzerInputContinuation: AsyncStream<AnalyzerInput>.Continuation?
    private var analyzer: SpeechAnalyzer?
    private var transcriber: SpeechTranscriber?
    private var converter: AVAudioConverter?
    private var audioFeedTask: Task<Void, Never>?
    private var resultsTask: Task<Void, Never>?
    private var hasInstalledAudioTap = false
    private var finalizedTranscript = ""
    private var volatileTranscript = ""

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
            guard let locale = await SpeechTranscriber.supportedLocale(
                equivalentTo: Locale.current
            ) else {
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
            guard let converter = AVAudioConverter(from: inputFormat, to: analyzerFormat) else {
                throw VoiceSessionError.audioConversionFailed
            }
            self.converter = converter
            startFeedingAudio(audioStream, analyzerFormat: analyzerFormat)
            phase = .listening
        } catch {
            fail(with: error)
        }
    }

    func finish() async {
        guard phase == .listening else { return }
        phase = .finalizing

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

    func cancel() {
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
        try audioSession.setCategory(.playAndRecord, mode: .spokenAudio)
        try audioSession.setActive(true)
    }

    private func startAudioCapture() throws -> AsyncStream<AVAudioPCMBuffer> {
        let (stream, continuation) = AsyncStream<AVAudioPCMBuffer>.makeStream()
        audioBufferContinuation = continuation

        let inputNode = audioEngine.inputNode
        let inputFormat = inputNode.outputFormat(forBus: 0)
        inputNode.installTap(
            onBus: 0,
            bufferSize: 4096,
            format: inputFormat
        ) { buffer, _ in
            continuation.yield(buffer)
        }
        hasInstalledAudioTap = true
        audioEngine.prepare()
        try audioEngine.start()
        return stream
    }

    private func startFeedingAudio(
        _ audioStream: AsyncStream<AVAudioPCMBuffer>,
        analyzerFormat: AVAudioFormat
    ) {
        audioFeedTask = Task { [weak self] in
            do {
                for await buffer in audioStream {
                    guard let self, !Task.isCancelled else { return }
                    let converted = try self.convert(buffer, to: analyzerFormat)
                    self.analyzerInputContinuation?.yield(AnalyzerInput(buffer: converted))
                }
            } catch {
                self?.fail(with: error)
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
    }

    private func convert(
        _ buffer: AVAudioPCMBuffer,
        to outputFormat: AVAudioFormat
    ) throws -> AVAudioPCMBuffer {
        guard let converter else {
            throw VoiceSessionError.audioConversionFailed
        }
        if buffer.format == outputFormat {
            return buffer
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
        return convertedBuffer
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
        stopAudioCapture()
        analyzerInputContinuation?.finish()
        audioFeedTask?.cancel()
        resultsTask?.cancel()
        deactivateAudioSession()
        errorMessage = (error as? LocalizedError)?.errorDescription
            ?? "语音识别暂时不可用，请稍后重试。"
        phase = .failed
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
