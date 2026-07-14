"""Orquestador LangGraph del pipeline de ciberseguridad (sección 4 del plan).

Este paquete NO contiene lógica de negocio de los agentes (eso vive en
`agents/`) — solo el estado compartido del grafo (`state.py`) y el
ensamblaje del `StateGraph` (`graph.py`), incluyendo la puerta de
autorización determinista descrita en la sección 8.1 del plan.
"""
