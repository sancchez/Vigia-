"""Agente de Escaneo (activo) — sección 3.1 del plan.

Envuelve Nuclei, OWASP ZAP baseline, Trivy, Grype y Semgrep. Todas estas
funciones representan la capa "Agente de Escaneo" del pipeline (sección 4):
solo se deben invocar contra objetivos con `autorizacion_firmada = true`
en el estado del grafo — esa puerta vive en el orquestador (LangGraph),
no aquí. Estos wrappers no verifican autorización por sí mismos porque
son de bajo nivel; el nodo determinista de autorización es responsabilidad
del grafo (sección 8.1 del plan).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from ._shared import ToolNotInstalledError, ToolResult, parse_jsonl, require_binary, run_command


@dataclass
class ScanFinding:
    tool: str
    raw: dict


@dataclass
class ScanResult:
    target: str
    tool: str
    findings: list[dict] = field(default_factory=list)
    raw: ToolResult | None = None


def run_nuclei(
    target: str,
    templates: str | None = None,
    severity: str | None = None,
    timeout: int = 900,
) -> ScanResult:
    """Escanea `target` con Nuclei (projectdiscovery/nuclei), +12.000 plantillas YAML.

    Repo: projectdiscovery/nuclei. Capa: Agente de Escaneo (sección 3.1).
    Cubre CVEs conocidas, configuraciones débiles, credenciales por defecto.
    Licencia MIT.

    Args:
        target: URL o host ya autorizado para escanear.
        templates: ruta o tag de plantillas a usar (ej. "cves/", "-t cves").
        severity: filtro de severidad, ej. "critical,high".

    Instalación si falta:
        go install -v github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest

    Raises:
        ToolNotInstalledError: si el binario `nuclei` no está disponible.
    """
    binary = require_binary(
        "nuclei",
        "go install -v github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest",
    )
    cmd = [binary, "-u", target, "-jsonl", "-silent"]
    if templates:
        cmd += ["-t", templates]
    if severity:
        cmd += ["-severity", severity]
    result = run_command("nuclei", cmd, timeout=timeout)
    findings = parse_jsonl(result.stdout)
    result.parsed = findings
    return ScanResult(target=target, tool="nuclei", findings=findings, raw=result)


def run_zap_baseline(target_url: str, timeout: int = 900) -> ScanResult:
    """Corre el escaneo dinámico (DAST) OWASP ZAP en modo baseline vía Docker.

    Repo: zaproxy/zaproxy. Capa: Agente de Escaneo (sección 3.1). Apache 2.0.
    Requiere Docker (imagen oficial `ghcr.io/zaproxy/zaproxy:stable`) — no se
    instala aquí automáticamente porque implica bajar una imagen pesada.

    Instalación/uso si Docker está disponible:
        docker run --rm -v $(pwd):/zap/wrk/:rw ghcr.io/zaproxy/zaproxy:stable \\
            zap-baseline.py -t <target_url> -J zap-report.json

    Raises:
        ToolNotInstalledError: si `docker` no está en PATH. En ese caso,
            usa el comando documentado arriba manualmente.
    """
    binary = require_binary(
        "docker",
        "instalar Docker Desktop (https://docs.docker.com/desktop/) y luego: "
        "docker pull ghcr.io/zaproxy/zaproxy:stable",
    )
    cmd = [
        binary,
        "run",
        "--rm",
        "-t",
        "ghcr.io/zaproxy/zaproxy:stable",
        "zap-baseline.py",
        "-t",
        target_url,
        "-J",
        "/zap/wrk/zap-report.json",
    ]
    result = run_command("zap-baseline", cmd, timeout=timeout)
    result.parsed = result.stdout
    return ScanResult(target=target_url, tool="zap-baseline", findings=[], raw=result)


def run_trivy_image(image: str, timeout: int = 600) -> ScanResult:
    """Escanea una imagen de contenedor en busca de CVEs con Trivy.

    Repo: aquasecurity/trivy. Capa: Agente de Escaneo (dependencias/infra),
    sección 3.1.

    Instalación si falta (Windows, via scoop/choco, o binario directo):
        scoop install trivy
        # o descargar el binario desde:
        # https://github.com/aquasecurity/trivy/releases

    Raises:
        ToolNotInstalledError: si el binario `trivy` no está disponible.
    """
    binary = require_binary(
        "trivy",
        "scoop install trivy  (o descargar binario de "
        "https://github.com/aquasecurity/trivy/releases)",
    )
    cmd = [binary, "image", "--format", "json", image]
    result = run_command("trivy", cmd, timeout=timeout)
    findings: list[dict] = []
    try:
        data = json.loads(result.stdout)
        for res in data.get("Results", []):
            findings.extend(res.get("Vulnerabilities", []) or [])
    except json.JSONDecodeError:
        pass
    result.parsed = findings
    return ScanResult(target=image, tool="trivy", findings=findings, raw=result)


def run_grype(target: str, timeout: int = 600) -> ScanResult:
    """Escanea composición de software (SCA) con Grype — vulnerabilidades en dependencias.

    Repo: anchore/grype. Capa: Agente de Escaneo, sección 3.1.
    `target` puede ser una imagen (docker:nombre), un directorio o un SBOM.

    Instalación si falta:
        # Windows (scoop):
        scoop install grype
        # o script oficial (bash/WSL):
        curl -sSfL https://raw.githubusercontent.com/anchore/grype/main/install.sh \\
            | sh -s -- -b /usr/local/bin

    Raises:
        ToolNotInstalledError: si el binario `grype` no está disponible.
    """
    binary = require_binary(
        "grype",
        "scoop install grype (o ver https://github.com/anchore/grype#installation)",
    )
    cmd = [binary, target, "-o", "json"]
    result = run_command("grype", cmd, timeout=timeout)
    findings: list[dict] = []
    try:
        data = json.loads(result.stdout)
        findings = data.get("matches", [])
    except json.JSONDecodeError:
        pass
    result.parsed = findings
    return ScanResult(target=target, tool="grype", findings=findings, raw=result)


def run_semgrep(path: str, config: str = "auto", timeout: int = 600) -> ScanResult:
    """Análisis estático de código (SAST) con Semgrep sobre el código fuente del cliente.

    Repo: semgrep/semgrep. Capa: Agente de Escaneo (código), sección 3.1.
    Instalado localmente vía pip en este proyecto (ver tools/requirements.txt).

    Args:
        path: carpeta o archivo de código fuente a analizar.
        config: set de reglas, "auto" detecta el lenguaje automáticamente.

    Raises:
        ToolNotInstalledError: si el binario `semgrep` no está disponible.
    """
    binary = require_binary(
        "semgrep",
        "pip install semgrep",
    )
    cmd = [binary, "--config", config, "--json", path]
    result = run_command("semgrep", cmd, timeout=timeout)
    findings: list[dict] = []
    try:
        data = json.loads(result.stdout)
        findings = data.get("results", [])
    except json.JSONDecodeError:
        pass
    result.parsed = findings
    return ScanResult(target=path, tool="semgrep", findings=findings, raw=result)


def query_osv_api(package_name: str, ecosystem: str, version: str | None = None) -> ScanResult:
    """Consulta la API pública de OSV.dev por vulnerabilidades conocidas de un paquete.

    Repo/servicio: google/osv.dev. Capa: Agente de Escaneo (dependencias),
    sección 3.1. No requiere instalar ningún binario — usa la API HTTP
    pública (https://api.osv.dev/v1/query) vía `requests`, así que
    funciona igual con o sin el binario `osv-scanner` (Go) instalado.

    Args:
        package_name: nombre del paquete (ej. "django", "lodash").
        ecosystem: ecosistema OSV (ej. "PyPI", "npm", "Go", "crates.io").
        version: versión exacta a consultar (opcional pero recomendado).

    Raises:
        ToolNotInstalledError: si el paquete `requests` no está instalado.
    """
    try:
        import requests
    except ImportError as exc:
        raise ToolNotInstalledError("requests", "pip install requests") from exc

    payload: dict = {"package": {"name": package_name, "ecosystem": ecosystem}}
    if version:
        payload["version"] = version
    resp = requests.post("https://api.osv.dev/v1/query", json=payload, timeout=30)
    data = resp.json() if resp.content else {}
    findings = data.get("vulns", [])
    raw = ToolResult(
        tool="osv-api",
        command=["POST", "https://api.osv.dev/v1/query", json.dumps(payload)],
        returncode=0 if resp.ok else resp.status_code,
        stdout=resp.text,
        stderr="" if resp.ok else f"HTTP {resp.status_code}",
        parsed=findings,
    )
    return ScanResult(target=f"{ecosystem}:{package_name}@{version}", tool="osv-api", findings=findings, raw=raw)


def run_osv_scanner(path: str, timeout: int = 300) -> ScanResult:
    """Escanea un directorio de proyecto con el binario oficial osv-scanner (Go).

    Repo: google/osv-scanner. Capa: Agente de Escaneo (dependencias),
    sección 3.1. Alternativa sin instalar nada: usar `query_osv_api()` en
    este mismo módulo, que llama la API HTTP directamente.

    Instalación si falta:
        go install github.com/google/osv-scanner/cmd/osv-scanner@latest

    Raises:
        ToolNotInstalledError: si el binario `osv-scanner` no está disponible.
    """
    binary = require_binary(
        "osv-scanner",
        "go install github.com/google/osv-scanner/cmd/osv-scanner@latest "
        "(o usar query_osv_api() que no requiere instalar nada)",
    )
    cmd = [binary, "--format", "json", "-r", path]
    result = run_command("osv-scanner", cmd, timeout=timeout)
    findings: list[dict] = []
    try:
        data = json.loads(result.stdout)
        findings = data.get("results", [])
    except json.JSONDecodeError:
        pass
    result.parsed = findings
    return ScanResult(target=path, tool="osv-scanner", findings=findings, raw=result)
