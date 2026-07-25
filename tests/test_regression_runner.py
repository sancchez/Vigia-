"""Fase 7 (openspec/changes/fase7-confianza-calidad) -- `eval/regression_runner.py`.

Ejercita `comparar_contra_baseline` (la función pura que decide si hay
regresión) con subidas/bajadas simuladas de métricas -- sin tocar el
`baseline.json` real del repo ni invocar el CLI completo, para que el test
sea rápido y no dependa del estado de ese archivo versionado."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "eval"))

from regression_runner import comparar_contra_baseline  # noqa: E402


def test_sin_regresion_cuando_metricas_iguales_o_mejores():
    baseline = {"recall": 0.80, "fp_rate": 0.10}
    metrics = {"recall": 0.85, "fp_rate": 0.05}
    assert comparar_contra_baseline(baseline, metrics, recall_tolerancia=0.0, fp_rate_tolerancia=0.0) == []


def test_regresion_detectada_cuando_recall_baja():
    baseline = {"recall": 0.90, "fp_rate": 0.10}
    metrics = {"recall": 0.80, "fp_rate": 0.10}
    problemas = comparar_contra_baseline(baseline, metrics, recall_tolerancia=0.0, fp_rate_tolerancia=0.0)
    assert len(problemas) == 1
    assert "recall" in problemas[0]


def test_regresion_detectada_cuando_fp_rate_sube():
    baseline = {"recall": 0.90, "fp_rate": 0.10}
    metrics = {"recall": 0.90, "fp_rate": 0.30}
    problemas = comparar_contra_baseline(baseline, metrics, recall_tolerancia=0.0, fp_rate_tolerancia=0.0)
    assert len(problemas) == 1
    assert "fp_rate" in problemas[0]


def test_ambas_metricas_degradadas_reporta_ambas():
    baseline = {"recall": 0.90, "fp_rate": 0.10}
    metrics = {"recall": 0.70, "fp_rate": 0.40}
    problemas = comparar_contra_baseline(baseline, metrics, recall_tolerancia=0.0, fp_rate_tolerancia=0.0)
    assert len(problemas) == 2


def test_tolerancia_explicita_absorbe_caida_pequena():
    baseline = {"recall": 0.90, "fp_rate": 0.10}
    metrics = {"recall": 0.88, "fp_rate": 0.11}
    problemas = comparar_contra_baseline(baseline, metrics, recall_tolerancia=0.05, fp_rate_tolerancia=0.05)
    assert problemas == []
