"""Portable desktop entry point; shared by .command and server run.sh."""
from pathlib import Path
import os
import sys
import threading
import time
import urllib.request
import json
import webbrowser

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)

def main():
    preview = '--preview' in sys.argv or os.environ.get('MP_PREVIEW') == '1'
    if preview:
        os.environ['MP_PREVIEW'] = '1'
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env', override=False)
    host = '127.0.0.1' if preview else os.environ.get('HOST', '127.0.0.1')
    port = 8765 if preview else int(os.environ.get('PORT', '8000'))
    url = f'http://127.0.0.1:{port}'
    browser = '--browser' in sys.argv
    try:
        with urllib.request.urlopen(url + '/api/creatorpilot/health', timeout=1) as response:
            existing = json.load(response)
        if existing.get('application') == 'MP CreatorStudio' and existing.get('preview', False) == preview and (not preview or existing.get('preview_policy') == 'read-and-draft'):
            if browser: webbrowser.open(url)
            print('MP CreatorStudio läuft bereits: ' + url)
            return
    except Exception:
        pass
    if browser:
        def open_when_ready():
            for _ in range(90):
                try:
                    with urllib.request.urlopen(url + '/api/creatorpilot/health', timeout=1) as r:
                        status = json.load(r)
                        if status.get('application') == 'MP CreatorStudio' and status.get('preview', False) == preview and (not preview or status.get('preview_policy') == 'read-and-draft'):
                            webbrowser.open(url)
                            return
                except Exception:
                    time.sleep(1)
        threading.Thread(target=open_when_ready, daemon=True).start()
    if preview:
        print('GESCHÜTZTER MITLESEMODUS · Live lesen & KI-Vorschläge, kein Nachrichtenversand.')
    print('MP CreatorStudio: ' + url + '\nZum Beenden Strg+C drücken.')
    import uvicorn
    uvicorn.run('app.main:app', host=host, port=port)

if __name__ == '__main__':
    main()
