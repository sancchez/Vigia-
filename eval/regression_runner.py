#!/usr/bin/env python3
"""
eval/regression_runner.py
==========================

Fase 7 (openspec/changes/fase7-confianza-calidad): automatiza el paso 4 del
loop de la seccion 8.2 ("ningun cambio de prompt o de herramienta se pasa a
produccion sin antes correr el set de evaluacion completo. Si un cambio baja
el recall o sube los falsos positivos, no se despliega"), que `eval/README.md`
ya describia como proceso MANUAL ("correr `python eval/run_eval.py` ... y
comparar precision/recall contra la corrida anterior").

Que hace
--------
1. Corre `eval/run_eval.py --json` (in-process, reusando sus funciones --
   nunca se re-implementa el matching/metricas acá) contra la ground truth y
   los hallazgos que se le pasen.
2. Carga `eval/baseline.json` (métricas de referencia versionadas en git).
3. Compara recall y fp_rate de la corrida nueva contra el baseline:
   - Si el recall BAJA más de `--recall-tolerancia` (default 0.0 -- ninguna
     caída tolerada) -> regresión.
   - Si el fp_rate SUBE más de `--fp-rate-tolerancia` (default 0.0) ->
     regresión.
4. Sale con exit code != 0 si hay regresión, nombrando la métrica degradada.
   Exit code 0 si la corrida esta dentro de umbral (igual o mejor).

El baseline NUNCA se reescribe solo -- `--update-baseline` es un flag
explicito, mismo criterio que `eval/prompt_versions/README.md` exige para
promover un prompt nuevo (paso intencional y revisado, no automático).

Uso
---
    python eval/regression_runner.py
    python eval/regression_runner.py --ground-truth eval/ground_truth.yaml --findings eval/sample_findings.json
    python eval/regression_runner.py --update-baseline   # regenera baseline.json a partir de esta corrida
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# Reusa el harness existente tal cual (misma carpeta) -- nunca se duplica el
# algoritmo de matching/metricas acá.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run_eval import (  # noqa: E402
    compute_metrics,
    load_findings,
    load_ground_truth,
    match_findings,
)

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def cargar_baseline(path: str) -> dict | None:
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def guardar_baseline(path: str, metrics: dict, ground_truth_path: str, findings_path: str) -> None:
    payload = {
        "recall": metrics["recall"],
        "fp_rate": metrics["fp_rate"],
        "precision": metrics["precision"],
        "f1": metrics["f1"],
        "tp": metrics["tp"],
        "fp": metrics["fp"],
        "fn": metrics["fn"],
        "generado_desde": {
            "ground_truth": os.path.relpath(ground_truth_path, os.path.dirname(path)),
            "findings": os.path.relpath(findings_path, os.path.dirname(path)),
        },
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")


def comparar_contra_baseline(
    baseline: dict, metrics: dict, recall_tolerancia: float, fp_rate_tolerancia: float
) -> list[str]:
    """Devuelve la lista de mensajes de regresión detectados (vacía si no hay
    ninguna). Recall: una caída de más de `recall_tolerancia` es regresión.
    FP-rate: una subida de más de `fp_rate_tolerancia` es regresión."""
    problemas: list[str] = []

    caida_recall = baseline["recall"] - metrics["recall"]
    if caida_recall > recall_tolerancia:
        problemas.append(
            f"recall bajó de {baseline['recall']:.2%} a {metrics['recall']:.2%} "
            f"(caída de {caida_recall:.2%}, tolerancia {recall_tolerancia:.2%})"
        )

    subida_fp_rate = metrics["fp_rate"] - baseline["fp_rate"]
    if subida_fp_rate > fp_rate_tolerancia:
        problemas.append(
            f"fp_rate subió de {baseline['fp_rate']:.2%} a {metrics['fp_rate']:.2%} "
            f"(subida de {subida_fp_rate:.2%}, tolerancia {fp_rate_tolerancia:.2%})"
        )

    return problemas


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(
        description="Gate de regresión automatizado: compara una corrida de eval/run_eval.py contra eval/baseline.json."
    )
    parser.add_argument("--ground-truth", default=os.path.join(here, "ground_truth.yaml"))
    parser.add_argument("--findings", default=os.path.join(here, "sample_findings.json"))
    parser.add_argument("--baseline", default=os.path.join(here, "baseline.json"))
    parser.add_argument(
        "--recall-tolerancia",
        type=float,
        default=0.0,
        help="Caída de recall tolerada antes de considerarla regresión (default: 0.0, ninguna).",
    )
    parser.add_argument(
        "--fp-rate-tolerancia",
        type=float,
        default=0.0,
        help="Subida de fp_rate tolerada antes de considerarla regresión (default: 0.0, ninguna).",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Paso EXPLÍCITO e intencional: sobreescribe baseline.json con las métricas de esta corrida.",
    )
    args = parser.parse_args()

    if not os.path.isfile(args.ground_truth):
        print(f"ERROR: no se encontró ground truth en: {args.ground_truth}", file=sys.stderr)
        return 1
    if not os.path.isfile(args.findings):
        print(f"ERROR: no se encontró archivo de hallazgos en: {args.findings}", file=sys.stderr)
        return 1

    ground_truth = load_ground_truth(args.ground_truth)
    findings = load_findings(args.findings)
    match = match_findings(ground_truth, findings)
    metrics = compute_metrics(match)

    print(
        f"Corrida actual: recall={metrics['recall']:.2%} fp_rate={metrics['fp_rate']:.2%} "
        f"precision={metrics['precision']:.2%} (tp={metrics['tp']} fp={metrics['fp']} fn={metrics['fn']})"
    )

    if args.update_baseline:
        guardar_baseline(args.baseline, metrics, args.ground_truth, args.findings)
        print(f"Baseline actualizado explícitamente en: {args.baseline}")
        return 0

    baseline = cargar_baseline(args.baseline)
    if baseline is None:
        print(
            f"ADVERTENCIA: no existe baseline en {args.baseline}. "
            "Corré con --update-baseline para crear uno inicial (paso manual, revisado).",
            file=sys.stderr,
        )
        return 1

    print(f"Baseline:       recall={baseline['recall']:.2%} fp_rate={baseline['fp_rate']:.2%}")

    problemas = comparar_contra_baseline(
        baseline, metrics, args.recall_tolerancia, args.fp_rate_tolerancia
    )
    if problemas:
        print("\nREGRESIÓN DETECTADA:", file=sys.stderr)
        for p in problemas:
            print(f"  - {p}", file=sys.stderr)
        return 1

    print("\nSin regresión: recall y fp_rate dentro de umbral respecto al baseline.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
