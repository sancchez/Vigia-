# tools/ — Herramientas open source envueltas (sección 3 del plan)

Esta carpeta **no reinventa** escáneres ni motores de detección — envuelve con
wrappers Python las herramientas de código abierto listadas en la sección 3
de `plan-proyecto-ciberseguridad.md`. El orquestador LangGraph importa estos
módulos como `@tool`.

Entorno verificado en esta máquina: `git 2.49.0`, `go 1.26.1`, `python 3.14.6`
(vía `py`/ruta directa — el alias `python` de Windows Store no resuelve),
`pip 26.1.2`. **Docker no está instalado.**

Si `nuclei`/`subfinder`/`amass` no aparecen todavía en `%USERPROFILE%\go\bin`,
el `go install` se lanzó pero no terminó de compilar/descargar en la ventana
de esta sesión — volver a correr el mismo comando (`go install ...@latest`)
para reintentar/continuar; Go retoma la caché de módulos ya descargados.

## 3.1 Frente de vulnerabilidades / exploits

| Herramienta | Repo | Estado local | Cómo se instaló / instala |
|---|---|---|---|
| **Nuclei** | projectdiscovery/nuclei | `go install` lanzado (Go 1.26.1 disponible) — build/descarga de dependencias en curso al momento de este reporte, no confirmado en `~/go/bin` todavía | `go install -v github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest` |
| **Subfinder** | projectdiscovery/subfinder | `go install` lanzado — mismo estado que Nuclei | `go install -v github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest` |
| **OWASP Amass** | owasp-amass/amass | `go install` lanzado — mismo estado que Nuclei (árbol de dependencias más grande, puede tardar más) | `go install -v github.com/owasp-amass/amass/v4/...@master` |
| **Exploit-DB / searchsploit** | offensive-security/exploitdb | **Instalado** — índice CSV en `tools/vendor/exploitdb/` (10 MB) | Descargado directo (ver nota abajo) — el repo GitHub oficial ahora solo redirige a `gitlab.com/exploit-database/exploitdb` |
| **OSV.dev** | google/osv.dev (API) | **Funcional sin instalar nada** — `tools/scan.py::query_osv_api()` llama `https://api.osv.dev/v1/query` con `requests` | — |
| **osv-scanner** (binario Go, opcional) | google/osv-scanner | No instalado | `go install github.com/google/osv-scanner/cmd/osv-scanner@latest` |
| **Semgrep** | semgrep/semgrep | **Instalado** — `pip install semgrep` (v1.169.0), corre nativo en Windows | ya hecho |
| **Trivy** | aquasecurity/trivy | No instalado (requiere binario propio) | `scoop install trivy` o binario de https://github.com/aquasecurity/trivy/releases |
| **Grype** | anchore/grype | No instalado (requiere binario propio) | `scoop install grype` o ver https://github.com/anchore/grype#installation |
| **OWASP ZAP** | zaproxy/zaproxy | No instalado — requiere Docker (no disponible en esta máquina) | `docker pull ghcr.io/zaproxy/zaproxy:stable` luego `zap-baseline.py` (ver `tools/scan.py::run_zap_baseline`) |
| **Metasploit** | rapid7/metasploit-framework | No instalado (instalador pesado, uso opcional avanzado) | ver https://docs.metasploit.com/docs/using-metasploit/getting-started/nightly-installers.html |
| Faraday / PentestGPT / PentAGI / XBOW | — | Referencias de arquitectura (sección 3.1), no se envuelven como herramienta | — |

## 3.2 Frente anti-suplantación

| Herramienta | Repo | Estado local | Cómo se instaló / instala |
|---|---|---|---|
| **dnstwist** | elceef/dnstwist | **Instalado** — `pip install dnstwist` (v20250130), probado contra `google.com` (298 variantes) | ya hecho |
| **mcp-dnstwist** | BurtTheCoder/mcp-dnstwist | No instalado — servidor MCP alternativo, se conecta directo al orquestador sin pasar por `tools/antisuplantacion.py` | `git clone https://github.com/BurtTheCoder/mcp-dnstwist` y seguir su README para registrarlo como servidor MCP |
| **Sherlock** | sherlock-project/sherlock | **Instalado** — `pip install sherlock-project` (v0.16.0) | ya hecho |
| **phishing_catcher (CertStream)** | x0rz/phishing_catcher | No instalado — es un worker de larga duración (websocket permanente), no una llamada puntual | `pip install certstream tqdm && git clone https://github.com/x0rz/phishing_catcher tools/vendor/phishing_catcher` |
| **certthreat** | PAST2212/certthreat | No instalado — requiere configurar keywords de marca propias antes de correr | `git clone https://github.com/PAST2212/certthreat tools/vendor/certthreat` y configurar según su README |
| **Google Safe Browsing API** | API de Google | Wrapper listo, requiere API key propia (gratuita) | obtener key en https://developers.google.com/safe-browsing/v4/get-started, exportar `GOOGLE_SAFE_BROWSING_API_KEY` |

## Nota sobre exploitdb

El repo `github.com/offensive-security/exploitdb` ahora solo contiene un
`README.md` que redirige a `gitlab.com/exploit-database/exploitdb` (rama
`main`). En vez de clonar el repo completo (incluye miles de archivos de
PoC, pesado), se descargó únicamente el índice oficial que usa el propio
`searchsploit` — `files_exploits.csv` (~10 MB, 47k+ registros) y
`files_shellcodes.csv` — con:

```bash
curl -sL -o tools/vendor/exploitdb/files_exploits.csv \
  https://gitlab.com/exploit-database/exploitdb/-/raw/main/files_exploits.csv
curl -sL -o tools/vendor/exploitdb/files_shellcodes.csv \
  https://gitlab.com/exploit-database/exploitdb/-/raw/main/files_shellcodes.csv
```

`tools/exploit_intel.py` busca offline sobre este CSV (equivalente a
`searchsploit <query>` o `searchsploit --cve <CVE>`), sin depender del
script Perl/bash original ni de conexión a internet una vez descargado.
Si se necesita el contenido completo de los PoC (no solo el índice), se
puede clonar el repo GitLab completo o usar https://www.exploit-db.com/search
como alternativa online.

## Wrappers Python

| Archivo | Agente del pipeline (sección 4) | Herramientas que envuelve |
|---|---|---|
| `_shared.py` | — (utilidades comunes) | detección de binarios, ejecución de subprocess, `ToolNotInstalledError` |
| `recon.py` | Agente de Recon (pasivo) | Subfinder, OWASP Amass (`enum -passive`) |
| `scan.py` | Agente de Escaneo (activo) | Nuclei, ZAP baseline, Trivy, Grype, Semgrep, OSV (API + `osv-scanner`) |
| `exploit_intel.py` | Agente de Verificación (base de conocimiento) | Exploit-DB / searchsploit (offline, CSV local) |
| `antisuplantacion.py` | Agente Anti-Suplantación | dnstwist, Sherlock, certthreat (doc), phishing_catcher (doc), Safe Browsing API |

Todos los módulos son **importables sin error** aunque la herramienta
subyacente no esté instalada — la falla ocurre solo al llamar la función,
con `ToolNotInstalledError` y el comando exacto de instalación en el mensaje.
Verificado con `python -c "import tools.recon, tools.scan, tools.exploit_intel, tools.antisuplantacion"`.

## Instalación rápida de lo que sí se instaló aquí

```bash
pip install -r tools/requirements.txt
```

Para las herramientas Go (Nuclei, Subfinder, Amass, osv-scanner), Trivy,
Grype, ZAP y Metasploit: usar los comandos exactos de la tabla de arriba
(cada wrapper también los repite en su docstring y en el mensaje de error
de `ToolNotInstalledError`).
