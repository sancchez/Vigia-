# Autorización de Pruebas de Seguridad (Scope Agreement)

**Documento N.°:** ______________________
**Fecha de emisión:** ______________________

---

## Advertencia previa (léase antes de firmar)

Este documento es el **único mecanismo legal** que autoriza a los prestadores del servicio a realizar pruebas de seguridad informática sobre los activos digitales del Cliente. Sin este documento firmado por un representante con facultad para autorizarlo, **ningún escaneo activo, prueba de intrusión o verificación de vulnerabilidades puede realizarse**, bajo ninguna circunstancia.

El acceso no autorizado a un sistema informático constituye un delito en Colombia (ver Sección 9 — Marco Legal). Este documento existe precisamente para que las pruebas aquí descritas **no** constituyan ese delito: se realizan con el consentimiento informado, expreso y por escrito del titular de los sistemas.

---

## 1. Identificación de las partes

### 1.1 Prestador del servicio

| Campo | Valor |
|---|---|
| Razón social / Nombre | ______________________ |
| NIT / Documento de identidad | ______________________ |
| Dirección | ______________________ |
| Representante o responsable técnico | ______________________ |
| Correo electrónico de contacto | ______________________ |
| Teléfono de contacto (incluir línea de emergencia) | ______________________ |

### 1.2 Cliente (titular de los activos a evaluar)

| Campo | Valor |
|---|---|
| Razón social / Nombre de la empresa | ______________________ |
| NIT | ______________________ |
| Dirección | ______________________ |
| Nombre completo del representante que firma | ______________________ |
| Cargo del representante (debe tener facultad para autorizar) | ______________________ |
| Documento de identidad del representante | ______________________ |
| Correo electrónico de contacto | ______________________ |
| Teléfono de contacto | ______________________ |

**Declaración de facultad:** el representante del Cliente que firma este documento declara bajo su responsabilidad que **es el propietario de los activos descritos en el alcance**, o que cuenta con **autorización expresa del propietario** (por ejemplo: proveedor de hosting, dueño del dominio, casa matriz) para consentir las pruebas aquí descritas. El Prestador no asume responsabilidad si esta declaración resulta falsa; dicha responsabilidad recae exclusivamente sobre quien firma en representación del Cliente.

---

## 2. Alcance exacto autorizado (Scope)

Solo se autorizan pruebas sobre los activos explícitamente listados a continuación. **Todo lo que no esté en esta lista se considera fuera de alcance y NO está autorizado**, aunque pertenezca a la misma empresa o esté visiblemente relacionado.

### 2.1 Dominios y subdominios autorizados

| # | Dominio / Subdominio | Notas (ej. ambiente de producción, staging, etc.) |
|---|---|---|
| 1 | ______________________ | ______________________ |
| 2 | ______________________ | ______________________ |
| 3 | ______________________ | ______________________ |

### 2.2 Direcciones IP / rangos de red autorizados

| # | IP o rango CIDR | Notas |
|---|---|---|
| 1 | ______________________ | ______________________ |
| 2 | ______________________ | ______________________ |

### 2.3 Aplicaciones, APIs o sistemas específicos autorizados

| # | Nombre de la aplicación/API | URL o ruta de acceso | Ambiente (prod/staging/lab) |
|---|---|---|---|
| 1 | ______________________ | ______________________ | ______________________ |
| 2 | ______________________ | ______________________ | ______________________ |

### 2.4 Cuentas de redes sociales / marca a monitorear (módulo anti-suplantación, si aplica)

| # | Plataforma | Usuario / nombre de marca a vigilar |
|---|---|---|
| 1 | ______________________ | ______________________ |
| 2 | ______________________ | ______________________ |

☐ **No aplica** — el Cliente no contrata el módulo anti-suplantación en este acuerdo.

---

## 3. Tipos de prueba autorizados

Marque explícitamente cada tipo de prueba que el Cliente autoriza. Ningún tipo de prueba no marcado está autorizado.

### 3.1 Pruebas pasivas (bajo riesgo — no generan tráfico anómalo hacia el objetivo)

☐ Enumeración pasiva de subdominios (fuentes públicas: certificados SSL/TLS, DNS público, motores de búsqueda)
☐ Identificación de tecnologías expuestas (versiones de software visibles públicamente)
☐ Búsqueda de activos expuestos por error (paneles de administración, backups públicos, repositorios de código)
☐ Monitoreo de Certificate Transparency logs para detectar dominios similares recién registrados
☐ Búsqueda de perfiles en redes sociales que suplanten la marca

### 3.2 Pruebas activas (requieren esta autorización firmada, generan tráfico directo hacia el objetivo)

☐ Escaneo de vulnerabilidades conocidas (CVEs) sobre aplicaciones web
☐ Escaneo dinámico de aplicaciones (DAST)
☐ Escaneo de vulnerabilidades en dependencias/librerías de software
☐ Escaneo de configuraciones de infraestructura y contenedores
☐ Análisis estático de código fuente (SAST) — **solo si el Cliente entrega el código fuente**
☐ Pruebas de credenciales por defecto o configuraciones débiles conocidas
☐ Otro (especificar): ______________________

**Nota importante:** en ningún caso se autorizan pruebas de ingeniería social (phishing dirigido a empleados), ataques de denegación de servicio (DoS/DDoS), ni explotación activa de vulnerabilidades más allá de lo necesario para confirmar su existencia, salvo que se marque expresamente en la sección 3.3.

### 3.3 Autorizaciones adicionales explícitas (requieren marca individual, no incluidas por defecto)

☐ Explotación controlada de vulnerabilidades confirmadas, limitada a demostrar impacto sin alterar ni destruir datos
☐ Simulación de phishing dirigido a empleados (requiere anexo separado con lista de destinatarios autorizados)
☐ Pruebas fuera del horario laboral únicamente (ver ventana de tiempo, sección 4)

---

## 4. Ventana de tiempo autorizada

| Campo | Valor |
|---|---|
| Fecha y hora de inicio | ______________________ |
| Fecha y hora de finalización | ______________________ |
| Horario permitido dentro de ese rango (ej. solo 22:00–06:00) | ______________________ |
| Zona horaria | Colombia (UTC-5), salvo que se indique otra: ______________________ |

Ninguna prueba activa está autorizada fuera de esta ventana. Si el Prestador requiere extender el plazo, debe solicitarse una **adenda firmada** antes de la fecha de finalización — el vencimiento del plazo revoca automáticamente la autorización para pruebas activas, sin necesidad de aviso adicional.

---

## 5. Exclusiones explícitas — qué NO se toca bajo ninguna circunstancia

Independientemente de lo listado en la Sección 2, quedan **expresamente excluidos** de cualquier prueba activa:

- Sistemas de terceros que solo estén enlazados o integrados (pasarelas de pago, proveedores de correo, CDNs, APIs de terceros), salvo que ese tercero tenga su propia autorización firmada.
- Bases de datos de producción con datos reales de clientes — no se realiza extracción, modificación ni eliminación de datos bajo ninguna prueba.
- Cuentas de correo, WhatsApp Business o redes sociales del Cliente, salvo el monitoreo pasivo autorizado en la Sección 3.1.
- Cualquier activo no listado explícitamente en la Sección 2, aunque pertenezca al mismo grupo empresarial.
- Ataques de denegación de servicio (DoS/DDoS) de cualquier tipo.
- Ingeniería social no autorizada expresamente en la Sección 3.3.
- Cualquier prueba fuera de la ventana de tiempo de la Sección 4.
- Otras exclusiones específicas del Cliente: ______________________

---

## 6. Confidencialidad de los hallazgos

- Toda la información obtenida durante las pruebas (vulnerabilidades encontradas, datos de configuración, credenciales expuestas, cualquier dato sensible observado incidentalmente) es **estrictamente confidencial** y se trata como secreto empresarial del Cliente.
- El Prestador se compromete a no divulgar, publicar, compartir ni usar los hallazgos con fines distintos a la elaboración del reporte de seguridad para el Cliente, salvo:
  - Autorización expresa y por escrito del Cliente (ej. para caso de estudio, con anonimización).
  - Requerimiento de autoridad judicial o administrativa competente.
- Toda la evidencia recolectada (capturas, logs, reportes) se almacena de forma cifrada y se elimina o entrega al Cliente según se acuerde, dentro de un plazo máximo de ______________________ días tras la entrega del reporte final.
- Esta obligación de confidencialidad **sobrevive la terminación de este acuerdo** de forma indefinida respecto a la información clasificada como confidencial.
- El personal del Prestador que tenga acceso a los hallazgos está sujeto a esta misma obligación de confidencialidad.

---

## 7. Exención de responsabilidad por interrupciones de servicio

El Cliente reconoce y acepta que:

1. Las pruebas activas descritas en la Sección 3.2, por su naturaleza técnica, **pueden generar interrupciones, lentitud, errores o comportamiento inesperado** en los sistemas evaluados, incluso cuando el Prestador actúa con el máximo cuidado profesional y sigue las mejores prácticas del sector.
2. El Prestador **no garantiza cero interrupciones** durante la ventana de pruebas activas y no será responsable por pérdidas de disponibilidad, ingresos dejados de percibir, o daños indirectos o consecuenciales derivados de dichas interrupciones, siempre que haya actuado dentro del alcance y las condiciones aquí autorizadas.
3. Se recomienda al Cliente:
   - Realizar respaldos (backups) completos de los sistemas en alcance antes del inicio de la ventana de pruebas.
   - Tener disponible personal técnico de contacto durante la ventana autorizada, capaz de responder a incidentes.
   - Considerar realizar las pruebas activas en horarios de menor tráfico, si el modelo de negocio lo permite.
4. En caso de que una prueba activa cause una interrupción significativa e imprevista, el Prestador se compromete a **detener inmediatamente la prueba en curso** al ser notificado (ver protocolo de contacto de emergencia, Sección 8) y a colaborar de buena fe en el diagnóstico y mitigación del incidente.
5. Esta exención no aplica en casos de negligencia grave o dolo comprobado por parte del Prestador, ni exime al Prestador de actuar dentro del alcance, tipos de prueba y ventana de tiempo aquí autorizados — cualquier acción fuera de esos límites no está cubierta por este acuerdo y compromete la responsabilidad plena de quien la ejecute.

---

## 8. Protocolo de contacto de emergencia durante la ventana de pruebas

| Rol | Nombre | Teléfono | Correo | Disponibilidad |
|---|---|---|---|---|
| Contacto técnico del Prestador | ______________________ | ______________________ | ______________________ | ______________________ |
| Contacto técnico del Cliente | ______________________ | ______________________ | ______________________ | ______________________ |

Ante cualquier incidente durante la ventana autorizada, cualquiera de las partes puede solicitar la **detención inmediata** de las pruebas activas, comunicándose por el canal más rápido disponible (teléfono preferido sobre correo).

---

## 9. Marco legal aplicable

Este acuerdo se enmarca dentro de la legislación colombiana vigente, en particular:

- **Ley 1273 de 2009** ("De la protección de la información y de los datos"), que tipifica el **acceso abusivo a sistema informático** y delitos informáticos conexos, con penas de hasta 120 meses de prisión, **independientemente de la intención** de quien accede sin autorización. Este documento es precisamente el instrumento que excluye la conducta del Prestador de dicho tipo penal, al mediar consentimiento expreso, informado y por escrito del titular del sistema, dentro del alcance y condiciones aquí pactados.
- **Ley 2573 de 2026**, que refuerza las obligaciones de las empresas colombianas en materia de controles de validación digital y traslada la carga de la prueba hacia quien tenga mejores condiciones de demostrar el fraude o la suplantación. Las pruebas y hallazgos derivados de este acuerdo pueden servir al Cliente como parte de su evidencia de debida diligencia en materia de ciberseguridad frente a esta ley.
- Demás normativa aplicable en materia de protección de datos personales (Ley 1581 de 2012 y decretos reglamentarios), en cuanto a cualquier dato personal que pudiera observarse incidentalmente durante las pruebas.

Las partes se someten a la jurisdicción de los jueces y tribunales de la República de Colombia para cualquier controversia derivada de este acuerdo, salvo que se pacte expresamente un mecanismo alternativo de resolución de conflictos: ______________________

---

## 10. Vigencia y terminación anticipada

- Este acuerdo rige exclusivamente durante la ventana de tiempo definida en la Sección 4.
- Cualquiera de las partes puede **revocar la autorización de forma anticipada**, en cualquier momento, mediante comunicación escrita (correo electrónico es suficiente) al contacto de la otra parte. La revocación es efectiva de inmediato al momento de su recepción; toda prueba activa en curso debe detenerse tan pronto se reciba la notificación.
- La terminación anticipada no afecta las obligaciones de confidencialidad de la Sección 6, que continúan vigentes.

---

## 11. Firmas

Al firmar este documento, ambas partes confirman haber leído, entendido y aceptado la totalidad de las condiciones aquí descritas, incluyendo el alcance exacto, las exclusiones, la ventana de tiempo y las cláusulas de confidencialidad y exención de responsabilidad.

**Por el Prestador del servicio:**

Nombre: ______________________
Cargo: ______________________
Firma: ______________________
Fecha: ______________________

**Por el Cliente:**

Nombre: ______________________
Cargo: ______________________
Firma: ______________________
Fecha: ______________________

---

*Plantilla de uso interno. Se recomienda revisión por un abogado antes de su primer uso con un cliente real, para adaptarla a casos particulares o a normativa sectorial específica del Cliente.*
