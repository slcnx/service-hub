#!/usr/bin/env bash
# Install/Uninstall ServiceHub as a native macOS LaunchAgent (Background Daemon)
# Automatically builds and associates ServiceHub.app bundle to avoid:
# 1. "来自未标识的开发者 / 身份不明" warning in System Settings -> Login Items
# 2. Displaying as generic "bash"
# 3. TCC permission issues (Operation not permitted)

set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
PLIST_NAME="com.slcnx.service-hub.plist"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$PLIST_DIR/$PLIST_NAME"
APP_PATH="$HOME/Applications/ServiceHub.app"
APP_LAUNCHER="$APP_PATH/Contents/MacOS/ServiceHub"
LOGS_DIR="$HOME/.config/service-hub/logs"

# Preferred Python interpreter
PYTHON_BIN="/opt/homebrew/Caskroom/miniconda/base/envs/jupy/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
    PYTHON_BIN="$(which python3)"
fi

if [ "$1" == "--uninstall" ] || [ "$1" == "-u" ]; then
    echo "Uninstalling ServiceHub LaunchAgent..."
    launchctl bootout "gui/$(id -u)/com.slcnx.service-hub" 2>/dev/null || launchctl unload "$PLIST_PATH" 2>/dev/null || true
    rm -f "$PLIST_PATH"
    echo "✅ ServiceHub LaunchAgent removed successfully."
    exit 0
fi

echo "🚀 Building / Updating ServiceHub.app native bundle..."
"$PYTHON_BIN" "$DIR/build_mac_app.py"

# Register bundle with macOS LaunchServices
if [ -x "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister" ]; then
    /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP_PATH" 2>/dev/null || true
fi

mkdir -p "$LOGS_DIR"
mkdir -p "$PLIST_DIR"

echo "📝 Installing ServiceHub as macOS LaunchAgent with native app identity..."

cat << PLIST > "$PLIST_PATH"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.slcnx.service-hub</string>
    <key>AssociatedBundleIdentifiers</key>
    <array>
        <string>com.slcnx.servicehub</string>
    </array>
    <key>ProgramArguments</key>
    <array>
        <string>$APP_LAUNCHER</string>
        <string>--port</string>
        <string>9099</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$LOGS_DIR/service-hub.log</string>
    <key>StandardErrorPath</key>
    <string>$LOGS_DIR/service-hub-error.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$HOME/.local/bin:$PATH</string>
    </dict>
</dict>
</plist>
PLIST

# Unload previous instance if running
launchctl bootout "gui/$(id -u)/com.slcnx.service-hub" 2>/dev/null || launchctl unload "$PLIST_PATH" 2>/dev/null || true
# Load new configuration
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH" 2>/dev/null || launchctl load -w "$PLIST_PATH"

echo "✅ ServiceHub has been registered and started as a macOS Background App!"
echo "   - App Bundle: $APP_PATH"
echo "   - Identifier: com.slcnx.servicehub"
echo "   - Plist:      $PLIST_PATH"
echo "   - Log:        $LOGS_DIR/service-hub.log"
echo "   - Web UI:     http://127.0.0.1:9099"
echo ""
echo "Manage with:"
echo "   launchctl load -w $PLIST_PATH    # Start / Enable"
echo "   launchctl unload $PLIST_PATH      # Stop / Disable"
echo "   tail -f $LOGS_DIR/service-hub.log # View logs"
