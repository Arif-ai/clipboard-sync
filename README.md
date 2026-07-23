# Universal iOS <-> Windows Clipboard Sync

A lightweight, high-performance background service for real-time bidirectional clipboard synchronization between Windows PCs and iOS devices (iPhone/iPad).

Supports **Text**, **Rich Text (RTF)**, **Safari/Web Clips**, **Apple Binary Property Lists (bplist)**, and **Images/Screenshots**.

---

## Features

- ⚡ **Instant Bidirectional Sync**: Seamlessly copy on PC and paste on iPhone, or copy on iPhone and paste on PC.
- 🖼️ **Image & Screenshot Support**: Full PNG image sync for screenshots and photos.
- 🧹 **Automatic Plist & RTF Decoding**: Converts Apple binary property lists and Rich Text Format into clean plain text.
- 🔄 **Loop Prevention**: Smart state tracking prevents infinite sync echoes.
- 🚀 **Silent Windows Autostart**: Runs invisibly in the background on Windows boot.

---

## Quick Start (Windows Setup)

1. Clone or download this repository:
   ```bash
   git clone https://github.com/your-username/clipboard-sync.git
   cd clipboard-sync
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Run the server:
   ```bash
   python main.py
   ```

---

## iOS Shortcuts Setup

### Option A: Dedicated Shortcuts (Recommended)

1. **Send to PC (iPhone -> PC)**:
   - Action: `Get Clipboard`
   - Action: `Get Contents of URL` (`http://<YOUR-PC-IP>:5000/send`, Method: **POST**, Body: **Clipboard**)

2. **Get from PC (PC -> iPhone)**:
   - Action: `Get Contents of URL` (`http://<YOUR-PC-IP>:5000/get`, Method: **GET**)
   - Action: `Copy to Clipboard`

### Option B: Merged Shortcut

- Endpoint: `http://<YOUR-PC-IP>:5000/sync` (Supports dynamic priority bidirectional sync)

---

## License

MIT License. Free for personal and commercial distribution.
