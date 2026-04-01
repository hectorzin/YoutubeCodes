import os
import re
import sys
import json
import gzip
import pickle
import time
import subprocess
import requests
from datetime import datetime
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from playwright.sync_api import sync_playwright
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, MofNCompleteColumn
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text
import questionary
from rich.rule import Rule
from rich import box

console = Console()

SCOPES = ['https://www.googleapis.com/auth/youtube.force-ssl']
TOKEN_FILE = 'token.pickle'
CREDENTIALS_FILE = 'client_secret.json'
CUPONES_FILE = 'cupones.txt'
REPORTE_LINKS_FILE = 'links_rotos.txt'
LINKS_ESTADO_FILE       = 'links_estado.json'
COMENTARIOS_ESTADO_FILE = 'comentarios_estado.json'
EXCLUSIONES_FILE         = 'exclusiones.txt'
DOMINIOS_IGNORADOS_FILE  = 'dominios_ignorados.txt'
CACHE_VIDEOS_FILE = 'cache_videos.json.gz'

MESES_ES = {
    1: 'ENERO', 2: 'FEBRERO', 3: 'MARZO', 4: 'ABRIL',
    5: 'MAYO', 6: 'JUNIO', 7: 'JULIO', 8: 'AGOSTO',
    9: 'SEPTIEMBRE', 10: 'OCTUBRE', 11: 'NOVIEMBRE', 12: 'DICIEMBRE'
}

PATRON_FECHA = r'[A-ZÁÉÍÓÚ]+\s+\d{4}'
PATRON_URL = r'https?://[^\s\)\]>\"\']*aliexpress\.com[^\s\)\]>\"\']*'
PATRON_URL_AMAZON = r'https?://(?:amzn\.to|amzn\.eu|www\.amazon\.[a-z.]+)/[^\s\)\]>\"\']*'
PATRON_URL_OTROS  = r'https?://[^\s\)\]>\"\']*'
DOMINIOS_EXCLUIR  = ('aliexpress.com', 'amzn.to', 'amzn.eu', 'amazon.', 'youtube.com', 'youtu.be')

HEADERS_REQUESTS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

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


def preguntar(mensaje):
    return Prompt.ask(mensaje, choices=['s', 'n'], default='n') == 's'


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


def extraer_links_amazon(descripcion):
    return list(set(re.findall(PATRON_URL_AMAZON, descripcion)))


def comprobar_link_amazon_chrome(page, url):
    """Comprobación via Chrome: ok si hay botón de compra, si no → sin_stock."""
    try:
        page.goto(url, wait_until='domcontentloaded', timeout=20000)
        page.wait_for_timeout(2000)
        texto = page.inner_text('body').lower()
        if not page.title() or page.title() in ('amazon.es', 'amazon.com', 'amazon'):
            return 'roto'
        indicadores_comprable = [
            'añadir a la cesta',
            'comprar ahora',
            'add to cart',
            'add to basket',
            'buy now',
        ]
        if any(ind in texto for ind in indicadores_comprable):
            return 'ok'
        return 'sin_stock'
    except Exception:
        return 'roto'


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
    if not CHROME_EXE:
        console.print('[red]ERROR: No se encontró Chrome en las rutas habituales.[/red]')
        for p in CHROME_RUTAS:
            console.print(f'  [dim]{p}[/dim]')
        return False

    with console.status('Cerrando Chrome si está abierto...'):
        if sys.platform == 'win32':
            subprocess.run(['taskkill', '/F', '/IM', 'chrome.exe'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.run(['pkill', '-f', 'Google Chrome'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1)

    console.print(f'Arrancando Chrome en puerto [cyan]{CHROME_DEBUG_PORT}[/cyan]...')
    subprocess.Popen([
        CHROME_EXE,
        f'--remote-debugging-port={CHROME_DEBUG_PORT}',
        f'--user-data-dir={CHROME_USER_DATA}',
        '--no-first-run',
        '--no-default-browser-check',
    ])

    with console.status('Esperando a Chrome...'):
        for _ in range(20):
            try:
                r = requests.get(f'http://127.0.0.1:{CHROME_DEBUG_PORT}/json/version', timeout=2)
                if r.ok:
                    console.print('[green]✓[/green] Chrome listo\n')
                    return True
            except Exception:
                pass
            time.sleep(1)

    console.print('[red]ERROR: Chrome no respondió en el puerto de depuración.[/red]')
    return False


def cerrar_chrome():
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
        titulo = page.title().lower()
        if 'just a moment' in titulo or 'checking your browser' in titulo:
            return True
        texto = page.inner_text('body').lower()
        return ('we need to check if you are a robot' in texto
                or 'checking if the site connection is secure' in texto
                or 'enable javascript and cookies to continue' in texto)
    except Exception:
        return False


def esperar_si_captcha(page):
    page.wait_for_timeout(4000)
    if not es_captcha(page):
        return
    console.print('\n[yellow]⚠  CAPTCHA detectado. Resuélvelo en Chrome y pulsa ENTER para continuar...[/yellow]')
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


def chequear_links_videos(video_links):
    """video_links: [(video, [(tipo, url, linea, comment_id, texto_completo), ...]), ...]"""
    if not video_links:
        console.print('[yellow]No se encontraron links de AliExpress.[/yellow]')
        return [], []

    cookies = cargar_cookies_aliexpress()
    if not cookies:
        console.print('[yellow]AVISO: aliexpress_cookies.json no encontrado. Saltando verificación de links.[/yellow]')
        return [], []

    if not iniciar_chrome():
        console.print('[yellow]Saltando verificación de links.[/yellow]')
        return [], []

    playwright = sync_playwright().start()
    try:
        browser = playwright.chromium.connect_over_cdp(f'http://127.0.0.1:{CHROME_DEBUG_PORT}')
    except Exception as e:
        console.print(f'[red]AVISO: No se pudo conectar a Chrome: {e}[/red]')
        playwright.stop()
        cerrar_chrome()
        return [], []

    context = browser.contexts[0]
    context.add_cookies(cookies)
    page = context.new_page()

    # Construir lista de URLs únicas preservando orden
    seen = set()
    urls_orden = []
    for _, pares in video_links:
        for tipo, url, linea, comment_id, texto_completo in pares:
            if url not in seen:
                seen.add(url)
                urls_orden.append(url)

    cache = {}
    links_rotos = []
    links_geo = []

    with Progress(
        SpinnerColumn(),
        TextColumn('[progress.description]{task.description}'),
        BarColumn(),
        MofNCompleteColumn(),
        TextColumn('[dim]{task.fields[url]}[/dim]'),
        console=console,
    ) as progress:
        task = progress.add_task('Comprobando links', total=len(urls_orden), url='')
        for url in urls_orden:
            progress.update(task, url=url[:70])
            cache[url] = comprobar_link_chrome(page, url)
            progress.advance(task)

    for video, pares in video_links:
        titulo = video['snippet']['title']
        vid_id = video['id']
        for tipo, url, linea, comment_id, texto_completo in pares:
            entrada = {'video': titulo, 'video_id': vid_id, 'url': url, 'linea': linea,
                       'tipo': tipo, 'comment_id': comment_id, 'texto_completo': texto_completo}
            if cache[url] == 'roto':
                links_rotos.append(entrada)
            elif cache[url] == 'geo':
                links_geo.append(entrada)

    page.close()
    playwright.stop()
    cerrar_chrome()
    return links_rotos, links_geo


def _imprimir_grupo(items, simbolo, estilo):
    video_actual = None
    for item in items:
        if item['video'] != video_actual:
            video_actual = item['video']
            console.print(f'\n  [bold]{item["video"]}[/bold]')
            console.print(f'  [dim]https://studio.youtube.com/video/{item["video_id"]}[/dim]')
        tipo_sym = '📝' if item.get('tipo') == 'descripcion' else '💬'
        console.print(f'  [{estilo}]{simbolo}[/{estilo}] {tipo_sym} {item["linea"][:100]}')


def _escribir_grupo(f, items, simbolo):
    video_actual = None
    for item in items:
        if item['video'] != video_actual:
            video_actual = item['video']
            f.write(f'\n  Video: {item["video"]}\n')
            f.write(f'    https://studio.youtube.com/video/{item["video_id"]}\n')
        tipo_sym = '📝' if item.get('tipo') == 'descripcion' else '💬'
        f.write(f'  {simbolo} {tipo_sym} {item["linea"]}\n')


def cargar_estado_links():
    if not os.path.exists(LINKS_ESTADO_FILE):
        return None
    with open(LINKS_ESTADO_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def guardar_estado_links(links_rotos, links_geo):
    with open(LINKS_ESTADO_FILE, 'w', encoding='utf-8') as f:
        json.dump({
            'rotos': len({e['url'] for e in links_rotos}),
            'geo': len({e['url'] for e in links_geo}),
            'rotos_ali':   len({e['url'] for e in links_rotos if e.get('tienda') == 'aliexpress'}),
            'rotos_amz':   len({e['url'] for e in links_rotos if e.get('tienda') == 'amazon'}),
            'rotos_otros': len({e['url'] for e in links_rotos if e.get('tienda') == 'otro'}),
            'fecha': datetime.now().strftime('%d/%m/%Y %H:%M'),
        }, f)


def cargar_estado_comentarios():
    if not os.path.exists(COMENTARIOS_ESTADO_FILE):
        return None
    with open(COMENTARIOS_ESTADO_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def guardar_cache_videos(videos, info_canal):
    data = json.dumps({'videos': videos, 'info_canal': info_canal,
                       'fecha': datetime.now().strftime('%d/%m/%Y %H:%M')},
                      ensure_ascii=False).encode('utf-8')
    with gzip.open(CACHE_VIDEOS_FILE, 'wb') as f:
        f.write(data)


def cargar_cache_videos():
    if not os.path.exists(CACHE_VIDEOS_FILE):
        return None, None, None
    with gzip.open(CACHE_VIDEOS_FILE, 'rb') as f:
        data = json.loads(f.read().decode('utf-8'))
    return data['videos'], data['info_canal'], data.get('fecha', '?')


def guardar_estado_comentarios(actualizados, sin_actualizar, sin_cupones):
    with open(COMENTARIOS_ESTADO_FILE, 'w', encoding='utf-8') as f:
        json.dump({
            'actualizados': actualizados,
            'sin_actualizar': sin_actualizar,
            'sin_cupones': sin_cupones,
            'fecha': datetime.now().strftime('%d/%m/%Y %H:%M'),
        }, f)


def guardar_reporte_links(links_rotos, links_geo):
    guardar_estado_links(links_rotos, links_geo)
    console.rule()
    if not links_rotos and not links_geo:
        console.print('[green]✓ No se encontraron links con problemas.[/green]')
        return

    if links_rotos:
        console.print(f'\n[red bold]✗ ELIMINADOS / NO PROMOCIONABLES ({len(links_rotos)}):[/red bold]')
        console.rule(style='red')
        _imprimir_grupo(links_rotos, '✗', 'red')

    if links_geo:
        console.print(f'\n[yellow bold]⚠  NO DISPONIBLES EN TU REGIÓN ({len(links_geo)}):[/yellow bold]')
        console.rule(style='yellow')
        _imprimir_grupo(links_geo, '⚠', 'yellow')

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

    if links_rotos or links_geo:
        console.print(f'\n[dim]Reporte guardado en "{REPORTE_LINKS_FILE}"[/dim]')


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


# ── Acciones del menú ─────────────────────────────────────────────────────────

def accion_actualizar_cupones(youtube, videos, nuevo_bloque, patron):
    videos_con_cupones = buscar_videos_con_cupones(videos, patron)

    if not videos_con_cupones:
        console.print('[yellow]No se encontró ningún vídeo con el bloque de cupones.[/yellow]')
        return

    console.print(Panel(nuevo_bloque, title='Bloque de cupones a usar', border_style='cyan'))
    pendientes_list = [v for v in videos_con_cupones if nuevo_bloque not in v['snippet']['description']]
    pendientes = len(pendientes_list)
    pendientes_ids = {v['id'] for v in pendientes_list}

    console.print(f'\n[bold]{len(videos_con_cupones)}[/bold] vídeos con cupones encontrados\n')
    for v in videos_con_cupones:
        estilo = 'white' if v['id'] in pendientes_ids else 'dim'
        console.print(f'  [dim]·[/dim] [{estilo}]{v["snippet"]["title"]}[/{estilo}]')

    lecturas = 1 + (len(videos) // 50 + 1) * 2
    coste = pendientes * 50 + lecturas
    console.print()
    console.print(f'  [dim]Vídeos a modificar: [bold]{pendientes}[/bold]  ·  coste estimado: [/dim][bold rgb(255,0,0)]~{coste} unidades[/bold rgb(255,0,0)]')
    console.print(f'  📊 [link=https://console.cloud.google.com/apis/api/youtube.googleapis.com/quotas][bold cyan]Ver cuota disponible en Google Cloud ↗[/bold cyan][/link]')
    console.print()
    if not preguntar(f'¿Actualizar [bold]{pendientes}[/bold] vídeo{"s" if pendientes != 1 else ""}?'):
        console.print('[yellow]Cancelado.[/yellow]')
        return

    console.print()
    actualizados = sin_cambios = omitidos = 0
    omitidos_lista = []

    with Progress(
        SpinnerColumn(),
        TextColumn('[progress.description]{task.description}'),
        BarColumn(),
        MofNCompleteColumn(),
        TextColumn('[dim]{task.fields[titulo]}[/dim]'),
        console=console,
    ) as progress:
        task = progress.add_task('Actualizando', total=pendientes, titulo='')
        for video in videos_con_cupones:
            titulo = video['snippet']['title']
            resultado = actualizar_video(youtube, video, nuevo_bloque, patron)
            if resultado == 'ok':
                actualizados += 1
                progress.update(task, titulo=titulo[:60])
                progress.advance(task)
            elif resultado == 'sin_cambios':
                sin_cambios += 1
            elif resultado == 'demasiado_larga':
                omitidos += 1
                omitidos_lista.append((titulo, video['id']))
                progress.update(task, titulo=titulo[:60])
                progress.advance(task)

    console.print()
    console.rule()
    sin_match = len(videos) - len(videos_con_cupones)
    console.print(f'Total: [bold]{len(videos)}[/bold]  ·  Sin cupones: {sin_match}  ·  Con cupones: {len(videos_con_cupones)}')
    console.print(f'[green]✓[/green] Actualizados: [bold green]{actualizados}[/bold green]  ·  Sin cambios: {sin_cambios}', end='')
    if omitidos:
        console.print(f'  ·  [red]Omitidos por longitud: {omitidos}[/red]')
        for titulo, vid_id in omitidos_lista:
            console.print(f'  [red]  · {titulo}[/red]')
            console.print(f'    [link=https://studio.youtube.com/video/{vid_id}][blue]https://studio.youtube.com/video/{vid_id}[/blue][/link]')
    else:
        console.print()


def reemplazar_link_en_videos(youtube, videos, url_vieja, url_nueva):
    afectados = [v for v in videos if url_vieja in v['snippet']['description']]
    if not afectados:
        return
    console.print(f'  {len(afectados)} descripción(es) afectadas. Actualizando...')
    for video in afectados:
        snippet = video['snippet']
        nueva_desc = snippet['description'].replace(url_vieja, url_nueva)
        youtube.videos().update(
            part='snippet',
            body={'id': video['id'], 'snippet': snippet | {'description': nueva_desc}}
        ).execute()
        snippet['description'] = nueva_desc
        console.print(f'  [green]✓[/green] 📝 {snippet["title"]}')


def reemplazar_link_en_comentarios(youtube, entradas, url_vieja, url_nueva):
    console.print(f'  {len(entradas)} comentario(s) afectados. Actualizando...')
    for entry in entradas:
        texto_nuevo = entry['texto_completo'].replace(url_vieja, url_nueva)
        try:
            youtube.comments().update(
                part='snippet',
                body={'id': entry['comment_id'], 'snippet': {'textOriginal': texto_nuevo}}
            ).execute()
            console.print(f'  [green]✓[/green] 💬 {entry["video"]}')
        except Exception as e:
            console.print(f'  [red]✗[/red] 💬 {entry["video"]}: {e}')


def accion_comprobar_links(youtube, videos, channel_id=None):
    links_ali_unicos = {
        url for v in videos
        for url in extraer_links_aliexpress(v['snippet']['description'])
    }
    links_amz_unicos = {
        url for v in videos
        for url in extraer_links_amazon(v['snippet']['description'])
    }
    dominios_ignorados = cargar_dominios_ignorados()
    links_otros_unicos = {
        url for v in videos
        for url in re.findall(PATRON_URL_OTROS, v['snippet']['description'])
        if not any(d in url for d in DOMINIOS_EXCLUIR)
    }
    links_otros_sin_ignorados = {
        url for url in links_otros_unicos
        if not any(
            ('.'.join(re.sub(r'^https?://', '', url).split('/')[0].split('.')[-2:])) == d
            for d in dominios_ignorados
        )
    }
    n_ali         = len(links_ali_unicos)
    n_amz         = len(links_amz_unicos)
    n_otros       = len(links_otros_sin_ignorados)
    n_otros_total = len(links_otros_unicos)
    n_com         = len(videos)

    console.print(f'  Amazon:      [bold]{n_amz}[/bold] links únicos [dim](Chrome, detecta sin stock)[/dim]')
    console.print(f'  AliExpress:  [bold]{n_ali}[/bold] links únicos [dim](Chrome, detecta links rotos o geo-restringidos)[/dim]')
    ignorados_txt = f' [dim](+ {n_otros_total - n_otros} ignorados)[/dim]' if n_otros_total > n_otros else ''
    console.print(f'  Otros links: [bold]{n_otros}[/bold] links únicos{ignorados_txt} [dim](rápido, detecta "page not found")[/dim]')
    console.print(f'  Comentarios: [bold]{n_com}[/bold] vídeos a escanear · coste: [bold rgb(255,0,0)]~{n_com} unidades[/bold rgb(255,0,0)]')
    console.print(f'  📊 [link=https://console.cloud.google.com/apis/api/youtube.googleapis.com/quotas][bold cyan]Ver cuota disponible en Google Cloud ↗[/bold cyan][/link]')
    console.print()
    console.print('[red]AVISO: Amazon y AliExpress cerrarán Chrome y lo abrirán en modo depuración.[/red]\n')

    OPT_AMZ          = f'Amazon ({n_amz} links)'
    OPT_ALI          = f'AliExpress ({n_ali} links)'
    OPT_OTROS        = f'Otros links ({n_otros} links)'
    OPT_OTROS_IGNOR  = f'Otros links ignorados ({n_otros_total - n_otros} links)'
    OPT_COM          = f'Comentarios fijados (~{n_com} unidades extra)'

    opciones_disponibles = []
    if n_amz:
        opciones_disponibles.append(OPT_AMZ)
    if n_ali:
        opciones_disponibles.append(OPT_ALI)
    if n_otros:
        opciones_disponibles.append(OPT_OTROS)
    if dominios_ignorados and n_otros_total > n_otros:
        opciones_disponibles.append(OPT_OTROS_IGNOR)
    if videos:
        opciones_disponibles.append(OPT_COM)

    if not opciones_disponibles:
        console.print('[yellow]No hay links que comprobar.[/yellow]')
        return

    seleccion = questionary.checkbox('¿Qué quieres comprobar?', choices=opciones_disponibles).ask()
    if not seleccion:
        console.print('[yellow]Cancelado.[/yellow]')
        return

    hacer_amz    = OPT_AMZ        in seleccion
    hacer_ali    = OPT_ALI        in seleccion
    hacer_otros  = OPT_OTROS      in seleccion
    hacer_ignor  = OPT_OTROS_IGNOR in seleccion
    incluir_comentarios = OPT_COM   in seleccion

    # ── Amazon (Chrome) ───────────────────────────────────────────────────────
    links_amazon_rotos = []

    if hacer_amz and n_amz:
        video_links_amz = []
        for video in videos:
            descripcion = video['snippet']['description']
            links = extraer_links_amazon(descripcion)
            if links:
                pares = [('descripcion', url, linea_con_link(descripcion, url), None, None) for url in links]
                video_links_amz.append((video, pares))

        if iniciar_chrome():
            playwright_amz = sync_playwright().start()
            try:
                browser_amz = playwright_amz.chromium.connect_over_cdp(f'http://127.0.0.1:{CHROME_DEBUG_PORT}')
                page_amz = browser_amz.contexts[0].new_page()
                cache_amz = {}
                with Progress(
                    SpinnerColumn(),
                    TextColumn('[progress.description]{task.description}'),
                    BarColumn(),
                    MofNCompleteColumn(),
                    TextColumn('[dim]{task.fields[url]}[/dim]'),
                    console=console,
                ) as progress:
                    task = progress.add_task('Comprobando Amazon', total=n_amz, url='')
                    for url in links_amz_unicos:
                        progress.update(task, url=url[:70])
                        cache_amz[url] = comprobar_link_amazon_chrome(page_amz, url)
                        progress.advance(task)
                page_amz.close()
                playwright_amz.stop()
                cerrar_chrome()
                for video, pares in video_links_amz:
                    for tipo, url, linea, comment_id, texto_completo in pares:
                        estado = cache_amz.get(url, 'ok')
                        if estado in ('roto', 'sin_stock'):
                            links_amazon_rotos.append({
                                'video': video['snippet']['title'], 'video_id': video['id'],
                                'url': url, 'linea': linea,
                                'tipo': tipo, 'comment_id': comment_id, 'texto_completo': texto_completo,
                                'tienda': 'amazon',
                                'estado_detalle': 'sin_stock' if estado == 'sin_stock' else None,
                            })
            except Exception as e:
                console.print(f'[red]Error comprobando Amazon: {e}[/red]')
                playwright_amz.stop()
                cerrar_chrome()

    # ── AliExpress (Chrome) ───────────────────────────────────────────────────
    links_rotos_ali, links_geo_ali = [], []
    video_links = []

    if hacer_ali:
        for video in videos:
            descripcion = video['snippet']['description']
            links = extraer_links_aliexpress(descripcion)
            if links:
                pares = [('descripcion', url, linea_con_link(descripcion, url), None, None) for url in links]
                video_links.append((video, pares))

        if incluir_comentarios:
            with Progress(
                SpinnerColumn(),
                TextColumn('[progress.description]{task.description}'),
                BarColumn(),
                MofNCompleteColumn(),
                TextColumn('[dim]{task.fields[titulo]}[/dim]'),
                console=console,
            ) as progress:
                task = progress.add_task('Obteniendo comentarios', total=n_com, titulo='')
                for video in videos:
                    progress.update(task, titulo=video['snippet']['title'][:60])
                    try:
                        resp = youtube.commentThreads().list(
                            part='snippet', videoId=video['id'],
                            maxResults=1, order='relevance',
                        ).execute()
                        items = resp.get('items', [])
                        if items:
                            thread = items[0]
                            top = thread['snippet']['topLevelComment']['snippet']
                            if top.get('authorChannelId', {}).get('value', '') == channel_id:
                                texto = top.get('textOriginal', '') or top.get('textDisplay', '')
                                comment_id = thread['snippet']['topLevelComment']['id']
                                links_com = extraer_links_aliexpress(texto)
                                if links_com:
                                    pares = [('comentario', url, linea_con_link(texto, url), comment_id, texto)
                                             for url in links_com]
                                    video_links.append((video, pares))
                    except Exception:
                        pass
                    progress.advance(task)

        if video_links:
            links_rotos_ali, links_geo_ali = chequear_links_videos(video_links)

    # ── Otros links (page not found) ─────────────────────────────────────────
    links_otros_rotos = []

    urls_otros_a_comprobar = set()
    if hacer_otros:
        urls_otros_a_comprobar |= links_otros_sin_ignorados
    if hacer_ignor:
        urls_otros_a_comprobar |= (links_otros_unicos - links_otros_sin_ignorados)

    urls_cloudflare = []

    if urls_otros_a_comprobar:
        with Progress(SpinnerColumn(), TextColumn('[progress.description]{task.description}'),
                      BarColumn(), MofNCompleteColumn(), console=console) as progress:
            task = progress.add_task('Comprobando otros links', total=len(urls_otros_a_comprobar))
            for url in urls_otros_a_comprobar:
                try:
                    r = requests.get(url, headers=HEADERS_REQUESTS, allow_redirects=True, timeout=10)
                    texto = r.text.lower()
                    if r.status_code == 403 and 'just a moment' in texto:
                        urls_cloudflare.append(url)
                    elif r.status_code == 404 or 'page not found' in texto or 'página no encontrada' in texto:
                        for v in videos:
                            if url in v['snippet']['description']:
                                links_otros_rotos.append({
                                    'url': url,
                                    'video': v['snippet']['title'],
                                    'video_id': v['id'],
                                    'tipo': 'descripcion',
                                    'tienda': 'otro',
                                    'linea': linea_con_link(v['snippet']['description'], url),
                                })
                except Exception:
                    pass
                progress.advance(task)

    if urls_cloudflare:
        console.print(f'\n[yellow]{len(urls_cloudflare)} link(s) protegidos por Cloudflare (no se pueden comprobar sin navegador):[/yellow]')
        for url in urls_cloudflare:
            console.print(f'  [dim]{url}[/dim]')
        resp = console.input('\n[dim]¿Comprobar estos links con Chrome? [s/n]: [/dim]').strip().lower()
        if resp == 's' and iniciar_chrome():
            playwright_cf = sync_playwright().start()
            try:
                browser_cf = playwright_cf.chromium.connect_over_cdp(f'http://127.0.0.1:{CHROME_DEBUG_PORT}')
                page_cf = browser_cf.contexts[0].new_page()
                with Progress(SpinnerColumn(), TextColumn('[progress.description]{task.description}'),
                              BarColumn(), MofNCompleteColumn(),
                              TextColumn('[dim]{task.fields[url]}[/dim]'),
                              console=console) as progress:
                  task_cf = progress.add_task('Comprobando con Chrome', total=len(urls_cloudflare), url='')
                  for url in urls_cloudflare:
                    progress.update(task_cf, url=url[:70])
                    try:
                        page_cf.goto(url, wait_until='domcontentloaded', timeout=20000)
                        esperar_si_captcha(page_cf)
                        texto = page_cf.inner_text('body').lower()
                        titulo = page_cf.title().lower()
                        if ('page not found' in texto or 'página no encontrada' in texto
                                or 'page not found' in titulo or 'not found' in titulo
                                or titulo.startswith('404')):
                            for v in videos:
                                if url in v['snippet']['description']:
                                    links_otros_rotos.append({
                                        'url': url,
                                        'video': v['snippet']['title'],
                                        'video_id': v['id'],
                                        'tipo': 'descripcion',
                                        'tienda': 'otro',
                                        'linea': linea_con_link(v['snippet']['description'], url),
                                    })
                    except Exception:
                        pass
                    progress.advance(task_cf)
            finally:
                playwright_cf.stop()

    # ── Combinar y guardar ────────────────────────────────────────────────────
    for e in links_rotos_ali + links_geo_ali:
        e.setdefault('tienda', 'aliexpress')

    links_rotos = links_amazon_rotos + links_rotos_ali + links_otros_rotos
    links_geo   = links_geo_ali

    guardar_reporte_links(links_rotos, links_geo)

    todos_problemas = links_rotos + links_geo
    if not todos_problemas:
        return

    urls_unicas = list(dict.fromkeys(e['url'] for e in todos_problemas))
    console.print('\n[bold]Links con problemas:[/bold]\n')
    for i, url in enumerate(urls_unicas, 1):
        if any(e.get('tienda') == 'amazon' and e['url'] == url for e in todos_problemas):
            tienda_sym = '🛒'
        elif any(e.get('tienda') == 'otro' and e['url'] == url for e in todos_problemas):
            tienda_sym = '🔗'
        else:
            tienda_sym = '🛍'
        console.print(f'  [bold]{i}.[/bold] {tienda_sym} {url}')
        for e in todos_problemas:
            if e['url'] == url:
                tipo_sym = '📝' if e['tipo'] == 'descripcion' else '💬'
                detalle = ' [yellow](sin stock)[/yellow]' if e.get('estado_detalle') == 'sin_stock' else ''
                console.print(f'     [dim]{tipo_sym} {e["video"]}[/dim]{detalle}')

    console.print()
    while True:
        resp = console.input('[dim]Número a reemplazar (o Enter para terminar): [/dim]').strip()
        if not resp:
            break
        try:
            idx = int(resp) - 1
            if not 0 <= idx < len(urls_unicas):
                raise ValueError
        except ValueError:
            console.print('[red]Número no válido.[/red]')
            continue
        url_vieja = urls_unicas[idx]
        url_nueva = console.input(f'[dim]Nueva URL para {url_vieja[:60]}...: [/dim]').strip()
        if not url_nueva:
            continue
        entradas_url = [e for e in todos_problemas if e['url'] == url_vieja]
        entradas_desc = [e for e in entradas_url if e['tipo'] == 'descripcion']
        entradas_com  = [e for e in entradas_url if e['tipo'] == 'comentario']
        if entradas_desc:
            reemplazar_link_en_videos(youtube, videos, url_vieja, url_nueva)
        if entradas_com:
            reemplazar_link_en_comentarios(youtube, entradas_com, url_vieja, url_nueva)


def cargar_exclusiones():
    if not os.path.exists(EXCLUSIONES_FILE):
        return set()
    with open(EXCLUSIONES_FILE, 'r', encoding='utf-8') as f:
        return {line.split('#')[0].strip() for line in f if line.strip() and not line.startswith('#')}


def guardar_exclusiones(videos_nuevos):
    # Leer entradas actuales (id + comentario) para no perder títulos ya guardados
    entradas = {}
    if os.path.exists(EXCLUSIONES_FILE):
        with open(EXCLUSIONES_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    vid_id = line.split('#')[0].strip()
                    entradas[vid_id] = line
    # Añadir los nuevos con su título
    for video in videos_nuevos:
        vid_id = video['id']
        titulo = video['snippet']['title']
        entradas[vid_id] = f'{vid_id}  # {titulo}'
    with open(EXCLUSIONES_FILE, 'w', encoding='utf-8') as f:
        for vid_id in sorted(entradas):
            f.write(entradas[vid_id] + '\n')


def cargar_dominios_ignorados():
    if not os.path.exists(DOMINIOS_IGNORADOS_FILE):
        return set()
    with open(DOMINIOS_IGNORADOS_FILE, 'r', encoding='utf-8') as f:
        return {line.strip() for line in f if line.strip() and not line.startswith('#')}


def guardar_dominios_ignorados(dominios):
    with open(DOMINIOS_IGNORADOS_FILE, 'w', encoding='utf-8') as f:
        for d in sorted(dominios):
            f.write(d + '\n')


def accion_videos_sin_cupones(videos, patron):
    exclusiones = cargar_exclusiones()
    sin_cupones = [
        v for v in videos
        if not re.search(patron, v['snippet']['description'], re.DOTALL)
        and v['id'] not in exclusiones
    ]
    if not sin_cupones:
        console.print('[green]✓ Todos los vídeos tienen el bloque de cupones (o están excluidos).[/green]')
        return

    console.print(f'[bold]{len(sin_cupones)}[/bold] vídeos [yellow]sin[/yellow] bloque de cupones:\n')
    for i, v in enumerate(sin_cupones, 1):
        console.print(f'  [bold]{i}.[/bold] {v["snippet"]["title"]}')
        console.print(f'     [link=https://studio.youtube.com/video/{v["id"]}][blue]https://studio.youtube.com/video/{v["id"]}[/blue][/link]')

    console.print()
    respuesta = console.input(
        '[dim]Introduce los números a excluir separados por comas (o Enter para ninguno): [/dim]'
    ).strip()

    if respuesta:
        try:
            indices = [int(n.strip()) - 1 for n in respuesta.split(',')]
            seleccionados = [sin_cupones[i] for i in indices if 0 <= i < len(sin_cupones)]
            if seleccionados:
                guardar_exclusiones(seleccionados)
                console.print(f'[green]✓ {len(seleccionados)} vídeos añadidos a exclusiones.[/green]')
        except ValueError:
            console.print('[red]Entrada no válida, no se guardó nada.[/red]')


def accion_comprobar_comentarios(youtube, videos, nuevo_bloque, patron, channel_id):
    videos_con_cupones = buscar_videos_con_cupones(videos, patron)
    if not videos_con_cupones:
        console.print('[yellow]No hay vídeos con cupones.[/yellow]')
        return

    n = len(videos_con_cupones)
    console.print(f'[bold]{n}[/bold] vídeos con cupones. Coste estimado: [bold rgb(255,0,0)]~{n} unidades[/bold rgb(255,0,0)]')
    console.print(f'  📊 [link=https://console.cloud.google.com/apis/api/youtube.googleapis.com/quotas][bold cyan]Ver cuota disponible en Google Cloud ↗[/bold cyan][/link]')
    console.print()
    if not preguntar(f'¿Comprobar comentarios fijados de {n} vídeo{"s" if n != 1 else ""}?'):
        console.print('[yellow]Cancelado.[/yellow]')
        return

    actualizados = 0
    sin_actualizar = 0
    sin_cupones_count = 0
    # sin_actualizar_lista: (titulo, vid_id, comment_id)
    sin_actualizar_lista = []
    sin_cupones_lista = []

    with Progress(
        SpinnerColumn(),
        TextColumn('[progress.description]{task.description}'),
        BarColumn(),
        MofNCompleteColumn(),
        TextColumn('[dim]{task.fields[titulo]}[/dim]'),
        console=console,
    ) as progress:
        task = progress.add_task('Comprobando comentarios', total=n, titulo='')
        for video in videos_con_cupones:
            titulo = video['snippet']['title']
            vid_id = video['id']
            progress.update(task, titulo=titulo[:60])
            try:
                resp = youtube.commentThreads().list(
                    part='snippet',
                    videoId=vid_id,
                    maxResults=1,
                    order='relevance',
                ).execute()
                items = resp.get('items', [])
                encontrado = False
                if items:
                    thread = items[0]
                    top = thread['snippet']['topLevelComment']['snippet']
                    autor_id = top.get('authorChannelId', {}).get('value', '')
                    if autor_id == channel_id:
                        texto = top.get('textOriginal', '') or top.get('textDisplay', '')
                        comment_id = thread['snippet']['topLevelComment']['id']
                        if nuevo_bloque in texto:
                            actualizados += 1
                            encontrado = True
                        elif re.search(patron, texto, re.DOTALL):
                            sin_actualizar += 1
                            sin_actualizar_lista.append((titulo, vid_id, comment_id))
                            encontrado = True
                if not encontrado:
                    sin_cupones_count += 1
                    sin_cupones_lista.append((titulo, vid_id))
            except Exception:
                sin_cupones_count += 1
                sin_cupones_lista.append((titulo, vid_id))
            progress.advance(task)

    guardar_estado_comentarios(actualizados, sin_actualizar, sin_cupones_count)

    console.print()
    console.rule()
    console.print(f'[green]✓[/green] Actualizados: [bold green]{actualizados}[/bold green]  ·  '
                  f'[yellow]⚠[/yellow] Sin actualizar: [bold yellow]{sin_actualizar}[/bold yellow]  ·  '
                  f'[red]✗[/red] Sin cupones: [bold red]{sin_cupones_count}[/bold red]')

    if sin_actualizar_lista:
        console.print(f'\n[yellow bold]Sin actualizar ({len(sin_actualizar_lista)}):[/yellow bold]')
        for titulo, vid_id, _ in sin_actualizar_lista:
            console.print(f'  [yellow]·[/yellow] {titulo}')
            console.print(f'    [link=https://studio.youtube.com/video/{vid_id}/edit][blue]https://studio.youtube.com/video/{vid_id}/edit[/blue][/link]')

    if sin_cupones_lista:
        console.print(f'\n[red bold]Sin comentario fijado con cupones ({len(sin_cupones_lista)}):[/red bold]')
        for titulo, vid_id in sin_cupones_lista:
            console.print(f'  [red]·[/red] {titulo}')
            console.print(f'    [link=https://studio.youtube.com/video/{vid_id}/edit][blue]https://studio.youtube.com/video/{vid_id}/edit[/blue][/link]')

    if sin_actualizar_lista:
        console.print()
        coste = len(sin_actualizar_lista) * 50
        console.print(f'  [dim]Coste de actualizar comentarios: [/dim][bold rgb(255,0,0)]~{coste} unidades[/bold rgb(255,0,0)]')
        console.print(f'  📊 [link=https://console.cloud.google.com/apis/api/youtube.googleapis.com/quotas][bold cyan]Ver cuota disponible en Google Cloud ↗[/bold cyan][/link]')
        console.print()
        resp = console.input(f'  [dim]¿Actualizar {len(sin_actualizar_lista)} comentario{"s" if len(sin_actualizar_lista) != 1 else ""}? [s/n]: [/dim]').strip().lower()
        if resp == 's':
            corregidos = 0
            n_act = len(sin_actualizar_lista)
            with Progress(
                SpinnerColumn(),
                TextColumn('[progress.description]{task.description}'),
                BarColumn(),
                MofNCompleteColumn(),
                TextColumn('[dim]{task.fields[titulo]}[/dim]'),
                console=console,
            ) as progress:
                task = progress.add_task('Actualizando comentarios', total=n_act, titulo='')
                for titulo, vid_id, comment_id in sin_actualizar_lista:
                    progress.update(task, titulo=titulo[:60])
                    try:
                        youtube.comments().update(
                            part='snippet',
                            body={
                                'id': comment_id,
                                'snippet': {'textOriginal': nuevo_bloque},
                            }
                        ).execute()
                        corregidos += 1
                    except Exception as e:
                        console.print(f'  [red]✗[/red] {titulo}: {e}')
                    progress.advance(task)
            guardar_estado_comentarios(actualizados + corregidos, sin_actualizar - corregidos, sin_cupones_count)


# ── Menú principal ────────────────────────────────────────────────────────────

def obtener_info_canal(youtube):
    resp = youtube.channels().list(part='snippet,statistics', mine=True).execute()
    item = resp['items'][0]
    stats = item['statistics']
    return {
        'id': item['id'],
        'nombre': item['snippet']['title'],
        'handle': item['snippet'].get('customUrl', ''),
        'suscriptores': int(stats.get('subscriberCount', 0)),
        'visualizaciones': int(stats.get('viewCount', 0)),
    }


def dibujar_cabecera(info_canal, n_videos, nuevo_bloque, stats=None, estado_links=None, estado_comentarios=None):
    ahora = datetime.now()
    mes = f'{MESES_ES[ahora.month][0]}{MESES_ES[ahora.month][1:].lower()} {ahora.year}'
    handle = info_canal['handle'] or ''
    nombre = info_canal['nombre']

    # ── Columna izquierda ──────────────────────────────────────────
    izq = Table.grid(padding=(0, 0))
    izq.add_column(no_wrap=True)
    izq.add_row(Text.assemble(('¡Bienvenido, ', ''), (nombre + '!', 'bold white')))
    izq.add_row('')
    yt_label = '   ▶  YouTube   '
    yt_pad   = ' ' * len(yt_label)
    izq.add_row(Text.assemble((' ' * 19, ''), (yt_pad, 'on red')))
    izq.add_row(Text.assemble((' ' * 19, ''), (yt_label, 'bold white on red')))
    izq.add_row(Text.assemble((' ' * 19, ''), (yt_pad, 'on red')))
    izq.add_row('')
    info_txt = Text(' ')
    if handle:
        info_txt.append(handle, style='white')
        info_txt.append('  ·  ', style='dim')
    info_txt.append(f'{n_videos}', style='cyan')
    info_txt.append(' vídeos', style='dim')
    info_txt.append('  ·  ', style='dim')
    info_txt.append(mes, style='dim')
    izq.add_row(info_txt)

    subs = info_canal.get('suscriptores', 0)
    views = info_canal.get('visualizaciones', 0)
    if subs or views:
        izq.add_row('')
        stats_txt = Text(' ')
        stats_txt.append(f'{subs:,}'.replace(',', '.'), style='cyan')
        stats_txt.append(' suscriptores', style='dim')
        stats_txt.append('  ·  ', style='dim')
        stats_txt.append(f'{views:,}'.replace(',', '.'), style='cyan')
        stats_txt.append(' visualizaciones', style='dim')
        izq.add_row(stats_txt)

    if nuevo_bloque:
        primera = nuevo_bloque.splitlines()[0]
        izq.add_row('')
        izq.add_row(Text('Cupones activos', style='cyan bold'))
        izq.add_row(Text('─' * 24, style='bright_black'))
        izq.add_row('')
        izq.add_row(Text.assemble(('● ', 'green'), (primera[:45], 'dim')))
    else:
        izq.add_row('')
        izq.add_row(Text('Cupones', style='cyan bold'))
        izq.add_row(Text('─' * 24, style='bright_black'))
        izq.add_row('')
        izq.add_row(Text.assemble(('○ ', 'yellow'), ('cupones.txt no encontrado', 'dim')))

    if estado_comentarios:
        izq.add_row('')
        izq.add_row(Text('Comentarios fijados', style='cyan bold'))
        izq.add_row(Text('─' * 24, style='bright_black'))
        izq.add_row('')
        c_act = estado_comentarios['actualizados']
        c_sin_act = estado_comentarios['sin_actualizar']
        c_sin_cup = estado_comentarios['sin_cupones']
        izq.add_row(Text.assemble(('● ', 'green'), (f'{c_act} ', 'green'), ('actualizados', 'dim')))
        izq.add_row(Text.assemble(('● ', 'red' if c_sin_act else 'green'), (f'{c_sin_act} ', 'red' if c_sin_act else 'green'), ('sin actualizar', 'dim')))
        izq.add_row(Text.assemble(('● ', 'yellow' if c_sin_cup else 'green'), (f'{c_sin_cup} ', 'yellow' if c_sin_cup else 'green'), ('sin cupones en comentario', 'dim')))
        izq.add_row(Text(f'  (último escaneo: {estado_comentarios["fecha"]})', style='dim'))

    # ── Columna derecha ────────────────────────────────────────────
    der = Table.grid(padding=(0, 0))
    der.add_column(no_wrap=True)
    der.add_row(Text('Estado del canal', style='cyan bold'))
    der.add_row(Text('─' * 24, style='bright_black'))
    der.add_row('')
    der.add_row(Text.assemble(('● ', 'blue'), (f'{n_videos} ', ''), ('vídeos en el canal', 'dim')))
    if stats:
        con, sin, excl = stats['con_cupones'], stats['sin_cupones'], stats['excluidos']
        der.add_row(Text.assemble(('● ', 'green'), (f'{con} ', 'cyan'), ('con cupones', 'dim')))
        der.add_row(Text.assemble(('● ', 'red'), (f'{sin} ', 'red' if sin else 'green'), ('sin cupones ', 'dim'), ('✗' if sin else '✓', 'red' if sin else 'green')))
        act = stats.get('actualizados', 0)
        pend = stats.get('por_actualizar', 0)
        der.add_row(Text.assemble(('● ', 'green'), (f'{act} ', 'green'), ('actualizados', 'dim')))
        der.add_row(Text.assemble(('● ', 'red' if pend else 'green'), (f'{pend} ', 'red' if pend else 'green'), ('por actualizar ', 'dim'), ('✗' if pend else '✓', 'red' if pend else 'green')))
        if excl:
            der.add_row(Text.assemble(('● ', 'dark_orange'), (f'{excl} ', 'cyan'), ('excluidos (de cupones)', 'dim')))
    der.add_row('')

    if estado_links:
        der.add_row(Text('Links', style='cyan bold'))
        der.add_row(Text('─' * 24, style='bright_black'))
        der.add_row('')
        geo = estado_links['geo']
        rotos_ali   = estado_links.get('rotos_ali',   estado_links['rotos'])
        rotos_amz   = estado_links.get('rotos_amz',   0)
        rotos_otros = estado_links.get('rotos_otros', 0)
        der.add_row(Text.assemble(('● ', 'red' if rotos_ali else 'green'), (f'{rotos_ali} ', 'red' if rotos_ali else 'green'), ('AliExpress rotos', 'dim')))
        der.add_row(Text.assemble(('● ', 'yellow' if geo else 'green'), (f'{geo} ', 'yellow' if geo else 'green'), ('no disponibles en tu región', 'dim')))
        der.add_row(Text.assemble(('● ', 'red' if rotos_amz else 'green'), (f'{rotos_amz} ', 'red' if rotos_amz else 'green'), ('Amazon rotos', 'dim')))
        der.add_row(Text.assemble(('● ', 'red' if rotos_otros else 'green'), (f'{rotos_otros} ', 'red' if rotos_otros else 'green'), ('otros rotos', 'dim')))
        der.add_row(Text(f'  última comprobación: {estado_links["fecha"]}', style='dim'))
        der.add_row('')

    # ── Layout dos columnas ────────────────────────────────────────
    grid = Table.grid(expand=True, padding=(0, 3))
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)
    grid.add_row(izq, der)

    console.print(Panel(
        grid,
        title=Text.assemble((' ◆ ', 'bold cyan'), ('YouTubeCodes', 'bold white'), (' v1.0 ', 'dim')),
        title_align='left',
        border_style='cyan',
        padding=(1, 2),
    ))


def accion_listar_otros_links(videos):
    dominios_ignorados = cargar_dominios_ignorados()

    # url → list de (vid_id, titulo) — todos, ignorados incluidos
    url_a_videos = {}
    for video in videos:
        desc = video['snippet']['description']
        vid_id = video['id']
        titulo = video['snippet']['title']
        urls = re.findall(PATRON_URL_OTROS, desc)
        for url in dict.fromkeys(urls):
            if any(d in url for d in DOMINIOS_EXCLUIR):
                continue
            url_a_videos.setdefault(url, [])
            if (vid_id, titulo) not in url_a_videos[url]:
                url_a_videos[url].append((vid_id, titulo))

    if not url_a_videos:
        console.print('[green]No se encontraron otros links.[/green]')
        return

    # agrupar por dominio
    por_dominio = {}
    for url, vids in url_a_videos.items():
        host = re.sub(r'^https?://', '', url).split('/')[0]
        partes = host.split('.')
        dominio = '.'.join(partes[-2:]) if len(partes) >= 2 else host
        por_dominio.setdefault(dominio, []).append((url, vids))

    por_dominio = dict(sorted(por_dominio.items(), key=lambda x: -len(x[1])))
    total_urls = sum(len(v) for v in por_dominio.values())
    console.print(f'  [bold]{total_urls}[/bold] links únicos en [bold]{len(por_dominio)}[/bold] dominios\n')

    dominios_lista = list(por_dominio.items())
    for i, (dominio, links) in enumerate(dominios_lista, 1):
        n_vids = len({vid_id for _, vids in links for vid_id, _ in vids})
        ignorado_tag = ' [dim](ignorado)[/dim]' if dominio in dominios_ignorados else ''
        console.print(f'[bold]{i}.[/bold] [bold cyan]{dominio}[/bold cyan]{ignorado_tag}  [dim]{len(links)} links · {n_vids} vídeos[/dim]')
        console.print(f'  [dim]{"─" * 60}[/dim]')
        for url, vids in links:
            console.print(f'  [link={url}][blue]{url}[/blue][/link]')
            for vid_id, titulo in vids:
                url_video = f'https://studio.youtube.com/video/{vid_id}/edit'
                console.print(f'    [dim][link={url_video}]{titulo[:70]}[/link][/dim]')
        console.print()

    resp = console.input('[dim]Números de dominios a ignorar en futuras comprobaciones (ej: 1 3 5) o Enter para omitir: [/dim]').strip()
    if resp:
        nuevos = set()
        for tok in resp.split():
            try:
                idx = int(tok) - 1
                if 0 <= idx < len(dominios_lista):
                    nuevos.add(dominios_lista[idx][0])
            except ValueError:
                pass
        if nuevos:
            guardar_dominios_ignorados(dominios_ignorados | nuevos)
            console.print(f'[green]Ignorados: {", ".join(sorted(nuevos))}[/green]')


def mostrar_menu(info_canal, n_videos, nuevo_bloque, stats=None, estado_links=None, estado_comentarios=None, offline=False, quota_agotada=False):
    console.clear()
    dibujar_cabecera(info_canal, n_videos, nuevo_bloque, stats, estado_links, estado_comentarios)
    if quota_agotada:
        console.print('[bold red]⚠  CUOTA DE LA API DE YOUTUBE AGOTADA — usando caché local (se resetea a medianoche hora del Pacífico)[/bold red]')
    elif offline:
        console.print('[bold red]⚠  MODO OFFLINE — los datos son del caché local, no de YouTube en tiempo real[/bold red]')
    console.print()


def main():
    offline = '--offline' in sys.argv

    if not os.path.exists(CREDENTIALS_FILE) and not offline:
        console.print(f'[red]ERROR: No se encontró el archivo "{CREDENTIALS_FILE}"[/red]')
        console.print('Renombra tu archivo de credenciales a "client_secret.json" y ponlo en esta carpeta.')
        return

    # Cargar cupones (opcional — opciones 1 y 3 lo necesitan)
    nuevo_bloque = None
    patron = None
    if os.path.exists(CUPONES_FILE):
        with open(CUPONES_FILE, 'r', encoding='utf-8') as f:
            contenido = f.read().strip()
        if contenido:
            patron = construir_patron(contenido)
            nuevo_bloque = añadir_fecha_si_falta(contenido)
    if not nuevo_bloque:
        console.print(f'[yellow]AVISO: "{CUPONES_FILE}" no encontrado o vacío. Las opciones 1 y 3 no estarán disponibles.[/yellow]')

    console.clear()
    console.print()
    console.print('  [bold cyan]◆ YouTubeCodes[/bold cyan]  [dim]Gestor de cupones de AliExpress[/dim]')
    console.rule(style='bright_black')
    console.print()

    quota_agotada = False
    youtube = None
    if offline:
        videos, info_canal, fecha_cache = cargar_cache_videos()
        if not videos:
            console.print('[red]ERROR: No hay caché de vídeos. Ejecuta primero sin --offline.[/red]')
            return
        console.print(f'[yellow]Modo offline[/yellow] — usando caché del {fecha_cache} ([bold]{len(videos)}[/bold] vídeos)')
        console.print('[dim]Las acciones que modifiquen YouTube no estarán disponibles.[/dim]\n')
    else:
        console.print('[bold]Autenticando con YouTube...[/bold]')
        youtube = autenticar()
        console.print('[green]✓[/green] Autenticación correcta\n')

        try:
            with console.status('[bold]Obteniendo lista de vídeos del canal...[/bold]'):
                videos = obtener_todos_los_videos(youtube)
                info_canal = obtener_info_canal(youtube)
            guardar_cache_videos(videos, info_canal)
            console.print(f'[green]✓[/green] [bold]{len(videos)}[/bold] vídeos en el canal')
        except HttpError as e:
            if e.resp.status == 403 and 'quotaExceeded' in str(e.content):
                console.print('[bold red]⚠  Cuota de YouTube agotada — cargando caché local...[/bold red]')
                videos, info_canal, fecha_cache = cargar_cache_videos()
                if not videos:
                    console.print('[red]ERROR: No hay caché. Vuelve a intentarlo mañana cuando se resetee la cuota.[/red]')
                    return
                console.print(f'[yellow]Usando caché del {fecha_cache}[/yellow] ([bold]{len(videos)}[/bold] vídeos)')
                offline = True
                quota_agotada = True
            else:
                raise

    OPT_CUPONES      = 'Actualizar cupones en las descripciones'
    OPT_LINKS        = 'Comprobar links'
    OPT_OTROS_LINKS  = 'Listar otros links (afiliados, blogs, etc.)'
    OPT_SIN_CUP      = 'Ver vídeos sin bloque de cupones'
    OPT_COMENTARIOS  = 'Comprobar comentarios fijados con cupones'
    OPT_RESCAN       = 'Recargar vídeos del canal'
    OPT_SALIR        = 'Salir'

    channel_id = info_canal['id']

    def build_opciones():
        ops = []
        if nuevo_bloque:
            ops.append(OPT_CUPONES)
        ops.append(OPT_LINKS)
        ops.append(OPT_OTROS_LINKS)
        ops.append(OPT_SIN_CUP)
        ops.append(OPT_COMENTARIOS)
        if not offline:
            ops.append(OPT_RESCAN)
        ops.append(OPT_SALIR)
        return ops

    def calcular_stats():
        if not patron:
            return None
        exclusiones = cargar_exclusiones()
        con_cupones = [v for v in videos if re.search(patron, v['snippet']['description'], re.DOTALL)]
        excl = sum(1 for v in videos if v['id'] in exclusiones)
        sin = len(videos) - len(con_cupones) - excl
        actualizados = sum(1 for v in con_cupones if nuevo_bloque in v['snippet']['description'])
        por_actualizar = len(con_cupones) - actualizados
        return {
            'con_cupones': len(con_cupones),
            'sin_cupones': sin,
            'excluidos': excl,
            'actualizados': actualizados,
            'por_actualizar': por_actualizar,
        }

    while True:
        mostrar_menu(info_canal, len(videos), nuevo_bloque, calcular_stats(),
                     cargar_estado_links(), cargar_estado_comentarios(), offline, quota_agotada)
        opcion = questionary.select(
            'Elige una opción:',
            choices=build_opciones(),
            use_shortcuts=False,
        ).ask()

        if opcion is None or opcion == OPT_SALIR:
            console.clear()
            console.print('[dim]Hasta luego.[/dim]')
            break

        if opcion == OPT_RESCAN:
            with console.status('[bold]Recargando vídeos del canal...[/bold]'):
                videos = obtener_todos_los_videos(youtube)
            continue

        console.print()
        if opcion == OPT_CUPONES:
            accion_actualizar_cupones(youtube, videos, nuevo_bloque, patron)
        elif opcion == OPT_LINKS:
            accion_comprobar_links(youtube, videos, channel_id)
        elif opcion == OPT_OTROS_LINKS:
            accion_listar_otros_links(videos)
        elif opcion == OPT_SIN_CUP:
            accion_videos_sin_cupones(videos, patron)
        elif opcion == OPT_COMENTARIOS:
            accion_comprobar_comentarios(youtube, videos, nuevo_bloque, patron, channel_id)

        console.print()
        console.rule(style='bright_black')
        console.input('\n  [dim]Pulsa Enter para volver al menú...[/dim]')


if __name__ == '__main__':
    main()
