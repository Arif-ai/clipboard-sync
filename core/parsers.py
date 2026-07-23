import os
import re
import plistlib
from html.parser import HTMLParser

class HTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.result = []
    def handle_data(self, data):
        self.result.append(data)
    def get_text(self):
        return "".join(self.result).strip()

def clean_html(html_str):
    try:
        parser = HTMLTextExtractor()
        parser.feed(html_str)
        text = parser.get_text()
        if text:
            return text
    except Exception:
        pass
    clean = re.sub(r'<[^<]+?>', '', html_str).strip()
    return clean if clean else html_str

def extract_rtf_text(rtf_str):
    try:
        text = re.sub(r"\\'([0-9a-fA-F]{2})", lambda m: bytes.fromhex(m.group(1)).decode('latin1', errors='ignore'), rtf_str)
        text = re.sub(r'\{\\(?:fonttbl|colortbl|stylesheet|info|expandedcolortbl)[^{}]*\}', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\\[a-zA-Z0-9\-]+ ?', '', text)
        text = re.sub(r'[{}]', '', text)
        clean_lines = [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith(';')]
        result = "\n".join(clean_lines).strip()
        result = re.sub(r'^[;\s]+', '', result)
        return result
    except Exception as e:
        print(f"[RTF PARSE ERROR] {e}", flush=True)
        return rtf_str

def extract_text_from_incoming(incoming_bytes):
    if not incoming_bytes:
        return ""
    
    # 1. Handle Apple Binary Plist (bplist00)
    idx = incoming_bytes.find(b'bplist00')
    if idx != -1:
        plist_bytes = incoming_bytes[idx:]
        try:
            plist = plistlib.loads(plist_bytes)
            if isinstance(plist, dict):
                if 'WebMainResource' in plist:
                    res_data = plist['WebMainResource'].get('WebResourceData', b'')
                    if res_data:
                        raw_str = res_data.decode('utf-8', errors='ignore')
                        return clean_html(raw_str)
                
                for key in ['public.utf8-plain-text', 'NSStringPboardType', 'NSString', 'WebResourceData']:
                    if key in plist:
                        val = plist[key]
                        if isinstance(val, bytes):
                            val = val.decode('utf-8', errors='ignore')
                        if isinstance(val, str) and val.strip():
                            if val.strip().startswith('{\\rtf1'):
                                return extract_rtf_text(val.strip())
                            return val.strip()
                
                def find_strings_in_dict(d):
                    found = []
                    if isinstance(d, dict):
                        for k, v in d.items():
                            found.extend(find_strings_in_dict(v))
                    elif isinstance(d, list):
                        for item in d:
                            found.extend(find_strings_in_dict(item))
                    elif isinstance(d, bytes):
                        s = d.decode('utf-8', errors='ignore').strip()
                        if s and not s.startswith('http') and len(s) > 2:
                            if s.startswith('{\\rtf1'):
                                s = extract_rtf_text(s)
                            else:
                                s = clean_html(s)
                            found.append(s)
                    elif isinstance(d, str):
                        s = d.strip()
                        if s and not s.startswith('http') and len(s) > 2:
                            if s.startswith('{\\rtf1'):
                                s = extract_rtf_text(s)
                            else:
                                s = clean_html(s)
                            found.append(s)
                    return found
                
                strings = find_strings_in_dict(plist)
                meta_keys = {'WebMainResource', 'WebResourceMIMEType', 'WebResourceURL', 'WebResourceFrameName', 'WebResourceData', 'WebResourceTextEncodingName', 'text/html', 'UTF-8'}
                clean_strings = [s for s in strings if s not in meta_keys]
                if clean_strings:
                    return "\n".join(clean_strings)

            elif isinstance(plist, str):
                s = plist.strip()
                if s.startswith('{\\rtf1'):
                    return extract_rtf_text(s)
                return s
        except Exception as e:
            print(f"[PLIST PARSE ERROR] {e}", flush=True)

        return ""

    # 2. Standard Text / RTF / HTML
    raw_text = incoming_bytes.decode('utf-8', errors='ignore').strip()
    if raw_text.startswith('{\\rtf1'):
        return extract_rtf_text(raw_text)
    if raw_text.startswith('<html') or raw_text.startswith('<!DOCTYPE'):
        return clean_html(raw_text)

    return raw_text
