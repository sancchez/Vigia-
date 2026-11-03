"""Remediación DETERMINISTA por hallazgo — sin LLM, entregable a un cliente.

Por qué existe
--------------
`agents/remediacion.py` le pide a Claude el texto de arreglo de cada
hallazgo. Sin `ANTHROPIC_API_KEY` (o con la API caída), cada remediación
quedaba así:

    "[Remediación pendiente de redacción — LLM no disponible: ...]"

Honesto, pero inentregable: el cliente paga y recibe un placeholder por cada
hallazgo. Y no hace falta un modelo de lenguaje para esto — el arreglo de un
"Content Security Policy (CSP) Header Not Set" o de un
`Access-Control-Allow-Origin: *` es conocido, documentado y siempre el
mismo. Que dependa de una API key es una decisión de arquitectura, no una
propiedad del problema.

De dónde sale el texto (nada se inventa)
----------------------------------------
1. **La propia herramienta.** ZAP trae `solution` por alerta y Nuclei
   `info.remediation`: texto escrito por los mantenedores del chequeo que
   disparó. Cuando está, se cita textualmente.
2. **Catálogo curado** (`_CATALOGO`), por familia de hallazgo, con el
   comando/directiva concreta. Cada entrada existe porque la familia salió
   en una corrida real (ver `eval/live_run_report.md`), no por completitud.
3. **Cuando no hay ni una ni otra**, el texto lo DICE y deriva a revisión
   humana, en vez de rellenar con genéricos que suenan a algo.

Mismo patrón y estilo que `tools/takedown.py` (borrador determinista de
takedown) y `tools/report_export.py` (markdown ejecutivo): función pura,
sin red, sin LLM, auditable con `git blame`.
"""

from __future__ import annotations

from tools.triage import (
    descripcion_de_la_herramienta,
    evidencia_corta,
    solucion_de_la_herramienta,
)

# Cada entrada: (patrones en el nombre del hallazgo, por qué importa, pasos).
# Los pasos son concretos y verificables — una cabecera a agregar, una
# directiva a cambiar —, no consejos genéricos de seguridad.
_CATALOGO: tuple[tuple[tuple[str, ...], str, tuple[str, ...]], ...] = (
    (
        ("content security policy", "csp:"),
        "Sin una política de contenido, el navegador ejecuta cualquier script que "
        "logre inyectarse en la página. Es la mitigación que convierte un XSS en "
        "un intento fallido en vez de un robo de sesión.",
        (
            "Agregar la cabecera `Content-Security-Policy` en el servidor web o el "
            "reverse proxy que sirve la aplicación.",
            "Empezar en modo observación con `Content-Security-Policy-Report-Only` "
            "para no romper funcionalidad, revisar los reportes y recién ahí pasar "
            "a modo bloqueo.",
            "Política inicial razonable: `default-src 'self'; object-src 'none'; "
            "frame-ancestors 'none'; base-uri 'self'`.",
            "Evitar `unsafe-inline` y `unsafe-eval`: con ellos la política queda "
            "puesta pero no protege del ataque que se busca frenar.",
        ),
    ),
    (
        ("cross-domain misconfiguration", "cors", "access-control-allow-origin"),
        "La aplicación le permite a CUALQUIER sitio web leer sus respuestas desde "
        "el navegador de un usuario autenticado. Un sitio de terceros puede pedir "
        "datos en nombre del usuario y leer el resultado.",
        (
            "Reemplazar `Access-Control-Allow-Origin: *` por una lista explícita de "
            "los dominios propios que realmente necesitan consumir la API.",
            "Validar el `Origin` de la petición contra esa lista en el servidor; no "
            "reflejarlo tal cual (reflejarlo equivale a dejar el comodín).",
            "Si ningún sitio externo necesita consumir la API desde el navegador, "
            "quitar la cabecera CORS por completo.",
        ),
    ),
    (
        ("httponly", "http only flag"),
        "Sin `HttpOnly`, cualquier JavaScript de la página puede leer la cookie de "
        "sesión. Eso convierte un XSS en un robo de sesión directo.",
        (
            "Marcar la cookie de sesión con `HttpOnly` donde se emite (configuración "
            "del framework de sesiones, no del servidor web).",
            "Revisar que ninguna funcionalidad del frontend dependa de leer esa "
            "cookie por JavaScript antes de aplicarlo.",
        ),
    ),
    (
        ("without secure flag", "cookie secure"),
        "Sin `Secure`, la cookie viaja también por HTTP plano y puede leerse en "
        "cualquier red intermedia (WiFi de un café, un proxy corporativo).",
        (
            "Marcar la cookie con `Secure` para que solo viaje por HTTPS.",
            "Verificar que todo el sitio esté servido por HTTPS antes de aplicarlo.",
        ),
    ),
    (
        ("samesite",),
        "Sin `SameSite`, el navegador manda la cookie de sesión también cuando la "
        "petición la origina otro sitio, que es la condición que habilita CSRF.",
        (
            "Marcar la cookie de sesión con `SameSite=Lax` (o `Strict` si no hay "
            "flujos de retorno desde sitios externos, como pasarelas de pago).",
            "`SameSite=None` solo con `Secure`, y únicamente si un sitio externo "
            "legítimo necesita la cookie.",
        ),
    ),
    (
        ("x-frame-options", "clickjacking", "frame-ancestors"),
        "Sin esta protección, otro sitio puede embeber la aplicación en un iframe "
        "invisible y hacer que el usuario haga clic en algo que no ve (clickjacking).",
        (
            "Agregar `Content-Security-Policy: frame-ancestors 'none'` (o "
            "`'self'` si la propia app se embebe a sí misma).",
            "Agregar además `X-Frame-Options: DENY` para navegadores viejos.",
        ),
    ),
    (
        ("x-content-type-options",),
        "Sin esta cabecera, el navegador puede reinterpretar el tipo de un archivo "
        "subido y ejecutar como script algo que se sirvió como texto o imagen.",
        ("Agregar `X-Content-Type-Options: nosniff` a todas las respuestas.",),
    ),
    (
        ("strict-transport-security", "hsts"),
        "Sin HSTS, la primera visita por HTTP puede ser interceptada y redirigida a "
        "un sitio controlado por un atacante antes de que HTTPS llegue a activarse.",
        (
            "Agregar `Strict-Transport-Security: max-age=31536000; includeSubDomains`.",
            "Confirmar antes que TODOS los subdominios sirven HTTPS válido: con "
            "`includeSubDomains` un subdominio sin certificado deja de ser accesible.",
        ),
    ),
    (
        ("referrer-policy",),
        "Sin política de referente, la aplicación filtra la URL completa (que puede "
        "incluir identificadores o tokens en la query) a cada sitio externo enlazado.",
        ("Agregar `Referrer-Policy: strict-origin-when-cross-origin`.",),
    ),
    (
        ("permissions policy", "permissions-policy", "feature policy", "feature-policy"),
        "Sin esta cabecera, cualquier script embebido puede pedir acceso a cámara, "
        "micrófono o ubicación en nombre del sitio.",
        (
            "Agregar `Permissions-Policy` denegando lo que la app no usa, por "
            "ejemplo `camera=(), microphone=(), geolocation=()`.",
        ),
    ),
    (
        ("cache-control", "cache control", "storable content", "retrieved from cache"),
        "Respuestas con datos de sesión guardadas en cachés intermedias o en el "
        "disco del navegador pueden quedar accesibles para el siguiente usuario del "
        "mismo equipo.",
        (
            "Servir las respuestas autenticadas con "
            "`Cache-Control: no-store, no-cache, must-revalidate`.",
            "Dejar el cacheo agresivo solo para recursos estáticos versionados "
            "(CSS/JS/imágenes con hash en el nombre).",
        ),
    ),
    (
        ("server leaks", "x-powered-by", "version information", "x-aspnet-version"),
        "El servidor publica su producto y versión exactos. No es explotable por sí "
        "mismo, pero le ahorra a un atacante el trabajo de averiguar contra qué "
        "vulnerabilidades conocidas apuntar.",
        (
            "Desactivar la firma del servidor (`server_tokens off` en Nginx, "
            "`ServerTokens Prod` + `ServerSignature Off` en Apache).",
            "Quitar la cabecera `X-Powered-By` en la capa de aplicación o en el "
            "reverse proxy.",
        ),
    ),
    (
        ("anti-csrf", "csrf"),
        "Los formularios no incluyen un token anti-CSRF visible. Si además la cookie "
        "de sesión no tiene `SameSite`, otro sitio puede disparar acciones en nombre "
        "del usuario autenticado.",
        (
            "Habilitar la protección CSRF del framework (token por sesión, validado "
            "del lado del servidor en cada petición que cambia estado).",
            "Combinarlo con `SameSite=Lax` en la cookie de sesión: son dos capas, no "
            "alternativas.",
        ),
    ),
    (
        ("directory browsing", "directory listing"),
        "El servidor lista el contenido de los directorios, exponiendo archivos que "
        "no estaban pensados para ser encontrados.",
        (
            "Desactivar el listado de directorios (`autoindex off` en Nginx, "
            "`Options -Indexes` en Apache).",
        ),
    ),
    (
        ("weak cipher", "insecure cipher", "deprecated tls", "sslv", "tls 1.0", "tls 1.1"),
        "La conexión acepta algoritmos de cifrado obsoletos, que hoy son atacables "
        "por un intermediario en la red.",
        (
            "Limitar el servidor a TLS 1.2 y 1.3 y a suites modernas.",
            "Verificar el resultado con una prueba de configuración TLS antes y "
            "después del cambio.",
        ),
    ),
)

_DERIVAR_A_HUMANO = (
    "No hay una remediación estándar catalogada para este hallazgo y la herramienta "
    "no documentó una. Requiere revisión de un analista antes de enviarse al cliente: "
    "este texto NO es una recomendación, es la declaración de que falta una."
)


def _entrada_catalogo(tipo: str) -> tuple[str, tuple[str, ...]] | None:
    clave = str(tipo or "").strip().lower()
    if not clave:
        return None
    for patrones, por_que, pasos in _CATALOGO:
        if any(patron in clave for patron in patrones):
            return por_que, pasos
    return None


def remediacion_determinista(hallazgo: dict) -> str:
    """Texto de remediación en markdown para UN hallazgo, sin LLM.

    `hallazgo` es un item de `prioritized_findings` (ya pasó por
    `tools/triage.py`, así que trae `tipo`, `endpoint`, `evidencia`,
    `severidad` y `urgencia_negocio`).
    """
    tipo = str(hallazgo.get("tipo") or "hallazgo sin nombre")
    lineas: list[str] = [f"**{tipo}**", ""]

    # --- Qué se encontró, con la evidencia concreta -------------------------
    endpoint = str(hallazgo.get("endpoint") or "").strip()
    evidencia = evidencia_corta(hallazgo.get("evidencia"))
    parametro = str(hallazgo.get("parametro") or "").strip()
    instancias = hallazgo.get("instancias")

    detalle: list[str] = []
    if endpoint:
        detalle.append(f"Ubicación: `{endpoint}`")
    if parametro:
        detalle.append(f"Parámetro afectado: `{parametro}`")
    if evidencia:
        detalle.append(f"Evidencia observada: `{evidencia}`")
    if isinstance(instancias, int) and instancias > 1:
        detalle.append(f"Instancias afectadas: {instancias}")
    if detalle:
        lineas.append("- " + "\n- ".join(detalle))
        lineas.append("")

    # --- Por qué importa ----------------------------------------------------
    entrada = _entrada_catalogo(tipo)
    if entrada:
        por_que, pasos = entrada
        lineas.append(f"Por qué importa: {por_que}")
        lineas.append("")
        lineas.append("Qué hay que hacer:")
        for i, paso in enumerate(pasos, start=1):
            lineas.append(f"{i}. {paso}")
        lineas.append("")
    else:
        descripcion = descripcion_de_la_herramienta(hallazgo.get("raw"))
        if descripcion:
            lineas.append(f"Por qué importa: {descripcion}")
            lineas.append("")

    # --- Qué dice la propia herramienta ------------------------------------
    solucion = solucion_de_la_herramienta(hallazgo.get("raw"))
    if solucion:
        etiqueta = "Solución documentada por la herramienta que lo detectó"
        if entrada:
            etiqueta += " (coincide con lo anterior)"
        lineas.append(f"{etiqueta}: {solucion}")
        lineas.append("")
    elif not entrada:
        lineas.append(_DERIVAR_A_HUMANO)
        lineas.append("")

    if hallazgo.get("exploit_publico"):
        cves = hallazgo.get("cves_confirmados") or hallazgo.get("cves_detectados") or []
        lineas.append(
            "Prioridad: existe exploit público conocido en Exploit-DB"
            + (f" para {', '.join(cves)}" if cves else "")
            + ". Atender antes que el resto de la lista."
        )
        lineas.append("")

    return "\n".join(lineas).strip()
