"""
build_exe.py — Build ClipboardSync.exe using PyInstaller

Usage:
    pip install pyinstaller
    python build_exe.py
"""
import subprocess
import sys

cmd = [
    "pyinstaller",
    "--onefile",
    "--noconsole",
    "--name", "ClipboardSync",
    "--add-data", "core;core",
    "--hidden-import", "plistlib",
    "--hidden-import", "PIL",
    "--hidden-import", "flask",
    "--hidden-import", "pyperclip",
    "main.py"
]

print("Building ClipboardSync.exe...")
result = subprocess.run(cmd, cwd=".")
if result.returncode == 0:
    print("\n✅ Build successful! Find ClipboardSync.exe in the dist/ folder.")
else:
    print("\n❌ Build failed. Check output above for errors.")
    sys.exit(1)
