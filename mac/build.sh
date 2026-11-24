#!/bin/zsh
# Build «Видение доски.app» into ~/Applications.
set -euo pipefail
HERE=${0:A:h}
ROOT=${HERE:h}
BUILD="$HERE/build"
APP="$HOME/Applications/Видение доски.app"
mkdir -p "$BUILD" "$HOME/Applications"

swiftc -O -o "$BUILD/BoardVision" "$HERE/main.swift" -framework Cocoa -framework WebKit

if [[ ! -f "$BUILD/AppIcon.icns" ]]; then
  swift "$HERE/make_icon.swift" "$BUILD/AppIcon.iconset" "$ROOT/vision/web/vendor/pieces.merida.css"
  iconutil -c icns "$BUILD/AppIcon.iconset" -o "$BUILD/AppIcon.icns"
fi
if [[ ! -f "$ROOT/vision/web/apple-touch-icon.png" ]]; then
  swift "$HERE/make_icon.swift" --web "$ROOT/vision/web" "$ROOT/vision/web/vendor/pieces.merida.css"
fi

mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BUILD/BoardVision" "$APP/Contents/MacOS/BoardVision"
cp "$BUILD/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"
cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Видение доски</string>
  <key>CFBundleDisplayName</key><string>Видение доски</string>
  <key>CFBundleIdentifier</key><string>local.alkbt.board-vision</string>
  <key>CFBundleExecutable</key><string>BoardVision</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.1</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>CFBundleDevelopmentRegion</key><string>ru</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
</dict>
</plist>
PLIST
# where the window finds the server (mac/main.swift reads it)
/usr/libexec/PlistBuddy -c "Add :BoardVisionRoot string $ROOT" "$APP/Contents/Info.plist"
codesign --force -s - "$APP" >/dev/null 2>&1
touch "$APP"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP" || true
echo "built: $APP"
