"""Agentes del pipeline de ciberseguridad — un módulo por agente (sección 4/7 del plan).

Cada módulo expone una función `node(state) -> dict` que LangGraph registra
como nodo del `StateGraph` (ver `orchestrator/graph.py`). Los system
prompts de cada agente son copia literal de la sección 7 del plan maestro
(`plan-proyecto-ciberseguridad.md`) — no se reescriben.

`verificacion.py` es la única excepción deliberada: es lógica Python
determinista, sin LLM, tal como exige la sección 8.1 del plan
("separado del razonamiento de la IA").
"""
