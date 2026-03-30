import json
import os
import sys
import time
import subprocess
import requests
from playwright.sync_api import sync_playwright

CHROME_RUTAS = [
    r'C:\Program Files\Google\Chrome\Application\chrome.exe',
    r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
    os.path.expandvars(r'%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe'),
] if sys.platform == 'win32' else [
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
] if sys.platform == 'darwin' else [
    '/usr/bin/google-chrome', '/usr/bin/chromium-browser', '/usr/bin/chromium',
]
CHROME_EXE = next((p for p in CHROME_RUTAS if os.path.exists(p)), None)
CHROME_USER_DATA = r'C:\Temp\chrome-debug' if sys.platform == 'win32' else '/tmp/chrome-debug'
CHROME_PORT = 9222

def iniciar_chrome():
    subprocess.run(['taskkill', '/F', '/IM', 'chrome.exe'],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1)
    subprocess.Popen([CHROME_EXE, f'--remote-debugging-port={CHROME_PORT}',
                      f'--user-data-dir={CHROME_USER_DATA}',
                      '--no-first-run', '--no-default-browser-check'])
    for _ in range(20):
        try:
            if requests.get(f'http://127.0.0.1:{CHROME_PORT}/json/version', timeout=2).ok:
                print('Chrome listo.\n')
                return
        except Exception:
            pass
        time.sleep(1)
    print('ERROR: Chrome no respondió.')
    sys.exit(1)

def cerrar_chrome():
    subprocess.run(['taskkill', '/F', '/IM', 'chrome.exe'],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

URLS = [
    ('https://s.click.aliexpress.com/e/_EJf3Zdg',  'A INVESTIGAR'),
]

with open('aliexpress_cookies.json', 'r', encoding='utf-8') as f:
    cookies_raw = json.load(f)

cookies_pw = [{
    'name': c['name'],
    'value': c['value'],
    'domain': c.get('domain', '.aliexpress.com'),
    'path': c.get('path', '/'),
} for c in cookies_raw]


iniciar_chrome()
with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp(f'http://127.0.0.1:{CHROME_PORT}')
    context = browser.contexts[0]
    context.add_cookies(cookies_pw)
    page = context.new_page()

    for url, etiqueta in URLS:
        page.goto(url, wait_until='domcontentloaded', timeout=20000)
        page.wait_for_timeout(3000)

        titulo = page.title()
        texto = page.inner_text('body')
        url_final = page.url

        print(f'\n=== {etiqueta} ===')
        print(f'  URL final : {url_final}')
        print(f'  Título    : {repr(titulo)}')
        print(f'  Texto body (primeros 400 chars):')
        print(f'  {texto[:400]}')

        # Pausa si parece captcha para poder inspeccionarlo
        tiene_recaptcha = (page.locator('iframe[src*="recaptcha"]').count() > 0
                           or page.locator('iframe[src*="captcha"]').count() > 0)
        texto_lower = texto.lower()
        sospechoso = ('punish' in url_final or 'captcha' in url_final.lower()
                      or 'baxia' in url_final or 'sec.aliexpress' in url_final
                      or 'we need to check if you are a robot' in texto_lower
                      or tiene_recaptcha)
        print(f'  reCAPTCHA iframe: {tiene_recaptcha}')
        if sospechoso:
            print('\n  *** POSIBLE CAPTCHA — revisa Chrome y pulsa ENTER para continuar ***')
            input()

    page.close()
cerrar_chrome()
