import atexit
import os
import re
import sys
import json
import stat
import time
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from threading import Event, Lock
from urllib.parse import urlparse
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from playwright.sync_api import sync_playwright
from prompt_toolkit.application import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, MofNCompleteColumn
from rich.table import Table
from rich.text import Text
import questionary
from prompt_toolkit.styles import Style
from questionary.prompts import common as questionary_common
from rich.rule import Rule
from rich import box

try:
    import fcntl
except ImportError:
    fcntl = None

console = Console()

questionary_common.INDICATOR_SELECTED = '✓'
questionary_common.INDICATOR_UNSELECTED = '□'

CHECKBOX_STYLE = Style([
    ('selected', 'fg:ansigreen bold'),
    ('pointer', 'fg:ansigreen bold'),
    ('highlighted', 'bold'),
    ('instruction', 'fg:ansibrightblack'),
])

CONFIRM_STYLE = Style([
    ('pointer', 'fg:ansigreen bold'),
    ('highlighted', 'bold'),
    ('selected', 'bold'),
    ('instruction', 'fg:ansibrightblack'),
])

SCOPES = ['https://www.googleapis.com/auth/youtube.force-ssl']
BASE_DIR = Path(__file__).resolve().parent
TOKEN_FILE = BASE_DIR / 'token.json'
CREDENTIALS_FILE = BASE_DIR / 'client_secret.json'
CUPONES_FILE = BASE_DIR / 'cupones.txt'
REPORTE_LINKS_FILE = BASE_DIR / 'links_rotos.txt'
LINKS_ESTADO_FILE = BASE_DIR / 'links_estado.json'
COMENTARIOS_ESTADO_FILE = BASE_DIR / 'comentarios_estado.json'
EXCLUSIONES_FILE = BASE_DIR / 'exclusiones.txt'
CACHE_VIDEOS_FILE = BASE_DIR / 'cache_videos.json'
ALIEXPRESS_COOKIES_FILE = BASE_DIR / 'aliexpress_cookies.json'
BACKUPS_DIR = BASE_DIR / 'backups'
LOCK_FILE = BASE_DIR / '.youtubecodes.lock'

MESES_ES = {
    1: 'ENERO', 2: 'FEBRERO', 3: 'MARZO', 4: 'ABRIL',
    5: 'MAYO', 6: 'JUNIO', 7: 'JULIO', 8: 'AGOSTO',
    9: 'SEPTIEMBRE', 10: 'OCTUBRE', 11: 'NOVIEMBRE', 12: 'DICIEMBRE'
}

PATRON_FECHA = r'[A-ZÁÉÍÓÚ]+\s+\d{4}'
PATRON_URL_GENERICA = r'https?://[^\s\)\]>\"\']+'

ALLOWED_ALIEXPRESS_HOSTS = {'aliexpress.com'}
ALLOWED_AMAZON_HOSTS = {
    'amazon.es',
    'amazon.com',
    'amazon.de',
    'amazon.fr',
    'amazon.it',
    'amazon.nl',
    'amazon.pl',
    'amazon.se',
    'amazon.sa',
    'amazon.ae',
    'amazon.in',
    'amazon.ca',
    'amazon.co.uk',
    'amazon.co.jp',
    'amazon.com.au',
    'amazon.com.br',
    'amazon.com.mx',
    'amazon.com.tr',
    'amazon.com.be',
    'amazon.sg',
    'amzn.to',
    'amzn.eu',
}

HEADERS_REQUESTS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

PLAYWRIGHT_HEADLESS = True
LOCK_HANDLE = None


def hay_terminal_interactiva():
    return sys.stdin.isatty() and sys.stdout.isatty()


def habilitar_escape_para_volver(question):
    try:
        bindings = question.application.key_bindings
    except AttributeError:
        return question

    @bindings.add(Keys.Escape, eager=True)
    def _(_event):
        _event.app.exit(result=None)

    return question


def habilitar_teclas_resultado(question, key_results=None):
    if not key_results:
        return question
    try:
        bindings = question.application.key_bindings
    except AttributeError:
        return question

    for tecla, resultado in key_results.items():
        @bindings.add(tecla, eager=True)
        def _(_event, resultado=resultado):
            _event.app.exit(result=resultado)

    return question


def select_menu(mensaje, choices, key_results=None, **kwargs):
    question = questionary.select(
        mensaje,
        choices=choices,
        pointer='›',
        style=CONFIRM_STYLE,
        use_shortcuts=False,
        **kwargs,
    )
    question = habilitar_escape_para_volver(question)
    question = habilitar_teclas_resultado(question, key_results=key_results)
    return question.ask()


def confirmar_menu(mensaje, default='No'):
    opciones = ['Sí', 'No']
    default = default if default in opciones else 'No'
    respuesta = select_menu(
        mensaje,
        choices=opciones,
        default=default,
    )
    return respuesta == 'Sí'


def mostrar_atajos_menu_principal(offline=False):
    texto = Text()
    texto.append('Atajos: ', style='bold white')
    if not offline:
        texto.append('R', style='bold green')
        texto.append(' recarga vídeos   ', style='white')
    texto.append('Esc', style='bold yellow')
    texto.append(' sale', style='white')
    console.print(Panel.fit(texto, border_style='bright_black', padding=(0, 2)))


def checkbox_menu(mensaje, choices, **kwargs):
    return habilitar_escape_para_volver(questionary.checkbox(
        mensaje,
        choices=choices,
        pointer='›',
        instruction='(Espacio marca/desmarca, Enter confirma, Esc vuelve, Ctrl+C cancela)',
        style=CHECKBOX_STYLE,
        **kwargs,
    )).ask()


def _path(value):
    return value if isinstance(value, Path) else Path(value)


def _display_path(value):
    path = _path(value)
    try:
        return path.relative_to(BASE_DIR)
    except ValueError:
        return path


def escribir_texto_atomico(path, contenido):
    path = _path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent, text=True)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(contenido)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        asegurar_permisos_privados(path)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def escribir_json_atomico(path, data, *, ensure_ascii=False, indent=None):
    contenido = json.dumps(data, ensure_ascii=ensure_ascii, indent=indent)
    escribir_texto_atomico(path, contenido)


def leer_json_seguro(path, default=None, *, contexto='JSON'):
    path = _path(path)
    if not path.exists():
        return default
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        console.print(
            f'[yellow]AVISO: no se pudo leer {contexto} en "{_display_path(path)}": {exc}. '
            'Se ignorará y se regenerará si hace falta.[/yellow]'
        )
        return default


def generar_nombre_backup(tipo, item_id):
    sello = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    item_seguro = re.sub(r'[^A-Za-z0-9_.-]+', '_', str(item_id)).strip('_') or 'item'
    return BACKUPS_DIR / f'{sello}-{tipo}-{item_seguro}.json'


def guardar_backup(tipo, item_id, payload):
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    path = generar_nombre_backup(tipo, item_id)
    escribir_json_atomico(path, payload, ensure_ascii=False, indent=2)
    return path


def bloquear_instancia():
    global LOCK_HANDLE
    if LOCK_HANDLE is not None:
        return
    if fcntl is None:
        return
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    handle = open(LOCK_FILE, 'a+', encoding='utf-8')
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        try:
            handle.close()
        except OSError:
            pass
        console.print('[red]ERROR: Ya hay otra instancia de YouTubeCodes ejecutándose.[/red]')
        raise SystemExit(1)
    handle.seek(0)
    handle.truncate()
    handle.write(f'{os.getpid()}\n')
    handle.flush()
    LOCK_HANDLE = handle


def liberar_bloqueo():
    global LOCK_HANDLE
    if LOCK_HANDLE is None:
        return
    try:
        if fcntl is not None:
            fcntl.flock(LOCK_HANDLE.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        LOCK_HANDLE.close()
    except OSError:
        pass
    LOCK_HANDLE = None


def asegurar_permisos_privados(path):
    path = _path(path)
    if sys.platform == 'win32' or not path.exists():
        return
    try:
        modo = (
            stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR
            if path.is_dir()
            else stat.S_IRUSR | stat.S_IWUSR
        )
        os.chmod(path, modo)
    except OSError:
        pass


def asegurar_permisos_locales():
    for path in (
        CREDENTIALS_FILE,
        TOKEN_FILE,
        ALIEXPRESS_COOKIES_FILE,
        CACHE_VIDEOS_FILE,
        COMENTARIOS_ESTADO_FILE,
        LINKS_ESTADO_FILE,
        REPORTE_LINKS_FILE,
        CUPONES_FILE,
        EXCLUSIONES_FILE,
        BACKUPS_DIR,
    ):
        asegurar_permisos_privados(path)


def cerrar_sin_error(recurso):
    if recurso is None:
        return
    try:
        recurso.close()
    except Exception:
        pass


def actualizar_tarea(progress, task_id, progress_lock=None, advance=False, **kwargs):
    if progress is None or task_id is None:
        return
    if progress_lock is not None:
        with progress_lock:
            if kwargs:
                progress.update(task_id, **kwargs)
            if advance:
                progress.advance(task_id)
        return
    if kwargs:
        progress.update(task_id, **kwargs)
    if advance:
        progress.advance(task_id)


def compilar_patron_si_hace_falta(patron):
    return patron if hasattr(patron, 'search') else re.compile(patron, re.MULTILINE | re.DOTALL)


def normalizar_resultado_worker(nombre, resultado):
    if nombre == 'Amazon':
        if isinstance(resultado, dict):
            return {
                'rotos': resultado.get('rotos', []),
                'errores': resultado.get('errores', []),
                'advertencias': resultado.get('advertencias', []),
            }
        if isinstance(resultado, list):
            return {'rotos': resultado, 'errores': [], 'advertencias': []}
    else:
        if isinstance(resultado, dict):
            return {
                'rotos': resultado.get('rotos', []),
                'geo': resultado.get('geo', []),
                'captcha': resultado.get('captcha', []),
                'errores': resultado.get('errores', []),
                'errores_lectura': resultado.get('errores_lectura', []),
                'advertencias': resultado.get('advertencias', []),
            }
        if isinstance(resultado, tuple):
            if len(resultado) == 3:
                rotos, geo, captcha = resultado
                return {'rotos': rotos, 'geo': geo, 'captcha': captcha, 'errores': [], 'errores_lectura': [], 'advertencias': []}
            if len(resultado) == 4:
                rotos, geo, captcha, errores = resultado
                return {'rotos': rotos, 'geo': geo, 'captcha': captcha, 'errores': errores, 'errores_lectura': [], 'advertencias': []}
    raise TypeError(f'Resultado inesperado de worker {nombre}: {type(resultado).__name__}')


def esperar_volver_menu():
    bindings = KeyBindings()

    @bindings.add(Keys.Enter, eager=True)
    @bindings.add(Keys.Escape, eager=True)
    def _confirmar(event):
        event.app.exit(result=None)

    @bindings.add(Keys.ControlC, eager=True)
    def _cancelar(event):
        event.app.exit(exception=KeyboardInterrupt)

    app = Application(
        layout=Layout(Window(height=1, content=FormattedTextControl(''))),
        key_bindings=bindings,
        full_screen=False,
    )

    try:
        app.run()
    except KeyboardInterrupt:
        pass


def es_url_http_valida(url):
    parsed = urlparse(url)
    return parsed.scheme in ('http', 'https') and bool(parsed.netloc)


def normalizar_hostname(url):
    return (urlparse(url).hostname or '').lower().rstrip('.')


def es_host_aliexpress(hostname):
    return hostname == 'aliexpress.com' or any(hostname.endswith(f'.{base}') for base in ALLOWED_ALIEXPRESS_HOSTS)


def es_host_amazon(hostname):
    raiz = hostname[4:] if hostname.startswith('www.') else hostname
    return raiz in ALLOWED_AMAZON_HOSTS


def es_url_aliexpress(url):
    return es_url_http_valida(url) and es_host_aliexpress(normalizar_hostname(url))


def es_url_amazon(url):
    return es_url_http_valida(url) and es_host_amazon(normalizar_hostname(url))


def limpiar_url_extraida(url):
    return url.rstrip('.,;:')


def extraer_links_filtrados(descripcion, validador):
    vistos = set()
    links = []
    for candidato in re.findall(PATRON_URL_GENERICA, descripcion):
        url = limpiar_url_extraida(candidato)
        if validador(url) and url not in vistos:
            vistos.add(url)
            links.append(url)
    return links


def reemplazar_url_exacta(texto, url_vieja, url_nueva):
    reemplazos = 0

    def _repl(match):
        nonlocal reemplazos
        bruto = match.group(0)
        limpio = limpiar_url_extraida(bruto)
        sufijo = bruto[len(limpio):]
        if limpio != url_vieja:
            return bruto
        reemplazos += 1
        return url_nueva + sufijo

    nuevo_texto = re.sub(PATRON_URL_GENERICA, _repl, texto)
    return nuevo_texto, reemplazos


def lanzar_navegador_aislado(playwright, cookies=None, headless=PLAYWRIGHT_HEADLESS):
    browser = playwright.chromium.launch(
        headless=headless,
        args=[
            '--no-first-run',
            '--disable-background-networking',
            '--disable-breakpad',
            '--disable-crash-reporter',
        ],
    )
    context = browser.new_context(
        user_agent=HEADERS_REQUESTS['User-Agent'],
        locale='es-ES',
        viewport={'width': 1366, 'height': 900},
    )
    if cookies:
        context.add_cookies(cookies)
    return browser, context


def añadir_fecha_si_falta(nuevo_bloque):
    lineas = nuevo_bloque.splitlines()
    if not lineas:
        return nuevo_bloque
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


def construir_regex_primera_linea(linea):
    match = re.search(PATRON_FECHA, linea)
    if match:
        antes = re.escape(linea[:match.start()])
        despues = re.escape(linea[match.end():])
        return antes + PATRON_FECHA + despues
    if linea.endswith('*'):
        return re.escape(linea[:-1]) + rf'(?:\s*\({PATRON_FECHA}\))?\*'
    return re.escape(linea) + rf'(?:\s*\({PATRON_FECHA}\))?'


def construir_patron(nuevo_bloque_original):
    lineas = nuevo_bloque_original.splitlines()
    if not lineas:
        raise ValueError('El bloque de cupones está vacío.')
    partes = [construir_regex_primera_linea(lineas[0])]
    partes.extend(re.escape(linea) for linea in lineas[1:])
    cuerpo = r'(?:\r?\n)'.join(rf'[ \t]*{parte}[ \t]*' for parte in partes)
    return re.compile(cuerpo, re.MULTILINE)


def autenticar():
    creds = None

    if os.path.exists(TOKEN_FILE):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            console.print(
                f'[yellow]AVISO: token OAuth inválido en "{_display_path(TOKEN_FILE)}": {exc}. '
                'Se regenerará al autenticar.[/yellow]'
            )
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        escribir_texto_atomico(TOKEN_FILE, creds.to_json())

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
    return extraer_links_filtrados(descripcion, es_url_aliexpress)


def extraer_links_amazon(descripcion):
    return extraer_links_filtrados(descripcion, es_url_amazon)


def comprobar_link_amazon_navegador(page, url):
    """Comprobación vía navegador aislado: ok si hay botón de compra."""
    if not es_url_amazon(url):
        return 'roto'
    try:
        page.goto(url, wait_until='domcontentloaded', timeout=20000)
        page.wait_for_timeout(2000)
        texto = page.inner_text('body').lower()
        titulo = (page.title() or '').strip().lower()
        if any(ind in texto for ind in (
            'enter the characters you see below',
            'introduce los caracteres que ves a continuación',
        )):
            return 'error'
        if any(ind in texto for ind in (
            'lo sentimos. no hemos podido encontrar esa página',
            "sorry! we couldn't find that page",
            'dogs of amazon',
            'currently unavailable',
            'actualmente no disponible',
        )):
            return 'roto'
        if not titulo or titulo in ('amazon.es', 'amazon.com', 'amazon'):
            return 'error'
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
    except KeyboardInterrupt:
        raise
    except Exception:
        return 'error'


def linea_con_link(descripcion, url):
    for linea in descripcion.splitlines():
        if url in linea:
            return linea.strip()
    return url


def obtener_comentario_del_canal(youtube, video_id, channel_id, max_pages=2, max_results=100):
    next_page_token = None
    for _ in range(max_pages):
        resp = youtube.commentThreads().list(
            part='snippet',
            videoId=video_id,
            maxResults=max_results,
            order='relevance',
            pageToken=next_page_token,
        ).execute()
        for thread in resp.get('items', []):
            top_level = thread.get('snippet', {}).get('topLevelComment', {})
            snippet = top_level.get('snippet', {})
            autor_id = snippet.get('authorChannelId', {}).get('value', '')
            if autor_id != channel_id:
                continue
            return {
                'comment_id': top_level.get('id'),
                'texto': snippet.get('textOriginal', '') or snippet.get('textDisplay', ''),
            }
        next_page_token = resp.get('nextPageToken')
        if not next_page_token:
            break
    return None


def cargar_cookies_aliexpress():
    if not os.path.exists(ALIEXPRESS_COOKIES_FILE):
        return []
    asegurar_permisos_privados(ALIEXPRESS_COOKIES_FILE)
    raw = leer_json_seguro(ALIEXPRESS_COOKIES_FILE, default=[], contexto='cookies de AliExpress') or []
    cookies = []
    for cookie in raw:
        if not isinstance(cookie, dict) or 'name' not in cookie or 'value' not in cookie:
            continue
        cookies.append({
            'name': cookie['name'],
            'value': cookie['value'],
            'domain': cookie.get('domain', '.aliexpress.com'),
            'path': cookie.get('path', '/'),
        })
    return cookies


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


def comprobar_link_aliexpress_navegador(page, url):
    """Devuelve 'ok', 'roto', 'geo', 'captcha' o 'error'."""
    if not es_url_aliexpress(url):
        return 'roto'
    try:
        page.goto(url, wait_until='domcontentloaded', timeout=20000)
        page.wait_for_timeout(3000)
        if es_captcha(page):
            return 'captcha'
        titulo = (page.title() or '').lower()
        texto = page.inner_text('body').lower()
        if any(ind in texto or ind in titulo for ind in (
            'no está disponible en tu país',
            'not available in your country',
            'this product can\'t be shipped to your selected country',
            'no se puede enviar al país seleccionado',
        )):
            return 'geo'
        if any(ind in texto or ind in titulo for ind in (
            'sorry, this item is no longer available',
            'este artículo ya no está disponible',
            'page not found',
            'producto no encontrado',
            'item unavailable',
        )):
            return 'roto'
        if titulo and 'aliexpress' in titulo:
            return 'ok'
        return 'error'
    except KeyboardInterrupt:
        raise
    except Exception:
        return 'error'


def construir_video_links_descripcion(videos, extractor):
    video_links = []
    for video in videos:
        descripcion = video['snippet']['description']
        links = extractor(descripcion)
        if links:
            pares = [('descripcion', url, linea_con_link(descripcion, url), None, None) for url in links]
            video_links.append((video, pares))
    return video_links


def crear_entrada_link(video, tipo, url, linea, comment_id=None, texto_completo=None, **extra):
    entrada = {
        'video': video['snippet']['title'],
        'video_id': video['id'],
        'url': url,
        'linea': linea,
        'tipo': tipo,
        'comment_id': comment_id,
        'texto_completo': texto_completo,
    }
    entrada.update(extra)
    return entrada


def contar_urls_unicas(video_links):
    seen = set()
    total = 0
    for _, pares in video_links:
        for _, url, _, _, _ in pares:
            if url not in seen:
                seen.add(url)
                total += 1
    return total


def chequear_links_amazon(video_links, stop_event=None, mostrar_progreso=True, progress=None, task_id=None, progress_lock=None):
    if not video_links:
        if progress is not None and task_id is not None:
            actualizar_tarea(progress, task_id, progress_lock, total=1, completed=1, detalle='sin enlaces')
        return {'rotos': [], 'errores': [], 'advertencias': []}

    seen = set()
    urls_orden = []
    for _, pares in video_links:
        for _, url, _, _, _ in pares:
            if url not in seen:
                seen.add(url)
                urls_orden.append(url)

    cache_amz = {}
    error_global = None
    try:
        with sync_playwright() as playwright_amz:
            browser_amz = context_amz = page_amz = None
            try:
                browser_amz, context_amz = lanzar_navegador_aislado(playwright_amz, headless=True)
                page_amz = context_amz.new_page()
                if progress is not None and task_id is not None:
                    actualizar_tarea(progress, task_id, progress_lock, total=len(urls_orden), completed=0, detalle='')
                    for url in urls_orden:
                        if stop_event is not None and stop_event.is_set():
                            raise KeyboardInterrupt
                        actualizar_tarea(progress, task_id, progress_lock, detalle=url[:70])
                        cache_amz[url] = comprobar_link_amazon_navegador(page_amz, url)
                        actualizar_tarea(progress, task_id, progress_lock, advance=True)
                elif mostrar_progreso:
                    with Progress(
                        SpinnerColumn(),
                        TextColumn('[progress.description]{task.description}'),
                        BarColumn(),
                        MofNCompleteColumn(),
                        TextColumn('[dim]{task.fields[url]}[/dim]'),
                        console=console,
                    ) as progress:
                        task = progress.add_task('Comprobando Amazon', total=len(urls_orden), url='')
                        for url in urls_orden:
                            if stop_event is not None and stop_event.is_set():
                                raise KeyboardInterrupt
                            progress.update(task, url=url[:70])
                            cache_amz[url] = comprobar_link_amazon_navegador(page_amz, url)
                            progress.advance(task)
                else:
                    for url in urls_orden:
                        if stop_event is not None and stop_event.is_set():
                            raise KeyboardInterrupt
                        cache_amz[url] = comprobar_link_amazon_navegador(page_amz, url)
            finally:
                cerrar_sin_error(page_amz)
                cerrar_sin_error(context_amz)
                cerrar_sin_error(browser_amz)
    except Exception as e:
        if stop_event is not None and stop_event.is_set():
            raise KeyboardInterrupt from None
        error_global = str(e)
        console.print(f'[red]Error comprobando Amazon: {e}[/red]')

    links_amazon_rotos = []
    links_amazon_error = []
    for video, pares in video_links:
        for tipo, url, linea, comment_id, texto_completo in pares:
            estado = cache_amz.get(url, 'error' if error_global else 'ok')
            if estado in ('roto', 'sin_stock'):
                links_amazon_rotos.append(crear_entrada_link(
                    video,
                    tipo,
                    url,
                    linea,
                    comment_id,
                    texto_completo,
                    tienda='amazon',
                    estado_detalle='sin_stock' if estado == 'sin_stock' else None,
                ))
            elif estado == 'error':
                links_amazon_error.append(crear_entrada_link(
                    video,
                    tipo,
                    url,
                    linea,
                    comment_id,
                    texto_completo,
                    tienda='amazon',
                    estado_detalle='error_tecnico',
                    detalle_error=error_global,
                ))
    return {'rotos': links_amazon_rotos, 'errores': links_amazon_error, 'advertencias': []}


def obtener_links_aliexpress_comentarios(
    youtube,
    videos,
    channel_id,
    stop_event=None,
    mostrar_progreso=True,
    progress=None,
    task_id=None,
    progress_lock=None,
):
    video_links = []
    errores_lectura = []
    total = len(videos)

    if progress is not None and task_id is not None:
        actualizar_tarea(progress, task_id, progress_lock, total=total, completed=0, detalle='')
        for video in videos:
            if stop_event is not None and stop_event.is_set():
                raise KeyboardInterrupt
            actualizar_tarea(progress, task_id, progress_lock, detalle=video['snippet']['title'][:60])
            try:
                comentario = obtener_comentario_del_canal(youtube, video['id'], channel_id)
                if comentario:
                    texto = comentario['texto']
                    comment_id = comentario['comment_id']
                    links_com = extraer_links_aliexpress(texto)
                    if links_com:
                        pares = [('comentario', url, linea_con_link(texto, url), comment_id, texto)
                                 for url in links_com]
                        video_links.append((video, pares))
            except Exception as exc:
                errores_lectura.append({'video': video['snippet']['title'], 'video_id': video['id'], 'error': str(exc)})
            actualizar_tarea(progress, task_id, progress_lock, advance=True)
        return video_links, errores_lectura

    if mostrar_progreso:
        with Progress(
            SpinnerColumn(),
            TextColumn('[progress.description]{task.description}'),
            BarColumn(),
            MofNCompleteColumn(),
            TextColumn('[dim]{task.fields[titulo]}[/dim]'),
            console=console,
        ) as progress:
            task = progress.add_task('Obteniendo comentarios', total=total, titulo='')
            for video in videos:
                if stop_event is not None and stop_event.is_set():
                    raise KeyboardInterrupt
                progress.update(task, titulo=video['snippet']['title'][:60])
                try:
                    comentario = obtener_comentario_del_canal(youtube, video['id'], channel_id)
                    if comentario:
                        texto = comentario['texto']
                        comment_id = comentario['comment_id']
                        links_com = extraer_links_aliexpress(texto)
                        if links_com:
                            pares = [('comentario', url, linea_con_link(texto, url), comment_id, texto)
                                     for url in links_com]
                            video_links.append((video, pares))
                except Exception as exc:
                    errores_lectura.append({'video': video['snippet']['title'], 'video_id': video['id'], 'error': str(exc)})
                progress.advance(task)
        return video_links, errores_lectura

    for video in videos:
        if stop_event is not None and stop_event.is_set():
            raise KeyboardInterrupt
        try:
            comentario = obtener_comentario_del_canal(youtube, video['id'], channel_id)
            if comentario:
                texto = comentario['texto']
                comment_id = comentario['comment_id']
                links_com = extraer_links_aliexpress(texto)
                if links_com:
                    pares = [('comentario', url, linea_con_link(texto, url), comment_id, texto)
                             for url in links_com]
                    video_links.append((video, pares))
        except Exception as exc:
            errores_lectura.append({'video': video['snippet']['title'], 'video_id': video['id'], 'error': str(exc)})
    return video_links, errores_lectura


def comprobar_fuentes_aliexpress(
    youtube,
    videos,
    channel_id=None,
    stop_event=None,
    mostrar_progreso=True,
    links_progress=None,
    links_task_id=None,
    progress_lock=None,
):
    video_links = construir_video_links_descripcion(videos, extraer_links_aliexpress)
    if not video_links:
        if links_progress is not None and links_task_id is not None:
            actualizar_tarea(links_progress, links_task_id, progress_lock, total=1, completed=1, detalle='sin enlaces')
        return {'rotos': [], 'geo': [], 'captcha': [], 'errores': [], 'errores_lectura': [], 'advertencias': []}
    return chequear_links_videos(
        video_links,
        stop_event=stop_event,
        mostrar_progreso=mostrar_progreso,
        progress=links_progress,
        task_id=links_task_id,
        progress_lock=progress_lock,
    )


def comprobar_links_aliexpress_en_comentarios(
    youtube,
    videos,
    channel_id,
    stop_event=None,
    mostrar_progreso=True,
    progress=None,
    task_id=None,
    progress_lock=None,
):
    video_links, errores_lectura = obtener_links_aliexpress_comentarios(
        youtube,
        videos,
        channel_id,
        stop_event=stop_event,
        mostrar_progreso=mostrar_progreso,
        progress=progress,
        task_id=task_id,
        progress_lock=progress_lock,
    )
    if not video_links:
        actualizar_tarea(progress, task_id, progress_lock, detalle='sin enlaces')
        return {'rotos': [], 'geo': [], 'captcha': [], 'errores': [], 'errores_lectura': errores_lectura, 'advertencias': []}
    resultado = chequear_links_videos(
        video_links,
        stop_event=stop_event,
        mostrar_progreso=mostrar_progreso,
        progress=progress,
        task_id=task_id,
        progress_base_total=len(videos),
        progress_base_completed=len(videos),
        progress_lock=progress_lock,
    )
    resultado['errores_lectura'] = errores_lectura
    return resultado


def chequear_links_videos(
    video_links,
    stop_event=None,
    mostrar_progreso=True,
    progress=None,
    task_id=None,
    progress_base_total=0,
    progress_base_completed=0,
    progress_lock=None,
):
    """video_links: [(video, [(tipo, url, linea, comment_id, texto_completo), ...]), ...]"""
    if not video_links:
        console.print('[yellow]No se encontraron links de AliExpress.[/yellow]')
        if progress is not None and task_id is not None:
            actualizar_tarea(progress, task_id, progress_lock, total=1, completed=1, detalle='sin enlaces')
        return {'rotos': [], 'geo': [], 'captcha': [], 'errores': [], 'advertencias': []}

    cookies = cargar_cookies_aliexpress()
    if not cookies:
        console.print('[yellow]AVISO: aliexpress_cookies.json no encontrado. Saltando verificación de links.[/yellow]')
        return {
            'rotos': [],
            'geo': [],
            'captcha': [],
            'errores': [],
            'advertencias': ['AliExpress no se pudo verificar porque faltan cookies válidas.'],
        }

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
    links_captcha = []
    links_error = []
    error_global = None

    try:
        with sync_playwright() as playwright:
            browser = context = page = None
            browser, context = lanzar_navegador_aislado(playwright, cookies=cookies, headless=True)
            try:
                page = context.new_page()
                if progress is not None and task_id is not None:
                    actualizar_tarea(
                        progress,
                        task_id,
                        progress_lock,
                        total=progress_base_total + len(urls_orden),
                        completed=progress_base_completed,
                        detalle='',
                    )
                    for url in urls_orden:
                        if stop_event is not None and stop_event.is_set():
                            raise KeyboardInterrupt
                        actualizar_tarea(progress, task_id, progress_lock, detalle=url[:70])
                        cache[url] = comprobar_link_aliexpress_navegador(page, url)
                        actualizar_tarea(progress, task_id, progress_lock, advance=True)
                elif mostrar_progreso:
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
                            if stop_event is not None and stop_event.is_set():
                                raise KeyboardInterrupt
                            progress.update(task, url=url[:70])
                            cache[url] = comprobar_link_aliexpress_navegador(page, url)
                            progress.advance(task)
                else:
                    for url in urls_orden:
                        if stop_event is not None and stop_event.is_set():
                            raise KeyboardInterrupt
                        cache[url] = comprobar_link_aliexpress_navegador(page, url)
            finally:
                cerrar_sin_error(page)
                cerrar_sin_error(context)
                cerrar_sin_error(browser)

            urls_captcha = [url for url, estado in cache.items() if estado == 'captcha']
            if stop_event is not None and stop_event.is_set():
                raise KeyboardInterrupt
            if urls_captcha and mostrar_progreso and hay_terminal_interactiva() and confirmar_menu(
                f'Se detectó verificación de AliExpress en {len(urls_captcha)} link{"s" if len(urls_captcha) != 1 else ""}. ¿Reintentar en una ventana aislada?',
                default='No',
            ):
                browser = context = page = None
                browser, context = lanzar_navegador_aislado(playwright, cookies=cookies, headless=False)
                try:
                    page = context.new_page()
                    if mostrar_progreso:
                        with Progress(
                            SpinnerColumn(),
                            TextColumn('[progress.description]{task.description}'),
                            BarColumn(),
                            MofNCompleteColumn(),
                            TextColumn('[dim]{task.fields[url]}[/dim]'),
                            console=console,
                        ) as progress:
                            task = progress.add_task('Reintentando links con verificación', total=len(urls_captcha), url='')
                            for url in urls_captcha:
                                if stop_event is not None and stop_event.is_set():
                                    raise KeyboardInterrupt
                                progress.update(task, url=url[:70])
                                cache[url] = comprobar_link_aliexpress_navegador(page, url)
                                progress.advance(task)
                    else:
                        for url in urls_captcha:
                            if stop_event is not None and stop_event.is_set():
                                raise KeyboardInterrupt
                            cache[url] = comprobar_link_aliexpress_navegador(page, url)
                finally:
                    cerrar_sin_error(page)
                    cerrar_sin_error(context)
                    cerrar_sin_error(browser)
    except Exception as e:
        if stop_event is not None and stop_event.is_set():
            raise KeyboardInterrupt from None
        error_global = str(e)
        console.print(f'[red]AVISO: No se pudo comprobar AliExpress con Playwright: {e}[/red]')
        cache = {url: 'error' for url in urls_orden}

    for video, pares in video_links:
        for tipo, url, linea, comment_id, texto_completo in pares:
            entrada = crear_entrada_link(
                video,
                tipo,
                url,
                linea,
                comment_id,
                texto_completo,
                tienda='aliexpress',
            )
            if cache[url] == 'roto':
                links_rotos.append(entrada)
            elif cache[url] == 'geo':
                links_geo.append(entrada)
            elif cache[url] == 'captcha':
                links_captcha.append(entrada)
            elif cache[url] == 'error':
                entrada['estado_detalle'] = 'error_tecnico'
                entrada['detalle_error'] = error_global
                links_error.append(entrada)

    return {
        'rotos': links_rotos,
        'geo': links_geo,
        'captcha': links_captcha,
        'errores': links_error,
        'advertencias': [],
    }


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
    return leer_json_seguro(LINKS_ESTADO_FILE, default=None, contexto='estado de links')


def guardar_estado_links(links_rotos, links_geo, links_captcha=None, links_error=None, comentarios_error=None):
    links_captcha = links_captcha or []
    links_error = links_error or []
    comentarios_error = comentarios_error or []
    escribir_json_atomico(
        LINKS_ESTADO_FILE,
        {
            'rotos': len({e['url'] for e in links_rotos}),
            'geo': len({e['url'] for e in links_geo}),
            'captcha': len({e['url'] for e in links_captcha}),
            'errores': len({e['url'] for e in links_error}),
            'comentarios_error': len({e['video_id'] for e in comentarios_error}),
            'fecha': datetime.now().strftime('%d/%m/%Y %H:%M'),
        },
    )


def cargar_estado_comentarios():
    return leer_json_seguro(COMENTARIOS_ESTADO_FILE, default=None, contexto='estado de comentarios')


def guardar_cache_videos(videos, info_canal):
    escribir_json_atomico(
        CACHE_VIDEOS_FILE,
        {
            'videos': videos,
            'info_canal': info_canal,
            'fecha': datetime.now().strftime('%d/%m/%Y %H:%M'),
        },
        ensure_ascii=False,
    )


def cargar_cache_videos():
    data = leer_json_seguro(CACHE_VIDEOS_FILE, default=None, contexto='caché de vídeos')
    if not data:
        return None, None, None
    return data['videos'], data['info_canal'], data.get('fecha', '?')


def guardar_estado_comentarios(actualizados, sin_actualizar, sin_cupones, errores=0):
    escribir_json_atomico(
        COMENTARIOS_ESTADO_FILE,
        {
            'actualizados': actualizados,
            'sin_actualizar': sin_actualizar,
            'sin_cupones': sin_cupones,
            'errores': errores,
            'fecha': datetime.now().strftime('%d/%m/%Y %H:%M'),
        },
    )


def guardar_reporte_links(links_rotos, links_geo, links_captcha=None, links_error=None, comentarios_error=None, advertencias=None):
    links_captcha = links_captcha or []
    links_error = links_error or []
    comentarios_error = comentarios_error or []
    advertencias = advertencias or []
    guardar_estado_links(links_rotos, links_geo, links_captcha, links_error, comentarios_error)
    console.rule()
    if not links_rotos and not links_geo and not links_captcha and not links_error and not comentarios_error and not advertencias:
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

    if links_captcha:
        console.print(f'\n[yellow bold]¿ VERIFICACIÓN / CAPTCHA ({len(links_captcha)}):[/yellow bold]')
        console.rule(style='yellow')
        _imprimir_grupo(links_captcha, '?', 'yellow')

    if links_error:
        console.print(f'\n[red bold]‼ NO VERIFICADOS POR ERROR TÉCNICO ({len(links_error)}):[/red bold]')
        console.rule(style='red')
        _imprimir_grupo(links_error, '!', 'red')

    if comentarios_error:
        console.print(f'\n[yellow bold]‼ COMENTARIOS NO VERIFICADOS ({len(comentarios_error)}):[/yellow bold]')
        console.rule(style='yellow')
        for item in comentarios_error:
            console.print(f'  [yellow]![/yellow] {item["video"]}')
            console.print(f'  [dim]https://studio.youtube.com/video/{item["video_id"]}[/dim]')

    if advertencias:
        console.print(f'\n[yellow bold]‼ ESCANEO INCOMPLETO ({len(advertencias)}):[/yellow bold]')
        console.rule(style='yellow')
        for advertencia in advertencias:
            console.print(f'  [yellow]![/yellow] {advertencia}')

    lineas = [f'Reporte de links — {datetime.now().strftime("%d/%m/%Y %H:%M")}', '=' * 60]
    if links_rotos:
        lineas.extend(['', f'ELIMINADOS / NO PROMOCIONABLES ({len(links_rotos)}):', '-' * 60])
    contenido = '\n'.join(lineas) + '\n'
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', delete=False, dir=REPORTE_LINKS_FILE.parent) as tmp:
        tmp.write(contenido)
        if links_rotos:
            _escribir_grupo(tmp, links_rotos, '✗')
        if links_geo:
            tmp.write(f'\nNO DISPONIBLES EN TU REGIÓN ({len(links_geo)}):\n')
            tmp.write('-' * 60 + '\n')
            _escribir_grupo(tmp, links_geo, '⚠')
        if links_captcha:
            tmp.write(f'\nPENDIENTES DE VERIFICACIÓN / CAPTCHA ({len(links_captcha)}):\n')
            tmp.write('-' * 60 + '\n')
            _escribir_grupo(tmp, links_captcha, '?')
        if links_error:
            tmp.write(f'\nNO VERIFICADOS POR ERROR TÉCNICO ({len(links_error)}):\n')
            tmp.write('-' * 60 + '\n')
            _escribir_grupo(tmp, links_error, '!')
        if comentarios_error:
            tmp.write(f'\nCOMENTARIOS NO VERIFICADOS ({len(comentarios_error)}):\n')
            tmp.write('-' * 60 + '\n')
            for item in comentarios_error:
                tmp.write(f'  Video: {item["video"]}\n')
                tmp.write(f'    https://studio.youtube.com/video/{item["video_id"]}\n')
                if item.get('error'):
                    tmp.write(f'    Error: {item["error"]}\n')
        if advertencias:
            tmp.write(f'\nESCANEO INCOMPLETO ({len(advertencias)}):\n')
            tmp.write('-' * 60 + '\n')
            for advertencia in advertencias:
                tmp.write(f'  {advertencia}\n')
    os.replace(tmp.name, REPORTE_LINKS_FILE)
    asegurar_permisos_privados(REPORTE_LINKS_FILE)

    console.print(f'\n[dim]Reporte guardado en "{_display_path(REPORTE_LINKS_FILE)}"[/dim]')


def buscar_videos_con_cupones(videos, patron):
    patron = compilar_patron_si_hace_falta(patron)
    encontrados = []
    for video in videos:
        descripcion = video['snippet']['description']
        if patron.search(descripcion):
            encontrados.append(video)
    return encontrados


def actualizar_video(youtube, video, nuevo_bloque, patron):
    patron = compilar_patron_si_hace_falta(patron)
    snippet = video['snippet']
    descripcion_original = snippet['description']
    coincidencias = list(patron.finditer(descripcion_original))
    if not coincidencias:
        return 'sin_cambios'
    if len(coincidencias) > 1:
        return 'match_ambiguo'

    nueva_descripcion = patron.sub(nuevo_bloque, descripcion_original, count=1)

    if len(nueva_descripcion) > 5000:
        return 'demasiado_larga'

    guardar_backup('video', video['id'], {
        'video_id': video['id'],
        'titulo': snippet.get('title', ''),
        'descripcion_original': descripcion_original,
        'descripcion_nueva': nueva_descripcion,
    })
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
    console.print(f'  [dim]Vídeos a modificar: [bold]{pendientes}[/bold]  ·  coste estimado: [/dim][bold red]~{coste} unidades[/bold red]')
    console.print('  📊 [dim]Cuota:[/dim] [cyan]https://console.cloud.google.com/apis/api/youtube.googleapis.com/quotas[/cyan]')
    console.print()
    if pendientes == 0:
        console.print('[green]✓ No hay vídeos pendientes: el bloque ya está actualizado.[/green]')
        return
    if not confirmar_menu(f'¿Actualizar {pendientes} vídeo{"s" if pendientes != 1 else ""}?'):
        console.print('[yellow]Cancelado.[/yellow]')
        return

    console.print()
    actualizados = sin_cambios = omitidos = ambiguos = 0
    omitidos_lista = []
    ambiguos_lista = []

    with Progress(
        SpinnerColumn(),
        TextColumn('[progress.description]{task.description}'),
        BarColumn(),
        MofNCompleteColumn(),
        TextColumn('[dim]{task.fields[titulo]}[/dim]'),
        console=console,
    ) as progress:
        task = progress.add_task('Actualizando', total=pendientes, titulo='')
        for video in pendientes_list:
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
            elif resultado == 'match_ambiguo':
                ambiguos += 1
                ambiguos_lista.append((titulo, video['id']))
                progress.update(task, titulo=titulo[:60])
                progress.advance(task)

    console.print()
    console.rule()
    sin_match = len(videos) - len(videos_con_cupones)
    console.print(f'Total: [bold]{len(videos)}[/bold]  ·  Sin cupones: {sin_match}  ·  Con cupones: {len(videos_con_cupones)}')
    console.print(f'[green]✓[/green] Actualizados: [bold green]{actualizados}[/bold green]  ·  Sin cambios: {sin_cambios}', end='')
    if omitidos:
        console.print(f'  ·  [red]Omitidos por longitud: {omitidos}[/red]', end='')
    if ambiguos:
        console.print(f'  ·  [yellow]Bloque ambiguo: {ambiguos}[/yellow]')
    elif not omitidos:
        console.print()
    else:
        console.print()
    for titulo, vid_id in omitidos_lista:
        console.print(f'  [red]  · {titulo}[/red]')
        console.print(f'    [link=https://studio.youtube.com/video/{vid_id}][blue]https://studio.youtube.com/video/{vid_id}[/blue][/link]')
    if ambiguos_lista:
        for titulo, vid_id in ambiguos_lista:
            console.print(f'  [yellow]  · {titulo}[/yellow]')
            console.print(f'    [link=https://studio.youtube.com/video/{vid_id}][blue]https://studio.youtube.com/video/{vid_id}[/blue][/link]')


def reemplazar_link_en_videos(youtube, videos, url_vieja, url_nueva):
    afectados = [v for v in videos if url_vieja in v['snippet']['description']]
    if not afectados:
        return
    console.print(f'  {len(afectados)} descripción(es) afectadas. Actualizando...')
    for video in afectados:
        snippet = video['snippet']
        nueva_desc, reemplazos = reemplazar_url_exacta(snippet['description'], url_vieja, url_nueva)
        if not reemplazos:
            continue
        guardar_backup('video-link', video['id'], {
            'video_id': video['id'],
            'titulo': snippet.get('title', ''),
            'descripcion_original': snippet['description'],
            'descripcion_nueva': nueva_desc,
            'url_vieja': url_vieja,
            'url_nueva': url_nueva,
            'reemplazos': reemplazos,
        })
        youtube.videos().update(
            part='snippet',
            body={'id': video['id'], 'snippet': snippet | {'description': nueva_desc}}
        ).execute()
        snippet['description'] = nueva_desc
        console.print(f'  [green]✓[/green] 📝 {snippet["title"]}')


def reemplazar_link_en_comentarios(youtube, entradas, url_vieja, url_nueva):
    console.print(f'  {len(entradas)} comentario(s) afectados. Actualizando...')
    for entry in entradas:
        texto_nuevo, reemplazos = reemplazar_url_exacta(entry['texto_completo'], url_vieja, url_nueva)
        if not reemplazos:
            continue
        try:
            guardar_backup('comentario-link', entry['comment_id'], {
                'comment_id': entry['comment_id'],
                'video': entry.get('video', ''),
                'texto_original': entry['texto_completo'],
                'texto_nuevo': texto_nuevo,
                'url_vieja': url_vieja,
                'url_nueva': url_nueva,
                'reemplazos': reemplazos,
            })
            youtube.comments().update(
                part='snippet',
                body={'id': entry['comment_id'], 'snippet': {'textOriginal': texto_nuevo}}
            ).execute()
            console.print(f'  [green]✓[/green] 💬 {entry["video"]}')
        except Exception as e:
            console.print(f'  [red]✗[/red] 💬 {entry["video"]}: {e}')


def accion_comprobar_links(youtube, videos, channel_id=None):
    puede_comentarios = youtube is not None and bool(channel_id)
    links_ali_unicos = {
        url for v in videos
        for url in extraer_links_aliexpress(v['snippet']['description'])
    }
    links_amz_unicos = {
        url for v in videos
        for url in extraer_links_amazon(v['snippet']['description'])
    }
    n_ali = len(links_ali_unicos)
    n_amz = len(links_amz_unicos)
    n_com = len(videos)

    console.print(f'  Amazon:     [bold]{n_amz}[/bold] links únicos [dim](navegador aislado, detecta sin stock)[/dim]')
    console.print(f'  AliExpress: [bold]{n_ali}[/bold] links únicos [dim](navegador aislado, detecta rotos, geo o verificación)[/dim]')
    console.print(f'  Comentarios: [bold]{n_com}[/bold] vídeos a escanear · coste: [bold red]~{n_com} unidades[/bold red]')
    console.print('  📊 [dim]Cuota:[/dim] [cyan]https://console.cloud.google.com/apis/api/youtube.googleapis.com/quotas[/cyan]')
    console.print()
    console.print('[dim]Se usará un navegador aislado de Playwright; no se tocará tu Chrome principal.[/dim]\n')

    OPT_AMZ  = f'Amazon ({n_amz} links)'
    OPT_ALI  = f'AliExpress ({n_ali} links)'
    OPT_COM  = f'Comentarios fijados (~{n_com} unidades extra)'
    OPT_VOLVER = questionary.Choice(title='Volver', value='__volver__', disabled='Enter sin marcar')

    opciones_disponibles = []
    if n_amz:
        opciones_disponibles.append(OPT_AMZ)
    if n_ali:
        opciones_disponibles.append(OPT_ALI)
    if videos and puede_comentarios:
        opciones_disponibles.append(OPT_COM)

    if not opciones_disponibles:
        console.print('[yellow]No hay links que comprobar.[/yellow]')
        return

    seleccion = checkbox_menu(
        '¿Qué quieres comprobar?',
        choices=opciones_disponibles + [OPT_VOLVER],
    )
    if not seleccion:
        return

    hacer_amz           = OPT_AMZ in seleccion
    incluir_comentarios = puede_comentarios and OPT_COM in seleccion
    hacer_ali           = OPT_ALI in seleccion or incluir_comentarios

    console.print('[dim]Parar y volver al menú: Ctrl+C[/dim]\n')

    try:
        amazon_result = {'rotos': [], 'errores': [], 'advertencias': []}
        aliexpress_result = {'rotos': [], 'geo': [], 'captcha': [], 'errores': [], 'errores_lectura': [], 'advertencias': []}
        comentarios_result = {'rotos': [], 'geo': [], 'captcha': [], 'errores': [], 'errores_lectura': [], 'advertencias': []}
        stop_event = Event()
        workers = []
        ejecutar_en_paralelo = sum(bool(x) for x in (hacer_amz and n_amz, OPT_ALI in seleccion, incluir_comentarios)) > 1

        if hacer_amz and n_amz:
            video_links_amz = construir_video_links_descripcion(videos, extraer_links_amazon)
            workers.append((
                'Amazon',
                lambda: chequear_links_amazon(
                    video_links_amz,
                    stop_event=stop_event,
                    mostrar_progreso=not ejecutar_en_paralelo,
                ),
            ))

        if OPT_ALI in seleccion:
            workers.append((
                'AliExpress',
                lambda: comprobar_fuentes_aliexpress(
                    youtube,
                    videos,
                    channel_id=channel_id,
                    stop_event=stop_event,
                    mostrar_progreso=not ejecutar_en_paralelo,
                ),
            ))

        if incluir_comentarios:
            workers.append((
                'Comentarios',
                lambda: comprobar_links_aliexpress_en_comentarios(
                    youtube,
                    videos,
                    channel_id,
                    stop_event=stop_event,
                    mostrar_progreso=not ejecutar_en_paralelo,
                ),
            ))

        if ejecutar_en_paralelo:
            console.print('[dim]Ejecutando comprobaciones en paralelo para acelerar el proceso.[/dim]')
            with Progress(
                SpinnerColumn(),
                TextColumn('[progress.description]{task.description}'),
                BarColumn(),
                MofNCompleteColumn(),
                TextColumn('[dim]{task.fields[detalle]}[/dim]'),
                console=console,
            ) as progress:
                progress_lock = Lock()
                task_amz = task_com = task_ali = None
                if hacer_amz and n_amz:
                    task_amz = progress.add_task('Amazon', total=max(n_amz, 1), detalle='')
                if OPT_ALI in seleccion:
                    task_ali = progress.add_task('AliExpress', total=max(n_ali, 1), detalle='')
                if incluir_comentarios:
                    task_com = progress.add_task('Comentarios', total=max(n_com, 1), detalle='')

                workers = []
                if hacer_amz and n_amz:
                    video_links_amz = construir_video_links_descripcion(videos, extraer_links_amazon)
                    workers.append((
                        'Amazon',
                        lambda: chequear_links_amazon(
                            video_links_amz,
                            stop_event=stop_event,
                            mostrar_progreso=False,
                            progress=progress,
                            task_id=task_amz,
                            progress_lock=progress_lock,
                        ),
                    ))

                if OPT_ALI in seleccion:
                    workers.append((
                        'AliExpress',
                        lambda: comprobar_fuentes_aliexpress(
                            youtube,
                            videos,
                            channel_id=channel_id,
                            stop_event=stop_event,
                            mostrar_progreso=False,
                            links_progress=progress,
                            links_task_id=task_ali,
                            progress_lock=progress_lock,
                        ),
                    ))

                if incluir_comentarios:
                    workers.append((
                        'Comentarios',
                        lambda: comprobar_links_aliexpress_en_comentarios(
                            youtube,
                            videos,
                            channel_id,
                            stop_event=stop_event,
                            mostrar_progreso=False,
                            progress=progress,
                            task_id=task_com,
                            progress_lock=progress_lock,
                        ),
                    ))

                executor = ThreadPoolExecutor(max_workers=len(workers))
                cerrado = False
                try:
                    future_map = {
                        executor.submit(worker): nombre
                        for nombre, worker in workers
                    }
                    for future in as_completed(future_map):
                        nombre = future_map[future]
                        try:
                            resultado = normalizar_resultado_worker(nombre, future.result())
                        except KeyboardInterrupt:
                            raise
                        except Exception as e:
                            console.print(f'[red]Error en {nombre}: {e}[/red]')
                            resultado = normalizar_resultado_worker(nombre, [] if nombre == 'Amazon' else ([], [], []))
                        if nombre == 'Amazon':
                            amazon_result = resultado
                        elif nombre == 'AliExpress':
                            aliexpress_result = resultado
                        else:
                            comentarios_result = resultado
                except KeyboardInterrupt:
                    stop_event.set()
                    executor.shutdown(wait=True, cancel_futures=True)
                    cerrado = True
                    raise
                finally:
                    if not cerrado:
                        executor.shutdown(wait=True, cancel_futures=True)
        else:
            for nombre, worker in workers:
                resultado = normalizar_resultado_worker(nombre, worker())
                if nombre == 'Amazon':
                    amazon_result = resultado
                elif nombre == 'AliExpress':
                    aliexpress_result = resultado
                else:
                    comentarios_result = resultado

        # ── Combinar y guardar ────────────────────────────────────────────────
        links_rotos_ali = aliexpress_result['rotos'] + comentarios_result['rotos']
        links_geo_ali = aliexpress_result['geo'] + comentarios_result['geo']
        links_captcha_ali = aliexpress_result['captcha'] + comentarios_result['captcha']
        links_error_ali = aliexpress_result['errores'] + comentarios_result['errores']
        comentarios_error = comentarios_result.get('errores_lectura', [])
        advertencias = (
            amazon_result.get('advertencias', [])
            + aliexpress_result.get('advertencias', [])
            + comentarios_result.get('advertencias', [])
        )

        links_rotos = amazon_result['rotos'] + links_rotos_ali
        links_geo = links_geo_ali
        links_captcha = links_captcha_ali
        links_error = amazon_result['errores'] + links_error_ali

        guardar_reporte_links(links_rotos, links_geo, links_captcha, links_error, comentarios_error, advertencias)

        todos_problemas = links_rotos + links_geo
        if not todos_problemas:
            return

        if youtube is None:
            console.print('\n[yellow]Modo offline: el informe se ha generado, pero el reemplazo automático está desactivado.[/yellow]')
            return

        urls_unicas = list(dict.fromkeys(e['url'] for e in todos_problemas))
        console.print('\n[bold]Links con problemas:[/bold]\n')
        for i, url in enumerate(urls_unicas, 1):
            tienda_sym = '🛒' if any(e.get('tienda') == 'amazon' and e['url'] == url for e in todos_problemas) else '🛍'
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
            if not es_url_http_valida(url_nueva):
                console.print('[red]URL no válida. Debe comenzar por http:// o https://[/red]')
                continue
            entradas_url = [e for e in todos_problemas if e['url'] == url_vieja]
            entradas_desc = [e for e in entradas_url if e['tipo'] == 'descripcion']
            entradas_com  = [e for e in entradas_url if e['tipo'] == 'comentario']
            if entradas_desc:
                reemplazar_link_en_videos(youtube, videos, url_vieja, url_nueva)
            if entradas_com:
                reemplazar_link_en_comentarios(youtube, entradas_com, url_vieja, url_nueva)
    except KeyboardInterrupt:
        console.print('\n[yellow]Comprobación cancelada por el usuario.[/yellow]')
        return


def cargar_exclusiones():
    if not os.path.exists(EXCLUSIONES_FILE):
        return set()
    try:
        with open(EXCLUSIONES_FILE, 'r', encoding='utf-8') as f:
            return {line.split('#')[0].strip() for line in f if line.strip() and not line.startswith('#')}
    except OSError as exc:
        console.print(f'[yellow]AVISO: no se pudo leer "{_display_path(EXCLUSIONES_FILE)}": {exc}[/yellow]')
        return set()


def guardar_exclusiones(videos_nuevos):
    # Leer entradas actuales (id + comentario) para no perder títulos ya guardados
    entradas = {}
    if os.path.exists(EXCLUSIONES_FILE):
        try:
            with open(EXCLUSIONES_FILE, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        vid_id = line.split('#')[0].strip()
                        entradas[vid_id] = line
        except OSError as exc:
            console.print(f'[yellow]AVISO: no se pudo conservar el historial de exclusiones: {exc}[/yellow]')
    # Añadir los nuevos con su título
    for video in videos_nuevos:
        vid_id = video['id']
        titulo = video['snippet']['title']
        entradas[vid_id] = f'{vid_id}  # {titulo}'
    contenido = ''.join(f'{entradas[vid_id]}\n' for vid_id in sorted(entradas))
    escribir_texto_atomico(EXCLUSIONES_FILE, contenido)


def accion_videos_sin_cupones(videos, patron):
    patron = compilar_patron_si_hace_falta(patron)
    exclusiones = cargar_exclusiones()
    sin_cupones = [
        v for v in videos
        if not patron.search(v['snippet']['description'])
        and v['id'] not in exclusiones
    ]
    if not sin_cupones:
        console.print('[green]✓ Todos los vídeos tienen el bloque de cupones (o están excluidos).[/green]')
        return

    resumen = Text.assemble(
        (f'{len(sin_cupones)} ', 'red'),
        ('vídeos pendientes de bloque de cupones', 'dim'),
    )
    console.print(Panel(resumen, title='Pendientes', border_style='cyan'))

    tabla = Table(box=box.SIMPLE_HEAVY, expand=True, header_style='bold cyan')
    tabla.add_column('#', justify='right', style='cyan', no_wrap=True)
    tabla.add_column('Título', style='white', overflow='fold')
    tabla.add_column('Studio', style='blue', no_wrap=True)

    for i, video in enumerate(sin_cupones, 1):
        vid_id = video['id']
        titulo = Text(video['snippet']['title'], style='white')
        titulo.append('\n')
        titulo.append(f'https://youtu.be/{vid_id}', style=f'cyan underline link https://www.youtube.com/watch?v={vid_id}')
        tabla.add_row(
            str(i),
            titulo,
            f'[link=https://studio.youtube.com/video/{vid_id}]Abrir[/link]',
        )

    console.print(tabla)
    console.print()

    opciones = [
        questionary.Choice(
            title=f'{i:>3}. {video["snippet"]["title"]}',
            value=video['id'],
        )
        for i, video in enumerate(sin_cupones, 1)
    ]
    opciones.append(questionary.Choice(title='Volver sin excluir nada', value='__volver__'))

    seleccion_ids = checkbox_menu(
        'Selecciona los vídeos que quieres excluir:',
        choices=opciones,
    )

    if not seleccion_ids or '__volver__' in seleccion_ids:
        console.print('[yellow]Sin cambios.[/yellow]')
        return

    seleccionados = [video for video in sin_cupones if video['id'] in set(seleccion_ids)]
    if seleccionados:
        guardar_exclusiones(seleccionados)
        console.print(f'[green]✓ {len(seleccionados)} vídeos añadidos a exclusiones.[/green]')


def accion_comprobar_comentarios(youtube, videos, nuevo_bloque, patron, channel_id):
    patron = compilar_patron_si_hace_falta(patron)
    videos_con_cupones = buscar_videos_con_cupones(videos, patron)
    if not videos_con_cupones:
        console.print('[yellow]No hay vídeos con cupones.[/yellow]')
        return

    n = len(videos_con_cupones)
    console.print(f'[bold]{n}[/bold] vídeos con cupones. Coste estimado: [bold red]~{n} unidades[/bold red]')
    console.print('  📊 [dim]Cuota:[/dim] [cyan]https://console.cloud.google.com/apis/api/youtube.googleapis.com/quotas[/cyan]')
    console.print()
    if not confirmar_menu(f'¿Comprobar comentarios fijados de {n} vídeo{"s" if n != 1 else ""}?'):
        console.print('[yellow]Cancelado.[/yellow]')
        return

    actualizados = 0
    sin_actualizar = 0
    sin_cupones_count = 0
    errores_count = 0
    # sin_actualizar_lista: (titulo, vid_id, comment_id)
    sin_actualizar_lista = []
    sin_cupones_lista = []
    errores_lista = []

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
                comentario = obtener_comentario_del_canal(youtube, vid_id, channel_id)
                encontrado = False
                if comentario:
                    texto = comentario['texto']
                    comment_id = comentario['comment_id']
                    if nuevo_bloque in texto:
                        actualizados += 1
                        encontrado = True
                    elif patron.search(texto):
                        sin_actualizar += 1
                        sin_actualizar_lista.append((titulo, vid_id, comment_id, texto))
                        encontrado = True
                if not encontrado:
                    sin_cupones_count += 1
                    sin_cupones_lista.append((titulo, vid_id))
            except Exception as exc:
                errores_count += 1
                errores_lista.append((titulo, vid_id, str(exc)))
            progress.advance(task)

    guardar_estado_comentarios(actualizados, sin_actualizar, sin_cupones_count, errores_count)

    console.print()
    console.rule()
    console.print(f'[green]✓[/green] Actualizados: [bold green]{actualizados}[/bold green]  ·  '
                  f'[yellow]⚠[/yellow] Sin actualizar: [bold yellow]{sin_actualizar}[/bold yellow]  ·  '
                  f'[red]✗[/red] Sin cupones: [bold red]{sin_cupones_count}[/bold red]', end='')
    if errores_count:
        console.print(f'  ·  [red]Errores técnicos: {errores_count}[/red]')
    else:
        console.print()

    if sin_actualizar_lista:
        console.print(f'\n[yellow bold]Sin actualizar ({len(sin_actualizar_lista)}):[/yellow bold]')
        for titulo, vid_id, _ in sin_actualizar_lista:
            console.print(f'  [yellow]·[/yellow] {titulo}')
            console.print(f'    [link=https://www.youtube.com/watch?v={vid_id}][blue]https://www.youtube.com/watch?v={vid_id}[/blue][/link]')

    if sin_cupones_lista:
        console.print(f'\n[red bold]Sin comentario fijado con cupones ({len(sin_cupones_lista)}):[/red bold]')
        for titulo, vid_id in sin_cupones_lista:
            console.print(f'  [red]·[/red] {titulo}')
            console.print(f'    [link=https://www.youtube.com/watch?v={vid_id}][blue]https://www.youtube.com/watch?v={vid_id}[/blue][/link]')

    if errores_lista:
        console.print(f'\n[red bold]Errores técnicos al leer comentarios ({len(errores_lista)}):[/red bold]')
        for titulo, vid_id, error in errores_lista:
            console.print(f'  [red]·[/red] {titulo}')
            console.print(f'    [dim]{error}[/dim]')
            console.print(f'    [link=https://www.youtube.com/watch?v={vid_id}][blue]https://www.youtube.com/watch?v={vid_id}[/blue][/link]')

    if sin_actualizar_lista:
        console.print()
        coste = len(sin_actualizar_lista) * 50
        console.print(f'  [dim]Coste de actualizar comentarios: [/dim][bold red]~{coste} unidades[/bold red]')
        console.print('  📊 [dim]Cuota:[/dim] [cyan]https://console.cloud.google.com/apis/api/youtube.googleapis.com/quotas[/cyan]')
        console.print()
        if confirmar_menu(
            f'¿Actualizar {len(sin_actualizar_lista)} comentario{"s" if len(sin_actualizar_lista) != 1 else ""}?',
            default='No',
        ):
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
                for titulo, vid_id, comment_id, texto_original in sin_actualizar_lista:
                    progress.update(task, titulo=titulo[:60])
                    try:
                        guardar_backup('comentario', comment_id, {
                            'comment_id': comment_id,
                            'video_id': vid_id,
                            'video': titulo,
                            'texto_original': texto_original,
                            'texto_nuevo': nuevo_bloque,
                        })
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
            guardar_estado_comentarios(actualizados + corregidos, sin_actualizar - corregidos, sin_cupones_count, errores_count)


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
        c_err = estado_comentarios.get('errores', 0)
        izq.add_row(Text.assemble(('● ', 'green'), (f'{c_act} ', 'green'), ('actualizados', 'dim')))
        izq.add_row(Text.assemble(('● ', 'red' if c_sin_act else 'green'), (f'{c_sin_act} ', 'red' if c_sin_act else 'green'), ('sin actualizar', 'dim')))
        izq.add_row(Text.assemble(('● ', 'yellow' if c_sin_cup else 'green'), (f'{c_sin_cup} ', 'yellow' if c_sin_cup else 'green'), ('sin cupones en comentario', 'dim')))
        izq.add_row(Text.assemble(('● ', 'red' if c_err else 'green'), (f'{c_err} ', 'red' if c_err else 'green'), ('errores técnicos', 'dim')))
        izq.add_row(Text(f'  (último escaneo: {estado_comentarios["fecha"]})', style='dim'))

    # ── Columna derecha ────────────────────────────────────────────
    der = Table.grid(padding=(0, 0))
    der.add_column(no_wrap=True)
    der.add_row(Text('Estado del canal', style='cyan bold'))
    der.add_row(Text('─' * 24, style='bright_black'))
    der.add_row('')
    der.add_row(Text.assemble(('● ', 'blue'), (f'{n_videos} ', 'cyan'), ('vídeos en el canal', 'dim')))
    if stats:
        con, sin, excl = stats['con_cupones'], stats['sin_cupones'], stats['excluidos']
        der.add_row(Text.assemble(('● ', 'green'), (f'{con} ', 'cyan'), ('con cupones', 'dim')))
        der.add_row(Text.assemble(('● ', 'red' if sin else 'green'), (f'{sin} ', 'red' if sin else 'green'), ('sin cupones ', 'dim'), ('✗' if sin else '✓', 'red' if sin else 'green')))
        act = stats.get('actualizados', 0)
        pend = stats.get('por_actualizar', 0)
        der.add_row(Text.assemble(('● ', 'green'), (f'{act} ', 'green'), ('actualizados', 'dim')))
        der.add_row(Text.assemble(('● ', 'red' if pend else 'green'), (f'{pend} ', 'red' if pend else 'green'), ('por actualizar ', 'dim'), ('✗' if pend else '✓', 'red' if pend else 'green')))
        if excl:
            der.add_row(Text.assemble(('● ', 'yellow'), (f'{excl} ', 'cyan'), ('excluidos (de cupones)', 'dim')))
    der.add_row('')

    if estado_links:
        der.add_row(Text('Links AliExpress', style='cyan bold'))
        der.add_row(Text('─' * 24, style='bright_black'))
        der.add_row('')
        rotos, geo = estado_links['rotos'], estado_links['geo']
        captcha = estado_links.get('captcha', 0)
        errores = estado_links.get('errores', 0)
        comentarios_error = estado_links.get('comentarios_error', 0)
        der.add_row(Text.assemble(('● ', 'red' if rotos else 'green'), (f'{rotos} ', 'red' if rotos else 'green'), ('rotos ', 'dim'), ('✗' if rotos else '✓', 'red' if rotos else 'green')))
        der.add_row(Text.assemble(('● ', 'yellow' if geo else 'green'), (f'{geo} ', 'yellow' if geo else 'green'), ('no disponibles en tu región', 'dim')))
        der.add_row(Text.assemble(('● ', 'yellow' if captcha else 'green'), (f'{captcha} ', 'yellow' if captcha else 'green'), ('pendientes por verificación', 'dim')))
        der.add_row(Text.assemble(('● ', 'red' if errores else 'green'), (f'{errores} ', 'red' if errores else 'green'), ('errores técnicos al verificar', 'dim')))
        der.add_row(Text.assemble(('● ', 'yellow' if comentarios_error else 'green'), (f'{comentarios_error} ', 'yellow' if comentarios_error else 'green'), ('comentarios no verificados', 'dim')))
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


def mostrar_menu(info_canal, n_videos, nuevo_bloque, stats=None, estado_links=None, estado_comentarios=None, offline=False):
    console.clear()
    dibujar_cabecera(info_canal, n_videos, nuevo_bloque, stats, estado_links, estado_comentarios)
    if offline:
        console.print('[bold red]⚠  MODO OFFLINE — los datos son del caché local, no de YouTube en tiempo real[/bold red]')
    console.print()


def main():
    check_mode = '--check' in sys.argv

    if not check_mode and not hay_terminal_interactiva():
        console.print('[red]ERROR: Esta app necesita una terminal interactiva.[/red]')
        console.print('[yellow]Ábrela desde la terminal integrada y ejecútala ahí, no desde el panel Output/Code Runner/Task runner.[/yellow]')
        console.print('[dim]Ejemplo: source .venv/bin/activate && python YouTubeCodes.py --offline[/dim]')
        raise SystemExit(1)

    bloquear_instancia()
    atexit.register(liberar_bloqueo)

    if check_mode:
        asegurar_permisos_locales()
        if not os.path.exists(CREDENTIALS_FILE):
            console.print(f'[red]ERROR: No se encontró "{_display_path(CREDENTIALS_FILE)}"[/red]')
            raise SystemExit(1)
        youtube = autenticar()
        info = obtener_info_canal(youtube)
        console.print(f'[green]✓[/green] Autenticación correcta — canal: [bold]{info["nombre"]}[/bold] ({info["handle"]})')
        raise SystemExit(0)

    offline = '--offline' in sys.argv
    asegurar_permisos_locales()

    if not os.path.exists(CREDENTIALS_FILE) and not offline:
        console.print(f'[red]ERROR: No se encontró el archivo "{_display_path(CREDENTIALS_FILE)}"[/red]')
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
        console.print(f'[yellow]AVISO: "{_display_path(CUPONES_FILE)}" no encontrado o vacío. Las opciones 1 y 3 no estarán disponibles.[/yellow]')

    console.clear()
    console.print()
    console.print('  [bold cyan]◆ YouTubeCodes[/bold cyan]  [dim]Gestor de cupones de AliExpress[/dim]')
    console.rule(style='bright_black')
    console.print()

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

        with console.status('[bold]Obteniendo lista de vídeos del canal...[/bold]'):
            videos = obtener_todos_los_videos(youtube)
            info_canal = obtener_info_canal(youtube)
        guardar_cache_videos(videos, info_canal)
        console.print(f'[green]✓[/green] [bold]{len(videos)}[/bold] vídeos en el canal')

    OPT_CUPONES      = 'Actualizar cupones en las descripciones'
    OPT_LINKS        = 'Comprobar links de AliExpress y Amazon'
    OPT_SIN_CUP      = 'Ver vídeos sin bloque de cupones'
    OPT_COMENTARIOS  = 'Comprobar comentarios fijados con cupones'
    OPT_RESCAN       = 'Recargar vídeos del canal'
    OPT_SALIR        = 'Salir'

    channel_id = info_canal['id']

    def build_opciones():
        ops = []
        if nuevo_bloque and not offline:
            ops.append(OPT_CUPONES)
        ops.append(OPT_LINKS)
        if nuevo_bloque:
            ops.append(OPT_SIN_CUP)
            if not offline:
                ops.append(OPT_COMENTARIOS)
        ops.append(OPT_SALIR)
        return ops

    def calcular_stats():
        if not patron:
            return None
        exclusiones = cargar_exclusiones()
        con_cupones = [v for v in videos if patron.search(v['snippet']['description'])]
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
                     cargar_estado_links(), cargar_estado_comentarios(), offline)
        mostrar_atajos_menu_principal(offline)
        opcion = select_menu(
            'Elige una opción:',
            choices=build_opciones(),
            instruction='(Usa flechas, Enter confirma, R recarga, Esc sale)' if not offline else '(Usa flechas, Enter confirma, Esc sale)',
            key_results={'r': OPT_RESCAN, 'R': OPT_RESCAN} if not offline else None,
        )

        if opcion is None or opcion == OPT_SALIR:
            console.clear()
            console.print('[dim]Hasta luego.[/dim]')
            break

        if opcion == OPT_RESCAN:
            with console.status('[bold]Recargando vídeos del canal...[/bold]'):
                videos = obtener_todos_los_videos(youtube)
                info_canal = obtener_info_canal(youtube)
            guardar_cache_videos(videos, info_canal)
            continue

        console.print()
        if opcion == OPT_CUPONES:
            accion_actualizar_cupones(youtube, videos, nuevo_bloque, patron)
        elif opcion == OPT_LINKS:
            accion_comprobar_links(youtube, videos, channel_id)
        elif opcion == OPT_SIN_CUP:
            accion_videos_sin_cupones(videos, patron)
        elif opcion == OPT_COMENTARIOS:
            accion_comprobar_comentarios(youtube, videos, nuevo_bloque, patron, channel_id)

        console.print()
        console.rule(style='bright_black')
        console.print('\n  [dim]Pulsa Enter o Esc para volver al menú...[/dim]')
        esperar_volver_menu()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        console.print('\n[yellow]Cancelado por el usuario.[/yellow]')
