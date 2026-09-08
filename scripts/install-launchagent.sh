#!/usr/bin/env bash
# Install/Uninstall ServiceHub as a macOS LaunchAgent (Background Daemon)

set -e

PLIST_NAME="com.slcnx.service-hub.plist"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$PLIST_DIR/$PLIST_NAME"
SERVICE_HUB_BIN="$HOME/.local/bin/service-hub"

if [ ! -f "$SERVICE_HUB_BIN" ]; then
    # Fallback to local script
    DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
    SERVICE_HUB_BIN="$DIR/service-hub"
fi

LOGS_DIR="$HOME/.config/service-hub/logs"
mkdir -p "$LOGS_DIR"
mkdir -p "$PLIST_DIR"

if [ "$1" == "--uninstall" ] || [ "$1" == "-u" ]; then
    echo "Uninstalling ServiceHub LaunchAgent..."
    launchctl unload "$PLIST_PATH" 2>/dev/null || true
    rm -f "$PLIST_PATH"
    echo "✅ ServiceHub LaunchAgent removed successfully."
    exit 0
fi

echo "Installing ServiceHub as macOS LaunchAgent..."

cat << PLIST > "$PLIST_PATH"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.slcnx.service-hub</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>-c</string>
        <string>exec $SERVICE_HUB_BIN --port 9099</string>
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
launchctl unload "$PLIST_PATH" 2>/dev/null || true
launchctl load -w "$PLIST_PATH"

echo "✅ ServiceHub has been registered and started as a macOS LaunchAgent!"
echo "   - Plist: $PLIST_PATH"
echo "   - Log:   $LOGS_DIR/service-hub.log"
echo "   - Web UI: http://127.0.0.1:9099"
echo ""
echo "Manage with:"
echo "   launchctl load -w $PLIST_PATH    # Start / Enable"
echo "   launchctl unload $PLIST_PATH      # Stop / Disable"
echo "   tail -f $LOGS_DIR/service-hub.log # View logs"
