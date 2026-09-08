#!/usr/bin/env python3
"""
Builds a native macOS ServiceHub.app Bundle and registers the LaunchAgent.
This ensures:
1. macOS Background Task Management (BTM) recognizes the app by name: "ServiceHub.app" (instead of "bash")
2. Avoids "来自未标识的开发者 / 身份不明" warning in System Settings -> Login Items
3. Solves macOS TCC "Operation not permitted" sandbox issues when running from ~/Documents
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw

APP_NAME = "ServiceHub.app"
BUNDLE_ID = "com.slcnx.servicehub"
APP_DIR = Path.home() / "Applications" / APP_NAME
CONTENTS = APP_DIR / "Contents"
MACOS_DIR = CONTENTS / "MacOS"
RESOURCES_DIR = CONTENTS / "Resources"
SOURCE_DIR = Path(__file__).parent.resolve()


def generate_icon(iconset_dir: Path):
    iconset_dir.mkdir(parents=True, exist_ok=True)
    sizes = [16, 32, 64, 128, 256, 512, 1024]
    
    # Create base 1024x1024 image
    img = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # Rounded rectangle background (deep indigo / purple gradient style)
    draw.rounded_rectangle([64, 64, 960, 960], radius=200, fill=(79, 70, 229, 255), outline=(129, 140, 248, 255), width=20)
    
    # Simple rocket / gear motif
    # Rocket body
    draw.polygon([(512, 220), (400, 520), (624, 520)], fill=(255, 255, 255, 255))
    draw.polygon([(400, 520), (350, 680), (450, 620)], fill=(239, 68, 68, 255))
    draw.polygon([(624, 520), (674, 680), (574, 620)], fill=(239, 68, 68, 255))
    draw.ellipse([462, 340, 562, 440], fill=(59, 130, 246, 255))
    draw.ellipse([482, 360, 542, 420], fill=(255, 255, 255, 255))
    # Rocket flame
    draw.polygon([(472, 620), (512, 760), (552, 620)], fill=(245, 158, 11, 255))
    
    for s in sizes:
        resized = img.resize((s, s), Image.Resampling.LANCZOS)
        if s <= 512:
            resized.save(iconset_dir / f"icon_{s}x{s}.png")
            # 2x Retina
            if s * 2 <= 1024:
                resized2x = img.resize((s * 2, s * 2), Image.Resampling.LANCZOS)
                resized2x.save(iconset_dir / f"icon_{s}x{s}@2x.png")
        if s == 1024:
            resized.save(iconset_dir / "icon_512x512@2x.png")

    icns_path = RESOURCES_DIR / "AppIcon.icns"
    subprocess.run(["iconutil", "-c", "icns", str(iconset_dir), "-o", str(icns_path)], check=True)
    shutil.rmtree(iconset_dir, ignore_errors=True)
    print("✅ AppIcon.icns generated")


def compile_launcher():
    c_source = MACOS_DIR / "launcher.c"
    binary_path = MACOS_DIR / "ServiceHub"
    
    # Detect best python
    preferred_python = "/opt/homebrew/Caskroom/miniconda/base/envs/jupy/bin/python"
    if not os.path.exists(preferred_python):
        preferred_python = sys.executable

    c_code = f"""
#include <unistd.h>
#include <stdio.h>
#include <stdlib.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <libgen.h>

int main(int argc, char *argv[]) {{
    char path[PATH_MAX];
    uint32_t size = sizeof(path);
    if (_NSGetExecutablePath(path, &size) != 0) {{
        return 1;
    }}
    char *dir = dirname(path);
    char script_path[PATH_MAX];
    snprintf(script_path, sizeof(script_path), "%s/service_hub.py", dir);

    const char *python = "{preferred_python}";
    if (access(python, X_OK) != 0) {{
        python = "/opt/homebrew/bin/python3";
    }}

    char *new_argv[argc + 3];
    new_argv[0] = (char *)python;
    new_argv[1] = script_path;
    for (int i = 1; i < argc; i++) {{
        new_argv[i + 1] = argv[i];
    }}
    new_argv[argc + 1] = NULL;

    execv(python, new_argv);
    perror("execv failed");
    return 1;
}}
"""
    with open(c_source, "w") as f:
        f.write(c_code)

    subprocess.run(["clang", "-O2", str(c_source), "-o", str(binary_path)], check=True)
    c_source.unlink()
    binary_path.chmod(0o755)
    print("✅ Native Mach-O launcher compiled")


def build_app():
    print(f"Building {APP_DIR}...")
    MACOS_DIR.mkdir(parents=True, exist_ok=True)
    RESOURCES_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Info.plist
    info_plist = CONTENTS / "Info.plist"
    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleIdentifier</key>
    <string>{BUNDLE_ID}</string>
    <key>CFBundleName</key>
    <string>ServiceHub</string>
    <key>CFBundleDisplayName</key>
    <string>ServiceHub</string>
    <key>CFBundleExecutable</key>
    <string>ServiceHub</string>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0.0</string>
    <key>CFBundleVersion</key>
    <string>1.0.0</string>
    <key>LSUIElement</key>
    <true/>
    <key>NSHumanReadableCopyright</key>
    <string>Copyright © 2026 slcnx. MIT License.</string>
</dict>
</plist>
"""
    with open(info_plist, "w") as f:
        f.write(plist_content)

    # 2. Copy service_hub.py
    shutil.copy2(SOURCE_DIR / "service_hub.py", MACOS_DIR / "service_hub.py")

    # 3. Generate icon
    iconset_dir = APP_DIR / "AppIcon.iconset"
    generate_icon(iconset_dir)

    # 4. Compile Mach-O launcher
    compile_launcher()

    # 5. Ad-hoc codesign
    try:
        subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(APP_DIR)], check=True)
        print("✅ App bundle signed with ad-hoc signature")
    except Exception as e:
        print(f"⚠️ Codesign warning: {e}")

    print(f"🎉 Successfully built: {APP_DIR}")


if __name__ == "__main__":
    build_app()
