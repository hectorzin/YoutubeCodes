import sys

from playwright.sync_api import sync_playwright

import YouTubeCodes as yc

URLS = [
    ('https://s.click.aliexpress.com/e/_EJf3Zdg', 'A INVESTIGAR'),
]


def main():
    cookies = yc.cargar_cookies_aliexpress()
    with sync_playwright() as playwright:
        browser, context = yc.lanzar_navegador_aislado(playwright, cookies=cookies, headless=False)
        try:
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
                print('  Texto body (primeros 400 chars):')
                print(f'  {texto[:400]}')

                if yc.es_captcha(page):
                    print('\n  *** POSIBLE CAPTCHA — revisa la ventana aislada y pulsa ENTER para continuar ***')
                    input()
        finally:
            context.close()
            browser.close()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
