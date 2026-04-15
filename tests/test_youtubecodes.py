import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import YouTubeCodes as yc


class AskStub:
    def __init__(self, answer):
        self.answer = answer

    def ask(self):
        return self.answer


class ContextManagerStub:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self.value

    def __exit__(self, exc_type, exc, tb):
        return False


class KeyBindingsStub:
    def __init__(self):
        self.calls = []

    def add(self, *args, **kwargs):
        self.calls.append((args, kwargs))

        def decorator(func):
            return func

        return decorator


class QuestionStub:
    def __init__(self):
        self.application = MagicMock()
        self.application.key_bindings = KeyBindingsStub()


class YouTubeCodesTests(unittest.TestCase):
    def setUp(self):
        yc.liberar_bloqueo()

    def tearDown(self):
        yc.liberar_bloqueo()

    def test_habilitar_escape_para_volver_registra_escape(self):
        question = QuestionStub()
        resultado = yc.habilitar_escape_para_volver(question)
        self.assertIs(resultado, question)
        self.assertEqual(question.application.key_bindings.calls[0][0][0], yc.Keys.Escape)

    def test_habilitar_teclas_resultado_registra_atajo(self):
        question = QuestionStub()
        resultado = yc.habilitar_teclas_resultado(question, {'r': 'recargar'})
        self.assertIs(resultado, question)
        self.assertEqual(question.application.key_bindings.calls[0][0][0], 'r')

    def test_select_menu_usa_estilo_unificado(self):
        with patch.object(yc.questionary, 'select', return_value=AskStub('Salir')) as select_mock:
            respuesta = yc.select_menu('Elige', ['Salir'])
        self.assertEqual(respuesta, 'Salir')
        self.assertEqual(select_mock.call_args.kwargs['pointer'], '›')
        self.assertIs(select_mock.call_args.kwargs['style'], yc.CONFIRM_STYLE)

    def test_confirmar_menu_devuelve_true_con_si(self):
        with patch.object(yc.questionary, 'select', return_value=AskStub('Sí')) as select_mock:
            self.assertTrue(yc.confirmar_menu('¿Continuar?', default='Sí'))
            select_mock.assert_called_once()
            self.assertEqual(select_mock.call_args.kwargs['pointer'], '›')
            self.assertIs(select_mock.call_args.kwargs['style'], yc.CONFIRM_STYLE)

    def test_confirmar_menu_devuelve_false_con_no(self):
        with patch.object(yc.questionary, 'select', return_value=AskStub('No')):
            self.assertFalse(yc.confirmar_menu('¿Continuar?'))

    def test_mostrar_atajos_menu_principal_destaca_r_y_escape(self):
        with patch.object(yc.console, 'print') as print_mock:
            yc.mostrar_atajos_menu_principal(offline=False)
        panel = print_mock.call_args.args[0]
        self.assertIn('Atajos:', panel.renderable.plain)
        self.assertIn('R', panel.renderable.plain)
        self.assertIn('Esc', panel.renderable.plain)

    def test_mostrar_atajos_menu_principal_offline_oculta_r(self):
        with patch.object(yc.console, 'print') as print_mock:
            yc.mostrar_atajos_menu_principal(offline=True)
        panel = print_mock.call_args.args[0]
        self.assertIn('Esc', panel.renderable.plain)
        self.assertNotIn('recarga vídeos', panel.renderable.plain)

    def test_checkbox_menu_usa_indicadores_claros(self):
        with patch.object(yc.questionary, 'checkbox', return_value=AskStub(['uno'])) as checkbox_mock:
            respuesta = yc.checkbox_menu('Elige', choices=['uno', 'dos'])
        self.assertEqual(respuesta, ['uno'])
        self.assertEqual(yc.questionary_common.INDICATOR_SELECTED, '✓')
        self.assertEqual(yc.questionary_common.INDICATOR_UNSELECTED, '□')
        self.assertEqual(checkbox_mock.call_args.kwargs['instruction'], '(Espacio marca/desmarca, Enter confirma, Esc vuelve, Ctrl+C cancela)')

    def test_anadir_fecha_si_falta_agrega_mes_y_asterisco(self):
        bloque = '*CUPONES*\nLinea 2'
        resultado = yc.añadir_fecha_si_falta(bloque)
        self.assertRegex(resultado.splitlines()[0], r'^\*CUPONES \(.*\)\*$')

    def test_anadir_fecha_si_falta_no_duplica_fecha_existente(self):
        bloque = 'CUPONES (ABRIL 2026)\nLinea 2'
        self.assertEqual(yc.añadir_fecha_si_falta(bloque), bloque)

    def test_construir_patron_acepta_fechas_distintas(self):
        original = 'CUPONES (MARZO 2026)\nLinea media\nUltima linea'
        patron = yc.construir_patron(original)
        actual = 'CUPONES (ABRIL 2026)\nLinea media\nUltima linea'
        self.assertIsNotNone(patron.search(actual))

    def test_extraer_links_deduplica(self):
        texto = (
            'https://es.aliexpress.com/item/123.html\n'
            'https://es.aliexpress.com/item/123.html\n'
            'https://amzn.to/demo'
        )
        self.assertEqual(
            yc.extraer_links_aliexpress(texto),
            ['https://es.aliexpress.com/item/123.html'],
        )
        self.assertEqual(yc.extraer_links_amazon(texto), ['https://amzn.to/demo'])

    def test_extraer_links_filtra_hosts_maliciosos(self):
        texto = (
            'https://evil.example/path/aliexpress.com/item/123\n'
            'https://es.aliexpress.com/item/456.html\n'
            'https://amazon.evil.example/demo\n'
            'https://amazon.es/dp/B012345678'
        )
        self.assertEqual(yc.extraer_links_aliexpress(texto), ['https://es.aliexpress.com/item/456.html'])
        self.assertEqual(yc.extraer_links_amazon(texto), ['https://amazon.es/dp/B012345678'])

    def test_linea_con_link_devuelve_linea_correcta(self):
        descripcion = 'uno\ndos https://amzn.to/demo\ntres'
        self.assertEqual(yc.linea_con_link(descripcion, 'https://amzn.to/demo'), 'dos https://amzn.to/demo')

    def test_actualizar_video_devuelve_sin_cambios_si_no_hay_match(self):
        youtube = MagicMock()
        video = {'id': 'abc', 'snippet': {'description': 'sin bloque', 'title': 'Video'}}
        with patch.object(yc, 'guardar_backup'):
            resultado = yc.actualizar_video(youtube, video, 'nuevo bloque', r'bloque antiguo')
        self.assertEqual(resultado, 'sin_cambios')
        youtube.videos.assert_not_called()

    def test_actualizar_video_omite_si_supera_limite(self):
        youtube = MagicMock()
        descripcion = 'bloque antiguo'
        video = {'id': 'abc', 'snippet': {'description': descripcion, 'title': 'Video'}}
        bloque_largo = 'x' * 5001
        with patch.object(yc, 'guardar_backup'):
            resultado = yc.actualizar_video(youtube, video, bloque_largo, r'bloque antiguo')
        self.assertEqual(resultado, 'demasiado_larga')
        youtube.videos.assert_not_called()

    def test_actualizar_video_actualiza_snippet(self):
        youtube = MagicMock()
        update_chain = youtube.videos.return_value.update.return_value
        update_chain.execute.return_value = {}
        video = {
            'id': 'abc',
            'snippet': {'description': 'antes bloque antiguo despues', 'title': 'Video'},
        }
        with patch.object(yc, 'guardar_backup') as backup_mock:
            resultado = yc.actualizar_video(youtube, video, 'bloque nuevo', r'bloque antiguo')
        self.assertEqual(resultado, 'ok')
        youtube.videos.return_value.update.assert_called_once()
        backup_mock.assert_called_once()
        body = youtube.videos.return_value.update.call_args.kwargs['body']
        self.assertEqual(body['snippet']['description'], 'antes bloque nuevo despues')

    def test_actualizar_video_detecta_match_ambiguo(self):
        youtube = MagicMock()
        video = {
            'id': 'abc',
            'snippet': {'description': 'bloque antiguo\nx\nbloque antiguo', 'title': 'Video'},
        }
        with patch.object(yc, 'guardar_backup'):
            resultado = yc.actualizar_video(youtube, video, 'nuevo', r'bloque antiguo')
        self.assertEqual(resultado, 'match_ambiguo')
        youtube.videos.assert_not_called()

    def test_accion_actualizar_cupones_no_hace_nada_si_pendientes_0(self):
        youtube = MagicMock()
        videos = [{
            'id': 'vid1',
            'snippet': {'description': 'inicio\nbloque nuevo\nfin', 'title': 'Video 1'},
        }]
        with patch.object(yc.console, 'print') as print_mock, \
             patch.object(yc, 'confirmar_menu') as confirmar_mock, \
             patch.object(yc, 'actualizar_video') as actualizar_mock:
            yc.accion_actualizar_cupones(youtube, videos, 'bloque nuevo', r'bloque nuevo')
        confirmar_mock.assert_not_called()
        actualizar_mock.assert_not_called()
        self.assertTrue(any('No hay vídeos pendientes' in str(call.args[0]) for call in print_mock.call_args_list if call.args))

    def test_accion_actualizar_cupones_prompt_sin_markup_rich(self):
        youtube = MagicMock()
        videos = [{
            'id': 'vid1',
            'snippet': {'description': 'inicio\nbloque antiguo\nfin', 'title': 'Video 1'},
        }]
        with patch.object(yc.console, 'print'), \
             patch.object(yc, 'confirmar_menu', return_value=False) as confirmar_mock, \
             patch.object(yc, 'actualizar_video') as actualizar_mock:
            yc.accion_actualizar_cupones(youtube, videos, 'bloque nuevo', r'bloque antiguo')
        confirmar_mock.assert_called_once_with('¿Actualizar 1 vídeo?')
        actualizar_mock.assert_not_called()

    def test_guardar_y_cargar_exclusiones(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'exclusiones.txt'
            videos = [
                {'id': 'vid2', 'snippet': {'title': 'Titulo 2'}},
                {'id': 'vid1', 'snippet': {'title': 'Titulo 1'}},
            ]
            with patch.object(yc, 'EXCLUSIONES_FILE', str(path)):
                yc.guardar_exclusiones(videos)
                self.assertEqual(yc.cargar_exclusiones(), {'vid1', 'vid2'})
                contenido = path.read_text(encoding='utf-8')
                self.assertIn('vid1  # Titulo 1', contenido)
                self.assertIn('vid2  # Titulo 2', contenido)

    def test_reemplazar_link_en_comentarios_actualiza_texto(self):
        youtube = MagicMock()
        youtube.comments.return_value.update.return_value.execute.return_value = {}
        entradas = [{
            'comment_id': 'c1',
            'texto_completo': 'antes https://vieja.test despues',
            'video': 'Video demo',
        }]
        with patch.object(yc.console, 'print'), patch.object(yc, 'guardar_backup'):
            yc.reemplazar_link_en_comentarios(youtube, entradas, 'https://vieja.test', 'https://nueva.test')
        body = youtube.comments.return_value.update.call_args.kwargs['body']
        self.assertEqual(body['snippet']['textOriginal'], 'antes https://nueva.test despues')

    def test_reemplazar_url_exacta_no_toca_substrings(self):
        texto = 'https://vieja.test y https://vieja.test.extra'
        nuevo, reemplazos = yc.reemplazar_url_exacta(texto, 'https://vieja.test', 'https://nueva.test')
        self.assertEqual(reemplazos, 1)
        self.assertEqual(nuevo, 'https://nueva.test y https://vieja.test.extra')

    def test_es_url_amazon_rechaza_lookalikes(self):
        self.assertFalse(yc.es_url_amazon('https://amazon.shop/phish'))
        self.assertFalse(yc.es_url_amazon('https://www.amazon.click/demo'))
        self.assertTrue(yc.es_url_amazon('https://amazon.es/dp/B012345678'))

    def test_lanzar_navegador_aislado_configura_contexto_y_cookies(self):
        playwright = MagicMock()
        browser = MagicMock()
        context = MagicMock()
        playwright.chromium.launch.return_value = browser
        browser.new_context.return_value = context
        cookies = [{'name': 'a', 'value': 'b', 'domain': '.aliexpress.com', 'path': '/'}]

        resultado_browser, resultado_contexto = yc.lanzar_navegador_aislado(
            playwright,
            cookies=cookies,
            headless=False,
        )

        playwright.chromium.launch.assert_called_once()
        self.assertEqual(playwright.chromium.launch.call_args.kwargs['headless'], False)
        browser.new_context.assert_called_once()
        context.add_cookies.assert_called_once_with(cookies)
        self.assertIs(resultado_browser, browser)
        self.assertIs(resultado_contexto, context)

    def test_chequear_links_videos_sin_cookies_sale_sin_comprobar(self):
        video_links = [('video', [('descripcion', 'https://es.aliexpress.com/item/1.html', 'linea', None, None)])]
        with patch.object(yc, 'cargar_cookies_aliexpress', return_value=[]), \
             patch.object(yc.console, 'print'):
            resultado = yc.chequear_links_videos(video_links)
        self.assertEqual(resultado['rotos'], [])
        self.assertEqual(resultado['geo'], [])
        self.assertEqual(resultado['captcha'], [])
        self.assertEqual(resultado['errores'], [])
        self.assertTrue(resultado['advertencias'])

    def test_chequear_links_videos_en_paralelo_no_pregunta_por_captcha(self):
        video = {'id': 'vid1', 'snippet': {'title': 'Video 1'}}
        video_links = [(video, [('descripcion', 'https://es.aliexpress.com/item/1.html', 'linea', None, None)])]
        browser = MagicMock()
        context = MagicMock()
        page = MagicMock()
        context.new_page.return_value = page
        playwright = MagicMock()

        with patch.object(yc, 'cargar_cookies_aliexpress', return_value=[{'name': 'a', 'value': 'b'}]), \
             patch.object(yc, 'lanzar_navegador_aislado', return_value=(browser, context)), \
             patch.object(yc, 'comprobar_link_aliexpress_navegador', return_value='captcha'), \
             patch.object(yc, 'confirmar_menu') as confirmar_mock, \
             patch.object(yc, 'sync_playwright', return_value=ContextManagerStub(playwright)), \
             patch.object(yc.console, 'print'):
            resultado = yc.chequear_links_videos(video_links, mostrar_progreso=False)

        confirmar_mock.assert_not_called()
        self.assertEqual(resultado['rotos'], [])
        self.assertEqual(resultado['geo'], [])
        self.assertEqual(len(resultado['captcha']), 1)

    def test_accion_comprobar_links_tiene_opcion_volver(self):
        with patch.object(yc, 'checkbox_menu', return_value=['Volver']), \
             patch.object(yc.console, 'print'), \
             patch.object(yc, 'lanzar_navegador_aislado') as lanzar_mock:
            yc.accion_comprobar_links(youtube=MagicMock(), videos=[{'id': 'vid1', 'snippet': {'description': '', 'title': 'Video'}}])
        lanzar_mock.assert_not_called()

    def test_accion_comprobar_links_offline_no_ofrece_comentarios(self):
        videos = [{'id': 'vid1', 'snippet': {'description': 'https://amzn.to/demo', 'title': 'Video 1'}}]

        def checkbox_stub(*args, **kwargs):
            self.assertNotIn('Comentarios fijados (~1 unidades extra)', kwargs['choices'])
            return ['Volver']

        with patch.object(yc, 'checkbox_menu', side_effect=checkbox_stub), \
             patch.object(yc.console, 'print'):
            yc.accion_comprobar_links(youtube=None, videos=videos, channel_id=None)

    def test_accion_comprobar_links_con_solo_comentarios_dispara_revision_ali(self):
        youtube = MagicMock()
        youtube.commentThreads.return_value.list.return_value.execute.return_value = {
            'items': [{
                'snippet': {
                    'topLevelComment': {
                        'id': 'c1',
                        'snippet': {
                            'authorChannelId': {'value': 'chan1'},
                            'textOriginal': 'https://es.aliexpress.com/item/123.html',
                        },
                    },
                },
            }],
        }
        videos = [{'id': 'vid1', 'snippet': {'description': '', 'title': 'Video 1'}}]
        opcion = 'Comentarios fijados (~1 unidades extra)'

        with patch.object(yc, 'checkbox_menu', return_value=[opcion]), \
             patch.object(yc.console, 'print'), \
             patch.object(yc, 'comprobar_links_aliexpress_en_comentarios', return_value={'rotos': [], 'geo': [], 'captcha': [], 'errores': [], 'errores_lectura': []}) as comentarios_mock, \
             patch.object(yc, 'guardar_reporte_links'):
            yc.accion_comprobar_links(youtube=youtube, videos=videos, channel_id='chan1')

        comentarios_mock.assert_called_once()

    def test_accion_comprobar_links_puede_cancelarse_con_ctrl_c(self):
        videos = [{'id': 'vid1', 'snippet': {'description': 'https://es.aliexpress.com/item/123.html', 'title': 'Video 1'}}]
        with patch.object(yc, 'checkbox_menu', return_value=['AliExpress (1 links)']), \
             patch.object(yc, 'chequear_links_videos', side_effect=KeyboardInterrupt), \
             patch.object(yc.console, 'print') as print_mock:
            yc.accion_comprobar_links(youtube=MagicMock(), videos=videos, channel_id='chan1')
        self.assertTrue(any('Comprobación cancelada por el usuario.' in str(call.args[0]) for call in print_mock.call_args_list if call.args))

    def test_accion_comprobar_links_paralelo_atrapa_error_de_worker(self):
        videos = [{
            'id': 'vid1',
            'snippet': {
                'description': 'https://amzn.to/demo https://es.aliexpress.com/item/123.html',
                'title': 'Video 1',
            },
        }]
        seleccion = ['Amazon (1 links)', 'AliExpress (1 links)']
        with patch.object(yc, 'checkbox_menu', return_value=seleccion), \
             patch.object(yc, 'chequear_links_amazon', side_effect=RuntimeError('boom')), \
             patch.object(yc, 'comprobar_fuentes_aliexpress', return_value={'rotos': [], 'geo': [], 'captcha': [], 'errores': [], 'errores_lectura': []}), \
             patch.object(yc, 'guardar_reporte_links'), \
             patch.object(yc.console, 'print') as print_mock:
            yc.accion_comprobar_links(youtube=MagicMock(), videos=videos, channel_id='chan1')
        self.assertTrue(any('Error en Amazon: boom' in str(call.args[0]) for call in print_mock.call_args_list if call.args))

    def test_accion_videos_sin_cupones_permte_excluir_desde_checkbox(self):
        videos = [
            {'id': 'vid1', 'snippet': {'description': 'sin cupones', 'title': 'Video 1'}},
            {'id': 'vid2', 'snippet': {'description': 'sin cupones', 'title': 'Video 2'}},
        ]
        with patch.object(yc, 'cargar_exclusiones', return_value=set()), \
             patch.object(yc, 'checkbox_menu', return_value=['vid1']), \
             patch.object(yc, 'guardar_exclusiones') as guardar_mock, \
             patch.object(yc.console, 'print'):
            yc.accion_videos_sin_cupones(videos, r'BLOQUE_INEXISTENTE')
        guardar_mock.assert_called_once()
        guardados = guardar_mock.call_args.args[0]
        self.assertEqual([v['id'] for v in guardados], ['vid1'])

    def test_accion_videos_sin_cupones_muestra_link_en_opciones(self):
        videos = [
            {'id': 'vid1', 'snippet': {'description': 'sin cupones', 'title': 'Video 1'}},
        ]

        def checkbox_stub(*args, **kwargs):
            opciones = kwargs['choices']
            self.assertIn('Link: https://youtu.be/vid1', opciones[0].title)
            return ['__volver__']

        with patch.object(yc, 'cargar_exclusiones', return_value=set()), \
             patch.object(yc, 'checkbox_menu', side_effect=checkbox_stub), \
             patch.object(yc.console, 'print'):
            yc.accion_videos_sin_cupones(videos, r'BLOQUE_INEXISTENTE')

    def test_main_offline_arranca_y_sale_con_menu_simulado(self):
        videos = [{'id': 'vid1', 'snippet': {'description': '', 'title': 'Video 1'}}]
        info = {
            'id': 'chan1',
            'nombre': 'Canal Demo',
            'handle': '@canaldemo',
            'suscriptores': 10,
            'visualizaciones': 20,
        }

        def exists_stub(path):
            if path == yc.CUPONES_FILE:
                return False
            return True

        with patch.object(yc.sys, 'argv', ['YouTubeCodes.py', '--offline']), \
             patch.object(yc, 'hay_terminal_interactiva', return_value=True), \
             patch.object(yc, 'bloquear_instancia'), \
             patch.object(yc.atexit, 'register'), \
             patch.object(yc, 'cargar_cache_videos', return_value=(videos, info, '15/04/2026 10:00')), \
             patch.object(yc, 'cargar_estado_links', return_value=None), \
             patch.object(yc, 'cargar_estado_comentarios', return_value=None), \
             patch.object(yc.os.path, 'exists', side_effect=exists_stub), \
             patch.object(yc.questionary, 'select', return_value=AskStub('Salir')), \
             patch.object(yc.console, 'print'), \
             patch.object(yc.console, 'clear'), \
             patch.object(yc.console, 'rule'):
            yc.main()

    def test_main_offline_oculta_acciones_que_modifican_youtube(self):
        videos = [{'id': 'vid1', 'snippet': {'description': '', 'title': 'Video 1'}}]
        info = {
            'id': 'chan1',
            'nombre': 'Canal Demo',
            'handle': '@canaldemo',
            'suscriptores': 10,
            'visualizaciones': 20,
        }

        def exists_stub(path):
            if path == yc.CUPONES_FILE:
                return True
            return True

        def select_stub(*args, **kwargs):
            choices = kwargs['choices']
            self.assertNotIn('Actualizar cupones en las descripciones', choices)
            self.assertNotIn('Comprobar comentarios fijados con cupones', choices)
            self.assertIn('Comprobar links de AliExpress y Amazon', choices)
            return AskStub('Salir')

        with patch.object(yc.sys, 'argv', ['YouTubeCodes.py', '--offline']), \
             patch.object(yc, 'hay_terminal_interactiva', return_value=True), \
             patch.object(yc, 'bloquear_instancia'), \
             patch.object(yc.atexit, 'register'), \
             patch.object(yc, 'cargar_cache_videos', return_value=(videos, info, '15/04/2026 10:00')), \
             patch.object(yc, 'cargar_estado_links', return_value=None), \
             patch.object(yc, 'cargar_estado_comentarios', return_value=None), \
             patch.object(yc.os.path, 'exists', side_effect=exists_stub), \
             patch.object(yc.questionary, 'select', side_effect=select_stub), \
             patch.object(yc.console, 'print'), \
             patch.object(yc.console, 'clear'), \
             patch.object(yc.console, 'rule'):
            yc.main()


if __name__ == '__main__':
    unittest.main()
