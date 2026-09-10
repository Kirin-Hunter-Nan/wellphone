//
//  Item.swift
//  WellPhone
//
//  Created by 南佳琪 on 2026/9/10.
//

import Foundation
import SwiftData

@Model
final class Item {
    var timestamp: Date
    
    init(timestamp: Date) {
        self.timestamp = timestamp
    }
}
