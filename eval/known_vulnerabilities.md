# Catálogo de vulnerabilidades conocidas — OWASP Juice Shop

Este documento es la **ground truth humana** (legible) usada para medir precisión y
recall del pipeline de agentes (ver `eval/README.md`, sección "El loop"). Su versión
parseable por máquina, 1:1 con este catálogo, vive en `eval/ground_truth.yaml`.

Fuente: challenges oficiales y documentados de OWASP Juice Shop
(https://github.com/juice-shop/juice-shop, `pwning-guide` / `data/static/codefixes`
y el "score board" oficial del proyecto). Solo se listan aquí vulnerabilidades que
Juice Shop realmente contiene y que están descritas en su documentación pública —
no se inventa ninguna clase de falla nueva.

Cada entrada corresponde 1:1 a un `id` en `ground_truth.yaml`.

---

## VULN-001 — SQL Injection en login (bypass de autenticación)

- **Tipo:** `sql_injection`
- **Ubicación aproximada:** `POST /rest/user/login` (formulario de login, campo email)
- **Severidad:** Crítica
- **Descripción:** El campo de email del formulario de login concatena la entrada
  del usuario directamente en una consulta SQL/sequelize sin parametrizar,
  permitiendo un bypass de autenticación clásico (`' OR 1=1--`) para iniciar sesión
  como cualquier usuario, incluido el administrador, sin conocer la contraseña.
  Corresponde al challenge oficial "Login Admin" / "Login Bender" de Juice Shop.

## VULN-002 — XSS reflejado en buscador de productos

- **Tipo:** `xss_reflected`
- **Ubicación aproximada:** `GET /rest/products/search?q=<payload>` (buscador de la
  tienda, componente de búsqueda del frontend)
- **Severidad:** Media
- **Descripción:** El parámetro de búsqueda de productos se refleja en el DOM sin
  sanitizar correctamente, permitiendo ejecutar JavaScript arbitrario en el
  navegador de la víctima si hace clic en un enlace manipulado. Corresponde al
  challenge oficial "DOM XSS".

## VULN-003 — XSS persistente/almacenado en reseñas de producto (o feedback)

- **Tipo:** `xss_stored`
- **Ubicación aproximada:** formulario de comentarios/reseñas de producto y
  formulario de "Customer Feedback" (`/rest/products/reviews`,
  `/api/Feedbacks`)
- **Severidad:** Alta
- **Descripción:** El contenido enviado por el usuario en reseñas de producto o
  en el formulario de feedback no se sanitiza al almacenarse ni al renderizarse
  de vuelta, permitiendo XSS persistente que afecta a cualquier usuario (incluido
  un administrador) que visualice esa reseña/feedback después. Corresponde a los
  challenges oficiales "Persistent XSS" (varias variantes, ej. producto/feedback).

## VULN-004 — Broken Access Control / IDOR en cesta de la compra de otro usuario

- **Tipo:** `broken_access_control`
- **Ubicación aproximada:** `GET/PUT /rest/basket/{id}` (endpoint de la cesta,
  el `id` de otro usuario es adivinable/enumerable)
- **Severidad:** Alta
- **Descripción:** Un usuario autenticado puede acceder o modificar la cesta de
  la compra de otro usuario simplemente cambiando el `id` en la URL/petición, sin
  que el backend verifique que la cesta pertenece al usuario autenticado
  (Insecure Direct Object Reference). Corresponde al challenge oficial
  "View Basket" / "Basket Access".

## VULN-005 — Broken Access Control en panel de administración

- **Tipo:** `broken_access_control`
- **Ubicación aproximada:** ruta del frontend `/#/administration` y sus llamadas
  a `/rest/admin/*`
- **Severidad:** Crítica
- **Descripción:** El panel de administración es alcanzable por cualquier
  usuario autenticado (o en algunas versiones, sin verificación de rol adecuada
  en el cliente), exponiendo datos de usuarios, pedidos y funciones sensibles
  sin control de acceso basado en rol robusto del lado del servidor. Corresponde
  al challenge oficial "Access the administration section of the store".

## VULN-006 — Vulnerabilidad de JWT (clave débil / algoritmo inseguro)

- **Tipo:** `jwt_vuln`
- **Ubicación aproximada:** tokens emitidos en `POST /rest/user/login`, validados
  en middleware de autenticación de la API (`Authorization: Bearer <jwt>`)
- **Severidad:** Alta
- **Descripción:** Los JWT de Juice Shop están firmados con una clave predecible/
  débil (documentada en el propio proyecto como parte del reto) y en algunos
  desafíos se explota la confusión de algoritmo (RS256→HS256) o la manipulación
  del payload (ej. cambiar el rol a "admin") para forjar tokens válidos.
  Corresponde a los challenges oficiales "Forged Signed JWT" / "JWT Issues".

## VULN-007 — Exposición de datos sensibles (feed de metadatos / respaldo)

- **Tipo:** `sensitive_data_exposure`
- **Ubicación aproximada:** archivos accesibles directamente como
  `/ftp/acquisitions.md`, `/ftp/eastere.gg`, `/ftp/package.json.bak` y rutas
  similares dentro del directorio `/ftp` servido públicamente
- **Severidad:** Alta
- **Descripción:** El servidor expone públicamente un directorio "ftp" simulado
  con archivos de respaldo y documentos internos (contratos, notas de
  adquisición) que no deberían ser accesibles sin autenticación. Corresponde a
  los challenges oficiales "Confidential Document" / "Deprecated Backup" /
  "Easter Egg".

## VULN-008 — Path Traversal en descarga de archivos del directorio ftp

- **Tipo:** `path_traversal`
- **Ubicación aproximada:** `GET /ftp/:file` — el parámetro de nombre de archivo
  no valida correctamente secuencias `../`, permitiendo salir del directorio
  `ftp` previsto (ej. para llegar a `package.json` en la raíz del servidor)
- **Severidad:** Alta
- **Descripción:** El endpoint de descarga de archivos del directorio "ftp" es
  vulnerable a path traversal (con distintos niveles de codificación/encoding
  para evadir el filtro parcial que Juice Shop sí aplica), permitiendo leer
  archivos fuera del directorio previsto. Corresponde al challenge oficial
  "Deprecated Backup" / "Directory Listing" y sus variantes con traversal.

## VULN-009 — Inyección NoSQL/JSON en reseñas de producto (edición de reseña ajena)

- **Tipo:** `broken_access_control`
- **Ubicación aproximada:** `PATCH /rest/products/reviews` (edición de reseñas)
- **Severidad:** Media
- **Descripción:** El endpoint de edición de reseñas permite modificar reseñas
  que pertenecen a otro usuario porque no valida la propiedad del recurso
  (variante adicional de IDOR/broken access control sobre un recurso distinto
  a la cesta). Corresponde al challenge oficial "Edit any user's Review".

## VULN-010 — Reset de contraseña débil (seguridad basada en preguntas de seguridad predecibles)

- **Tipo:** `broken_authentication`
- **Ubicación aproximada:** `POST /rest/user/reset-password` y flujo de
  "pregunta de seguridad" del frontend
- **Severidad:** Media
- **Descripción:** El flujo de recuperación de contraseña usa preguntas de
  seguridad cuya respuesta es fácilmente adivinable o buscable públicamente
  (ej. "¿nombre de tu mascota?" para una cuenta de demo con datos públicos),
  permitiendo tomar el control de una cuenta ajena sin necesidad de la
  contraseña original. Corresponde al challenge oficial "Reset Bender's / Jim's
  password".

## VULN-011 — Falta de rate limiting / fuerza bruta en login

- **Tipo:** `sensitive_data_exposure`
- **Ubicación aproximada:** `POST /rest/user/login`
- **Severidad:** Media
- **Descripción:** El endpoint de login no impone límite de intentos, lo que
  permite ataques de fuerza bruta/credential stuffing contra cuentas de
  usuario sin bloqueo ni captcha. Relacionado con el challenge oficial "Login
  CISO" resuelto vía fuerza bruta sobre contraseñas filtradas.

---

## Nota de alcance

Esta lista cubre 11 clases de vulnerabilidad representativas y bien documentadas
de Juice Shop, suficientes para ejercitar el pipeline de evaluación (categorías:
inyección, XSS reflejado y persistente, control de acceso roto en dos variantes,
JWT, exposición de datos, path traversal, y autenticación débil). Juice Shop tiene
más de 100 challenges en total; este catálogo NO pretende ser exhaustivo de todos
ellos, sino una muestra representativa y verificable contra la cual medir
precisión/recall de forma consistente. Se puede ampliar agregando nuevas entradas
`VULN-0XX` a este archivo y a `ground_truth.yaml` en paralelo (ver
`eval/README.md`).
