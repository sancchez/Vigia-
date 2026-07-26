#!/usr/bin/env python3
"""
eval/run_eval.py
=================

Script de evaluacion standalone del "Proyecto Ciberseguridad IA" (ver
eval/README.md para como encaja en el loop de la seccion 8.2 del plan maestro).

Que hace
--------
1. Carga la ground truth (eval/ground_truth.yaml) — la lista de vulnerabilidades
   REALES y conocidas de OWASP Juice Shop, usada como laboratorio de evaluacion.
2. Carga una lista de "hallazgos reportados" (por defecto eval/sample_findings.json)
   en el formato en el que el pipeline real (orchestrator/agents, todavia no
   construido en este repo) deberia emitir sus resultados.
3. Empareja cada hallazgo reportado contra la ground truth usando matching por
   tipo + similitud de ubicacion (no exact-string-match ingenuo, ver
   `locations_match` mas abajo).
4. Calcula TP / FP / FN, precision y recall, e imprime un reporte de texto plano.

Esquema esperado de "hallazgos reportados por el pipeline" (JSON)
------------------------------------------------------------------
Este es el contrato que se espera del pipeline real cuando exista
(referenciado aqui como comentario/nota, NO como import real, porque
orchestrator/ y agents/ los construye otro agente en paralelo):

    # from orchestrator.reporting import FindingsSchema  # (referencia futura, no se importa)

El JSON debe tener la forma:

    {
      "findings": [
        {
          "id": "F-001",                 # str, identificador unico del hallazgo
          "type": "sql_injection",       # str, una de las categorias conocidas
                                          #   (sql_injection, xss_reflected, xss_stored,
                                          #    broken_access_control, jwt_vuln,
                                          #    sensitive_data_exposure, path_traversal,
                                          #    broken_authentication, ...)
          "endpoint": "/rest/user/login",# str, ubicacion/endpoint aproximado
                                          #   (alias aceptado: "location")
          "severity": "critical",        # str: critical | high | medium | low
          "confidence": 0.95,            # float 0-1, confianza del agente/herramienta
          "description": "..."           # str, descripcion corta legible
        },
        ...
      ]
    }

Ground truth (eval/ground_truth.yaml)
--------------------------------------
Lista de vulnerabilidades reales bajo la clave `vulnerabilities`, cada una con
`id`, `type`, `location`, `severity`, `description`. Ver eval/known_vulnerabilities.md
para el catalogo legible en prosa, 1:1 con el YAML.

Algoritmo de matching (por que no es exact string match)
----------------------------------------------------------
Los endpoints reales rara vez coinciden caracter por caracter entre lo que
reporta una herramienta (que suele incluir query params, ids concretos,
encoding, texto extra entre parentesis, etc.) y la ubicacion aproximada de la
ground truth (que usa placeholders como `{id}` o `:file`). Por eso:

  1. Se normaliza cada ubicacion: se corta en el primer espacio/parentesis
     (texto aclaratorio) o `?` (query string), se pasan placeholders tipo
     `{id}` o `:file` a cadena vacia, se quita slash final y se pasa a
     minusculas.
  2. Dos ubicaciones "matchean" si una es prefijo de la otra tras normalizar,
     o si comparten los mismos primeros segmentos de path no vacios.
  3. El tipo (`type`) debe coincidir exactamente (normalizado a snake_case,
     minusculas) — el matching de ubicacion por si solo no basta, porque dos
     vulnerabilidades distintas pueden vivir en el mismo endpoint
     (ver VULN-004 y VULN-009 en la ground truth, ambas en /rest/... pero
     con tipos distintos).
  4. Cada hallazgo reportado se puede emparejar como maximo con UNA entrada
     de la ground truth (y viceversa) — un emparejamiento 1 a 1 greedy.

Requisitos
----------
Solo dependencia externa: PyYAML (`pip install pyyaml`). Si no esta instalado,
el script lo indica claramente y aborta con un mensaje de instalacion en vez
de fallar con un traceback opaco.

Uso
---
    python run_eval.py
    python run_eval.py --ground-truth ground_truth.yaml --findings sample_findings.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Optional

# Fuerza UTF-8 en stdout/stderr: en Windows la consola por defecto (cp1252/cp850)
# rompe los acentos de este reporte. No afecta plataformas donde ya es UTF-8.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

try:
    import yaml
except ImportError:
    print(
        "ERROR: falta la dependencia 'PyYAML'.\n"
        "Instalala con:  pip install pyyaml\n"
        "(o) py -m pip install pyyaml\n",
        file=sys.stderr,
    )
    sys.exit(1)

# Fase 9, Grupo 0 (openspec/changes/fase9-motor-dirigido): normalización de
# tipo/ubicación, capa de adaptación al CARGAR los findings (opción (a) de
# tasks.md 0.2 -- la más barata, sin migración de DB). `tools/` vive un nivel
# arriba de `eval/`; este script se corre como archivo suelto
# (`py -3 eval/run_eval.py`), así que Python solo pone `eval/` en `sys.path`
# por default -- hay que agregar la raíz del repo a mano para poder
# `import tools.normalizacion_hallazgos`. Mismo patrón que ya usa
# `eval/regression_runner.py` con `run_eval` (línea ~47 de ese archivo).
_ROOT_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT_REPO not in sys.path:
    sys.path.insert(0, _ROOT_REPO)

from tools.normalizacion_hallazgos import tipo_canonico, ubicacion_relativa  # noqa: E402


# ---------------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------------

@dataclass
class GroundTruthVuln:
    id: str
    type: str
    location: str
    severity: str
    description: str = ""


@dataclass
class Finding:
    id: str
    type: str
    location: str
    severity: str
    confidence: float = 0.0
    description: str = ""


@dataclass
class MatchResult:
    true_positives: list = field(default_factory=list)   # list[(GroundTruthVuln, Finding)]
    false_positives: list = field(default_factory=list)  # list[Finding]
    false_negatives: list = field(default_factory=list)  # list[GroundTruthVuln]


# ---------------------------------------------------------------------------
# Carga de datos
# ---------------------------------------------------------------------------

def load_ground_truth(path: str) -> list[GroundTruthVuln]:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    raw_list = data.get("vulnerabilities", []) if data else []
    result = []
    for item in raw_list:
        result.append(
            GroundTruthVuln(
                id=str(item["id"]),
                type=str(item["type"]).strip().lower(),
                location=str(item["location"]).strip(),
                severity=str(item.get("severity", "unknown")).strip().lower(),
                description=str(item.get("description", "")).strip(),
            )
        )
    return result


def load_findings(path: str) -> list[Finding]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    raw_list = data.get("findings", []) if isinstance(data, dict) else data
    result = []
    for item in raw_list:
        # Fase 9, Grupo 0: acepta tanto el esquema documentado en el
        # docstring del módulo (`type`/`endpoint`/`location`) como el de una
        # fila de la tabla `findings` tal como se persiste/exporta hoy
        # (`tipo`/`endpoint`) o el de un export ad-hoc en español
        # (`tipo`/`ubicacion`, ver `eval/baseline_real/dvwa_autenticado_*`).
        # Sin esto, un hallazgo correcto queda con `type`/`location` vacíos
        # y jamás puede matchear -- medido: ver `eval/failure_log.md`.
        tipo_crudo = item.get("type") or item.get("tipo") or ""
        location = (
            item.get("endpoint")
            or item.get("location")
            or item.get("ubicacion")
            or ""
        )
        result.append(
            Finding(
                id=str(item.get("id", "?")),
                type=tipo_canonico(tipo_crudo),
                location=ubicacion_relativa(str(location).strip()),
                severity=str(item.get("severity") or item.get("severidad") or "unknown").strip().lower(),
                confidence=float(item.get("confidence", 0.0) or 0.0),
                description=str(item.get("description", "")).strip(),
            )
        )
    return result


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

_PLACEHOLDER_RE = re.compile(r"\{[^}]*\}|:[a-zA-Z_][a-zA-Z0-9_]*")
_CUTOFF_RE = re.compile(r"[\s(?].*$")


def normalize_location(raw: str) -> str:
    """Normaliza un endpoint/ubicacion para comparacion aproximada.

    - Corta cualquier texto aclaratorio despues de un espacio o parentesis,
      y cualquier query string despues de '?'.
    - Reemplaza placeholders tipo {id} o :file por cadena vacia.
    - Quita slash final, pasa a minusculas.
    """
    if not raw:
        return ""
    cut = _CUTOFF_RE.sub("", raw.strip())
    cut = _PLACEHOLDER_RE.sub("", cut)
    cut = cut.lower().rstrip("/")
    return cut


def locations_match(gt_location: str, finding_location: str) -> bool:
    gt_norm = normalize_location(gt_location)
    f_norm = normalize_location(finding_location)
    if not gt_norm or not f_norm:
        return False
    if gt_norm == f_norm:
        return True
    if gt_norm.startswith(f_norm) or f_norm.startswith(gt_norm):
        return True
    # Comparar por segmentos de path compartidos (al menos 2 segmentos no vacios)
    gt_segments = [s for s in gt_norm.split("/") if s]
    f_segments = [s for s in f_norm.split("/") if s]
    if not gt_segments or not f_segments:
        return False
    shared = 0
    for a, b in zip(gt_segments, f_segments):
        if a == b:
            shared += 1
        else:
            break
    return shared >= 2 or (shared >= 1 and shared == min(len(gt_segments), len(f_segments)))


def match_findings(
    ground_truth: list[GroundTruthVuln], findings: list[Finding]
) -> MatchResult:
    result = MatchResult()
    unmatched_findings = list(findings)

    for gt in ground_truth:
        match: Optional[Finding] = None
        for f in unmatched_findings:
            if f.type == gt.type and locations_match(gt.location, f.location):
                match = f
                break
        if match is not None:
            result.true_positives.append((gt, match))
            unmatched_findings.remove(match)
        else:
            result.false_negatives.append(gt)

    result.false_positives = unmatched_findings
    return result


# ---------------------------------------------------------------------------
# Metricas y reporte
# ---------------------------------------------------------------------------

def compute_metrics(match: MatchResult) -> dict:
    tp = len(match.true_positives)
    fp = len(match.false_positives)
    fn = len(match.false_negatives)
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall) > 0
        else 0.0
    )
    # Fase 7 (openspec/changes/fase7-confianza-calidad): tasa de falsos
    # positivos como KPI de producto explícito, no solo un derivado implícito
    # de precision. Mismo denominador que precision (TP+FP) -- fp_rate es
    # simplemente `1 - precision` cuando hay al menos un hallazgo reportado,
    # pero se expone con su propio nombre porque el gate de regresión
    # (`regression_runner.py`) y el reporte lo tratan como métrica de primera
    # clase (spec.md: "El harness ... MUST exponer una tasa de falsos
    # positivos (FP-rate)").
    fp_rate = fp / (tp + fp) if (tp + fp) > 0 else 0.0
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "fp_rate": fp_rate,
    }


def print_report(match: MatchResult, metrics: dict) -> None:
    line = "=" * 78
    print(line)
    print("REPORTE DE EVALUACION — Proyecto Ciberseguridad IA (eval/run_eval.py)")
    print(line)
    print()
    print(f"{'Metrica':<20}{'Valor':<10}")
    print("-" * 30)
    print(f"{'True Positives':<20}{metrics['tp']:<10}")
    print(f"{'False Positives':<20}{metrics['fp']:<10}")
    print(f"{'False Negatives':<20}{metrics['fn']:<10}")
    print(f"{'Precision':<20}{metrics['precision']:.2%}")
    print(f"{'Recall':<20}{metrics['recall']:.2%}")
    print(f"{'F1-score':<20}{metrics['f1']:.2%}")
    print(f"{'FP-rate':<20}{metrics['fp_rate']:.2%}")
    print()

    print(line)
    print(f"TRUE POSITIVES ({len(match.true_positives)})")
    print(line)
    if not match.true_positives:
        print("  (ninguno)")
    for gt, f in match.true_positives:
        print(f"  [{gt.id}] <-> [{f.id}]  tipo={gt.type}")
        print(f"      ground_truth: {gt.location}")
        print(f"      reportado:    {f.location}  (confianza={f.confidence:.2f})")
    print()

    print(line)
    print(f"FALSE POSITIVES ({len(match.false_positives)}) — reportado pero no es real / no está en la ground truth")
    print(line)
    if not match.false_positives:
        print("  (ninguno)")
    for f in match.false_positives:
        print(f"  [{f.id}] tipo={f.type} ubicacion={f.location} (confianza={f.confidence:.2f})")
        if f.description:
            print(f"      descripcion: {f.description}")
    print()

    print(line)
    print(f"FALSE NEGATIVES ({len(match.false_negatives)}) — existía pero el pipeline no lo encontró")
    print(line)
    if not match.false_negatives:
        print("  (ninguno)")
    for gt in match.false_negatives:
        print(f"  [{gt.id}] tipo={gt.type} ubicacion={gt.location} severidad={gt.severity}")
        if gt.description:
            print(f"      descripcion: {gt.description.strip()}")
    print()
    print(line)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(description="Evalua precision/recall de hallazgos contra ground truth de Juice Shop.")
    parser.add_argument(
        "--ground-truth",
        default=os.path.join(here, "ground_truth.yaml"),
        help="Ruta a ground_truth.yaml (default: eval/ground_truth.yaml)",
    )
    parser.add_argument(
        "--findings",
        default=os.path.join(here, "sample_findings.json"),
        help="Ruta al JSON de hallazgos reportados (default: eval/sample_findings.json)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help=(
            "Emite SOLO las metricas (tp/fp/fn/precision/recall/f1/fp_rate) como "
            "JSON en stdout, sin el reporte de texto -- salida parseable por "
            "maquina para `eval/regression_runner.py` u otros scripts."
        ),
    )
    args = parser.parse_args()

    if not os.path.isfile(args.ground_truth):
        print(f"ERROR: no se encontro ground truth en: {args.ground_truth}", file=sys.stderr)
        return 1
    if not os.path.isfile(args.findings):
        print(f"ERROR: no se encontro archivo de hallazgos en: {args.findings}", file=sys.stderr)
        return 1

    ground_truth = load_ground_truth(args.ground_truth)
    findings = load_findings(args.findings)

    if not ground_truth:
        print("ADVERTENCIA: la ground truth esta vacia.", file=sys.stderr)

    match = match_findings(ground_truth, findings)
    metrics = compute_metrics(match)

    if args.json:
        print(json.dumps(metrics, ensure_ascii=False))
    else:
        print_report(match, metrics)

    return 0


if __name__ == "__main__":
    sys.exit(main())
