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
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

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


def _rewrite_for_docker(target_url: str) -> str:
    """"localhost"/"127.0.0.1" dentro de un contenedor Docker se refiere al
    propio contenedor, no al host — un target de laboratorio corriendo en
    la máquina del usuario (Juice Shop, DVWA) necesita host.docker.internal.
    Un dominio real de internet no pasa por esta rama y sigue igual."""
    return target_url.replace("localhost", "host.docker.internal").replace(
        "127.0.0.1", "host.docker.internal"
    )


def _run_zap_script(
    script: str,
    target_url: str,
    extra_args: list[str],
    tool_name: str,
    timeout: int,
) -> ScanResult:
    """Corre un script de ZAP (`zap-baseline.py`, `zap-full-scan.py`, ...) vía Docker.

    Monta un directorio temporal del host en `/zap/wrk/` dentro del
    contenedor (sin eso el reporte JSON se queda encerrado en el
    contenedor descartable y nunca llega a `findings`) y parsea el JSON
    al volver. Estos scripts devuelven código != 0 cuando SÍ encuentran
    alertas (no es un error de ejecución) — por eso no se valida
    `returncode == 0`, se confía en que el JSON exista.

    Raises:
        ToolNotInstalledError: si `docker` no está en PATH.
    """
    binary = require_binary(
        "docker",
        "instalar Docker Desktop (https://docs.docker.com/desktop/) y luego: "
        "docker pull ghcr.io/zaproxy/zaproxy:stable",
    )
    zap_target = _rewrite_for_docker(target_url)
    with tempfile.TemporaryDirectory(prefix="vigia-zap-") as workdir:
        cmd = [
            binary,
            "run",
            "--rm",
            "--add-host",
            "host.docker.internal:host-gateway",
            "-v",
            f"{workdir}:/zap/wrk/:rw",
            "ghcr.io/zaproxy/zaproxy:stable",
            script,
            "-t",
            zap_target,
            "-J",
            "zap-report.json",
            *extra_args,
        ]
        result = run_command(tool_name, cmd, timeout=timeout)

        report_path = Path(workdir) / "zap-report.json"
        findings: list[dict] = []
        if report_path.exists():
            try:
                data = json.loads(report_path.read_text(encoding="utf-8"))
                for site in data.get("site", []):
                    findings.extend(site.get("alerts", []) or [])
            except json.JSONDecodeError:
                pass
        result.parsed = findings

    return ScanResult(target=target_url, tool=tool_name, findings=findings, raw=result)


def run_zap_baseline(target_url: str, timeout: int = 900) -> ScanResult:
    """Corre el escaneo dinámico (DAST) OWASP ZAP en modo baseline (pasivo) vía Docker.

    Repo: zaproxy/zaproxy. Capa: Agente de Escaneo (sección 3.1). Apache 2.0.
    Solo hace spidering + reglas pasivas — no ataca parámetros, así que es
    seguro correrlo de forma recurrente/automática contra un cliente real.
    No encuentra SQLi/XSS/IDOR (para eso ver `run_zap_active_scan`).

    Raises:
        ToolNotInstalledError: si `docker` no está en PATH.
    """
    return _run_zap_script("zap-baseline.py", target_url, [], "zap-baseline", timeout)


def run_zap_active_scan(
    target_url: str,
    minutes: int = 20,
    bearer_token: str | None = None,
    ajax_spider: bool = True,
    timeout: int | None = None,
) -> ScanResult:
    """Escaneo ACTIVO de ZAP (`zap-full-scan.py`) — sí ataca parámetros (SQLi, XSS, etc.).

    A diferencia de `run_zap_baseline`, esto envía payloads de ataque
    reales contra el objetivo. Solo debe correrse contra targets de
    laboratorio o con autorización explícita de pruebas activas — nunca
    como escaneo recurrente por defecto contra un cliente en producción.

    Args:
        minutes: presupuesto de tiempo para el spider + escaneo activo
            (flag `-m` de zap-full-scan.py). Rutas que requieren sesión
            iniciada (login, panel de admin, checkout) solo son
            alcanzables si el crawler llega a ellas dentro de este tiempo.
        bearer_token: si se pasa, se inyecta como header
            `Authorization: Bearer <token>` en TODAS las peticiones de ZAP
            vía `-config replacer.*` — así el escaneo activo cubre rutas
            autenticadas sin necesitar un script de login dentro de ZAP.
            Conseguir el token es responsabilidad del llamador (ej. un
            login previo contra el mismo target).
        ajax_spider: usa el spider basado en navegador real (`-j`) además
            del spider tradicional. El spider clásico solo sigue enlaces
            `<a href>` en el HTML estático — en una SPA (Angular, React,
            Vue) casi toda la navegación real pasa por JavaScript y nunca
            aparece ahí, así que sin esto ZAP nunca descubre las rutas de
            API que sí importan. Cuesta más tiempo de arranque (levanta un
            navegador headless dentro del contenedor).
        timeout: límite del subprocess en segundos. Por defecto,
            `minutes * 60` más 5 minutos de margen para spidering/arranque.

    Raises:
        ToolNotInstalledError: si `docker` no está en PATH.
    """
    extra_args = ["-m", str(minutes)]
    if ajax_spider:
        extra_args += ["-j"]
    if bearer_token:
        replacer = (
            "-config replacer.full_list(0).description=vigia-auth "
            "-config replacer.full_list(0).enabled=true "
            "-config replacer.full_list(0).matchtype=REQ_HEADER "
            "-config replacer.full_list(0).matchstr=Authorization "
            "-config replacer.full_list(0).regex=false "
            f"-config replacer.full_list(0).replacement=Bearer%20{bearer_token}"
        )
        extra_args += ["-z", replacer]
    # El AJAX Spider levanta un navegador headless dentro del contenedor —
    # overhead real de arranque/renderizado que el -m de ZAP no cubre del
    # todo. Encontrado en una corrida real: con -j, 20 min de presupuesto
    # más 5 min de margen no alcanzaron y el proceso se cortó a mitad.
    margen = 900 if ajax_spider else 300
    effective_timeout = timeout if timeout is not None else (minutes * 60 + margen)
    return _run_zap_script(
        "zap-full-scan.py", target_url, extra_args, "zap-full-scan", effective_timeout
    )


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
