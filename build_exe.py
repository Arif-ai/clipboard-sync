"""
build_exe.py — Build AirClip.exe using PyInstaller

Usage:
    pip install pyinstaller
    python build_exe.py
"""
import subprocess
import sys

cmd = [
    sys.executable, "-m", "PyInstaller",
    "--onefile",
    "--noconsole",
    "--name", "AirClip",
    "--add-data", "core;core",
    "--icon", "icon.ico",
    "--hidden-import", "plistlib",
    "--hidden-import", "PIL",
    "--hidden-import", "flask",
    "--hidden-import", "pyperclip",
    "--hidden-import", "webview",
    "--hidden-import", "webview.platforms.winforms",
    "--hidden-import", "zeroconf",
    "--hidden-import", "qrcode",
    "main.py"
]

print("Building AirClip.exe...")
result = subprocess.run(cmd, cwd=".")
if result.returncode == 0:
    print("\n✅ Build successful! Find AirClip.exe in the dist/ folder.")
else:
    print("\n❌ Build failed. Check output above for errors.")
    sys.exit(1)
