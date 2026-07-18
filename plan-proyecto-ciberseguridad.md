# Vigia — Plan Maestro

## Estado real de implementación (actualizado 2026-07-18)

Este documento nació como el plan de diseño *antes* de escribir código. Hoy ya hay un producto funcionando de verdad, probado en vivo — esta sección dice honestamente qué está construido y probado, qué está construido pero sin probar a fondo, y qué falta. El resto del documento (secciones 1-9) es el plan original y sigue siendo la referencia de visión/arquitectura; donde el estado real difiere de lo planeado, esta sección manda.

**Funciona y está probado en vivo (no solo escrito):**
- Pipeline LangGraph completo: orquestador → recon → gate de autorización (determinista) → escaneo → verificación → priorización → remediación → gate anti-suplantación → reportería (`orchestrator/`, `agents/`).
- Recon pasivo real con Subfinder + Amass (encontraron subdominios reales en pruebas).
- Escaneo activo real con Nuclei (CVEs/misconfiguraciones) **y OWASP ZAP** (DAST real vía Docker, tanto pasivo/`zap-baseline` como activo/`zap-full-scan` con inyección de token de sesión para cubrir rutas autenticadas — ver `tools/scan.py::run_zap_active_scan`).
- Manejo de errores real: timeouts de herramientas degradan con gracia (`ToolExecutionError`/`ToolTimeoutError`) en vez de tumbar el pipeline.
- Harness de evaluación real (`eval/run_eval.py` + `eval/ground_truth.yaml`, 11 vulnerabilidades documentadas de Juice Shop) — mide precisión/recall real, no estimado. Ver `eval/live_run_report.md` para el historial de corridas y números reales (recall subiendo con cada arreglo: 0% → 9% al conectar ZAP de verdad, en progreso hacia más con escaneo activo autenticado y más tiempo).
- **Backend multi-tenant real**: JWT + bcrypt propio, tablas `tenants`/`users`/`assets`/`scans`/`findings`/`subscriptions` (SQLite hoy, dialecto compatible con Postgres/Supabase). Endpoints reales: registro, login, gestión de dominios, historial de escaneos, hallazgos.
- **Frontend real** (`frontend/`, React+Vite+TS): login/registro + dashboard con nivel de riesgo, inventario de dominios, actividad de escaneos, estado de bienvenida para cuentas nuevas.
- Módulo anti-suplantación (`tools/antisuplantacion.py`, dnstwist + Sherlock) validado contra un caso real (anonimizado en este repo, ver sección 1).

**Construido pero sin probar a fondo o sin conectar todavía:**
- Trivy, Grype, Semgrep, OSV.dev (`tools/scan.py`) — wrappers escritos, nunca corridos contra un objetivo real en esta sesión. Relevantes sobre todo para el frente de dependencias/código fuente, no para escaneo web puro.
- Exploit-DB / `tools/exploit_intel.py` — usado por el Agente de Verificación, pero su índice offline no se ha auditado por cobertura real.
- Metasploit, Faraday — mencionados en el inventario (sección 3), cero código escrito todavía.
- Google Safe Browsing (`tools/antisuplantacion.py::check_safe_browsing`) — requiere API key que no está configurada.

**Falta para ser "un proyecto completo de ciberseguridad" (brechas reales, no aspiracionales):**
1. **Cumplimiento normativo (Ley 2573/ISO 27001) como producto** — mencionado en el plan (sección 4) y validado como el gancho de venta más fuerte por el research de mercado (`docs/market-research.md`, sección 3), pero no existe ni una línea de código todavía.
2. **Escaneo recurrente/programado** — hoy cada escaneo es manual (botón "Escanear ahora"). Sin esto no hay "vigilancia continua" real, que es la propuesta de valor central.
3. **Alertas por WhatsApp/email** cuando aparece un hallazgo crítico o un dominio clon nuevo — cero implementado.
4. **Pasarela de pagos** (Wompi recomendado) — en pausa a pedido explícito, pendiente de retomar.
5. **Repo público** con contenido ya anonimizado, pero el historial de git previo a la anonimización sigue expuesto — pendiente que el dueño lo haga privado.
6. **DVWA/WebGoat como targets de evaluación adicionales** — hoy el harness solo mide contra Juice Shop; un solo target de referencia puede sobreajustar el sistema a sus particularidades.
7. **Portal de cliente más allá del dashboard actual**: reportes descargables (PDF/DOCX), comparación entre escaneos en el tiempo, invitar más usuarios al tenant (hoy solo hay rol `owner` funcional).

## 0. Regla no negociable (léela antes que todo lo demás)

Este proyecto encuentra vulnerabilidades para **arreglarlas**, nunca para dañar ni para tocar sistemas de terceros sin permiso. Esa línea no es opcional ni ética solamente — es penal: la Ley 1273 de 2009 (Colombia) castiga el "acceso abusivo a sistema informático" con hasta 120 meses de cárcel, **sin importar la intención** de quien lo hizo. Es exactamente lo mismo que estudiar medicina: aprendes cómo funciona una enfermedad para curarla, no para contagiar a alguien.

Regla de oro operativa: **nunca se escanea ni se prueba nada que el dueño no haya autorizado por escrito.** Antes de tocar cualquier sistema de un cliente real, existe un documento firmado ("autorización de pruebas de seguridad" / scope agreement). Sin eso, no se toca nada — se prueba solo contra aplicaciones de laboratorio hechas para eso (OWASP Juice Shop, DVWA, HackTheBox, TryHackMe), que son gratis y legales para practicar indefinidamente.

## 1. Por qué esto tiene sentido de negocio (no es solo curiosidad técnica)

- Colombia registró 218.031 reclamaciones por fraude solo en el primer semestre de este año, y la SIC ya encendió alarmas por el crecimiento de la suplantación de identidad.
- Acaba de entrar en vigencia la **Ley 2573 de 2026**, que obliga a las empresas a reforzar sus controles de validación digital y pone la carga de la prueba sobre quien tenga mejores condiciones de demostrar el fraude. La mayoría de pymes no tiene ni idea de cómo cumplir esto todavía — ventana de oportunidad real y con urgencia legal, no inventada.
- El mercado internacional de esto (attack surface management / brand protection) ya es grande y probado: empresas como BrandShield, Bolster AI, PhishFort, Detectify, Intruder.io, Astra Security facturan entre $5.000 y $50.000+ USD/año por cliente. Para pyme colombiana el ticket baja mucho, pero el modelo de negocio (suscripción, no proyecto único) ya está validado afuera.
- Ya tienes un caso de uso real identificado (anonimizado en este documento): un concesionario de motos colombiano tuvo su WhatsApp hackeado y sufre suplantadores usando su nombre — es un ejemplo real y actual de la clase de problema que este proyecto resuelve. Es solo el caso semilla que motivó el proyecto, no un cliente ni un dato para publicar.

## 2. Qué es realmente el producto

No es "un hacker que busca exploits". Es una **plataforma de gestión de superficie de ataque y cumplimiento**, la misma categoría que Detectify o Intruder.io: un sistema de agentes de IA que:

1. Descubre qué tiene expuesto una empresa en internet (dominios, subdominios, apps, redes sociales).
2. Escanea esos activos autorizados en busca de vulnerabilidades conocidas (usando motores ya existentes y probados, no inventando exploits desde cero).
3. Verifica que los hallazgos sean reales (no falsos positivos) de forma determinista, separado del razonamiento de la IA.
4. Prioriza qué arreglar primero según el riesgo real para el negocio.
5. Genera instrucciones concretas de arreglo (no solo "tienes una falla", sino cómo corregirla).
6. Entrega todo en un reporte que un dueño de pyme sin conocimiento técnico pueda entender.
7. (Módulo opcional) Vigila si alguien está suplantando la marca/identidad de la empresa en internet.

Tu trabajo no es reinventar el escaneo de vulnerabilidades ni la detección de phishing desde cero — es **orquestar con agentes de IA herramientas ya existentes, gratuitas, probadas y legales**, y poner encima la capa de inteligencia que ellas no tienen: verificación, priorización con contexto de negocio, remediación redactada y reportes entendibles. Esa capa es tu producto real.

## 3. Inventario de repositorios y herramientas existentes (capas, no reinventar)

Verifiqué que todo esto existe hoy, es de código abierto, y se puede envolver con agentes en vez de reconstruirlo. Divídelo en dos frentes.

### 3.1 Frente de vulnerabilidades / exploits (defensivo)

| Herramienta | Repositorio | Qué hace | Capa del pipeline |
|---|---|---|---|
| **Nuclei** | `projectdiscovery/nuclei` | Escáner basado en +12.000 plantillas YAML que cubren CVEs conocidas, configuraciones débiles, credenciales por defecto. MIT. | Agente de Escaneo |
| **OWASP ZAP** | `zaproxy/zaproxy` | El escáner dinámico (DAST) de aplicaciones web más usado del mundo. Apache 2.0, mantenido por Checkmarx. | Agente de Escaneo |
| **OWASP Amass** | `owasp-amass/amass` | Mapeo de superficie de ataque y descubrimiento de activos (subdominios, infraestructura). | Agente de Recon |
| **Subfinder** | `projectdiscovery/subfinder` | Enumeración pasiva de subdominios — no toca el objetivo activamente, solo consulta fuentes públicas. Muy rápido. | Agente de Recon |
| **Exploit-DB / searchsploit** | `offensive-security/exploitdb` | Base de datos de exploits ya publicados y documentados, buscable offline. Es la referencia histórica del sector para saber qué vulnerabilidades ya tienen prueba de concepto pública. | Agente de Verificación / conocimiento base |
| **Metasploit Framework** | `rapid7/metasploit-framework` | Framework estándar de la industria con módulos para vulnerabilidades conocidas, usado en pentesting autorizado. | Agente de Escaneo (uso avanzado, opcional) |
| **OSV.dev** | `google/osv.dev` | Base de datos abierta de vulnerabilidades de Google + herramienta de escaneo de dependencias (librerías de tu código). | Agente de Escaneo (dependencias) |
| **Trivy** | `aquasecurity/trivy` | Escáner de contenedores, sistemas de archivos e infraestructura como código. +32.000 estrellas en GitHub, muy usado en la industria. | Agente de Escaneo |
| **Grype** | `anchore/grype` | Escáner de composición de software (SCA) — vulnerabilidades en dependencias/librerías. | Agente de Escaneo |
| **Semgrep** | `semgrep/semgrep` | Análisis estático de código (SAST) — encuentra fallas de seguridad directo en el código fuente del cliente, si te lo comparten. | Agente de Escaneo (código) |
| **Faraday** | `infobyte/faraday` | Centraliza resultados de Nuclei/ZAP/Nessus y ayuda a priorizar. Versión de pago para reportería avanzada. | Agrega hallazgos de todos los agentes |
| **PentestGPT** | `GreyDGL/PentestGPT` | Herramienta académica open source (USENIX Security 2024), 86.5% de éxito en benchmarks, ya resuelve el flujo "enumerar → analizar → explotar" con LLMs. | Referencia de arquitectura para tu Orquestador |
| **PentAGI** | proyecto open source | Coordina múltiples agentes de IA especializados (investigación, código, infraestructura) para pruebas de seguridad — el mismo concepto que estás construyendo, ya existe como referencia. | Referencia de arquitectura completa |
| **XBOW** | comercial (no open source) | La referencia número uno del sector — separa la verificación determinista de exploits del razonamiento de la IA, solo reporta hallazgos confirmados con pasos de reproducción. No lo usas directo, pero copias su principio de diseño. | Principio para tu Agente de Verificación |

### 3.2 Frente anti-suplantación / phishing / identidad

| Herramienta | Repositorio | Qué hace | Capa del pipeline |
|---|---|---|---|
| **dnstwist** | `elceef/dnstwist` | Motor de permutación de dominios — genera y verifica automáticamente variaciones de un dominio (typosquatting, ataques homográficos) para ver cuáles están registradas y activas. Esto es exactamente "detectar el dominio clonado antes de que estafe a alguien". | Agente Anti-Suplantación (núcleo) |
| **mcp-dnstwist** | `BurtTheCoder/mcp-dnstwist` | Ya existe un servidor MCP que envuelve dnstwist — se puede conectar directo a un agente sin escribir el wrapper tú mismo. | Integración directa con tu Orquestador |
| **CertStream + phishing_catcher** | `x0rz/phishing_catcher` | Escucha en tiempo real los logs de Certificate Transparency (todo certificado SSL emitido en el mundo) y marca dominios sospechosos apenas se registran — antes de que el sitio de phishing esté siquiera activo. | Agente Anti-Suplantación (monitoreo continuo) |
| **certthreat** | `PAST2212/certthreat` | Variante enfocada específicamente en monitorear nombres de marca y dominios de correo para detectar suplantación — más cercano a tu caso de uso que el genérico. | Agente Anti-Suplantación |
| **Sherlock** | `sherlock-project/sherlock` | Busca un nombre de usuario/marca en +400 redes sociales y plataformas — sirve para detectar perfiles falsos que se hacen pasar por el negocio (justo el problema del caso semilla del proyecto con WhatsApp/Instagram). | Agente Anti-Suplantación (redes sociales) |
| **Google Safe Browsing API** | API gratuita de Google | Verifica si una URL ya está marcada como maliciosa/phishing en la base de datos global de Google. | Agente de Verificación (anti-suplantación) |

Con esta lista, tu trabajo real es: conectar estas piezas con LangGraph, decidir qué hace cada agente con la salida de cada herramienta, y poner a Claude a interpretar/priorizar/redactar encima. No hay que escribir un escáner ni un motor de permutación de dominios desde cero — ya existen, son gratis, y son los que usa la industria entera.

## 4. Arquitectura de agentes (sobre tu stack ya existente: LangGraph + FastAPI + Claude)

```
                        ┌─────────────────────────┐
                        │   Agente Orquestador     │
                        │  (Task Manager / estado) │
                        └────────────┬────────────┘
                                     │
        ┌────────────┬──────────────┼──────────────┬────────────────┐
        ▼             ▼              ▼              ▼                ▼
┌───────────────┐┌──────────────┐┌──────────────┐┌───────────────┐┌──────────────────┐
│ Agente Recon  ││ Agente        ││ Agente        ││ Agente         ││ Agente            │
│ (pasivo)      ││ Escaneo       ││ Verificación  ││ Priorización   ││ Anti-Suplantación │
│               ││ (activo,      ││ (determinista,││ de Riesgo      ││ (opcional, marca/ │
│ subdominios,  ││ requiere      ││ NO-IA, evita  ││ (Claude scorea││ identidad)         │
│ tecnologías,  ││ autorización  ││ falsos        ││ severidad +   ││                    │
│ huella pública││ firmada)      ││ positivos)    ││ contexto de   ││                    │
│               ││               ││               ││ negocio)      ││                    │
└───────┬───────┘└──────┬───────┘└──────┬────────┘└───────┬───────┘└─────────┬──────────┘
        └────────────────┴───────────────┴────────────────┴──────────────────┘
                                            │
                                            ▼
                                ┌───────────────────────┐
                                │  Agente de Remediación │
                                │  (Claude redacta el    │
                                │  arreglo concreto)     │
                                └───────────┬───────────┘
                                            ▼
                                ┌───────────────────────┐
                                │  Agente de Reportería  │
                                │  (genera PDF/HTML para │
                                │  el cliente final)     │
                                └───────────────────────┘
```

### Función de cada agente y qué herramienta de la sección 3 usa

| Agente | Qué hace | Herramientas del inventario | Obligatorio o opcional |
|---|---|---|---|
| Orquestador | Reparte tareas, guarda estado, decide el flujo | LangGraph (tuyo) | Obligatorio (es la base) |
| Recon pasivo | Encuentra subdominios, tecnologías, huella pública — sin tocar nada activamente | Subfinder, OWASP Amass, crt.sh | Obligatorio, bajo riesgo legal |
| Escaneo activo | Corre escaneo contra el objetivo **ya autorizado** | Nuclei, OWASP ZAP, Trivy, Grype, Semgrep, OSV.dev | Obligatorio, solo con autorización firmada |
| Verificación determinista | Confirma que el hallazgo es real, no falso positivo | Reglas fijas + cruce con Exploit-DB/OSV para confirmar que el CVE es real | Obligatorio — sin esto el producto no es confiable |
| Priorización de riesgo | Traduce severidad técnica a riesgo real de negocio | Claude | Obligatorio, tu diferenciador frente a herramientas crudas |
| Remediación | Redacta el arreglo específico | Claude | Obligatorio |
| Reportería | Arma el documento final para el cliente | Claude + tu skill de PDF/DOCX | Obligatorio |
| Anti-Suplantación | Vigila dominios clon, perfiles falsos, WhatsApp suplantado | dnstwist, CertStream/phishing_catcher, certthreat, Sherlock, Safe Browsing | Opcional — módulo adicional o plan premium |
| Cumplimiento normativo | Mapea cada hallazgo a Ley 1273 / Ley 2573 / ISO 27001 | Claude con base de conocimiento normativa | Opcional, gancho de venta fuerte |

## 5. Fases de construcción

**Fase 0 — Base legal. ✅ Completa.** Plantilla de autorización de pruebas de seguridad (`legal/`).

**Fase 1 — MVP. ✅ Completa,** y más allá de lo planeado: no quedó en "Nuclei + ZAP baseline envuelto simple" — el pipeline completo de 7 agentes corre de punta a punta, con ZAP activo (no solo baseline) y probado en vivo contra Juice Shop varias veces, con bugs reales encontrados y arreglados en el camino (ver `eval/live_run_report.md`).

**Fase 2 — Multi-agente real. ✅ Completa.** Flujo completo en LangGraph: recon → gate autorización → escaneo → verificación → priorización → remediación → gate anti-suplantación → reportería.

**Fase 3 — Módulo anti-suplantación. ✅ Completa.** dnstwist + Sherlock integrados y validados contra el caso semilla (anonimizado). CertStream/phishing_catcher (monitoreo continuo) sigue pendiente — es una pieza de "Fase 5" en la práctica, no de validación puntual.

**Fase 4 — Productización. 🟡 En progreso, adelantada.** Ya existe backend multi-tenant real (auth, tenants, dominios, historial) y un dashboard funcional (React) — más que el "dashboard web simple" original. Falta: niveles de precio conectados a cobro real (pasarela de pagos, en pausa), reportes descargables, y programación de escaneos recurrentes.

**Fase 5 — Monitoreo continuo / suscripción. ⬜ No empezada.** Escaneo periódico automático, alertas por WhatsApp/Slack, cumplimiento normativo recurrente, CertStream para detección de dominios clon en tiempo real (no solo bajo demanda como hoy).

## 6. ¿Debería ser open source, si mi intención es ganar dinero?

Sí — y no es una contradicción, es el modelo que ya usa todo el sector. La pregunta correcta no es "¿regalo el proyecto o cobro por él?", es "¿qué parte regalo y qué parte cobro?". Eso se llama **open-core**, y es exactamente cómo Nuclei, ZAP y Faraday (los tres proyectos que estás usando como base) ganan dinero real hoy sin dejar de ser gratis en su núcleo.

Por qué específicamente en ciberseguridad el open source ayuda incluso a ganar más dinero, no menos: nadie confía en una herramienta de seguridad que no se pueda auditar. Un producto cerrado en este sector genera sospecha ("¿qué le hace de verdad a mi sistema?"), mientras que uno abierto genera confianza inmediata — por eso Nuclei (MIT), ZAP (Apache 2.0), PentestGPT y PentAGI son todos abiertos en su núcleo, no por generosidad sino porque así se venden mejor las capas de encima.

También te resuelve algo que dijiste tú mismo: no tienes ningún proyecto open source. En este sector específico, un repositorio bien hecho y con estrellas reales vale más como carta de presentación frente a un cliente o un reclutador que cualquier proyecto cerrado que nadie puede revisar.

### La división concreta (qué es gratis y qué cobras)

| Gratis / open source (GitHub) | De pago (lo que factura) |
|---|---|
| Motor de orquestación multi-agente (LangGraph) | Hosting del servicio (SaaS) — el cliente no monta nada |
| Agentes de Recon, Escaneo y Verificación básicos | Escaneo programado y monitoreo continuo |
| Wrappers de Nuclei/ZAP/dnstwist ya integrados | Dashboard y portal de cliente |
| Documentación y guía de instalación propia | Priorización con contexto de negocio (Claude, afinado por cliente) |
| | Reportes de cumplimiento normativo (Ley 2573, ISO 27001) |
| | Módulo Anti-Suplantación completo |
| | Soporte y onboarding |

Esto no es "regalar el proyecto para el que quieres cobrar" — el motor es el anzuelo (construye tu reputación, atrae usuarios técnicos, y algunos se vuelven clientes de la capa de pago), y la capa de pago es donde vive el negocio.

## 7. Prompt engineering — prompts base por agente

Estos son los prompts de sistema de arranque para cada agente. Se van a refinar con el tiempo (ver sección 8), pero esta es la base sólida para empezar a construir.

**Agente Orquestador**
```
Eres el orquestador de un pipeline de evaluación de seguridad. Tu única función es
decidir el siguiente paso del flujo según el estado actual (qué se ha descubierto,
qué falta, si hay autorización firmada para el objetivo).
Nunca ejecutas escaneos tú mismo. Nunca avanzas a la fase de Escaneo Activo si el
campo `autorizacion_firmada` del estado no es `true`. Si no existe autorización,
detén el flujo y reporta que falta el documento firmado.
```

**Agente de Recon (pasivo)**
```
Investigas la huella pública de un dominio/marca usando únicamente fuentes pasivas
(Subfinder, Amass, crt.sh). No te conectas activamente al objetivo, no envías
tráfico que no sea una consulta pública estándar. Devuelves: subdominios
encontrados, tecnologías detectadas, y activos que parezcan expuestos por error
(paneles de administración, backups públicos). Marca cada hallazgo con la fuente
exacta de donde salió.
```

**Agente de Escaneo (activo)**
```
Ejecutas Nuclei y OWASP ZAP contra el objetivo especificado en `scope`.
PRECONDICIÓN OBLIGATORIA: si `autorizacion_firmada` no es true, rechaza la tarea
y no ejecutes nada. Reporta cada hallazgo crudo con: plantilla/regla que lo
disparó, endpoint afectado, severidad reportada por la herramienta. No
interpretes ni prioricés todavía — eso lo hace otro agente.
```

**Agente de Verificación (determinista, sin LLM en el paso crítico)**
```
Recibes hallazgos crudos del Agente de Escaneo. Para cada uno: confirmas contra
la base OSV/Exploit-DB si el CVE referenciado es real y vigente, y vuelves a
intentar reproducir el hallazgo con una segunda consulta controlada. Solo
los hallazgos que pasan esta doble verificación avanzan al siguiente agente.
Todo lo demás se descarta o se marca como "no confirmado" — nunca se reporta al
cliente como si fuera un hecho.
```

**Agente de Priorización**
```
Recibes hallazgos ya verificados más contexto de negocio del cliente (qué hace
la empresa, qué sistemas son críticos para sus ventas). Traduces severidad
técnica (CVSS) a impacto real: "esto afecta tu página de pagos" pesa más que
"esto afecta una página informativa poco visitada", aunque el CVSS técnico sea
igual. Ordena los hallazgos de mayor a menor urgencia real para ESTE cliente.
```

**Agente de Remediación**
```
Para cada hallazgo priorizado, redactas la corrección específica: qué cambiar,
en qué archivo o configuración, con un ejemplo concreto cuando aplique. Escribes
para alguien que puede no ser técnico — evita jerga sin explicarla la primera vez.
Nunca prometes que el arreglo es 100% infalible; siempre recomienda re-escanear
después de aplicar el cambio.
```

**Agente de Reportería**
```
Compilas todo el flujo (recon, hallazgos verificados, prioridad, remediación) en
un reporte único, claro, sin tecnicismos innecesarios, usando la plantilla de
la empresa. Estructura: resumen ejecutivo (3-4 líneas), qué se encontró y por
qué importa, qué hacer primero, y anexo técnico para quien sí quiera el detalle.
```

**Agente Anti-Suplantación**
```
Buscas señales de que la marca/dominio del cliente está siendo suplantada: dominios
similares recién registrados (dnstwist + CertStream), perfiles en redes sociales
usando el mismo nombre o logo (Sherlock), URLs ya reportadas como maliciosas
(Safe Browsing). Para cada hallazgo, evalúas qué tan probable es que sea
suplantación real vs. coincidencia (empresa legítima con nombre parecido) y
explicas tu razonamiento. Adjuntas un borrador de solicitud de eliminación
(takedown) listo para enviar a la plataforma correspondiente.
```

## 8. Harness engineering y loop de mejora continua

Esto es lo que hace que el proyecto tenga bases sólidas para ir mejorando en vez de quedarse como una demo que funciona una vez y ya. Es la infraestructura *alrededor* de los prompts — cómo se ejecutan, cómo se verifican y cómo se corrigen con el tiempo.

**8.1 El harness (andamiaje de ejecución)**

- **Definición de herramientas (tool schemas):** cada herramienta del inventario (sección 3) se envuelve con una interfaz de función clara — entradas, salidas, y qué errores puede lanzar — para que los agentes la llamen de forma predecible, no como texto libre.
- **Sandboxing:** todo escaneo activo corre en un contenedor aislado, nunca con acceso directo a tu máquina ni a producción. Esto también te protege a ti si algo sale mal durante una prueba.
- **Puerta de autorización como nodo del grafo:** en LangGraph, la verificación de `autorizacion_firmada` no es una instrucción de prompt que el modelo podría ignorar — es un nodo de código determinista que bloquea físicamente el flujo si no se cumple. La seguridad del sistema no depende de que el LLM "se acuerde" de la regla.
- **Trazabilidad completa:** cada llamada a herramienta, cada decisión de cada agente, y cada output se guarda con timestamp y contexto. Esto no es opcional — es lo que te permite auditar qué pasó si algo falla, y es la materia prima del loop de mejora (8.2).

**8.2 El loop de evaluación y mejora continua**

1. **Set de evaluación fijo:** montas el pipeline completo contra aplicaciones de laboratorio con vulnerabilidades ya documentadas y conocidas (OWASP Juice Shop, DVWA, WebGoat). Como ya sabes qué vulnerabilidades tienen esas apps, puedes medir objetivamente qué tan bien las encuentra tu sistema.
2. **Métricas concretas:** de ese set calculas precisión (de lo que reportó, cuánto era real) y recall (de lo que existía, cuánto encontró). Sin esto, "mejorar" es una opinión, no un hecho medible.
3. **Control de versiones de prompts:** cada prompt de la sección 7 vive en git, versionado igual que el código. Un cambio de prompt es un commit, no una edición perdida en un chat.
4. **Regresión obligatoria antes de desplegar:** ningún cambio de prompt o de herramienta se pasa a producción sin antes correr el set de evaluación completo. Si un cambio baja el recall o sube los falsos positivos, no se despliega, aunque "se vea mejor" en un caso suelto.
5. **Revisión humana en los primeros clientes reales:** hasta que el pipeline tenga varios ciclos de evaluación consistentes, ningún reporte sale al cliente sin que tú lo revises primero. La automatización total se gana con evidencia, no se asume desde el día uno.
6. **Bitácora de fallos:** cada vez que el sistema se equivoca (falso positivo grave o algo que no encontró y debía), se documenta como caso de prueba nuevo que se agrega al set de evaluación — así el sistema no vuelve a fallar exactamente igual dos veces.

Este ciclo (evaluar → medir → versionar → probar regresión → desplegar → registrar fallos → volver a evaluar) es lo que separa un prototipo que impresiona una vez de un producto que un cliente puede pagar con confianza mes a mes.

## 9. Próximo paso concreto (actualizado 2026-07-18 — ver también la sección de estado real arriba)

Los primeros 3 puntos originales ya están hechos (autorización, MVP contra Juice Shop, repo en GitHub). Lo que sigue, en orden de impacto:

1. Subir el presupuesto de tiempo del escaneo activo de ZAP (`run_zap_active_scan`) y agregar login previo real, para medir cuánto sube el recall sobre las vulnerabilidades que requieren sesión iniciada (SQLi de login, IDOR de cesta, JWT) — en curso.
2. Mapeo de cumplimiento (Ley 2573/ISO 27001) como producto — es el gancho de venta validado por el research de mercado, y hoy no tiene ni una línea de código.
3. Escaneo recurrente/programado + alertas — sin esto, "vigilancia continua" es solo un botón manual, no la propuesta de valor real.
4. Hacer privado el repositorio (pendiente del dueño) y decidir si además se reescribe el historial de git.
5. Retomar la pasarela de pagos (Wompi) cuando se pida — está en pausa, no bloqueante para lo anterior.
6. Usar la demo funcionando para ofrecerle un diagnóstico gratuito a un negocio real (el caso semilla es candidato natural, con su consentimiento explícito) y de ahí escalar a clientes de pago.

---

*Este documento es un plan vivo — se actualiza a medida que el proyecto avanza. Última actualización: julio 2026.*
