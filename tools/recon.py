"""Agente de Recon (pasivo) — sección 3.1 del plan.

Envuelve Subfinder y OWASP Amass en modo pasivo: solo consultan fuentes
públicas (crt.sh, APIs de terceros), nunca envían tráfico activo al
objetivo. Capa del pipeline: "Agente de Recon" (ver sección 4 del plan,
tabla de agentes).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ._shared import ToolResult, require_binary, run_command


@dataclass
class ReconResult:
    """Hallazgos normalizados de recon pasivo (subdominios + fuente)."""

    domain: str
    subdomains: list[str] = field(default_factory=list)
    raw: ToolResult | None = None


def run_subfinder(domain: str, timeout: int = 180) -> ReconResult:
    """Enumera subdominios de forma pasiva con Subfinder (projectdiscovery/subfinder).

    Repo: projectdiscovery/subfinder. Capa: Agente de Recon (sección 3.1).
    Solo hace consultas pasivas a fuentes públicas — no toca el objetivo.

    Instalación si falta:
        go install -v github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest

    Raises:
        ToolNotInstalledError: si el binario `subfinder` no está en PATH
            ni en ~/go/bin.
    """
    binary = require_binary(
        "subfinder",
        "go install -v github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest",
    )
    cmd = [binary, "-d", domain, "-silent"]
    result = run_command("subfinder", cmd, timeout=timeout)
    subdomains = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    result.parsed = subdomains
    return ReconResult(domain=domain, subdomains=subdomains, raw=result)


def run_amass_passive(domain: str, timeout: int = 300) -> ReconResult:
    """Mapea superficie de ataque en modo pasivo con OWASP Amass.

    Repo: owasp-amass/amass. Capa: Agente de Recon (sección 3.1).
    Usa `amass enum -passive` — solo fuentes OSINT, sin resolución activa
    ni fuerza bruta de DNS contra el objetivo.

    Instalación si falta:
        go install -v github.com/owasp-amass/amass/v4/...@master

    Raises:
        ToolNotInstalledError: si el binario `amass` no está disponible.
    """
    binary = require_binary(
        "amass",
        "go install -v github.com/owasp-amass/amass/v4/...@master",
    )
    cmd = [binary, "enum", "-passive", "-d", domain, "-json", "/dev/stdout"]
    result = run_command("amass", cmd, timeout=timeout)
    subdomains: list[str] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        # amass -json emite un objeto JSON por línea con campo "name"
        try:
            import json

            obj = json.loads(line)
            name = obj.get("name")
            if name:
                subdomains.append(name)
        except Exception:
            continue
    result.parsed = subdomains
    return ReconResult(domain=domain, subdomains=subdomains, raw=result)
