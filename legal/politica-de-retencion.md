# Política de Retención y Supresión de Datos — Vigia

**Versión:** 1.0 (borrador)
**Fecha de emisión:** 2026-07-26
**Responsable:** `[RAZÓN SOCIAL / NIT / DOMICILIO]`
**Canal de solicitudes de supresión:** `[correo de contacto de habeas data]`

---

## 1. Principio

Vigia conserva cada dato únicamente durante el tiempo necesario para la finalidad que justificó su recolección, y lo suprime después. Los plazos de esta política se derivan del principio de **temporalidad** de la Ley 1581 de 2012 y no reemplazan plazos legales de conservación más largos que apliquen al Responsable (obligaciones tributarias, contables o procesales).

---

## 2. Estado de implementación — advertencia previa

**La supresión automática por vencimiento de plazos está implementada solo para dos tipos de dato.** Para todos los demás, no existe todavía un proceso programado que borre datos al cumplirse los plazos de la tabla de la Sección 3.

Lo que hoy **es automático y está verificado en el código**:

1. **Código fuente subido a revisión** — se borra en un bloque `finally`, así que se ejecuta aunque la revisión falle (`api/main.py:2153`, `api/main.py:2308`).
2. **Capturas de pantalla de dominios *lookalike*** — job periódico de APScheduler que borra los archivos más viejos que el plazo de retención (`api/scheduler.py:336` `run_screenshot_purge_once()`, registrado como job `vigia_purga_screenshots` en `api/scheduler.py:428`). Retención por defecto **90 días** (`VIGIA_SCREENSHOT_RETENCION_DIAS`), barrido cada 24 h por defecto (`VIGIA_SCREENSHOT_PURGE_INTERVAL_HOURS`).

Para el resto de filas de la Sección 3:

- Los plazos son **compromisos de política y objetivos de configuración**, no comportamientos ya automatizados del sistema.
- Mientras no exista el proceso automático, la supresión se ejecuta **a solicitud** por el canal indicado arriba, y por revisión manual periódica.

Esta advertencia se retirará de este documento únicamente cuando el proceso automático cubra todas las filas de la Sección 3 y se haya probado.

---

## 3. Plazos por tipo de dato

Todos los plazos son **configurables** por el Responsable y se cuentan desde el evento indicado, salvo que una obligación legal o un requerimiento de autoridad exija conservarlos más tiempo.

| Tipo de dato | Dónde vive | Plazo propuesto | Se cuenta desde | Estado |
|---|---|---|---|---|
| **Código fuente subido a revisión** | `uploads/<tenant>/<review_id>/` | Inmediato — se elimina al terminar la revisión, con éxito o con error | Fin de la ejecución | **Implementado y verificado** (`api/main.py:2153`, `:2308`) |
| **Capturas de pantalla de dominios *lookalike*** | `data/screenshots` (`VIGIA_SCREENSHOT_DIR`) | **90 días** (`VIGIA_SCREENSHOT_RETENCION_DIAS`) | Fecha de modificación del archivo (equivalente a la fecha de captura) | **Implementado y verificado** — job periódico `vigia_purga_screenshots` (`api/scheduler.py:336`, `:428`) |
| **Hallazgos de perfiles de redes sociales de terceros** (`perfil_red_social`) | Tabla `findings` (`raw_json`) | **90 días** si el Cliente no los marca como relevantes; **inmediato** si el Cliente los descarta como falso positivo | Fecha de detección o de descarte | Pendiente de automatizar |
| **Hallazgos de dominios *lookalike*** | Tabla `findings` | **24 meses** — plazo largo deliberado: la evidencia de un intento de suplantación puede ser necesaria para una denuncia o un proceso | Cierre del hallazgo | Pendiente de automatizar |
| **Hallazgos de vulnerabilidad sobre activos del Cliente** | Tabla `findings` y su historial de estados | **24 meses** tras el cierre del hallazgo — permite demostrar diligencia y detectar reincidencias | Cierre del hallazgo | Pendiente de automatizar |
| **Registros de ejecución de escaneos y trazas del pipeline** | Tabla `scans` (`trace_log_json`) | **12 meses** | Fin del escaneo | Pendiente de automatizar |
| **Reportes generados (PDF/DOCX)** | **No se almacenan.** Se renderizan en memoria y se transmiten en la respuesta HTTP (`api/main.py:1970`, `:2073`, `:3836`) | No aplica — no hay copia en servidor | No aplica | **No aplica — nada que retener** |
| **Texto del reporte final de un escaneo** | Columna `scans.reporte_final` (`db/schema.sql:102-114`) — es la fuente desde la que se re-renderiza un PDF/DOCX | **24 meses** | Fin del escaneo | Pendiente de automatizar |
| **Reportes DMARC** | Tablas del módulo DMARC | **12 meses** — son informes agregados con valor estadístico decreciente | Fecha de ingesta | Pendiente de automatizar |
| **Cuenta de usuario y datos de tenant** | Tablas `users`, `tenants`, `assets` | **90 días** tras la terminación del contrato, para permitir reactivación y exportación; después, supresión o anonimización | Terminación del contrato | Pendiente de automatizar |
| **Datos de facturación** | Módulo de pagos | Plazo legal aplicable en materia contable y tributaria (**mínimo 5 años**, art. 28 Código de Comercio) | Emisión del documento | Obligación legal |
| **Registros de actividad asociados a un abuso investigado** | Registros del sistema | Hasta la conclusión de la investigación o del proceso, aun si excede los plazos ordinarios | Apertura del caso | Excepción declarada |

### 3.1 Nota sobre el plazo de las capturas de pantalla

Una versión anterior de esta política proponía **180 días** para las capturas de dominios *lookalike*. El plazo se reduce a **90 días** por dos razones:

1. Es lo que el sistema **hace de verdad**: el job de purga usa 90 días por defecto (`api/scheduler.py:361`). Una política que promete un plazo distinto al implementado no es una política, es una discrepancia.
2. Es el criterio **más protector** para los terceros. Una captura de un dominio *lookalike* puede contener datos personales de un tercero que nunca consintió el tratamiento; el principio de temporalidad de la Ley 1581 de 2012 pide el plazo más corto compatible con la finalidad. La finalidad —acreditar la existencia del sitio suplantador ante el titular de la marca o ante una autoridad— se cumple con 90 días.

El hallazgo asociado (fila "Hallazgos de dominios *lookalike*") conserva su plazo de 24 meses: sobrevive el **registro** del intento de suplantación, no la copia de la página del tercero.

### 3.2 Estado de automatización de las filas restantes

Todas las filas marcadas "Pendiente de automatizar" lo están de verdad: **no existe hoy ningún job, tarea programada ni sentencia de borrado por vencimiento** para hallazgos, trazas de escaneo, reportes generados, reportes DMARC ni cuentas terminadas. El *scheduler* (`api/scheduler.py`) corre exactamente tres jobs, y solo uno de ellos —`vigia_purga_screenshots`— es de retención. Esas filas se atienden de forma manual y a solicitud, conforme a la Sección 4.

---

## 4. Supresión anticipada a solicitud

1. **El Cliente** puede solicitar en cualquier momento la supresión de sus activos, hallazgos, reportes y cuenta. El Responsable la ejecutará dentro de los quince (15) días hábiles del trámite de reclamo, salvo deber legal de conservación.
2. **Un tercero** cuyos datos hayan sido registrados como consecuencia de la vigilancia anti-suplantación (por ejemplo, el titular de un perfil de red social detectado por homonimia) puede solicitar su supresión por el mismo canal. El Responsable la atenderá en los mismos plazos y notificará al Cliente correspondiente.
3. La supresión de un hallazgo no elimina las obligaciones de conservación cuando exista una investigación en curso o un requerimiento de autoridad.

---

## 5. Efectos de la terminación del contrato

1. Durante los noventa (90) días siguientes a la terminación, el Cliente puede solicitar la **exportación** de sus reportes y hallazgos en formato legible.
2. Vencido ese plazo, los datos del Cliente se suprimen o anonimizan, salvo los sujetos a obligación legal de conservación.
3. Los respaldos (*backups*) pueden conservar copias durante un máximo de `[plazo del ciclo de respaldo — sugerido: 35 días]` adicionales; esas copias no se consultan salvo para recuperación ante desastre y se sobrescriben en el ciclo normal.

---

## 6. Anonimización como alternativa a la supresión

Cuando el Responsable requiera conservar información para métricas de calidad del servicio, lo hará mediante **datos agregados y anonimizados** —conteos, tasas de detección, distribuciones de severidad— sin identificadores de cliente, dominio ni persona. La información anonimizada no está sujeta a los plazos de la Sección 3.

---

## 7. Revisión

Esta política se revisará al menos una vez al año, y cada vez que se implemente o modifique el proceso de supresión automática. Cada revisión debe actualizar la columna "Estado" de la Sección 3 conforme a lo realmente implementado en el código.

---

Documento de trabajo. Requiere revisión de abogado colombiano especializado en derecho digital antes de uso contractual formal.
