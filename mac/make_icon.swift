// Draws the app icon: a 2x2 piece of the lichess "brown" board, the Merida white knight in the lower-left square
// and a green arrow from it to the upper-right square — "who takes -> whom", the drill's answer.
// usage: swift make_icon.swift <out.iconset> <pieces.merida.css>    — the Mac icon (rounded tile with margins)
//        swift make_icon.swift --web <vision/web> <pieces.merida.css> — apple-touch-icon.png (square, full-bleed)
//        and favicon.png
import Cocoa

let web = CommandLine.arguments[1] == "--web"
let args = Array(CommandLine.arguments.dropFirst(web ? 2 : 1))
let out = args[0]
let css = try! String(contentsOfFile: args[1], encoding: .utf8)
let re = try! NSRegularExpression(pattern: #"piece\.knight\.white \{\s*background-image: url\('data:image/svg\+xml;base64,([A-Za-z0-9+/=]+)'\)"#)
let m = re.firstMatch(in: css, range: NSRange(css.startIndex..., in: css))!
let b64 = String(css[Range(m.range(at: 1), in: css)!])
let knight = NSImage(data: Data(base64Encoded: b64)!)!

try? FileManager.default.createDirectory(atPath: out, withIntermediateDirectories: true)

func png(_ px: Int, fullBleed: Bool = false) -> Data {
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: px, pixelsHigh: px, bitsPerSample: 8,
                               samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                               colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    let s = CGFloat(px)
    let rect = fullBleed ? NSRect(x: 0, y: 0, width: s, height: s)
                         : NSRect(x: s * 0.1, y: s * 0.1, width: s * 0.8, height: s * 0.8)
    let path = fullBleed ? NSBezierPath(rect: rect) : NSBezierPath(roundedRect: rect, xRadius: s * 0.18, yRadius: s * 0.18)
    NSColor(red: 0.941, green: 0.851, blue: 0.710, alpha: 1).setFill()
    path.fill()
    path.addClip()
    NSColor(red: 0.710, green: 0.533, blue: 0.388, alpha: 1).setFill()
    let half = rect.width / 2
    NSRect(x: rect.minX, y: rect.minY, width: half, height: half).fill()
    NSRect(x: rect.midX, y: rect.midY, width: half, height: half).fill()
    let low = NSRect(x: rect.minX, y: rect.minY, width: half, height: half)
    knight.draw(in: low.insetBy(dx: half * 0.06, dy: half * 0.06))
    // the arrow, as chessground draws it: a thick line and a triangular head, green, a little transparent
    let green = NSColor(red: 0.082, green: 0.471, blue: 0.106, alpha: 0.9)
    let from = NSPoint(x: low.midX, y: low.midY)
    let to = NSPoint(x: rect.midX + half / 2, y: rect.midY + half / 2)
    let dx = to.x - from.x, dy = to.y - from.y
    let len = (dx * dx + dy * dy).squareRoot()
    let ux = dx / len, uy = dy / len
    let head = half * 0.42, width = half * 0.16
    let base = NSPoint(x: to.x - ux * head, y: to.y - uy * head)
    let line = NSBezierPath()
    line.move(to: NSPoint(x: from.x + ux * half * 0.18, y: from.y + uy * half * 0.18))
    line.line(to: base)
    line.lineWidth = width
    line.lineCapStyle = .round
    green.setStroke()
    line.stroke()
    let tri = NSBezierPath()
    tri.move(to: to)
    tri.line(to: NSPoint(x: base.x - uy * head * 0.62, y: base.y + ux * head * 0.62))
    tri.line(to: NSPoint(x: base.x + uy * head * 0.62, y: base.y - ux * head * 0.62))
    tri.close()
    green.setFill()
    tri.fill()
    NSGraphicsContext.restoreGraphicsState()
    return rep.representation(using: .png, properties: [:])!
}

if web {
    try! png(180, fullBleed: true).write(to: URL(fileURLWithPath: "\(out)/apple-touch-icon.png"))
    try! png(64).write(to: URL(fileURLWithPath: "\(out)/favicon.png"))
    print("web icons ->", out)
    exit(0)
}

for (name, px) in [("16x16", 16), ("16x16@2x", 32), ("32x32", 32), ("32x32@2x", 64), ("128x128", 128),
                   ("128x128@2x", 256), ("256x256", 256), ("256x256@2x", 512), ("512x512", 512), ("512x512@2x", 1024)] {
    try! png(px).write(to: URL(fileURLWithPath: "\(out)/icon_\(name).png"))
}
print("icons ->", out)
