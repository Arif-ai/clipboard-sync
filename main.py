import io
import os
import sys
import time
import subprocess
import threading
import pyperclip
from PIL import Image, ImageGrab
from flask import Flask, request, jsonify, send_file, Response

from core.parsers import extract_text_from_incoming, extract_rtf_text, clean_html

TEMP_DIR = os.environ.get("TEMP", "C:\\Temp")

app = Flask(__name__)

start_now = time.time()
pc_state = {
    "type": "text",
    "text": "Server Ready",
    "image_bytes": None,
    "timestamp": start_now
}

iphone_last_sync_timestamp = start_now
last_synced_text = None
last_synced_image_bytes = None

# --- DEDICATED ENDPOINTS ---
@app.route('/get', methods=['GET', 'POST'])
@app.route('/get-clip', methods=['GET', 'POST'])
def get_pc_clipboard():
    """Fetch current PC clipboard to iPhone."""
    try:
        if pc_state["type"] == "text" and pc_state.get("text"):
            return Response(pc_state["text"], status=200, content_type='text/plain; charset=utf-8')
        elif pc_state["type"] == "image" and pc_state.get("image_bytes"):
            return send_file(
                io.BytesIO(pc_state["image_bytes"]),
                mimetype='image/png',
                as_attachment=False
            )
    except Exception as e:
        print(f"[GET ERROR] {e}", flush=True)
    return Response(status=204)

@app.route('/send', methods=['POST'])
@app.route('/send-clip', methods=['POST'])
def send_iphone_clipboard():
    """Send iPhone clipboard to PC."""
    try:
        incoming_data = request.data or b''
        content_type = request.headers.get('Content-Type', '').lower()
        
        if incoming_data and ('image' in content_type or 'octet-stream' in content_type):
            img = Image.open(io.BytesIO(incoming_data))
            temp_path = os.path.join(TEMP_DIR, "iphone_clip.png")
            img.save(temp_path, "PNG")
            
            powershell_cmd = f"Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.Clipboard]::SetImage([System.Drawing.Image]::FromFile('{temp_path}'))"
            subprocess.run(["powershell", "-Command", powershell_cmd], capture_output=True)
            
            pc_state["type"] = "image"
            pc_state["image_bytes"] = incoming_data
            pc_state["timestamp"] = time.time()
            print("\n[SEND ENDPOINT] iPhone -> PC: Image updated on PC!\n", flush=True)
            return jsonify({"status": "success", "type": "image"}), 200

        elif incoming_data:
            text_str = extract_text_from_incoming(incoming_data)
            if text_str and '{"status"' not in text_str and not text_str.startswith('bplist00'):
                pyperclip.copy(text_str)
                pc_state["type"] = "text"
                pc_state["text"] = text_str
                pc_state["timestamp"] = time.time()
                print(f"\n[SEND ENDPOINT] iPhone -> PC: Text updated on PC: '{text_str[:40]}'\n", flush=True)
                return jsonify({"status": "success", "type": "text"}), 200

    except Exception as e:
        print(f"[SEND ERROR] {e}", flush=True)

    return Response(status=204)

# --- UNIFIED SYNC ENDPOINT (MERGED SHORTCUT COMPATIBILITY) ---
@app.route('/', methods=['GET', 'POST'])
@app.route('/sync', methods=['GET', 'POST'])
@app.route('/sync/', methods=['GET', 'POST'])
@app.route('/clip', methods=['GET', 'POST'])
@app.route('/clip/', methods=['GET', 'POST'])
@app.route('/clipboard', methods=['GET', 'POST'])
@app.route('/clipboard/', methods=['GET', 'POST'])
def sync_clipboard():
    try:
        global iphone_last_sync_timestamp, last_synced_text, last_synced_image_bytes
        
        # 1. PC HAS NEWER CONTENT -> SEND PC TO IPHONE
        if pc_state["timestamp"] > iphone_last_sync_timestamp:
            if pc_state["type"] == "text" and pc_state.get("text"):
                iphone_last_sync_timestamp = pc_state["timestamp"]
                last_synced_text = pc_state["text"]
                last_synced_image_bytes = None
                print(f"\n[UNIFIED SYNC] PC -> iPhone: Sending PC text to iPhone: '{pc_state['text'][:40]}'\n", flush=True)
                return Response(pc_state["text"], status=200, content_type='text/plain; charset=utf-8')
            elif pc_state["type"] == "image" and pc_state.get("image_bytes"):
                iphone_last_sync_timestamp = pc_state["timestamp"]
                last_synced_image_bytes = pc_state["image_bytes"]
                last_synced_text = None
                print("\n[UNIFIED SYNC] PC -> iPhone: Sending PC image to iPhone!\n", flush=True)
                return send_file(
                    io.BytesIO(pc_state["image_bytes"]),
                    mimetype='image/png',
                    as_attachment=False
                )

        # 2. PROCESS INCOMING IPHONE CONTENT
        incoming_data = request.data or b''
        content_type = request.headers.get('Content-Type', '').lower()
        
        if incoming_data and ('image' in content_type or 'octet-stream' in content_type):
            if incoming_data != last_synced_image_bytes:
                try:
                    img = Image.open(io.BytesIO(incoming_data))
                    temp_path = os.path.join(TEMP_DIR, "iphone_clip.png")
                    img.save(temp_path, "PNG")
                    
                    powershell_cmd = f"Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.Clipboard]::SetImage([System.Drawing.Image]::FromFile('{temp_path}'))"
                    subprocess.run(["powershell", "-Command", powershell_cmd], capture_output=True)
                    
                    pc_state["type"] = "image"
                    pc_state["image_bytes"] = incoming_data
                    pc_state["timestamp"] = time.time()
                    
                    last_synced_image_bytes = incoming_data
                    last_synced_text = None
                    iphone_last_sync_timestamp = pc_state["timestamp"]
                    print("\n[UNIFIED SYNC] iPhone -> PC: Image updated on PC!\n", flush=True)
                except Exception as e:
                    print(f"[IMAGE PROCESS ERROR] {e}", flush=True)

        elif incoming_data:
            try:
                text_str = extract_text_from_incoming(incoming_data)
                if text_str and text_str != last_synced_text and '{"status"' not in text_str and not text_str.startswith('bplist00'):
                    pyperclip.copy(text_str)
                    pc_state["type"] = "text"
                    pc_state["text"] = text_str
                    pc_state["timestamp"] = time.time()
                    
                    last_synced_text = text_str
                    last_synced_image_bytes = None
                    iphone_last_sync_timestamp = pc_state["timestamp"]
                    print(f"\n[UNIFIED SYNC] iPhone -> PC: Text updated on PC: '{text_str[:40]}'\n", flush=True)
            except Exception as e:
                print(f"[TEXT PROCESS ERROR] {e}", flush=True)

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"[SYNC ERROR] {e}", flush=True)

    return Response(status=204)

# --- BACKGROUND MONITOR FOR WINDOWS CLIPBOARD ---
def monitor_pc_clipboard():
    print("PC Clipboard Monitor active...", flush=True)
    last_text = None
    last_img_bytes = None

    while True:
        try:
            img = ImageGrab.grabclipboard()
            if isinstance(img, Image.Image):
                img_byte_arr = io.BytesIO()
                img.save(img_byte_arr, format='PNG')
                img_bytes = img_byte_arr.getvalue()
                
                if img_bytes != last_img_bytes:
                    last_img_bytes = img_bytes
                    pc_state["type"] = "image"
                    pc_state["image_bytes"] = img_bytes
                    pc_state["timestamp"] = time.time()
                    print("[MONITOR] PC Screenshot/Image captured!", flush=True)
                    time.sleep(1.0)
                    continue

            current_text = pyperclip.paste()
            if current_text and current_text != last_text:
                if '{"status"' in current_text or 'bplist00' in current_text or 'WebMainResource' in current_text:
                    pyperclip.copy("")
                    last_text = ""
                    continue
                if '{\\rtf1' in current_text:
                    current_text = extract_rtf_text(current_text)
                    pyperclip.copy(current_text)
                last_text = current_text
                current_text_str = str(current_text)
                pc_state["type"] = "text"
                pc_state["text"] = current_text_str
                pc_state["timestamp"] = time.time()
                print(f"[MONITOR] PC Text captured: {repr(current_text_str[:30])}", flush=True)

        except Exception as e:
            pass
            
        time.sleep(0.5)

if __name__ == '__main__':
    monitor_thread = threading.Thread(target=monitor_pc_clipboard, daemon=True)
    monitor_thread.start()
    app.run(host='0.0.0.0', port=5000)
