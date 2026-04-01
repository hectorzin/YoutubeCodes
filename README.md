# YouTubeCodes

Herramienta para gestionar el bloque de cupones de AliExpress en las descripciones y comentarios fijados de todos tus vídeos de YouTube, y para comprobar si los links de productos siguen activos.

## Qué hace

- Lee el bloque de cupones de `cupones.txt` y lo reemplaza en todas las descripciones donde aparezca
- Añade automáticamente el mes y año actual al encabezado del bloque si no lo tiene
- Comprueba si el comentario fijado de cada vídeo tiene el bloque actualizado y permite corregirlo
- Detecta vídeos cuya descripción superaría los 5.000 caracteres tras la actualización y los omite con aviso
- Extrae todos los links de AliExpress y Amazon de tus vídeos y comprueba si cada producto sigue disponible
- Distingue entre productos eliminados y productos no disponibles en tu región (AliExpress)
- Distingue entre productos rotos y descatalogados (Amazon), con opción de comprobación a fondo via Chrome
- Detecta y pausa cuando AliExpress muestra un CAPTCHA, para que puedas resolverlo manualmente
- Mantiene una lista de vídeos excluidos (sorteos, directos, etc.) que no deberían tener cupones

## Requisitos

- Python 3.11 o superior
- Google Chrome instalado (solo para comprobar links de AliExpress y Amazon a fondo)

## Instalación

```bash
pip install -r requirements.txt
```

## Archivos necesarios

Antes de ejecutar el programa necesitas crear estos archivos. Ver [SETUP.md](SETUP.md) para instrucciones detalladas.

| Archivo | Descripción |
|---|---|
| `client_secret.json` | Credenciales OAuth de Google (API de YouTube) |
| `config.py` | Credenciales de la API de afiliados de AliExpress |
| `aliexpress_cookies.json` | Cookies de sesión de AliExpress (exportadas con [Cookie-Editor](https://cookie-editor.com/)) |
| `cupones.txt` | Bloque de cupones que se insertará en las descripciones |

## cupones.txt

Pega el bloque de texto exactamente como quieres que aparezca en las descripciones. No hace falta que incluyas la fecha — el programa la añade solo.

Ejemplo:

```
*📌CUPONES de DESCUENTOS de ALIEXPRESS*
💰3€ para compras superiores a 15€: ABCD1234
💰5€ para compras superiores a 30€: EFGH5678
📢Prueba varios ya que no todos son válidos para todos los productos ni todos los países!
```

El programa detecta el bloque en cada vídeo buscando la primera y última línea de este archivo, así que si cambias el texto asegúrate de actualizar el archivo antes de ejecutar.

## config.py

Este archivo contiene las credenciales de la API de afiliados de AliExpress. Por ahora el programa no la usa activamente, pero el archivo debe existir.

Para obtener los valores, regístrate en el [Portal de Afiliados de AliExpress](https://portals.aliexpress.com/) y ve a **Herramientas → API**:

- `ALIEXPRESS_APP_KEY` — identificador de tu aplicación
- `ALIEXPRESS_APP_SECRET` — clave secreta de tu aplicación
- `ALIEXPRESS_TRACKING_ID` — tu ID de seguimiento de afiliado

```python
ALIEXPRESS_APP_KEY = 'tu_app_key'
ALIEXPRESS_APP_SECRET = 'tu_app_secret'
ALIEXPRESS_TRACKING_ID = 'tu_tracking_id'
```

## Uso

```bash
python YouTubeCodes.py
```

### Modo offline

```bash
python YouTubeCodes.py --offline
```

El modo offline **no llama a la API de YouTube en el arranque**, usando en su lugar el caché local guardado en la última ejecución normal (`cache_videos.json`). Esto es útil cuando quieres usar la herramienta sin gastar cuota de API, por ejemplo para comprobar links de AliExpress o Amazon, que no consumen cuota de YouTube.

En modo offline el menú muestra un aviso en rojo recordando que los datos pueden estar desactualizados. Las opciones que modifican YouTube (actualizar cupones, actualizar comentarios) siguen disponibles — es decisión tuya usarlas sabiendo que trabajas con datos en caché.

> **Nota:** Para usar el modo offline es necesario haber ejecutado el programa al menos una vez en modo normal, ya que es entonces cuando se genera el caché.

## Cuota de la API de YouTube

La API de YouTube tiene un límite de **10.000 unidades diarias**, que se resetea a medianoche hora del Pacífico. El coste aproximado de cada operación:

| Operación | Coste |
|---|---|
| Arranque (carga de vídeos) | ~5 unidades |
| Actualizar un vídeo con cupones | 50 unidades |
| Comprobar comentario fijado de un vídeo | 1 unidad |
| Actualizar un comentario | 50 unidades |
| Comprobar links de AliExpress/Amazon | 0 unidades (no usa API) |

El programa muestra el coste estimado antes de cada operación y enlaza a la consola de Google Cloud para ver la cuota disponible.

## Archivos generados

| Archivo | Descripción |
|---|---|
| `cache_videos.json` | Caché local de vídeos para el modo offline |
| `links_estado.json` | Resultado del último escaneo de links |
| `comentarios_estado.json` | Resultado del último escaneo de comentarios fijados |
| `links_rotos.txt` | Reporte detallado de links con problemas |
| `exclusiones.txt` | IDs de vídeos excluidos del bloque de cupones |

Todos estos archivos están en `.gitignore` y no se suben al repositorio.
