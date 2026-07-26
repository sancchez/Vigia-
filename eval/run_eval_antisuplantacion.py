#!/usr/bin/env python3
"""
eval/run_eval_antisuplantacion.py
==================================

Eval de regresion para el pipeline de antisuplantacion (WS2,
openspec/changes/fase10-antisuplantacion-lista-demo/tasks.md, tarea 2.3).

Analogo a `eval/run_eval.py`, pero en vez de comparar hallazgos de
vulnerabilidades contra una ground truth de vulnerabilidades, corre el
pipeline REAL de triaje de dominios lookalike
(`tools.antisuplantacion.triar_lookalikes`) sobre un snapshot congelado de una
corrida real de `run_dnstwist('bancolombia.com')`
(`eval/baseline_real/antisup_bancolombia_2026-07-25_snapshot.json`, ver tarea
2.1) y lo compara contra `eval/ground_truth_antisuplantacion.yaml` (tarea 2.2).

Por que existe
---------------
`triar_lookalikes` ya esta cableado en el camino de produccion (commit
`a05b133`) y se midio una vez a mano contra datos reales de bancolombia.com:
45 lookalikes registrados -> 10 defensivos, 35 terceros, 7 urgentes (ver
`tools/antisuplantacion.py::triar_lookalikes`, docstring). Sin este harness,
nada protege esa medicion de una regresion silenciosa -- exactamente el mismo
riesgo que ya paso una vez con `triar_lookalikes` (tenia tests verdes y
estaba desconectado del camino real, ver proposal.md de esta fase).

Advertencia anti-circular (leer eval/ground_truth_antisuplantacion.yaml)
-------------------------------------------------------------------------
El ground truth NO puede derivar sus etiquetas de la misma señal que usa el
codigo (compartir nameserver) sin volverse un test tautologico. Por eso cada
entrada del ground truth documenta si su etiqueta se verifico con una señal
INDEPENDIENTE (registrador WHOIS/RDAP del dominio, consultado en vivo) o si
-- por costo de esta corrida -- solo se pudo usar la misma señal que el
codigo (`senal: circular`, documentado explicitamente como limite). Este
runner reporta esa distincion en las metricas (`independiente` vs
`circular`) para que nunca se lea "100% de precision" como si probara mas de
lo que prueba.

Que mide
--------
1. Reconstruye `AntiSuplantacionResult`/`DomainVariant` desde el snapshot
   congelado (mismos dataclasses que usa `tools/antisuplantacion.py`, sin
   reimplementarlos).
2. Corre `triar_lookalikes()` real (import directo, no un mock) sobre esos
   datos.
3. Compara la particion resultante (propios/terceros/con_correo) contra las
   etiquetas del ground truth (defensivo/tercero/urgente).
4. Imprime conteos, discrepancias, y precision sobre el conjunto "urgente"
   (el que mas importa para severidad -- ver proposal.md).
5. Sale con exit code != 0 si la separacion defensivo/tercero se rompe
   (cualquier discrepancia en esas dos clases), para servir de gate de
   regresion real. Discrepancias en "urgente" bajan la precision reportada
   pero no hacen fallar el proceso por si solas si defensivo/tercero siguen
   intactos (la clase "urgente" es un subconjunto de "tercero": ver
   `--json`/reporte para el detalle exacto de que se rompio).

Uso
---
    py -3 eval/run_eval_antisuplantacion.py
    py -3 eval/run_eval_antisuplantacion.py --json
    py -3 eval/run_eval_antisuplantacion.py --snapshot otro.json --ground-truth otro.yaml
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field

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

# `tools/` vive un nivel arriba de `eval/`; mismo patron que `eval/run_eval.py`
# usa para poder `import tools.normalizacion_hallazgos`.
_ROOT_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT_REPO not in sys.path:
    sys.path.insert(0, _ROOT_REPO)

# Import REAL del pipeline bajo prueba -- nada de esto se reimplementa aqui.
from tools.antisuplantacion import (  # noqa: E402
    AntiSuplantacionResult,
    DomainVariant,
    TriajeLookalikes,
    triar_lookalikes,
)


# ---------------------------------------------------------------------------
# Carga de datos
# ---------------------------------------------------------------------------

def load_snapshot(path: str) -> AntiSuplantacionResult:
    """Reconstruye un `AntiSuplantacionResult` con `DomainVariant` reales
    desde el snapshot JSON congelado (tarea 2.1)."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    variants = [
        DomainVariant(
            fuzzer=item["fuzzer"],
            domain=item["domain"],
            dns_a=item.get("dns_a") or [],
            registered=bool(item.get("registered", True)),
            raw=item.get("raw") or {},
        )
        for item in data.get("variants", [])
    ]
    return AntiSuplantacionResult(
        target=data.get("target", ""),
        tool=data.get("tool", "dnstwist"),
        findings=variants,
    )


@dataclass
class GroundTruthEntry:
    domain: str
    etiqueta: str  # defensivo | tercero | urgente
    senal: str  # independiente | circular
    evidencia: str = ""


@dataclass
class GroundTruthDoc:
    entries: list[GroundTruthEntry] = field(default_factory=list)

    def por_dominio(self) -> dict:
        return {e.domain: e for e in self.entries}


def load_ground_truth(path: str) -> GroundTruthDoc:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    raw_list = data.get("dominios", []) if data else []
    entries = [
        GroundTruthEntry(
            domain=str(item["domain"]).strip(),
            etiqueta=str(item["etiqueta"]).strip().lower(),
            senal=str(item.get("senal", "circular")).strip().lower(),
            evidencia=str(item.get("evidencia", "")).strip(),
        )
        for item in raw_list
    ]
    return GroundTruthDoc(entries=entries)


# ---------------------------------------------------------------------------
# Comparacion pipeline vs ground truth
# ---------------------------------------------------------------------------

@dataclass
class Discrepancia:
    domain: str
    esperado: str
    obtenido: str
    senal: str


def clasificar_pipeline(triaje: TriajeLookalikes) -> dict:
    """Mapea la salida real de `triar_lookalikes` a la misma taxonomia de 3
    clases del ground truth (defensivo/tercero/urgente).

    `con_correo` es SUBCONJUNTO de `terceros` en el codigo real (ver
    `tools/antisuplantacion.py::triar_lookalikes`): un dominio con MX real
    esta en ambas listas. Para la comparacion 1:1 contra el ground truth
    (que usa una etiqueta excluyente por dominio) los dominios con MX se
    reclasifican aqui como "urgente", los demas terceros como "tercero".
    """
    resultado: dict[str, str] = {}
    con_correo_domains = {v.domain for v in triaje.con_correo}
    for v in triaje.propios:
        resultado[v.domain] = "defensivo"
    for v in triaje.terceros:
        resultado[v.domain] = "urgente" if v.domain in con_correo_domains else "tercero"
    return resultado


def comparar(
    gt: GroundTruthDoc, obtenido: dict
) -> tuple[list[Discrepancia], dict]:
    discrepancias: list[Discrepancia] = []
    gt_by_domain = gt.por_dominio()

    conteo_gt = {"defensivo": 0, "tercero": 0, "urgente": 0}
    conteo_obtenido = {"defensivo": 0, "tercero": 0, "urgente": 0}

    for domain, entry in gt_by_domain.items():
        conteo_gt[entry.etiqueta] = conteo_gt.get(entry.etiqueta, 0) + 1
        real = obtenido.get(domain)
        if real is None:
            discrepancias.append(
                Discrepancia(domain, entry.etiqueta, "(ausente del pipeline)", entry.senal)
            )
            continue
        conteo_obtenido[real] = conteo_obtenido.get(real, 0) + 1
        if real != entry.etiqueta:
            discrepancias.append(Discrepancia(domain, entry.etiqueta, real, entry.senal))

    # Dominios que el pipeline devolvio pero que no estan en el ground truth
    # (snapshot desalineado con el YAML -- tambien es una regresion real).
    extras = sorted(set(obtenido) - set(gt_by_domain))
    for domain in extras:
        discrepancias.append(
            Discrepancia(domain, "(no en ground truth)", obtenido[domain], "n/a")
        )

    return discrepancias, {"ground_truth": conteo_gt, "pipeline": conteo_obtenido}


def rompe_separacion_defensivo_tercero(discrepancias: list[Discrepancia]) -> bool:
    """True si alguna discrepancia cruza la frontera defensivo <-> (tercero|urgente).

    Esta es la regresion que "no negociable" del proposal.md: un dominio
    propio del cliente reportado como amenaza (falso positivo grave, ver
    docstring de `TriajeLookalikes`), o un dominio de tercero colado como
    defensivo (falso negativo grave: se dejaria de alertar sobre un lookalike
    real). Discrepancias DENTRO de tercero/urgente (ej. un "urgente" que bajo
    a "tercero" por perder su MX) se reportan pero no hacen fallar el gate
    por si solas -- son degradacion de severidad, no el error catastrofico
    que este gate existe para atrapar.
    """
    for d in discrepancias:
        clases = {d.esperado, d.obtenido}
        if "defensivo" in clases and clases != {"defensivo"}:
            return True
    return False


def precision_urgente(gt: GroundTruthDoc, obtenido: dict) -> dict:
    """Precision/recall del pipeline sobre el conjunto 'urgente' del ground
    truth -- el subconjunto que el proposal.md marca como el que mas importa
    medir (dominio de tercero + MX real = puede enviar correo phishing)."""
    gt_urgentes = {e.domain for e in gt.entries if e.etiqueta == "urgente"}
    pipeline_urgentes = {d for d, etiqueta in obtenido.items() if etiqueta == "urgente"}

    tp = len(gt_urgentes & pipeline_urgentes)
    fp = len(pipeline_urgentes - gt_urgentes)
    fn = len(gt_urgentes - pipeline_urgentes)
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "gt_urgentes": sorted(gt_urgentes),
        "pipeline_urgentes": sorted(pipeline_urgentes),
    }


# ---------------------------------------------------------------------------
# Reporte
# ---------------------------------------------------------------------------

def print_report(
    conteos: dict,
    discrepancias: list[Discrepancia],
    metrica_urgente: dict,
    señales: dict,
    gate_roto: bool,
) -> None:
    line = "=" * 78
    print(line)
    print("REPORTE DE EVALUACION — Antisuplantacion (eval/run_eval_antisuplantacion.py)")
    print(line)
    print()
    print("Conteos (ground truth vs pipeline real):")
    for etiqueta in ("defensivo", "tercero", "urgente"):
        gt_n = conteos["ground_truth"].get(etiqueta, 0)
        pl_n = conteos["pipeline"].get(etiqueta, 0)
        marca = "OK" if gt_n == pl_n else "DISTINTO"
        print(f"  {etiqueta:<12} ground_truth={gt_n:<4} pipeline={pl_n:<4} [{marca}]")
    print()
    print(
        f"Señales del ground truth: {señales.get('independiente', 0)} independientes, "
        f"{señales.get('circular', 0)} circulares (ver cabecera de "
        "eval/ground_truth_antisuplantacion.yaml)."
    )
    print()

    print(line)
    print(f"PRECISION SOBRE EL CONJUNTO 'URGENTE' (n={len(metrica_urgente['gt_urgentes'])} en ground truth)")
    print(line)
    print(f"  TP={metrica_urgente['tp']}  FP={metrica_urgente['fp']}  FN={metrica_urgente['fn']}")
    print(f"  precision={metrica_urgente['precision']:.2%}  recall={metrica_urgente['recall']:.2%}")
    print()

    print(line)
    print(f"DISCREPANCIAS ({len(discrepancias)})")
    print(line)
    if not discrepancias:
        print("  (ninguna -- el pipeline reproduce exactamente el ground truth)")
    for d in discrepancias:
        print(f"  {d.domain}: esperado={d.esperado} obtenido={d.obtenido} (señal={d.senal})")
    print()

    print(line)
    estado = "ROTA -- gate de regresion FALLA" if gate_roto else "intacta"
    print(f"Separacion defensivo/tercero: {estado}")
    print(line)


def build_metrics(
    conteos: dict, discrepancias: list[Discrepancia], metrica_urgente: dict, gate_roto: bool
) -> dict:
    return {
        "conteos_ground_truth": conteos["ground_truth"],
        "conteos_pipeline": conteos["pipeline"],
        "discrepancias": [
            {"domain": d.domain, "esperado": d.esperado, "obtenido": d.obtenido, "senal": d.senal}
            for d in discrepancias
        ],
        "urgente": {
            "tp": metrica_urgente["tp"],
            "fp": metrica_urgente["fp"],
            "fn": metrica_urgente["fn"],
            "precision": metrica_urgente["precision"],
            "recall": metrica_urgente["recall"],
        },
        "separacion_defensivo_tercero_rota": gate_roto,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(
        description="Eval de regresion del triaje de antisuplantacion (defensivo/tercero/urgente) contra un snapshot real."
    )
    parser.add_argument(
        "--snapshot",
        default=os.path.join(here, "baseline_real", "antisup_bancolombia_2026-07-25_snapshot.json"),
        help="Ruta al snapshot congelado de run_dnstwist (default: eval/baseline_real/antisup_bancolombia_2026-07-25_snapshot.json)",
    )
    parser.add_argument(
        "--ground-truth",
        default=os.path.join(here, "ground_truth_antisuplantacion.yaml"),
        help="Ruta a ground_truth_antisuplantacion.yaml",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emite solo las metricas como JSON en stdout (salida parseable por maquina).",
    )
    args = parser.parse_args()

    if not os.path.isfile(args.snapshot):
        print(f"ERROR: no se encontro snapshot en: {args.snapshot}", file=sys.stderr)
        return 1
    if not os.path.isfile(args.ground_truth):
        print(f"ERROR: no se encontro ground truth en: {args.ground_truth}", file=sys.stderr)
        return 1

    resultado = load_snapshot(args.snapshot)
    gt = load_ground_truth(args.ground_truth)

    if not gt.entries:
        print("ADVERTENCIA: la ground truth esta vacia.", file=sys.stderr)

    # Pipeline REAL bajo prueba.
    triaje = triar_lookalikes(resultado)
    obtenido = clasificar_pipeline(triaje)

    discrepancias, conteos = comparar(gt, obtenido)
    metrica_urgente = precision_urgente(gt, obtenido)
    gate_roto = rompe_separacion_defensivo_tercero(discrepancias)

    señales = {"independiente": 0, "circular": 0}
    for e in gt.entries:
        señales[e.senal] = señales.get(e.senal, 0) + 1

    metrics = build_metrics(conteos, discrepancias, metrica_urgente, gate_roto)

    if args.json:
        print(json.dumps(metrics, ensure_ascii=False))
    else:
        print_report(conteos, discrepancias, metrica_urgente, señales, gate_roto)

    return 1 if gate_roto else 0


if __name__ == "__main__":
    sys.exit(main())
