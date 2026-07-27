# Política de Uso Aceptable — Vigia

**Versión:** 1.0 (borrador)
**Fecha de emisión:** 2026-07-26
**Titular del servicio:** `Camilo Sánchez (persona natural, Colombia)`
**Reporte de abuso:** `camilosanch2008@gmail.com`

---

## 1. La regla que no admite excepción

**Vigia solo puede usarse contra activos digitales de los que el usuario sea propietario o respecto de los cuales tenga autorización escrita del titular.**

No hay excepción por buena intención, por urgencia, por interés académico, por "solo quería probar", ni por tratarse de un activo aparentemente abandonado. La legalidad del escaneo depende del consentimiento del titular del sistema, no del propósito de quien escanea.

Esta regla aplica a **todos** los módulos del servicio: reconocimiento, escaneo pasivo, escaneo activo, verificación de hallazgos, análisis de código y anti-suplantación.

---

## 2. Marco legal — por qué esto es un delito, no una infracción contractual

En Colombia, la **Ley 1273 de 2009** ("De la protección de la información y de los datos") incorporó al Código Penal el título de los delitos informáticos. Entre los tipos penales relevantes para el uso de una herramienta como esta:

| Artículo | Conducta tipificada |
|---|---|
| **269A** | Acceso abusivo a un sistema informático — acceder sin autorización, o mantenerse dentro contra la voluntad de quien tiene derecho a excluirlo. |
| **269B** | Obstaculización ilegítima de sistema informático o red de telecomunicación. |
| **269C** | Interceptación de datos informáticos. |
| **269D** | Daño informático. |
| **269E** | Uso de software malicioso. |
| **269F** | Violación de datos personales. |
| **269G** | Suplantación de sitios web para capturar datos personales. |

La ley contempla penas privativas de la libertad de hasta 120 meses y multas, con circunstancias de agravación punitiva. **El tipo penal del artículo 269A no exige que el acceso cause daño ni que se persiga un beneficio**: basta acceder sin autorización. Un escaneo activo contra un sistema ajeno puede configurar acceso abusivo con independencia de que quien lo ejecutó pensara que estaba ayudando.

La autorización escrita del titular del sistema es precisamente lo que excluye la conducta de ese tipo penal. Por eso el proyecto mantiene la plantilla [`legal/autorizacion-pruebas-seguridad.md`](autorizacion-pruebas-seguridad.md) y por eso su uso no es opcional cuando el activo no es propio.

Aplican además la **Ley 1581 de 2012** (protección de datos personales) y la **Ley 2573 de 2026**, en lo pertinente.

---

## 3. Cómo la plataforma hace cumplir esta regla técnicamente

La regla no depende únicamente de la buena fe del usuario. Está implementada en el producto:

### 3.1 Verificación de control de dominio

Registrar un dominio en la plataforma **no basta** para poder escanearlo. El usuario debe demostrar control publicando un token único emitido por la plataforma, en una de dos ubicaciones que solo alguien con control real puede escribir:

1. **Registro DNS TXT** en `_vigia-challenge.<dominio>` — requiere acceso al panel DNS del dominio. Es el método principal y la señal más fuerte de control del nombre.
2. **Archivo bien conocido** en `https://<dominio>/.well-known/vigia-verification.txt` — requiere poder publicar contenido en el servidor que responde por ese dominio. Es la alternativa estándar para quien administra el sitio pero no el DNS.

Es el mismo patrón que usan Google Search Console y plataformas equivalentes de seguridad. La plataforma comprueba efectivamente la publicación antes de marcar el activo como verificado.

### 3.2 Rechazo en el borde de la API

Las operaciones de escaneo pasivo, escaneo activo y anti-suplantación **rechazan con error `403`** cualquier objetivo que no corresponda a un activo verificado del propio tenant. El rechazo ocurre antes de invocar ninguna herramienta de escaneo.

### 3.3 Puerta de autorización determinista en el pipeline

Dentro del orquestador existe una condición de flujo escrita en código plano, sin intervención de ningún modelo de lenguaje: si el indicador de autorización no es verdadero, la ejecución se desvía a un nodo de bloqueo y el agente de escaneo nunca se invoca. El propio agente de escaneo revalida la condición antes de ejecutar cualquier herramienta (defensa en profundidad).

### 3.4 Consentimiento diferenciado para envío de código a terceros

El análisis de código con modelos de lenguaje exige dos consentimientos independientes en la misma solicitud. Si falta cualquiera de los dos, la solicitud se rechaza antes de que ningún archivo se escriba en disco.

### 3.5 Excepción de laboratorio, acotada

Se exime de verificación a `localhost` y a direcciones de rangos privados, *loopback* y *link-local*. La exención se aplica al host o IP declarado tal cual, evaluado con la librería estándar de direcciones IP — no es una lista de "dominios de prueba" declarados por el usuario. Un objetivo así solo es alcanzable desde la misma máquina o red que ejecuta la plataforma y no puede corresponder al sistema de un tercero.

### 3.6 Límites de volumen, ritmo e identidad del sondeo

Además de las puertas de autorización, la plataforma se autolimita en **cuánto** y **qué tan rápido** actúa. Estos límites protegen a los sistemas sondeados, no solo a la plataforma, y son parte de lo que el Titular se compromete a mantener.

| Límite | Qué hace | Dónde está en el código |
|---|---|---|
| **Rate limiting por tenant** | Los endpoints caros tienen cuota: `POST /scan`, `POST /scan/activo`, `POST /antisuplantacion` y `POST /reports/revision-ia`. La clave del límite es el `tenant_id` extraído del JWT —no el usuario— para que un tenant no multiplique su cuota creando cuentas; sin JWT válido cae a la IP. Excedido el límite, la API responde `429`. | `api/main.py:147` (`clave_rate_limit`), límites en `:181-185`, aplicados en `:2245`, `:2839`, `:3294`, `:3455`. Configurable por `VIGIA_RATE_LIMIT_*`. |
| **Throttling del sondeo de dominios de terceros** | Entre dos sondeos consecutivos se espera un mínimo (2 s por defecto), y entre dos sondeos **al mismo host** un mínimo mayor (10 s por defecto). Un dominio *lookalike* nunca recibe una ráfaga. | `tools/antisuplantacion.py:109` (`turno_de_sondeo`), configuración en `:90-93`. Variables `VIGIA_SONDEO_DELAY_SEGUNDOS`, `VIGIA_SONDEO_DELAY_MISMO_HOST`. |
| **Tope de concurrencia del sondeo** | Un semáforo limita a 2 (por defecto) los sondeos simultáneos a infraestructura de terceros. | `tools/antisuplantacion.py:99` (`_obtener_semaforo`), `VIGIA_SONDEO_MAX_CONCURRENTE`. |
| **Tope de escaneos activos simultáneos** | El escaneo activo —el único que envía cargas de prueba— reserva un cupo antes de arrancar. Agotados los cupos, la corrida espera y, si no se libera ninguno, falla con un error legible en lugar de sumar carga. | `tools/zap_api.py:182` (`turno_de_escaneo_activo`), reservado en `:622` y `:778`. `VIGIA_ZAP_MAX_CONCURRENTES`, `VIGIA_ZAP_ESPERA_SLOT_SEGUNDOS`. |
| **Dominio verificado obligatorio para registrar marca vigilada** | `POST /watched-brands` exige un `dominio_asociado` que ya sea un activo **verificado** del propio tenant, o responde `403`. El escucha de CertStream ignora las marcas sin dominio anclado, de modo que una marca no verificada no genera vigilancia. Las filas anteriores a este control quedaron sin anclar y se reportan con `verificada=false`. | `api/main.py:4042` (`_dominio_verificado_para_marca`), aplicado en `:4112` (`POST /watched-brands`); filtro en `api/certstream_listener.py:155-156`; columna en `db/schema.sql:255`. |

#### El bot se identifica a propósito

Todo sondeo a un dominio de terceros —incluidas las peticiones HTTP y el navegador Chromium que toma las capturas de pantalla— viaja con un User-Agent declarado: `Vigia-BrandMonitor/1.0` seguido de una URL donde consultar esta política y reclamar (`tools/antisuplantacion.py:62-71`, aplicado en `:536` para las peticiones HTTP y en `:675` para el navegador).

Esto es una decisión deliberada, no un descuido. Un bot que **se identifica y publica dónde reclamar** es lo contrario de una actividad encubierta: el administrador del sistema sondeado puede reconocerlo en sus logs, bloquearlo con una regla de `robots.txt` o de WAF, y escribir al canal de abuso de la Sección 7. Ocultar la identidad del sondeador facilitaría el trabajo pero eliminaría exactamente la vía por la que un tercero afectado puede oponerse. Se prefiere ser bloqueable.

> **Pendiente:** la URL del User-Agent apunta al repositorio público del proyecto mientras no exista un dominio institucional publicado. Debe reemplazarse por la URL definitiva (`VIGIA_BOT_USER_AGENT`) antes de operar comercialmente.

### 3.7 Límites conocidos de la aplicación técnica — declarados, no ocultos

La honestidad sobre lo que el control **no** hace es parte de la política:

- **El documento de autorización firmado no se almacena en la plataforma.** El indicador de autorización que acompaña cada solicitud de escaneo es una **declaración del propio usuario**. La plataforma verifica el control del dominio, no la existencia del contrato. Conservar y poder exhibir la autorización firmada es responsabilidad del usuario.
- **Marcas vigiladas — qué comprueba y qué no la verificación.** El alta de una marca sí exige hoy un vínculo con un activo de dominio ya verificado del propio tenant (Sección 3.6). Pero eso acredita **control de un dominio**, no **titularidad de la marca**: quien controla `ejemplo.com` puede registrar como vigilada cualquier cadena de texto anclada a ese dominio. Registrar una marca de la que no se es titular ni se tiene legitimación acreditada sigue siendo una violación de esta política aunque la comprobación automática lo permita.

---

## 4. Conductas expresamente prohibidas

Queda prohibido usar Vigia para:

1. Escanear, sondear, enumerar o probar cualquier activo del que no se sea propietario ni se tenga autorización escrita del titular.
2. Eludir, manipular o intentar burlar el mecanismo de verificación de control de dominio, incluidos: declarar un activo ajeno como propio, abusar de la excepción de rangos privados, o manipular el indicador de autorización.
3. Registrar como marca vigilada una marca de la que no se es titular ni se tiene legitimación acreditada.
4. Ejecutar o intentar ejecutar ataques de denegación de servicio, pruebas de carga destructivas o cualquier acción dirigida a degradar la disponibilidad de un sistema.
5. Extraer, modificar, cifrar o eliminar datos de sistemas evaluados, propios o ajenos.
6. Usar los hallazgos para extorsionar, presionar o coaccionar a un tercero, incluido el titular de un dominio *lookalike*.
7. Publicar hallazgos sobre infraestructura de terceros, o divulgarlos a personas distintas del titular legítimo de la marca afectada y de las autoridades competentes. Ver la [Política de Divulgación Responsable](divulgacion-responsable.md).
8. Realizar acción directa contra infraestructura de terceros: acceder a un dominio *lookalike*, intentar tomarlo, atacarlo o desfigurarlo.
9. Recolectar o cruzar información sobre personas naturales con fines distintos a la protección de la marca del propio usuario, o usar los perfiles de redes sociales detectados para acosar, señalar públicamente o exponer a una persona.
10. Enviar a análisis por modelos de lenguaje código fuente sobre el que no se tienen derechos suficientes, o cuya divulgación a un tercero infrinja obligaciones de confidencialidad.
11. Revender, sublicenciar o exponer el servicio a terceros como si fuera propio, sin acuerdo escrito con el Titular.
12. Cualquier uso que constituya delito bajo la Ley 1273 de 2009 o infracción de la Ley 1581 de 2012.

---

## 5. Qué NO hace la plataforma

Declarado expresamente, para que nadie —ni cliente, ni tercero afectado, ni autoridad— tenga que suponerlo:

| La plataforma **no** | Precisión |
|---|---|
| Ejecuta ataques de denegación de servicio | No existe ningún módulo de DoS, DDoS ni prueba de carga. |
| Ataca terceros | Ninguna herramienta se dirige a un objetivo que no sea un activo verificado del propio tenant. |
| Explota vulnerabilidades de forma general | El escaneo activo envía cargas de prueba para *detectar* vulnerabilidades y verificar hallazgos sospechosos sobre el activo autorizado. No busca escalar privilegios, no persiste acceso, no instala nada, no exfiltra datos. |
| Toma control de dominios *lookalike* | No se accede a ellos con fines distintos a la captura de evidencia pública, ni se intenta ninguna acción sobre su infraestructura. |
| Consulta ni almacena registros WHOIS | Decisión de diseño explícita. La correlación de dominios sospechosos se hace con señales de infraestructura pública (DNS, certificados, contenido), no con datos de registro que suelen incluir información personal del titular. |
| Retiene el código fuente del cliente | Se elimina del disco al terminar la revisión, con éxito o con error. |
| Realiza ingeniería social | No hay envío de correos de *phishing* simulado, ni contacto con empleados del cliente ni de terceros. |
| Sustituye una auditoría profesional ni certifica cumplimiento | Los reportes son orientativos. |

---

## 6. Consecuencias de la violación

1. **Suspensión inmediata de la cuenta**, sin preaviso, ante indicios razonables de violación de las Secciones 1 o 4. La suspensión no genera derecho a reembolso proporcional cuando la causa es imputable al usuario.
2. **Terminación del contrato** si la violación se confirma o se reitera.
3. **Conservación de evidencia**: los registros de actividad asociados a la conducta se conservan durante el tiempo necesario para atender reclamaciones o requerimientos de autoridad, aun por encima de los plazos ordinarios de la [Política de Retención](politica-de-retencion.md).
4. **Reporte a las autoridades competentes** cuando la conducta pueda constituir delito, y colaboración con la Fiscalía General de la Nación, el CSIRT/COLCERT o la autoridad que lo requiera.
5. **Indemnidad**: el usuario responde frente al Titular por las reclamaciones, sanciones y costas derivadas de su conducta, conforme a la Sección 11 de los [Términos de Servicio](terminos-de-servicio.md).

---

## 7. Reporte de abuso

Quien considere que un activo suyo ha sido escaneado desde esta plataforma sin su autorización puede escribir a `camilosanch2008@gmail.com` indicando el dominio o IP afectado y la fecha aproximada. El Titular investigará con los registros disponibles y responderá dentro de cinco (5) días hábiles.

---

Documento de trabajo. Requiere revisión de abogado colombiano especializado en derecho digital antes de uso contractual formal.
