# ⚡ AirClip

**AirClip** is a lightweight, open-source, local-first Universal Clipboard engine. It connects iPhone, iPad, Windows, macOS, and Linux seamlessly over local Wi-Fi with zero cloud dependencies and zero privacy leaks.

---

## ✨ Features

- 🔄 **Bidirectional Clipboard Sync**: Copy on PC → Paste on iPhone. Copy on iPhone → Paste on PC instantly.
- 🎨 **Apple Liquid Glass UI**: Desktop dashboard built with `pywebview` featuring dark HSL glassmorphism, ambient gradient orbs, and physical press animations.
- ⏸️ **Active / Deactivate Toggle Pill**: One-click status button to pause and resume clipboard synchronization on demand.
- 📱 **1-Click iOS Shortcuts & QR Code Pairing**: Scan QR codes displayed in the UI to pair instantly without typing URLs.
- 🔐 **Hardened Security**: 64-char Auth Token validation, private LAN-only filter, rate limiting, and 10MB payload size caps.
- 📡 **mDNS Auto-Discovery**: Broadcasts `_clipsync._tcp.local` across your local network.
- 🧹 **Smart Parser**: Strips HTML scripts/CSS and extracts clean target URLs from Google search redirects.

---

## 📱 1-Click iOS Shortcuts

- **Send Shortcut (iPhone → PC)**: [Import Shortcut](https://www.icloud.com/shortcuts/09cb23ded9f84cd1a0eeea91450c5a49)
- **Get Shortcut (PC → iPhone)**: [Import Shortcut](https://www.icloud.com/shortcuts/bc67f1f38b4c4be8bf1d33e50c5191c1)

---

## 🚀 Installation & Running

### Windows
1. Download standalone binary **`AirClip.exe`** from [GitHub Releases](https://github.com/Arif-ai/airclip/releases).
2. Double-click to launch! No installation required.

### Linux & macOS
```bash
git clone https://github.com/Arif-ai/airclip.git
cd airclip
chmod +x AirClip.sh
./AirClip.sh
```

---

## 📜 License

MIT License. Open-source software.
