"""
build_exe.py — Build AirClip.exe using PyInstaller

Usage:
    pip install pyinstaller
    python build_exe.py
"""
import os
import shutil
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
    "--hidden-import", "pystray",
    "--hidden-import", "pystray._win32",
    "main.py"
]

print("Building AirClip.exe...")
result = subprocess.run(cmd, cwd=".")

if result.returncode == 0:
    if os.path.exists("AirClip.sh"):
        os.makedirs("dist", exist_ok=True)
        shutil.copy("AirClip.sh", "dist/AirClip.sh")
    print("\n[SUCCESS] Build complete! AirClip.exe and AirClip.sh are ready in dist/")
else:
    print("\n[ERROR] Build failed.")
    sys.exit(1)
