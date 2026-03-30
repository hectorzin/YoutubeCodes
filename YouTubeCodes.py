import os
import re
import sys
import json
import pickle
import time
import subprocess
import requests
from datetime import datetime
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from playwright.sync_api import sync_playwright

SCOPES = ['https://www.googleapis.com/auth/youtube.force-ssl']
TOKEN_FILE = 'token.pickle'
CREDENTIALS_FILE = 'client_secret.json'
CUPONES_FILE = 'cupones.txt'
REPORTE_LINKS_FILE = 'links_rotos.txt'

MESES_ES = {
    1: 'ENERO', 2: 'FEBRERO', 3: 'MARZO', 4: 'ABRIL',
    5: 'MAYO', 6: 'JUNIO', 7: 'JULIO', 8: 'AGOSTO',
    9: 'SEPTIEMBRE', 10: 'OCTUBRE', 11: 'NOVIEMBRE', 12: 'DICIEMBRE'
}

PATRON_FECHA = r'[A-ZÁÉÍÓÚ]+\s+\d{4}'
PATRON_URL = r'https?://[^\s\)\]>\"\']*aliexpress\.com[^\s\)\]>\"\']*'

if sys.platform == 'win32':
    CHROME_RUTAS = [
        r'C:\Program Files\Google\Chrome\Application\chrome.exe',
        r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
        os.path.expandvars(r'%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe'),
    ]
    CHROME_USER_DATA = r'C:\Temp\chrome-debug'
elif sys.platform == 'darwin':
    CHROME_RUTAS = [
        '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    ]
    CHROME_USER_DATA = '/tmp/chrome-debug'
else:  # Linux
    CHROME_RUTAS = [
        '/usr/bin/google-chrome',
        '/usr/bin/google-chrome-stable',
        '/usr/bin/chromium-browser',
        '/usr/bin/chromium',
    ]
    CHROME_USER_DATA = '/tmp/chrome-debug'

CHROME_EXE = next((p for p in CHROME_RUTAS if os.path.exists(p)), None)
CHROME_DEBUG_PORT = 9222


def añadir_fecha_si_falta(nuevo_bloque):
    lineas = nuevo_bloque.splitlines()
    if not re.search(PATRON_FECHA, lineas[0]):
        ahora = datetime.now()
        fecha = f'{MESES_ES[ahora.month]} {ahora.year}'
        linea = lineas[0]
        if linea.endswith('*'):
            lineas[0] = f'{linea[:-1]} ({fecha})*'
        else:
            lineas[0] = f'{linea} ({fecha})'
        return '\n'.join(lineas)
    return nuevo_bloque


def construir_patron(nuevo_bloque_original):
    lineas = nuevo_bloque_original.splitlines()
    primera_orig = lineas[0]
    ultima = re.escape(lineas[-1])

    m = re.search(PATRON_FECHA, primera_orig)
    if m:
        antes = re.escape(primera_orig[:m.start()])
        despues = re.escape(primera_orig[m.end():])
        primera_flexible = antes + r'.+?' + despues
    else:
        primera_escaped = re.escape(primera_orig)
        if primera_orig.endswith('*'):
            primera_flexible = primera_escaped[:-2] + r'(?:\s*\(.+?\))?\*'
        else:
            primera_flexible = primera_escaped + r'(?:\s*\(.+?\))?'

    return primera_flexible + r'.*?' + ultima


def autenticar():
    creds = None

    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, 'rb') as f:
            creds = pickle.load(f)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, 'wb') as f:
            pickle.dump(creds, f)

    return build('youtube', 'v3', credentials=creds)


def obtener_todos_los_videos(youtube):
    print('Obteniendo lista de videos del canal...')

    canal = youtube.channels().list(part='contentDetails', mine=True).execute()
    playlist_uploads = canal['items'][0]['contentDetails']['relatedPlaylists']['uploads']

    video_ids = []
    next_page_token = None

    while True:
        respuesta = youtube.playlistItems().list(
            part='contentDetails',
            playlistId=playlist_uploads,
            maxResults=50,
            pageToken=next_page_token
        ).execute()

        for item in respuesta['items']:
            video_ids.append(item['contentDetails']['videoId'])

        next_page_token = respuesta.get('nextPageToken')
        if not next_page_token:
            break

    print(f'  Total de videos en el canal: {len(video_ids)}')

    videos = []
    for i in range(0, len(video_ids), 50):
        lote = video_ids[i:i + 50]
        respuesta = youtube.videos().list(
            part='snippet',
            id=','.join(lote)
        ).execute()
        videos.extend(respuesta['items'])

    return videos


def extraer_links_aliexpress(descripcion):
    return list(set(re.findall(PATRON_URL, descripcion)))


def linea_con_link(descripcion, url):
    for linea in descripcion.splitlines():
        if url in linea:
            return linea.strip()
    return url


def cargar_cookies_aliexpress():
    if not os.path.exists('aliexpress_cookies.json'):
        return []
    with open('aliexpress_cookies.json', 'r', encoding='utf-8') as f:
        raw = json.load(f)
    return [{'name': c['name'], 'value': c['value'],
             'domain': c.get('domain', '.aliexpress.com'),
             'path': c.get('path', '/')} for c in raw]


def iniciar_chrome():
    """Mata Chrome si está abierto, lo lanza con puerto de depuración y espera a que esté listo."""
    if not CHROME_EXE:
        print('ERROR: No se encontró Chrome en las rutas habituales.')
        print('Rutas buscadas:')
        for p in CHROME_RUTAS:
            print(f'  {p}')
        return False

    print('Cerrando Chrome si está abierto...')
    if sys.platform == 'win32':
        subprocess.run(['taskkill', '/F', '/IM', 'chrome.exe'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        subprocess.run(['pkill', '-f', 'Google Chrome'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1)

    print(f'Arrancando Chrome con puerto de depuración {CHROME_DEBUG_PORT}...')
    subprocess.Popen([
        CHROME_EXE,
        f'--remote-debugging-port={CHROME_DEBUG_PORT}',
        f'--user-data-dir={CHROME_USER_DATA}',
        '--no-first-run',
        '--no-default-browser-check',
    ])

    # Esperar a que Chrome esté listo aceptando conexiones CDP
    for _ in range(20):
        try:
            r = requests.get(f'http://127.0.0.1:{CHROME_DEBUG_PORT}/json/version', timeout=2)
            if r.ok:
                print('  Chrome listo.\n')
                return True
        except Exception:
            pass
        time.sleep(1)

    print('  ERROR: Chrome no respondió en el puerto de depuración.')
    return False


def cerrar_chrome():
    """Cierra Chrome."""
    if sys.platform == 'win32':
        subprocess.run(['taskkill', '/F', '/IM', 'chrome.exe'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        subprocess.run(['pkill', '-f', 'Google Chrome'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def es_captcha(page):
    url = page.url
    if 'punish' in url or 'captcha' in url or 'baxia' in url or 'sec.aliexpress' in url:
        return True
    try:
        if page.locator('iframe[src*="recaptcha"]').count() > 0:
            return True
        if page.locator('iframe[src*="captcha"]').count() > 0:
            return True
        texto = page.inner_text('body').lower()
        return 'we need to check if you are a robot' in texto
    except Exception:
        return False


def esperar_si_captcha(page):
    if es_captcha(page):
        print('\n⚠  CAPTCHA detectado. Resuélvelo en Chrome y pulsa ENTER para continuar...')
        input()
        page.wait_for_timeout(3000)


def comprobar_link_chrome(page, url):
    """Devuelve 'ok', 'roto' o 'geo'."""
    try:
        page.goto(url, wait_until='domcontentloaded', timeout=20000)
        page.wait_for_timeout(3000)
        esperar_si_captcha(page)
        titulo = page.title()
        if titulo:
            return 'ok'
        texto = page.inner_text('body')
        if 'no está disponible en tu país' in texto or 'not available in your country' in texto:
            return 'geo'
        return 'roto'
    except Exception:
        return 'roto'


def chequear_links_videos(videos):
    print('\n=== Comprobando links de AliExpress ===')

    # 1. Recopilar links por video
    video_links = []
    total_videos = len(videos)
    for i, video in enumerate(videos, 1):
        descripcion = video['snippet']['description']
        links = extraer_links_aliexpress(descripcion)
        if not links:
            continue
        titulo = video['snippet']['title']
        print(f'\nVideo {i}/{total_videos}: {titulo}')
        pares = []
        for url in links:
            linea = linea_con_link(descripcion, url)
            print(f'  → {linea[:100]}')
            pares.append((url, linea))
        video_links.append((video, pares))

    if not video_links:
        print('No se encontraron links de AliExpress.')
        return [], []

    # 2. Verificar con Chrome CDP
    cookies = cargar_cookies_aliexpress()
    if not cookies:
        print('\nAVISO: aliexpress_cookies.json no encontrado. Saltando verificación de links.')
        return [], []

    if not iniciar_chrome():
        print('Saltando verificación de links.')
        return [], []

    playwright = sync_playwright().start()
    try:
        browser = playwright.chromium.connect_over_cdp(f'http://127.0.0.1:{CHROME_DEBUG_PORT}')
    except Exception as e:
        print(f'\nAVISO: No se pudo conectar a Chrome: {e}')
        playwright.stop()
        cerrar_chrome()
        return [], []

    context = browser.contexts[0]
    context.add_cookies(cookies)
    page = context.new_page()

    links_rotos = []
    links_geo = []
    cache = {}  # url -> 'ok' / 'roto' / 'geo'
    urls_unicas = len({url for _, pares in video_links for url, _ in pares})
    comprobadas = 0

    for video, pares in video_links:
        titulo = video['snippet']['title']
        vid_id = video['id']
        for url, linea in pares:
            if url not in cache:
                comprobadas += 1
                print(f'  [{comprobadas}/{urls_unicas}] Comprobando: {url[:70]}')
                cache[url] = comprobar_link_chrome(page, url)
            entrada = {'video': titulo, 'video_id': vid_id, 'url': url, 'linea': linea}
            if cache[url] == 'roto':
                links_rotos.append(entrada)
            elif cache[url] == 'geo':
                links_geo.append(entrada)

    page.close()
    playwright.stop()
    cerrar_chrome()
    return links_rotos, links_geo


def _imprimir_grupo(items, simbolo):
    video_actual = None
    for item in items:
        if item['video'] != video_actual:
            video_actual = item['video']
            print(f'\n  Video: {item["video"]}')
            print(f'    https://www.youtube.com/watch?v={item["video_id"]}')
        print(f'  {simbolo} {item["linea"][:100]}')
        print(f'       URL: {item["url"]}')


def _escribir_grupo(f, items, simbolo):
    video_actual = None
    for item in items:
        if item['video'] != video_actual:
            video_actual = item['video']
            f.write(f'\n  Video: {item["video"]}\n')
            f.write(f'    https://www.youtube.com/watch?v={item["video_id"]}\n')
        f.write(f'  {simbolo} {item["linea"]}\n')
        f.write(f'       URL: {item["url"]}\n')


def guardar_reporte_links(links_rotos, links_geo):
    print('\n' + '=' * 60)
    if not links_rotos and not links_geo:
        print('✓ No se encontraron links con problemas.')
        return

    if links_rotos:
        print(f'\n✗ ELIMINADOS / NO PROMOCIONABLES ({len(links_rotos)}):')
        print('-' * 60)
        _imprimir_grupo(links_rotos, '✗')

    if links_geo:
        print(f'\n⚠  NO DISPONIBLES EN TU REGIÓN ({len(links_geo)}):')
        print('-' * 60)
        _imprimir_grupo(links_geo, '⚠')

    with open(REPORTE_LINKS_FILE, 'w', encoding='utf-8') as f:
        f.write(f'Reporte de links — {datetime.now().strftime("%d/%m/%Y %H:%M")}\n')
        f.write('=' * 60 + '\n')
        if links_rotos:
            f.write(f'\nELIMINADOS / NO PROMOCIONABLES ({len(links_rotos)}):\n')
            f.write('-' * 60 + '\n')
            _escribir_grupo(f, links_rotos, '✗')
        if links_geo:
            f.write(f'\nNO DISPONIBLES EN TU REGIÓN ({len(links_geo)}):\n')
            f.write('-' * 60 + '\n')
            _escribir_grupo(f, links_geo, '⚠')

    print(f'\nReporte guardado en "{REPORTE_LINKS_FILE}"')


def buscar_videos_con_cupones(videos, patron):
    encontrados = []
    for video in videos:
        descripcion = video['snippet']['description']
        if re.search(patron, descripcion, re.DOTALL):
            encontrados.append(video)
    return encontrados


def actualizar_video(youtube, video, nuevo_bloque, patron):
    snippet = video['snippet']
    descripcion_original = snippet['description']

    nueva_descripcion = re.sub(
        patron,
        lambda m: nuevo_bloque,
        descripcion_original,
        flags=re.DOTALL
    )

    if nueva_descripcion == descripcion_original:
        return 'sin_cambios'

    if len(nueva_descripcion) > 5000:
        return 'demasiado_larga'

    youtube.videos().update(
        part='snippet',
        body={
            'id': video['id'],
            'snippet': snippet | {'description': nueva_descripcion}
        }
    ).execute()

    return 'ok'


def main():
    if not os.path.exists(CREDENTIALS_FILE):
        print(f'ERROR: No se encontró el archivo "{CREDENTIALS_FILE}"')
        print('Renombra tu archivo de credenciales a "client_secret.json" y ponlo en esta carpeta.')
        return

    if not os.path.exists(CUPONES_FILE):
        print(f'ERROR: No se encontró el archivo "{CUPONES_FILE}"')
        print(f'Crea el archivo "{CUPONES_FILE}" y pega dentro el nuevo bloque de cupones.')
        return

    with open(CUPONES_FILE, 'r', encoding='utf-8') as f:
        nuevo_bloque = f.read().strip()

    if not nuevo_bloque:
        print(f'ERROR: El archivo "{CUPONES_FILE}" está vacío.')
        return

    patron = construir_patron(nuevo_bloque)
    nuevo_bloque = añadir_fecha_si_falta(nuevo_bloque)

    print('=== Actualizador de Cupones de AliExpress ===\n')
    print('Nuevo bloque de cupones a usar:')
    print('-' * 50)
    print(nuevo_bloque)
    print('-' * 50)
    print()

    print('Autenticando con YouTube...')
    youtube = autenticar()
    print('  Autenticación correcta.\n')

    videos = obtener_todos_los_videos(youtube)

    # --- Actualizar cupones ---
    videos_con_cupones = buscar_videos_con_cupones(videos, patron)

    if not videos_con_cupones:
        print('\nNo se encontró ningún video con el bloque de cupones.')
    else:
        print(f'\nVideos con cupones encontrados: {len(videos_con_cupones)}')
        for v in videos_con_cupones:
            print(f'  - {v["snippet"]["title"]}')

        print(f'\n¿Actualizar los {len(videos_con_cupones)} videos? (s/n): ', end='')
        if input().strip().lower() == 's':
            ROJO = '\033[91m'
            RST  = '\033[0m'
            print()
            actualizados = sin_cambios = omitidos = 0
            omitidos_lista = []
            for i, video in enumerate(videos_con_cupones, 1):
                titulo = video['snippet']['title']
                print(f'[{i}/{len(videos_con_cupones)}] {titulo}')
                resultado = actualizar_video(youtube, video, nuevo_bloque, patron)
                if resultado == 'ok':
                    actualizados += 1
                    print('  OK')
                elif resultado == 'sin_cambios':
                    sin_cambios += 1
                    print('  [SIN CAMBIOS]')
                elif resultado == 'demasiado_larga':
                    omitidos += 1
                    omitidos_lista.append(titulo)
                    nueva_len = len(re.sub(construir_patron(nuevo_bloque), lambda _: nuevo_bloque,
                                          video['snippet']['description'], flags=re.DOTALL))
                    print(f'{ROJO}  [OMITIDO] Descripción demasiado larga ({nueva_len} chars, máx 5000){RST}')

            sin_match = len(videos) - len(videos_con_cupones)
            print(f'\nTotal videos: {len(videos)}  |  Sin cupones: {sin_match}  |  Con cupones: {len(videos_con_cupones)}')
            print(f'✓ Actualizados: {actualizados}  |  Sin cambios: {sin_cambios}', end='')
            if omitidos:
                print(f'  |  {ROJO}Omitidos por longitud: {omitidos}{RST}')
                for t in omitidos_lista:
                    print(f'{ROJO}    - {t}{RST}')
            else:
                print()
        else:
            print('Cancelado.')

    # --- Comprobar links ---
    links_unicos = {
        url
        for v in videos
        for url in extraer_links_aliexpress(v['snippet']['description'])
    }
    print(f'\nSe han encontrado {len(links_unicos)} links únicos de AliExpress.')
    print('¿Quieres comprobar si están activos?')
    print('AVISO: Esto cerrará tu sesión de Chrome y lo abrirá en modo depuración.')
    print('(s/n): ', end='')
    if input().strip().lower() == 's':
        links_rotos, links_geo = chequear_links_videos(videos)
        guardar_reporte_links(links_rotos, links_geo)


if __name__ == '__main__':
    main()
