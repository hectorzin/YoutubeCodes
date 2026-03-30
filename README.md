# YouTubeCodes

Herramienta para actualizar automáticamente el bloque de cupones de AliExpress en las descripciones de todos tus vídeos de YouTube, y para comprobar si los links de productos siguen activos.

## Qué hace

- Lee el bloque de cupones del archivo `cupones.txt` y lo reemplaza en todas las descripciones donde aparezca
- Añade automáticamente el mes y año actual al encabezado del bloque si no lo tiene
- Detecta vídeos cuya descripción superaría los 5.000 caracteres tras la actualización y los omite con aviso
- Extrae todos los links de AliExpress de tus vídeos y comprueba si cada producto sigue disponible
- Distingue entre productos eliminados y productos no disponibles en tu región
- Detecta y pausa cuando AliExpress muestra un CAPTCHA, para que puedas resolverlo manualmente

## Requisitos

- Python 3.11 o superior
- Google Chrome instalado

## Instalación

```bash
pip install -r requirements.txt
```

## Archivos a crear

Antes de ejecutar el programa necesitas crear estos archivos. Ver [SETUP.md](SETUP.md) para instrucciones detalladas.

| Archivo | Descripción |
|---|---|
| `client_secret.json` | Credenciales OAuth de Google (API de YouTube) |
| `config.py` | Credenciales de la API de afiliados de AliExpress |
| `aliexpress_cookies.json` | Cookies de sesión de AliExpress |
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

```python
ALIEXPRESS_APP_KEY = 'tu_app_key'
ALIEXPRESS_APP_SECRET = 'tu_app_secret'
ALIEXPRESS_TRACKING_ID = 'tu_tracking_id'
```

## Uso

```bash
python YouTubeCodes.py
```

## Archivos de desarrollo

`test_links.py` es un script auxiliar usado durante el desarrollo para probar la detección de links de AliExpress (rotos, geo-restringidos, válidos) sin tener que ejecutar el programa completo. No es necesario para el uso normal de la herramienta.

El programa te irá preguntando paso a paso:
1. Muestra el bloque de cupones que va a usar y los vídeos donde lo encontró
2. Pregunta si quieres actualizar esos vídeos
3. Pregunta si quieres comprobar los links de AliExpress (esto abre Chrome automáticamente)
4. Genera un reporte `links_rotos.txt` con los links problemáticos
