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
LINKS_ESTADO_FILE  = 'links_estado.json'
EXCLUSIONES_FILE = 'exclusiones.txt'

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
        texto = page.inner_text('body').lower()
        return 'we need to check if you are a robot' in texto
    except Exception:
        return False


def esperar_si_captcha(page):
    if es_captcha(page):
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


def chequear_links_videos(videos):
    # Recopilar links por video
    video_links = []
    for video in videos:
        descripcion = video['snippet']['description']
        links = extraer_links_aliexpress(descripcion)
        if not links:
            continue
        pares = [(url, linea_con_link(descripcion, url)) for url in links]
        video_links.append((video, pares))

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
        for url, _ in pares:
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
        for url, linea in pares:
            entrada = {'video': titulo, 'video_id': vid_id, 'url': url, 'linea': linea}
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
        console.print(f'  [{estilo}]{simbolo}[/{estilo}] {item["linea"][:100]}')


def _escribir_grupo(f, items, simbolo):
    video_actual = None
    for item in items:
        if item['video'] != video_actual:
            video_actual = item['video']
            f.write(f'\n  Video: {item["video"]}\n')
            f.write(f'    https://studio.youtube.com/video/{item["video_id"]}\n')
        f.write(f'  {simbolo} {item["linea"]}\n')


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
    console.print(f'  [dim]Vídeos a modificar: [bold]{pendientes}[/bold]  ·  coste estimado: [bold]~{coste} unidades[/bold][/dim]')
    console.print(f'  [link=https://console.cloud.google.com/apis/api/youtube.googleapis.com/quotas][blue]Ver cuota disponible en Google Cloud →[/blue][/link]')
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
        console.print('[yellow]No se encontró ese link en ningún vídeo.[/yellow]')
        return
    console.print(f'  {len(afectados)} vídeo(s) afectados. Actualizando...')
    for video in afectados:
        snippet = video['snippet']
        nueva_desc = snippet['description'].replace(url_vieja, url_nueva)
        youtube.videos().update(
            part='snippet',
            body={'id': video['id'], 'snippet': snippet | {'description': nueva_desc}}
        ).execute()
        snippet['description'] = nueva_desc  # actualizar en memoria
        console.print(f'  [green]✓[/green] {snippet["title"]}')


def accion_comprobar_links(youtube, videos):
    links_unicos = {
        url
        for v in videos
        for url in extraer_links_aliexpress(v['snippet']['description'])
    }
    console.print(f'[bold]{len(links_unicos)}[/bold] links únicos de AliExpress encontrados.')
    console.print('[red]AVISO: Esto cerrará Chrome y lo abrirá en modo depuración.[/red]\n')
    if not preguntar('¿Comprobar si están activos?'):
        console.print('[yellow]Cancelado.[/yellow]')
        return
    links_rotos, links_geo = chequear_links_videos(videos)
    guardar_reporte_links(links_rotos, links_geo)

    todos_problemas = links_rotos + links_geo
    if not todos_problemas:
        return

    urls_unicas = list(dict.fromkeys(e['url'] for e in todos_problemas))
    console.print('\n[bold]Links con problemas:[/bold]\n')
    for i, url in enumerate(urls_unicas, 1):
        console.print(f'  [bold]{i}.[/bold] {url}')
        for e in todos_problemas:
            if e['url'] == url:
                console.print(f'     [dim]· {e["video"]}[/dim]')

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
        url_nueva = console.input(f'[dim]Nueva URL para reemplazar {url_vieja[:60]}...: [/dim]').strip()
        if url_nueva:
            reemplazar_link_en_videos(youtube, videos, url_vieja, url_nueva)


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


# ── Menú principal ────────────────────────────────────────────────────────────

def obtener_info_canal(youtube):
    resp = youtube.channels().list(part='snippet,statistics', mine=True).execute()
    item = resp['items'][0]
    stats = item['statistics']
    return {
        'nombre': item['snippet']['title'],
        'handle': item['snippet'].get('customUrl', ''),
        'suscriptores': int(stats.get('subscriberCount', 0)),
        'visualizaciones': int(stats.get('viewCount', 0)),
    }


def dibujar_cabecera(info_canal, n_videos, nuevo_bloque, stats=None, estado_links=None):
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
        der.add_row(Text('Links AliExpress', style='cyan bold'))
        der.add_row(Text('─' * 24, style='bright_black'))
        der.add_row('')
        rotos, geo = estado_links['rotos'], estado_links['geo']
        der.add_row(Text.assemble(('● ', 'red' if rotos else 'green'), (f'{rotos} ', 'red' if rotos else 'green'), ('rotos ', 'dim'), ('✗' if rotos else '✓', 'red' if rotos else 'green')))
        der.add_row(Text.assemble(('● ', 'yellow' if geo else 'green'), (f'{geo} ', 'yellow' if geo else 'green'), ('no disponibles en tu región', 'dim')))
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


def mostrar_menu(info_canal, n_videos, nuevo_bloque, stats=None, estado_links=None):
    console.clear()
    dibujar_cabecera(info_canal, n_videos, nuevo_bloque, stats, estado_links)
    console.print()


def main():
    if not os.path.exists(CREDENTIALS_FILE):
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
    console.print('[bold]Autenticando con YouTube...[/bold]')
    youtube = autenticar()
    console.print('[green]✓[/green] Autenticación correcta\n')

    with console.status('[bold]Obteniendo lista de vídeos del canal...[/bold]'):
        videos = obtener_todos_los_videos(youtube)
        info_canal = obtener_info_canal(youtube)
    console.print(f'[green]✓[/green] [bold]{len(videos)}[/bold] vídeos en el canal')

    OPT_CUPONES  = 'Actualizar cupones en las descripciones'
    OPT_LINKS    = 'Comprobar links de AliExpress'
    OPT_SIN_CUP  = 'Ver vídeos sin bloque de cupones'
    OPT_RESCAN   = 'Recargar vídeos del canal'
    OPT_SALIR    = 'Salir'

    def build_opciones():
        ops = []
        if nuevo_bloque:
            ops.append(OPT_CUPONES)
        ops.append(OPT_LINKS)
        if nuevo_bloque:
            ops.append(OPT_SIN_CUP)
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
        mostrar_menu(info_canal, len(videos), nuevo_bloque, calcular_stats(), cargar_estado_links())
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
            accion_comprobar_links(youtube, videos)
        elif opcion == OPT_SIN_CUP:
            accion_videos_sin_cupones(videos, patron)

        console.print()
        console.rule(style='bright_black')
        console.input('\n  [dim]Pulsa Enter para volver al menú...[/dim]')


if __name__ == '__main__':
    main()
