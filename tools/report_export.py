"""Exportación de reportes markdown a PDF/DOCX — item transversal de HANDOFF.md
("reportes descargables (PDF/DOCX)").

Ambos reportes que ya existen en el proyecto (`agents/reporteria.py` para un
scan técnico, `agents/cumplimiento.py` para el reporte de cumplimiento
normativo) generan su salida como texto markdown vía el LLM. Este módulo NO
vuelve a generar contenido: solo convierte ese markdown ya producido a bytes
descargables, en el servidor, para que la descarga sea igual sin importar el
navegador del cliente.

Elección de librerías (ver HANDOFF.md / handoff de esta sesión): `fpdf2` y
`python-docx` ya estaban instaladas en el entorno del proyecto y son puras en
Python — sin binarios de sistema (wkhtmltopdf, GTK/Pango de weasyprint) que
son dolorosos de instalar en Windows. A cambio, el parser de markdown de
abajo es deliberadamente simple: encabezados (`#`/`##`/`###`), listas con
`-`/`*`, negrita `**texto**` y párrafos. Tablas markdown (`| col | col |`)
se degradan a texto plano línea por línea — una limitación conocida, no un
renderer de markdown completo.
"""

from __future__ import annotations

import re
from io import BytesIO

from docx import Document
from fpdf import FPDF

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def _strip_bold(text: str) -> str:
    return _BOLD_RE.sub(r"\1", text)


_TYPOGRAFIA_A_ASCII = {
    "—": "-",  # em dash — Claude lo usa constantemente en sus reportes
    "–": "-",  # en dash
    "‘": "'",
    "’": "'",
    "“": '"',
    "”": '"',
    "…": "...",
    "•": "-",  # bullet suelto dentro de texto (no al inicio de línea)
}


def _latin1(text: str) -> str:
    """Las fuentes core de fpdf2 (helvetica) solo cubren latin-1.

    El acento español (á, é, í, ó, ú, ñ, ¿, ¡) SÍ está en latin-1, pero la
    puntuación "tipográfica" que Claude usa todo el tiempo en sus reportes
    (— em dash, comillas curvas, …) NO — sin este reemplazo previo, cada
    em dash del reporte real se convertía en un "?" suelto (bug real
    encontrado leyendo el PDF generado contra un reporte de verdad, no una
    suposición). Emoji/símbolos verdaderamente raros sí se reemplazan con
    "?" en vez de reventar la generación completa del PDF.
    """
    for original, ascii_equivalente in _TYPOGRAFIA_A_ASCII.items():
        text = text.replace(original, ascii_equivalente)
    return text.encode("latin-1", errors="replace").decode("latin-1")


def markdown_to_pdf_bytes(markdown_text: str, titulo: str = "Reporte") -> bytes:
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    def _linea(texto: str, alto: float) -> None:
        # `new_x="LMARGIN", new_y="NEXT"` es obligatorio aquí: el default de
        # fpdf2 (`new_x=RIGHT`) deja el cursor pegado al margen derecho
        # después de cada `multi_cell`, así que la SIGUIENTE línea revienta
        # con "Not enough horizontal space to render a single character" —
        # bug real encontrado probando esto contra un reporte de verdad, no
        # una suposición.
        pdf.multi_cell(0, alto, _latin1(texto), new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("helvetica", "B", 16)
    _linea(titulo, 10)
    pdf.ln(2)

    for raw_line in markdown_text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            pdf.ln(3)
            continue
        if stripped in ("---", "***"):
            pdf.ln(2)
            continue
        if line.startswith("### "):
            pdf.set_font("helvetica", "B", 12)
            _linea(_strip_bold(line[4:]), 8)
        elif line.startswith("## "):
            pdf.set_font("helvetica", "B", 13)
            _linea(_strip_bold(line[3:]), 8)
        elif line.startswith("# "):
            pdf.set_font("helvetica", "B", 15)
            _linea(_strip_bold(line[2:]), 9)
        elif stripped.startswith("- ") or stripped.startswith("* "):
            pdf.set_font("helvetica", "", 10.5)
            _linea(f"  - {_strip_bold(stripped[2:])}", 6)
        else:
            pdf.set_font("helvetica", "", 10.5)
            _linea(_strip_bold(line), 6)

    return bytes(pdf.output())


_SEVERIDADES_ORDEN: tuple[str, ...] = ("critical", "high", "medium", "low", "info")
_SEVERIDAD_LABEL = {
    "critical": "Críticos",
    "high": "Altos",
    "medium": "Medios",
    "low": "Bajos",
    "info": "Info",
}


def construir_markdown_ejecutivo(
    *,
    tenant_nombre: str,
    assets: list[dict],
    postura_por_activo: dict[str, dict[str, int]],
    kpis: dict,
    generado_en: str,
    dominios_clon: list[dict] | None = None,
    kpis_marca: dict | None = None,
) -> str:
    """Compone el markdown consolidado multi-activo del export ejecutivo
    (Fase 6c, `openspec/changes/fase6-endurecimiento-produccion`).

    No recalcula nada: `kpis` viene tal cual de `_kpis_de_tenant` (la misma
    capa que sirve `GET /kpis`, ver design.md Decisión 6c.2 -- "reusar el
    renderer") y `postura_por_activo` de `_postura_abierta_por_activo` en
    `api/main.py`. Este módulo solo da formato -- vive junto a
    `markdown_to_pdf_bytes`/`markdown_to_docx_bytes` porque es
    formato/infra, no un prompt de razonamiento (no toca
    `vigia_core_private/`).

    `assets` es una lista de filas crudas de la tabla `assets` (dicts con al
    menos `id`, `tipo`, `valor`, `verificado`, `autorizacion_activa`).

    `dominios_clon` / `kpis_marca` (opcionales) alimentan la sección
    "Exposición de marca": son, respectivamente, la salida de
    `api/main.py::_dominio_clon_out_de_row` (dicts con `endpoint`, `severidad`,
    `estado`, `score`, `scoring`, `screenshot`, `takedown`) y la de
    `GET /antisuplantacion/kpis`. Se pasan ya resueltos, este módulo no
    consulta la base -- mismo criterio que el resto de la función. Si vienen
    vacíos, la sección declara explícitamente que no hay vigilancia de marca
    corrida todavía, en vez de omitirse en silencio: un reporte que no
    menciona la marca es indistinguible de uno donde la marca está limpia.
    """
    lineas: list[str] = []
    lineas.append("# Reporte Ejecutivo — Vigia")
    lineas.append("")
    lineas.append(f"**Tenant:** {tenant_nombre}")
    lineas.append(f"**Generado:** {generado_en}")
    lineas.append(f"**Activos incluidos:** {len(assets)}")
    lineas.append("")
    lineas.append("## KPIs de remediación")
    lineas.append("")
    pct_sla = kpis.get("porcentaje_dentro_sla")
    lineas.append(
        f"- **% de hallazgos abiertos dentro de SLA:** "
        f"{pct_sla if pct_sla is not None else 'sin hallazgos abiertos'}"
        + ("%" if pct_sla is not None else "")
    )
    lineas.append("- **MTTR por severidad (días):**")
    mttr_por_sev = {m["severidad"]: m for m in kpis.get("mttr_por_severidad", [])}
    for sev in _SEVERIDADES_ORDEN:
        m = mttr_por_sev.get(sev)
        valor = m["mttr_dias"] if m and m.get("mttr_dias") is not None else "sin cierres"
        lineas.append(f"  - {_SEVERIDAD_LABEL[sev]}: {valor}")
    lineas.append("")
    lineas.append("## Postura por activo")
    lineas.append("")
    if not assets:
        lineas.append("Todavía no hay activos registrados para este tenant.")
    for asset in assets:
        estado_verif = "verificado" if asset.get("verificado") else "sin verificar"
        # Fix H-4: antes esta línea era engañosa por construcción -- ningún
        # endpoint escribía `autorizacion_activa`, así que TODO activo salía
        # como "solo escaneo pasivo" incluso si se le había corrido un ZAP
        # activo por `POST /scan/activo`. Ahora la columna sí se escribe
        # (`POST /assets/{id}/autorizacion-activa`) y la frase se acota a lo
        # que la columna realmente significa: si el escaneo activo RECURRENTE
        # está habilitado, no si alguna vez hubo un escaneo activo.
        if asset.get("autorizacion_activa"):
            estado_autor = "con autorización firmada para escaneo activo recurrente"
            if asset.get("autorizado_por"):
                estado_autor += f" (autorizó: {asset.get('autorizado_por')})"
        else:
            estado_autor = (
                "sin autorización firmada para escaneo activo recurrente "
                "(el ciclo programado solo hace recon pasivo)"
            )
        lineas.append(f"### {asset.get('valor')} ({asset.get('tipo')})")
        lineas.append(f"- Estado: {estado_verif}, {estado_autor}")
        conteo = postura_por_activo.get(asset.get("id"), {sev: 0 for sev in _SEVERIDADES_ORDEN})
        total = sum(conteo.values())
        if total == 0:
            lineas.append("- Sin hallazgos abiertos.")
        else:
            for sev in _SEVERIDADES_ORDEN:
                n = conteo.get(sev, 0)
                if n:
                    lineas.append(f"- {_SEVERIDAD_LABEL[sev]}: {n}")
        lineas.append("")

    lineas.extend(_seccion_exposicion_marca(dominios_clon or [], kpis_marca or {}))

    return "\n".join(lineas)


# Señales del scoring 0-4 de `tools/antisuplantacion.py::InfraScore`, en el
# orden en que se explican al cliente (de la más básica a la más específica).
_SEÑALES_SCORING: tuple[tuple[str, str], ...] = (
    ("dns_a", "DNS"),
    ("mx", "correo (MX)"),
    ("ssl", "certificado TLS"),
    ("http", "sitio web respondiendo"),
)


def _seccion_exposicion_marca(dominios_clon: list[dict], kpis_marca: dict) -> list[str]:
    """Sección "Exposición de marca" del export ejecutivo.

    Antes de esta sección el único export descargable del producto
    (`GET /reports/ejecutivo/download`) contenía exclusivamente KPIs de
    remediación técnica -- ni un dominio clon, ni su score, ni el borrador de
    takedown, pese a que la protección de marca es la capa que el producto
    vende. Un cliente que descargaba "el reporte" no veía nada de eso.

    Solo se listan datos ya calculados aguas arriba. En particular NO se
    recalcula el score ni se reescribe el borrador: se transcribe lo que ya
    está persistido en `findings.raw_json`, para que el PDF y el panel web
    nunca puedan discrepar.
    """
    lineas: list[str] = []
    lineas.append("## Exposición de marca")
    lineas.append("")

    total = kpis_marca.get("total_clones")
    activos = kpis_marca.get("clones_activos")
    if total is not None:
        lineas.append(f"- **Suplantaciones detectadas:** {total}")
    if activos is not None:
        lineas.append(f"- **Con infraestructura activa:** {activos}")
    mttd = kpis_marca.get("mttd_dias")
    if mttd is not None:
        lineas.append(f"- **MTTD (tiempo medio hasta detección):** {mttd} día(s) (aproximado)")
    tttd = kpis_marca.get("tttd_dias")
    if tttd is not None:
        lineas.append(f"- **TTTD (tiempo medio hasta takedown):** {tttd} día(s)")
    if total is not None or activos is not None:
        lineas.append("")

    if not dominios_clon:
        lineas.append(
            "No hay vigilancia de marca ejecutada para este tenant todavía. "
            "Ejecute la protección de marca sobre un dominio verificado "
            "(`POST /antisuplantacion`) para poblar esta sección."
        )
        lineas.append("")
        return lineas

    lineas.append(
        "Cada dominio se puntúa 0-4 contando cuántas señales de infraestructura activa "
        "responden: DNS, correo (MX), certificado TLS y sitio web. Un 4/4 es un clon "
        "operativo; un 0/4 es un dominio registrado pero sin servicios levantados."
    )
    lineas.append("")

    # Los más peligrosos primero: score alto = clon operativo.
    ordenados = sorted(
        dominios_clon,
        key=lambda d: (-(d.get("score") or 0), str(d.get("endpoint") or "")),
    )
    for d in ordenados:
        endpoint = d.get("endpoint") or "(sin dominio)"
        score = d.get("score") or 0
        lineas.append(f"### {endpoint}")
        # `etapa_marca` (calculada por `api/main.py::_etapa_marca`) es la misma
        # etiqueta que ve el cliente en el panel. Mostrar acá el `estado` crudo
        # ("en_progreso") hacía que el PDF y la pantalla dijeran cosas
        # distintas sobre el mismo hallazgo. Fallback al estado crudo para
        # hallazgos proyectados por código anterior a este campo.
        lineas.append(
            f"- Severidad: {_SEVERIDAD_LABEL.get(str(d.get('severidad')), d.get('severidad'))}"
            f" · Estado: {d.get('etapa_marca') or d.get('estado')}"
        )
        lineas.append(f"- Score de infraestructura activa: {score}/4")
        scoring = d.get("scoring") if isinstance(d.get("scoring"), dict) else {}
        if scoring:
            activas = [etiqueta for clave, etiqueta in _SEÑALES_SCORING if scoring.get(clave)]
            lineas.append(
                "- Señales activas: " + (", ".join(activas) if activas else "ninguna")
            )
            if scoring.get("mx"):
                lineas.append(
                    "- **Riesgo de correo:** el dominio tiene servidores de correo "
                    "configurados y puede enviar mensajes que aparenten ser de su organización."
                )
        if d.get("primera_vez_visto"):
            lineas.append(f"- Detectado por primera vez: {d.get('primera_vez_visto')}")
        # Trazabilidad de la gestión, no solo de la detección: si el cliente ya
        # mandó el takedown, el reporte tiene que poder probarlo (fecha,
        # destinatario) y dejar constancia del silencio del registrador -- es
        # exactamente la evidencia que se necesita para escalar a un tercero
        # (el registro del TLD, la marca, o un abogado).
        if d.get("takedown_enviado_en"):
            destinatario = d.get("takedown_destinatario")
            lineas.append(
                f"- Solicitud de eliminación enviada: {d.get('takedown_enviado_en')}"
                + (f" a {destinatario}" if destinatario else "")
            )
            sin_respuesta = d.get("registrador_sin_respuesta") or 0
            if sin_respuesta:
                lineas.append(
                    f"- **El registrador no respondió** ({sin_respuesta} seguimiento(s) registrado(s))."
                )
        lineas.append(
            "- Evidencia visual: "
            + (
                "captura de pantalla disponible en el panel"
                if d.get("screenshot")
                else "sin captura (el sitio no respondió o la captura no está habilitada)"
            )
        )
        if d.get("takedown"):
            lineas.append("- Borrador de solicitud de eliminación (takedown):")
            lineas.append("")
            for linea_takedown in str(d["takedown"]).splitlines():
                # Prefijo de cita: el parser markdown de este módulo no maneja
                # bloques `>`, así que se indenta como texto plano legible.
                lineas.append(f"    {linea_takedown}" if linea_takedown.strip() else "")
        lineas.append("")

    return lineas


def _add_runs(paragraph, text: str) -> None:
    """Agrega texto a un párrafo de python-docx, respetando **negrita** como runs separados."""
    pos = 0
    for m in _BOLD_RE.finditer(text):
        if m.start() > pos:
            paragraph.add_run(text[pos : m.start()])
        run = paragraph.add_run(m.group(1))
        run.bold = True
        pos = m.end()
    if pos < len(text):
        paragraph.add_run(text[pos:])


def markdown_to_docx_bytes(markdown_text: str, titulo: str = "Reporte") -> bytes:
    doc = Document()
    doc.add_heading(titulo, level=0)

    for raw_line in markdown_text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if stripped in ("---", "***"):
            continue
        if line.startswith("### "):
            doc.add_heading(line[4:], level=3)
        elif line.startswith("## "):
            doc.add_heading(line[3:], level=2)
        elif line.startswith("# "):
            doc.add_heading(line[2:], level=1)
        elif stripped.startswith("- ") or stripped.startswith("* "):
            _add_runs(doc.add_paragraph(style="List Bullet"), stripped[2:])
        else:
            _add_runs(doc.add_paragraph(), line)

    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()
