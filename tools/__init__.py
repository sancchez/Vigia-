"""Wrappers de herramientas open source para el pipeline de ciberseguridad.

Cada submódulo envuelve herramientas ya existentes (ver sección 3 del plan
`plan-proyecto-ciberseguridad.md`) con funciones tipadas que el orquestador
LangGraph puede llamar como @tool. Ningún módulo falla al importarse aunque
la herramienta subyacente no esté instalada en la máquina — el error
(`ToolNotInstalledError`) solo se lanza cuando se intenta ejecutar la
función correspondiente.
"""
