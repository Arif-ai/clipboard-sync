"""
AirClip ⚡ — Cross-Platform Bidirectional Clipboard Synchronizer
License: MIT

Features:
- Bidirectional Clipboard Synchronization (PC ↔ iPhone / Android / Mac / Linux)
- Active / Deactivate Toggle (Pause/Resume server on demand)
- Loop & Overwrite Conflict Prevention (Separate PC & Mobile payload states)
- Security Hardening (Auth Token, LAN IP Filter, Rate Limiting, 10MB Payload Cap)
- mDNS Auto-Discovery (_clipsync._tcp.local)
- Apple Liquid Glass Dashboard UI with dark QR code setup
- Built-in Developer Secret (/secret)
"""

import io
import os
import sys
import time
import uuid
import socket
import secrets
import base64
import subprocess
import threading
import queue
import json
import pyperclip
import webview
import qrcode
from collections import defaultdict
from PIL import Image, ImageGrab
from flask import Flask, request, jsonify, send_file, Response, abort
from zeroconf import Zeroconf, ServiceInfo

# ─── PyInstaller / Frozen Path Resolution ─────────────────────────────────────
if getattr(sys, 'frozen', False):
    basedir = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, basedir)
else:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.parsers import extract_text_from_incoming, extract_rtf_text, clean_html

# ─── Configuration Constants ──────────────────────────────────────────────────
TEMP_DIR    = os.environ.get("TEMP", os.environ.get("TMPDIR", "/tmp"))
PORT        = 5000
SECRET_FILE = os.path.join(os.path.expanduser("~"), ".clipsyncsecret")
MAX_PAYLOAD = 10 * 1024 * 1024   # 10 MB maximum request payload size
RATE_LIMIT  = 60                  # Maximum 60 requests/min per IP
PRIVATE_PREFIXES = (
    "192.168.", "10.", "172.16.", "172.17.", "172.18.", "172.19.",
    "172.20.", "172.21.", "172.22.", "172.23.", "172.24.", "172.25.",
    "172.26.", "172.27.", "172.28.", "172.29.", "172.30.", "172.31.",
    "127.", "::1",
)

# ─── Developer Secret Payload ──────────────────────────────────────────────────
DEVELOPER_SECRET = {
    "engine": "AirClip ⚡",
    "status": "ok",
    "tagline": "Universal Local Clipboard Engine. Built for speed and absolute privacy.",
    "secret": "⚡ Space and device boundaries mean nothing when clipboard is synchronized."
}

# ─── Security Token Management ────────────────────────────────────────────────
def load_or_create_token():
    """Load existing secret auth token or generate a fresh 64-char hex token."""
    if os.path.exists(SECRET_FILE):
        try:
            with open(SECRET_FILE, "r") as f:
                token = f.read().strip()
                if len(token) >= 32:
                    return token
        except Exception:
            pass
    token = secrets.token_hex(32)
    try:
        with open(SECRET_FILE, "w") as f:
            f.write(token)
    except Exception:
        pass
    return token

AUTH_TOKEN = load_or_create_token()

# ─── Global State & Synchronization Controls ──────────────────────────────────
app          = Flask(__name__)
log_queue    = queue.Queue()
ui_window    = None
is_active    = True
_active_lock = threading.Lock()

_rate_map  = defaultdict(list)
_rate_lock = threading.Lock()

def get_local_ip():
    """Dynamically determine the host computer's primary LAN IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

LOCAL_IP  = get_local_ip()
start_now = time.time()

# Separate PC and Mobile state to prevent overwrite loops and conflicts
pc_state = {
    "type": "text",
    "text": "Server Ready",
    "image_bytes": None,
    "timestamp": start_now
}
last_pc_clipboard_text = None
last_pc_clipboard_img  = None

# ─── Cross-Platform Clipboard Helper ──────────────────────────────────────────
def set_pc_image_clipboard(data):
    """Write raw PNG image bytes to the host system clipboard (Win/Mac/Linux)."""
    try:
        tmp_path = os.path.join(TEMP_DIR, f"iphone_clip_{time.time_ns()}.png")
        img = Image.open(io.BytesIO(data))
        img.save(tmp_path, "PNG")

        if sys.platform == "win32":
            ps_cmd = (
                f"Add-Type -AssemblyName System.Windows.Forms; "
                f"[System.Windows.Forms.Clipboard]::SetImage("
                f"[System.Drawing.Image]::FromFile('{tmp_path}'))"
            )
            subprocess.run(["powershell", "-Command", ps_cmd], capture_output=True)
        elif sys.platform == "darwin":
            try:
                subprocess.run(["pngpaste", tmp_path], capture_output=True)
            except Exception:
                script = (
                    f'set theClipboard to (read (POSIX file "{tmp_path}") as «class PNGf»)\n'
                    f'set the clipboard to theClipboard'
                )
                subprocess.run(["osascript", "-e", script], capture_output=True)
        else:  # Linux (xclip / wl-clipboard)
            try:
                subprocess.run(["xclip", "-selection", "clipboard", "-target", "image/png", "-i", tmp_path], capture_output=True)
            except Exception:
                subprocess.run(["wl-copy", "-t", "image/png", "<", tmp_path], shell=True, capture_output=True)
    except Exception as e:
        gui_log(f"Clipboard image write error: {e}", "ERROR")

# ─── Logging & Security Middleware ─────────────────────────────────────────────
def gui_log(msg, tag="INFO"):
    """Queue formatted log messages for thread-safe UI rendering."""
    ts = time.strftime("%H:%M:%S")
    log_queue.put((ts, msg, tag))

def is_private_ip(ip):
    """Check if the requesting client IP is on a private LAN network."""
    return ip.startswith(PRIVATE_PREFIXES)

def is_rate_limited(ip):
    """Enforce maximum request rates (60 req/min per IP)."""
    now = time.time()
    with _rate_lock:
        timestamps = _rate_map[ip]
        recent = [t for t in timestamps if now - t < 60]
        _rate_map[ip] = recent
        if len(recent) >= RATE_LIMIT:
            return True
        _rate_map[ip].append(now)
    return False

# Internal UI-only paths that bypass all security checks
_INTERNAL_PATHS = ("/api/logs", "/api/toggle-status", "/secret")

def check_request():
    """Security filter executed before every API request."""
    # Internal dashboard endpoints bypass all security and active checks
    if request.path in _INTERNAL_PATHS:
        return

    # Block all clipboard sync when server is deactivated
    if not is_active:
        gui_log("Blocked request: Server is DEACTIVATED", "WARN")
        abort(503, "AirClip server is currently deactivated by user")

    client_ip = request.remote_addr or ""

    # 1. LAN-Only Guard
    if not is_private_ip(client_ip):
        gui_log(f"BLOCKED non-LAN request from {client_ip}", "WARN")
        abort(403, "Access restricted to local network")

    # 2. Rate Limiting
    if is_rate_limited(client_ip):
        gui_log(f"RATE-LIMITED {client_ip}", "WARN")
        abort(429, "Too many requests")

    # 3. Payload Cap
    if request.content_length and request.content_length > MAX_PAYLOAD:
        abort(413, "Payload exceeds 10MB cap")

    # 4. Auth Token Verification (timing-safe)
    token = (
        request.headers.get("X-ClipSync-Token")
        or request.args.get("token")
        or ""
    )
    if not secrets.compare_digest(token, AUTH_TOKEN):
        gui_log(f"UNAUTHORIZED request from {client_ip}", "WARN")
        abort(401, "Invalid or missing auth token")

app.before_request(check_request)

# ─── API Endpoints & Developer Secret ──────────────────────────────────────────
@app.route("/secret", methods=["GET"])
def developer_secret():
    """Secret coded endpoint revealing developer message."""
    gui_log("⚡ Developer Secret unlocked!", "INFO")
    return jsonify(DEVELOPER_SECRET)

@app.route("/api/logs", methods=["GET"])
def get_logs_api():
    """Thread-safe API endpoint returning queued activity log items to the JS dashboard."""
    logs = []
    while not log_queue.empty():
        logs.append(log_queue.get())
    return jsonify({"logs": logs, "active": is_active})

@app.route("/api/toggle-status", methods=["POST"])
def toggle_status_api():
    """Toggle AirClip server active/deactivated state on demand."""
    global is_active
    with _active_lock:
        is_active = not is_active
        status_str = "ACTIVE" if is_active else "DEACTIVATED"
        gui_log(f"Server state changed to {status_str}", "INFO")
        return jsonify({"active": is_active})

@app.route("/get",      methods=["GET", "POST"])
@app.route("/get-clip", methods=["GET", "POST"])
def get_pc_clipboard():
    """Send current PC clipboard content (text or screenshot) to iPhone."""
    try:
        if pc_state["type"] == "text" and pc_state.get("text"):
            gui_log(f"PC → iPhone: '{pc_state['text'][:50]}'", "SEND")
            return Response(pc_state["text"], status=200, content_type="text/plain; charset=utf-8")
        elif pc_state["type"] == "image" and pc_state.get("image_bytes"):
            gui_log("PC → iPhone: Screenshot/Image", "SEND")
            return send_file(io.BytesIO(pc_state["image_bytes"]), mimetype="image/png")
    except Exception as e:
        gui_log(f"GET error: {e}", "ERROR")
    return Response(status=204)

@app.route("/send",      methods=["POST"])
@app.route("/send-clip", methods=["POST"])
def send_iphone_clipboard():
    """Receive copied content (text or image) from iPhone and apply to PC clipboard."""
    global last_pc_clipboard_text, last_pc_clipboard_img
    try:
        data         = request.data or b""
        content_type = request.headers.get("Content-Type", "").lower()

        if data and ("image" in content_type or "octet-stream" in content_type):
            set_pc_image_clipboard(data)
            last_pc_clipboard_img = data
            pc_state.update({"type": "image", "image_bytes": data, "timestamp": time.time()})
            gui_log("iPhone → PC: Image received", "RECV")
            return jsonify({"status": "ok", "type": "image"}), 200

        elif data:
            text = extract_text_from_incoming(data)
            if text and '{"status"' not in text and not text.startswith("bplist00"):
                pyperclip.copy(text)
                last_pc_clipboard_text = text
                pc_state.update({"type": "text", "text": text, "timestamp": time.time()})
                gui_log(f"iPhone → PC: '{text[:50]}'", "RECV")
                return jsonify({"status": "ok", "type": "text"}), 200
    except Exception as e:
        gui_log(f"SEND error: {e}", "ERROR")
    return Response(status=204)

@app.route("/",          methods=["GET", "POST"])
@app.route("/sync",      methods=["GET", "POST"])
@app.route("/sync/",     methods=["GET", "POST"])
@app.route("/clip",      methods=["GET", "POST"])
@app.route("/clip/",     methods=["GET", "POST"])
@app.route("/clipboard", methods=["GET", "POST"])
@app.route("/clipboard/",methods=["GET", "POST"])
def sync_clipboard():
    """Unified bidirectional sync endpoint with conflict & overwrite protection."""
    global last_pc_clipboard_text, last_pc_clipboard_img
    try:
        data = request.data or b""
        # If payload provided, update PC clipboard (iPhone → PC)
        if data:
            content_type = request.headers.get("Content-Type", "").lower()
            if "image" in content_type or "octet-stream" in content_type:
                set_pc_image_clipboard(data)
                last_pc_clipboard_img = data
                pc_state.update({"type": "image", "image_bytes": data, "timestamp": time.time()})
                gui_log("iPhone → PC: Image received", "RECV")
                return jsonify({"status": "ok", "type": "image"}), 200
            else:
                text = extract_text_from_incoming(data)
                if text and '{"status"' not in text and not text.startswith("bplist00"):
                    pyperclip.copy(text)
                    last_pc_clipboard_text = text
                    pc_state.update({"type": "text", "text": text, "timestamp": time.time()})
                    gui_log(f"iPhone → PC: '{text[:50]}'", "RECV")
                    return jsonify({"status": "ok", "type": "text"}), 200
        else:
            # If no payload, fetch PC clipboard (PC → iPhone)
            return get_pc_clipboard()
    except Exception as e:
        gui_log(f"Sync error: {e}", "ERROR")
    return Response(status=204)

# ─── PC Clipboard Background Monitor ──────────────────────────────────────────
def monitor_pc_clipboard():
    """Background thread polling PC system clipboard for local copy events."""
    global last_pc_clipboard_text, last_pc_clipboard_img
    gui_log("PC Clipboard monitor active", "INFO")
    while True:
        try:
            if is_active:
                # 1. Check for image on PC clipboard
                img = ImageGrab.grabclipboard()
                if isinstance(img, Image.Image):
                    buf = io.BytesIO()
                    img.save(buf, format="PNG")
                    img_bytes = buf.getvalue()
                    if img_bytes != last_pc_clipboard_img:
                        last_pc_clipboard_img = img_bytes
                        pc_state.update({"type": "image", "image_bytes": img_bytes, "timestamp": time.time()})
                        gui_log("PC: Screenshot copied", "LOCAL")
                        time.sleep(1.0)
                        continue

                # 2. Check for text on PC clipboard
                text = pyperclip.paste()
                if text and text != last_pc_clipboard_text:
                    if '{"status"' in text or "bplist00" in text or "WebMainResource" in text:
                        pyperclip.copy("")
                        last_pc_clipboard_text = ""
                        continue
                    if "{\\rtf1" in text:
                        text = extract_rtf_text(text)
                        pyperclip.copy(text)
                    last_pc_clipboard_text = text
                    pc_state.update({"type": "text", "text": text, "timestamp": time.time()})
                    gui_log(f"PC: Text copied — '{text[:40]}'", "LOCAL")
        except Exception:
            pass
        time.sleep(0.5)

# ─── mDNS Zeroconf Registration ───────────────────────────────────────────────
def register_mdns():
    """Broadcast _clipsync._tcp.local mDNS service for zero-config local discovery."""
    try:
        zeroconf = Zeroconf()
        info = ServiceInfo(
            "_clipsync._tcp.local.",
            "AirClip._clipsync._tcp.local.",
            addresses=[socket.inet_aton(LOCAL_IP)],
            port=PORT,
            properties={"version": "1.0.0", "auth": "token"},
            server="airclip.local.",
        )
        zeroconf.register_service(info)
        gui_log("mDNS Auto-Discovery active (_clipsync._tcp.local)", "INFO")
    except Exception as e:
        gui_log(f"mDNS error: {e}", "WARN")

# ─── QR Code Generator ─────────────────────────────────────────────────────────
def generate_qr_base64(url):
    """Generate seamless dark-themed QR code PNG base64 string."""
    try:
        qr = qrcode.QRCode(version=1, box_size=5, border=1)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="#f0f0f5", back_color="#0e0e14")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode('utf-8')
    except Exception:
        return ""

# ─── Flask Server Thread ───────────────────────────────────────────────────────
def run_flask():
    app.run(host="0.0.0.0", port=PORT, threaded=True, debug=False, use_reloader=False)

# ─── Apple Liquid Glass UI Dashboard HTML ───────────────────────────────────────
def build_html():
    icloud_send_url = "https://www.icloud.com/shortcuts/09cb23ded9f84cd1a0eeea91450c5a49"
    icloud_get_url  = "https://www.icloud.com/shortcuts/bc67f1f38b4c4be8bf1d33e50c5191c1"
    
    send_qr_b64 = generate_qr_base64(icloud_send_url)
    get_qr_b64  = generate_qr_base64(icloud_get_url)

    send_url_clean = f"http://{LOCAL_IP}:{PORT}/send"
    get_url_clean  = f"http://{LOCAL_IP}:{PORT}/get"
    token_display  = AUTH_TOKEN[:8] + "••••••••" + AUTH_TOKEN[-4:]

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AirClip ⚡</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

  :root {{
    --bg:           #08080c;
    --panel:        rgba(255,255,255,0.05);
    --panel-border: rgba(255,255,255,0.10);
    --glass:        rgba(255,255,255,0.08);
    --blue:         #3b82f6;
    --green:        #34d399;
    --amber:        #fbbf24;
    --red:          #f87171;
    --purple:       #a78bfa;
    --text:         #f0f0f5;
    --muted:        rgba(240,240,245,0.45);
    --radius:       16px;
    --radius-sm:    10px;
  }}

  body {{
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    background: var(--bg);
    color: var(--text);
    height: 100vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    -webkit-font-smoothing: antialiased;
    user-select: none;
  }}

  body::before {{
    content: '';
    position: fixed; inset: 0; pointer-events: none; z-index: 0;
    background:
      radial-gradient(ellipse 700px 400px at 20% 10%,  rgba(59,130,246,0.12) 0%, transparent 60%),
      radial-gradient(ellipse 500px 300px at 80% 80%,  rgba(167,139,250,0.10) 0%, transparent 60%),
      radial-gradient(ellipse 400px 300px at 50% 110%, rgba(52,211,153,0.08) 0%, transparent 55%);
  }}

  .container {{ position: relative; z-index: 1; padding: 16px 18px; display: flex; flex-direction: column; gap: 10px; flex: 1; min-height: 0; }}

  /* Header */
  .header {{
    display: flex; align-items: center; justify-content: space-between;
    background: var(--panel); border: 1px solid var(--panel-border);
    border-radius: var(--radius); padding: 12px 16px;
    backdrop-filter: blur(24px) saturate(180%);
    -webkit-backdrop-filter: blur(24px) saturate(180%);
    animation: slideDown 0.5s cubic-bezier(0.16,1,0.3,1) both;
  }}
  .brand {{ display: flex; align-items: center; gap: 10px; }}
  
  /* Liquid Glass Icon Badge */
  .brand-icon {{
    width: 36px; height: 36px; border-radius: 10px;
    background: linear-gradient(135deg, rgba(255,255,255,0.14) 0%, rgba(255,255,255,0.03) 100%);
    border: 1px solid rgba(255,255,255,0.18);
    box-shadow: 0 8px 24px rgba(0,0,0,0.5), inset 0 1px 1px rgba(255,255,255,0.3);
    backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px);
    display: flex; align-items: center; justify-content: center;
    font-size: 16px; transition: all 0.25s cubic-bezier(0.16,1,0.3,1);
    cursor: pointer;
  }}
  .brand-icon:hover {{
    transform: translateY(-2px) scale(1.05);
    border-color: rgba(255,255,255,0.35);
    box-shadow: 0 12px 30px rgba(59,130,246,0.3), inset 0 1px 2px rgba(255,255,255,0.5);
  }}
  .brand-icon:active {{ transform: translateY(1px) scale(0.96); }}

  .brand-name {{ font-size: 16px; font-weight: 700; letter-spacing: -0.3px; }}
  .badges {{ display: flex; gap: 6px; }}

  /* Interactive Status Toggle Pill (Active / Deactivated) */
  .status-pill {{
    display: flex; align-items: center; gap: 6px;
    background: rgba(52,211,153,0.12); border: 1px solid rgba(52,211,153,0.25);
    border-radius: 100px; padding: 4px 12px;
    font-size: 10px; font-weight: 600; color: var(--green); text-transform: uppercase;
    transition: all 0.25s cubic-bezier(0.16,1,0.3,1); cursor: pointer; user-select: none;
  }}
  .status-pill:hover {{ transform: scale(1.05); background: rgba(52,211,153,0.20); }}
  .status-pill:active {{ transform: scale(0.95); }}
  .status-dot {{
    width: 6px; height: 6px; border-radius: 50%; background: var(--green);
    box-shadow: 0 0 8px var(--green); animation: pulse 2s ease-in-out infinite;
    transition: all 0.25s ease;
  }}

  .status-pill.deactivated {{
    background: rgba(248,113,113,0.12); border-color: rgba(248,113,113,0.25);
    color: var(--red);
  }}
  .status-pill.deactivated:hover {{ background: rgba(248,113,113,0.20); }}
  .status-pill.deactivated .status-dot {{
    background: var(--red); box-shadow: 0 0 8px var(--red); animation: none;
  }}

  .mdns-pill {{
    display: flex; align-items: center; gap: 4px;
    background: rgba(167,139,250,0.12); border: 1px solid rgba(167,139,250,0.25);
    border-radius: 100px; padding: 4px 10px;
    font-size: 10px; font-weight: 600; color: var(--purple); text-transform: uppercase;
    transition: all 0.2s ease; cursor: pointer;
  }}
  .mdns-pill:hover {{ transform: scale(1.04); background: rgba(167,139,250,0.18); }}

  /* URLs Grid */
  .urls-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }}
  .url-card {{
    background: var(--panel); border: 1px solid var(--panel-border);
    border-radius: var(--radius-sm); padding: 10px 12px;
    backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px);
    transition: all 0.2s cubic-bezier(0.16,1,0.3,1);
    animation: slideUp 0.6s cubic-bezier(0.16,1,0.3,1) both;
  }}
  .url-card:hover {{
    transform: translateY(-2px); border-color: rgba(255,255,255,0.20);
    box-shadow: 0 8px 24px rgba(0,0,0,0.3);
  }}
  .url-label {{ font-size: 9px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.8px; color: var(--muted); margin-bottom: 4px; }}
  .url-row {{ display: flex; align-items: center; gap: 6px; }}
  .url-text {{ font-size: 11px; font-family: 'SF Mono', 'Consolas', monospace; color: var(--blue); flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .copy-btn {{
    padding: 4px 10px; border-radius: 6px; border: none; cursor: pointer;
    font-size: 10px; font-weight: 600; font-family: 'Inter', sans-serif;
    background: rgba(59,130,246,0.18); color: var(--blue);
    border: 1px solid rgba(59,130,246,0.25); transition: all 0.15s ease;
  }}
  .copy-btn:hover {{ background: rgba(59,130,246,0.35); transform: scale(1.05); }}
  .copy-btn:active {{ transform: scale(0.95); }}
  .copy-btn.copied {{ background: rgba(52,211,153,0.18); color: var(--green); border-color: rgba(52,211,153,0.25); }}

  /* Dark Glass QR Code Section */
  .qr-section {{
    display: flex; gap: 8px;
    background: var(--panel); border: 1px solid var(--panel-border);
    border-radius: var(--radius-sm); padding: 10px;
    backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px);
    animation: slideUp 0.65s cubic-bezier(0.16,1,0.3,1) 0.05s both;
  }}
  .qr-box {{
    flex: 1; display: flex; align-items: center; gap: 10px;
    background: rgba(14,14,20,0.8); padding: 8px 10px; border-radius: 8px;
    border: 1px solid rgba(255,255,255,0.08); transition: all 0.2s ease;
  }}
  .qr-box:hover {{
    transform: translateY(-1px); border-color: rgba(255,255,255,0.18);
  }}
  .qr-img {{ width: 56px; height: 56px; border-radius: 8px; border: 1px solid rgba(255,255,255,0.15); }}
  .qr-info {{ display: flex; flex-direction: column; gap: 2px; }}
  .qr-title {{ font-size: 10px; font-weight: 700; color: var(--text); }}
  .qr-sub {{ font-size: 9px; color: var(--muted); }}

  /* Token Row */
  .token-row {{
    display: flex; align-items: center; justify-content: space-between;
    background: rgba(251,191,36,0.06); border: 1px solid rgba(251,191,36,0.18);
    border-radius: var(--radius-sm); padding: 8px 12px;
    backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px);
    animation: slideUp 0.65s cubic-bezier(0.16,1,0.3,1) 0.1s both;
    transition: all 0.2s ease;
  }}
  .token-row:hover {{ border-color: rgba(251,191,36,0.30); }}
  .token-label {{ font-size: 9px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.8px; color: var(--amber); }}
  .token-val {{ font-size: 10px; font-family: 'SF Mono','Consolas',monospace; color: var(--amber); opacity: 0.9; }}
  .token-btn {{
    padding: 4px 10px; border-radius: 6px; border: none; cursor: pointer;
    font-size: 10px; font-weight: 600; font-family: 'Inter', sans-serif;
    background: rgba(251,191,36,0.15); color: var(--amber);
    border: 1px solid rgba(251,191,36,0.25); transition: all 0.15s ease;
  }}
  .token-btn:hover {{ background: rgba(251,191,36,0.28); transform: scale(1.05); }}
  .token-btn:active {{ transform: scale(0.95); }}

  /* Activity Feed */
  .feed-section {{ display:flex; flex-direction:column; flex:1; min-height:0; animation: slideUp 0.7s cubic-bezier(0.16,1,0.3,1) 0.15s both; }}
  .feed-header {{ display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; }}
  .feed-title {{ font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.8px; color: var(--muted); }}
  .clear-btn {{
    padding: 3px 10px; border-radius: 5px; border: 1px solid var(--panel-border);
    background: var(--panel); color: var(--muted); font-size: 9px; font-weight: 500;
    font-family: 'Inter', sans-serif; cursor: pointer; transition: all 0.15s ease;
  }}
  .clear-btn:hover {{ color: var(--text); background: var(--glass); transform: scale(1.04); }}

  .feed-scroll {{
    flex: 1; overflow-y: auto; min-height: 0;
    background: var(--panel); border: 1px solid var(--panel-border);
    border-radius: var(--radius-sm); padding: 8px;
    backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px);
    display: flex; flex-direction: column; gap: 4px;
  }}
  .feed-scroll::-webkit-scrollbar {{ width: 4px; }}
  .feed-scroll::-webkit-scrollbar-thumb {{ background: rgba(255,255,255,0.12); border-radius: 4px; }}

  .log-entry {{
    display: flex; align-items: baseline; gap: 6px; padding: 5px 8px;
    border-radius: 6px; background: rgba(255,255,255,0.03);
    border: 1px solid rgba(255,255,255,0.05); animation: fadeIn 0.2s ease both;
    font-size: 11px; line-height: 1.4; transition: all 0.15s ease;
  }}
  .log-entry:hover {{ background: rgba(255,255,255,0.06); transform: translateX(2px); }}
  .log-time {{ font-size: 9px; color: var(--muted); font-family:'Consolas',monospace; flex-shrink:0; }}
  .log-tag {{
    font-size: 8px; font-weight: 700; letter-spacing: 0.5px;
    text-transform: uppercase; flex-shrink: 0; width: 34px; text-align: center;
    padding: 1px 3px; border-radius: 3px;
  }}
  .tag-SEND  {{ color: var(--blue);   background: rgba(59,130,246,0.15);   border: 1px solid rgba(59,130,246,0.2); }}
  .tag-RECV  {{ color: var(--green);  background: rgba(52,211,153,0.12);   border: 1px solid rgba(52,211,153,0.2); }}
  .tag-LOCAL {{ color: var(--amber);  background: rgba(251,191,36,0.10);   border: 1px solid rgba(251,191,36,0.2); }}
  .tag-WARN  {{ color: var(--red);    background: rgba(248,113,113,0.10);  border: 1px solid rgba(248,113,113,0.2); }}
  .tag-ERROR {{ color: var(--red);    background: rgba(248,113,113,0.15);  border: 1px solid rgba(248,113,113,0.25); }}
  .tag-INFO  {{ color: var(--purple); background: rgba(167,139,250,0.10);  border: 1px solid rgba(167,139,250,0.2); }}
  .log-msg {{ color: var(--text); opacity: 0.9; word-break: break-word; }}

  .empty-state {{ flex:1; display:flex; align-items:center; justify-content:center; color:var(--muted); font-size:11px; }}

  @keyframes pulse {{ 0%,100% {{ opacity:1; box-shadow:0 0 8px var(--green); }} 50% {{ opacity:0.6; box-shadow:0 0 16px var(--green); }} }}
  @keyframes slideDown {{ from{{ opacity:0; transform:translateY(-10px); }} to{{ opacity:1; transform:translateY(0); }} }}
  @keyframes slideUp   {{ from{{ opacity:0; transform:translateY(8px);   }} to{{ opacity:1; transform:translateY(0); }} }}
  @keyframes fadeIn    {{ from{{ opacity:0; transform:translateX(-4px);  }} to{{ opacity:1; transform:translateX(0); }} }}
</style>
</head>
<body>
<div class="container">
  <!-- Header -->
  <div class="header">
    <div class="brand" onclick="revealSecret()">
      <div class="brand-icon" title="AirClip Liquid Glass">⚡</div>
      <span class="brand-name">AirClip</span>
    </div>
    <div class="badges">
      <div class="mdns-pill"><span>📡 mDNS Active</span></div>
      <!-- Clickable Active / Deactivate Toggle Pill -->
      <div class="status-pill active" id="status-toggle" onclick="toggleServerStatus()" title="Click to Pause / Deactivate">
        <div class="status-dot" id="status-dot"></div>
        <span id="status-text">Active</span>
      </div>
    </div>
  </div>

  <!-- Endpoint URLs -->
  <div class="urls-grid">
    <div class="url-card">
      <div class="url-label">iPhone → PC (Send)</div>
      <div class="url-row">
        <span class="url-text" id="url-send">{send_url_clean}</span>
        <button class="copy-btn" onclick="copyURL('{send_url_clean}', this)">Copy</button>
      </div>
    </div>
    <div class="url-card">
      <div class="url-label">PC → iPhone (Get)</div>
      <div class="url-row">
        <span class="url-text" id="url-get">{get_url_clean}</span>
        <button class="copy-btn" onclick="copyURL('{get_url_clean}', this)">Copy</button>
      </div>
    </div>
  </div>

  <!-- Dark Glass QR Code Section -->
  <div class="qr-section">
    <div class="qr-box">
      <img class="qr-img" src="data:image/png;base64,{send_qr_b64}" alt="Send QR">
      <div class="qr-info">
        <span class="qr-title">Scan Send Shortcut</span>
        <span class="qr-sub">1-Click iOS Import</span>
      </div>
    </div>
    <div class="qr-box">
      <img class="qr-img" src="data:image/png;base64,{get_qr_b64}" alt="Get QR">
      <div class="qr-info">
        <span class="qr-title">Scan Get Shortcut</span>
        <span class="qr-sub">1-Click iOS Import</span>
      </div>
    </div>
  </div>

  <!-- Auth Token -->
  <div class="token-row">
    <span class="token-label">🔐 Auth Token</span>
    <span class="token-val">{token_display}</span>
    <button class="token-btn" onclick="copyToken()">Copy Token</button>
  </div>

  <!-- Activity Feed -->
  <div class="feed-section">
    <div class="feed-header">
      <span class="feed-title">Live Activity</span>
      <button class="clear-btn" onclick="clearLog()">Clear</button>
    </div>
    <div class="feed-scroll" id="feed">
      <div class="empty-state" id="empty">Waiting for clipboard events…</div>
    </div>
  </div>
</div>

<script>
const FULL_TOKEN = {json.dumps(AUTH_TOKEN)};

function revealSecret() {{
  fetch('http://127.0.0.1:{PORT}/secret')
    .then(r => r.json())
    .then(data => {{
      appendLog('⚡ SECRET: ' + data.secret, 'INFO', new Date().toTimeString().slice(0,8));
    }});
}}

function toggleServerStatus() {{
  fetch('http://127.0.0.1:{PORT}/api/toggle-status', {{ method: 'POST' }})
    .then(r => r.json())
    .then(data => {{
      updateStatusUI(data.active);
    }})
    .catch(err => console.error('Toggle error:', err));
}}

function updateStatusUI(active) {{
  const pill = document.getElementById('status-toggle');
  const txt  = document.getElementById('status-text');
  if (active) {{
    pill.className = 'status-pill active';
    txt.textContent = 'Active';
  }} else {{
    pill.className = 'status-pill deactivated';
    txt.textContent = 'Deactivated';
  }}
}}

function copyURL(url, btn) {{
  navigator.clipboard.writeText(url).catch(() => {{
    const ta = document.createElement('textarea');
    ta.value = url; document.body.appendChild(ta); ta.select();
    document.execCommand('copy'); document.body.removeChild(ta);
  }});
  btn.textContent = 'Copied!'; btn.classList.add('copied');
  setTimeout(() => {{ btn.textContent = 'Copy'; btn.classList.remove('copied'); }}, 1500);
}}

function copyToken() {{
  navigator.clipboard.writeText(FULL_TOKEN).catch(() => {{
    const ta = document.createElement('textarea');
    ta.value = FULL_TOKEN; document.body.appendChild(ta); ta.select();
    document.execCommand('copy'); document.body.removeChild(ta);
  }});
  appendLog('Auth token copied to clipboard', 'INFO', new Date().toTimeString().slice(0,8));
}}

function clearLog() {{
  const feed = document.getElementById('feed');
  feed.innerHTML = '<div class="empty-state" id="empty">Waiting for clipboard events…</div>';
}}

function appendLog(msg, tag, ts) {{
  const feed = document.getElementById('feed');
  const empty = document.getElementById('empty');
  if (empty) empty.remove();
  const entry = document.createElement('div');
  entry.className = 'log-entry';
  entry.innerHTML =
    '<span class="log-time">' + ts + '</span>' +
    '<span class="log-tag tag-' + tag + '">' + tag + '</span>' +
    '<span class="log-msg">' + escapeHtml(msg) + '</span>';
  feed.appendChild(entry);
  feed.scrollTop = feed.scrollHeight;
  if (feed.children.length > 200) feed.removeChild(feed.firstChild);
}}

function escapeHtml(s) {{
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}}

setInterval(() => {{
  fetch('http://127.0.0.1:{PORT}/api/logs')
    .then(r => r.json())
    .then(data => {{
      if (data) {{
        if (data.active !== undefined) updateStatusUI(data.active);
        if (data.logs) {{
          data.logs.forEach(l => appendLog(l[1], l[2], l[0]));
        }}
      }}
    }})
    .catch(() => {{}});
}}, 400);

window.addEventListener('load', () => {{
  appendLog('AirClip Engine v1.0.0 active on port 5000', 'INFO', new Date().toTimeString().slice(0,8));
  appendLog('mDNS Auto-Discovery (_clipsync._tcp.local) active', 'INFO', new Date().toTimeString().slice(0,8));
  appendLog('LAN-only security · Rate limiter · 10MB payload cap', 'INFO', new Date().toTimeString().slice(0,8));
}});
</script>
</body>
</html>"""

# ─── Main Entry Point ─────────────────────────────────────────────────────────
def main():
    global ui_window

    # 1. Start Flask HTTP Server thread
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # 2. Start PC Clipboard background monitor thread
    monitor_thread = threading.Thread(target=monitor_pc_clipboard, daemon=True)
    monitor_thread.start()

    # 3. Start mDNS zeroconf responder thread
    mdns_thread = threading.Thread(target=register_mdns, daemon=True)
    mdns_thread.start()

    # 4. Initialize & launch pywebview Liquid Glass Dashboard Window
    html = build_html()
    window = webview.create_window(
        "AirClip ⚡",
        html=html,
        width=640,
        height=620,
        min_size=(560, 520),
        background_color="#08080c",
        frameless=False,
    )
    ui_window = window
    webview.start(debug=False)

if __name__ == "__main__":
    main()
