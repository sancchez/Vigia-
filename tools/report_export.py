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
        estado_autor = "autorizado para escaneo activo" if asset.get("autorizacion_activa") else "solo escaneo pasivo"
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

    return "\n".join(lineas)


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
