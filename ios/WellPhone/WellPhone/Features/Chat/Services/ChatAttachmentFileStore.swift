import Foundation

protocol ChatAttachmentFileStoring {
    func persist(_ data: Data, id: UUID, fileExtension: String) throws -> String
    func read(path: String) throws -> Data
}

struct LocalChatAttachmentFileStore: ChatAttachmentFileStoring {
    private let fileManager: FileManager

    init(fileManager: FileManager = .default) {
        self.fileManager = fileManager
    }

    func persist(_ data: Data, id: UUID, fileExtension: String) throws -> String {
        let base = try fileManager.url(
            for: .applicationSupportDirectory,
            in: .userDomainMask,
            appropriateFor: nil,
            create: true
        ).appendingPathComponent("WellPhoneAttachments", isDirectory: true)
        try fileManager.createDirectory(at: base, withIntermediateDirectories: true)
        let filename = id.uuidString.lowercased() + "." + fileExtension
        let url = base.appendingPathComponent(filename)
        try data.write(to: url, options: .atomic)
        return url.path
    }

    func read(path: String) throws -> Data {
        try Data(contentsOf: URL(fileURLWithPath: path))
    }
}
