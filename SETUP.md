# Configuración inicial

## 1. Credenciales de YouTube (`client_secret.json`)

El programa necesita acceso a la API de YouTube para leer y editar las descripciones de tus vídeos. Para eso necesitas crear un proyecto en Google Cloud y descargar las credenciales OAuth.

1. Ve a [Google Cloud Console](https://console.cloud.google.com/)
2. Crea un proyecto nuevo (o usa uno existente)
3. En el menú lateral ve a **APIs y servicios → Biblioteca**
4. Busca **YouTube Data API v3** y actívala
5. Ve a **APIs y servicios → Credenciales**
6. Pulsa **Crear credenciales → ID de cliente de OAuth**
7. Tipo de aplicación: **Aplicación de escritorio**
8. Descarga el JSON y renómbralo a `client_secret.json`
9. Ponlo en la carpeta del proyecto

La primera vez que ejecutes el programa se abrirá el navegador para que autorices el acceso a tu cuenta. A partir de entonces el token se guarda en `token.pickle` y no vuelve a pedir autorización.

> Si tu proyecto de Google Cloud está en modo de prueba, añade tu cuenta de Google como usuario de prueba en **APIs y servicios → Pantalla de consentimiento de OAuth → Usuarios de prueba**.

---

## 2. Cookies de AliExpress (`aliexpress_cookies.json`)

El programa abre Chrome y navega a las páginas de producto de AliExpress para comprobar si los links siguen activos. Para que AliExpress no lo trate como un bot necesita que le pases tu sesión iniciada, y eso se hace mediante las cookies. Tener una sesión activa reduce considerablemente la frecuencia con la que AliExpress muestra CAPTCHAs durante la comprobación.

### Cómo exportar las cookies

1. Instala la extensión **Cookie-Editor** en Chrome: [cookie-editor.com](https://cookie-editor.com/)
2. Inicia sesión en [AliExpress](https://es.aliexpress.com) con tu cuenta
3. Con AliExpress abierto, haz clic en el icono de Cookie-Editor
4. Pulsa el botón **Export** (esquina inferior derecha) → **Export as JSON**
5. Guarda el contenido en un archivo llamado `aliexpress_cookies.json` en la carpeta del proyecto

### Cuándo renovarlas

Las cookies caducan con el tiempo (normalmente en semanas o meses). Si el programa empieza a detectar todos los links como rotos cuando no deberían estarlo, vuelve a exportar las cookies.

> Las cookies contienen tu sesión de AliExpress. No las compartas ni las subas a ningún repositorio público.
