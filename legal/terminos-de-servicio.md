# Términos de Servicio — Vigia

**Versión:** 1.0 (borrador)
**Fecha de emisión:** 2026-07-26
**Titular del servicio:** `[RAZÓN SOCIAL / NIT / DOMICILIO]`
**Contacto:** `[correo de contacto comercial y de soporte]`

---

## 0. Naturaleza de este documento

Estos Términos regulan el acceso y uso de **Vigia**, una plataforma de gestión de superficie de ataque y detección de suplantación de marca, cuando se consume como servicio operado por el Titular.

El **código fuente** publicado en el repositorio público de Vigia se rige por la licencia Apache 2.0 (archivo `LICENSE`), no por estos Términos. Estos Términos aplican a quien usa el servicio operado por el Titular; la licencia de código aplica a quien descarga, modifica o redistribuye el software.

Al crear una cuenta o usar el servicio, el Cliente acepta estos Términos, la [Política de Uso Aceptable](politica-uso-aceptable.md), el [Aviso de Privacidad](aviso-de-privacidad.md), la [Política de Retención](politica-de-retencion.md) y la [Política de Divulgación Responsable](divulgacion-responsable.md). Todos forman un solo cuerpo contractual.

---

## 1. Definiciones

| Término | Significado |
|---|---|
| **Titular** | Quien opera el servicio Vigia, identificado arriba. |
| **Cliente** | Persona natural o jurídica que contrata o usa el servicio. |
| **Activo** | Dominio, aplicación o dirección IP registrada por el Cliente en la plataforma. |
| **Activo verificado** | Activo respecto del cual el Cliente demostró control mediante el mecanismo técnico de la Sección 4. |
| **Escaneo pasivo** | Recolección de información sin envío de tráfico de ataque hacia el objetivo. |
| **Escaneo activo** | Envío de peticiones diseñadas para provocar respuestas anómalas (inyección, XSS, etc.) contra el objetivo. |
| **Hallazgo** | Vulnerabilidad, exposición o señal de suplantación detectada y registrada por la plataforma. |

---

## 2. Objeto y descripción del servicio

Vigia orquesta herramientas de seguridad de terceros (Nuclei, OWASP ZAP, Amass, Subfinder, dnstwist, Sherlock, Semgrep, entre otras) y les añade una capa de verificación, priorización y redacción de reportes. Concretamente, el servicio puede:

1. Enumerar la superficie expuesta de los Activos del Cliente (subdominios, servicios, tecnologías).
2. Ejecutar escaneo pasivo de vulnerabilidades sobre Activos verificados.
3. Ejecutar escaneo **activo** sobre Activos verificados, únicamente bajo la condición de consentimiento diferenciado de la Sección 5.
4. Detectar dominios *lookalike* (variantes tipográficas y homográficas de la marca del Cliente) y perfiles públicos de redes sociales que usen un nombre de usuario similar al de la marca.
5. Generar reportes de hallazgos, priorización y recomendaciones de remediación.
6. Opcionalmente, y solo con doble consentimiento explícito, analizar código fuente que el Cliente suba, mediante modelos de lenguaje de terceros (Sección 6).

El servicio **no** presta asesoría jurídica, no realiza *takedowns* por cuenta propia, no representa al Cliente ante autoridades y no sustituye una auditoría de seguridad realizada por profesionales certificados.

---

## 3. Cuentas y multi-tenencia

1. El Cliente es responsable de la confidencialidad de sus credenciales y de toda actividad realizada bajo su cuenta.
2. Las contraseñas se almacenan con hash `bcrypt`; el Titular no puede recuperarlas ni conocerlas.
3. Los datos de cada Cliente están segregados lógicamente por identificador de tenant a nivel de la capa de aplicación. **El Cliente reconoce expresamente** que, a la fecha de esta versión, esa segregación se implementa mediante filtrado en las consultas de la aplicación y **no** mediante *row-level security* del motor de base de datos, y que la base de datos **no está cifrada en reposo**. El Titular se compromete a informar cuando esto cambie.

---

## 4. Autorización de escaneo — condición no negociable

**Regla de oro: el servicio no escanea ningún activo cuyo control el Cliente no haya demostrado.**

1. Antes de que un Activo de tipo dominio o aplicación pueda ser escaneado, el Cliente debe demostrar su control publicando un token único emitido por la plataforma, en una de estas dos ubicaciones:
   - un registro DNS TXT en `_vigia-challenge.<dominio>`, o
   - un archivo en `https://<dominio>/.well-known/vigia-verification.txt`.
2. La plataforma comprueba esa publicación de forma efectiva antes de marcar el Activo como verificado. Los endpoints de escaneo pasivo, escaneo activo y anti-suplantación rechazan con error `403` cualquier objetivo que no corresponda a un Activo verificado del Cliente.
3. **Excepción de laboratorio:** se exime de verificación a `localhost` y a direcciones de rangos privados, *loopback* y *link-local*, porque un objetivo así solo es alcanzable desde la misma máquina o red que ejecuta la plataforma y no puede corresponder al sistema de un tercero.
4. La verificación de control **es un requisito técnico, no un sustituto de la autorización jurídica**. Cuando el Cliente encarga pruebas sobre activos de un tercero (por ejemplo, una agencia que administra el sitio de su propio cliente), debe existir además una autorización escrita del titular real del sistema. La plantilla está en [`legal/autorizacion-pruebas-seguridad.md`](autorizacion-pruebas-seguridad.md).
5. **Limitación conocida y declarada:** la plataforma **no almacena hoy el documento de autorización firmado**. El indicador `autorizacion_firmada` que acompaña cada solicitud de escaneo es una **declaración del propio Cliente** hecha en el momento de la petición. Conservar el documento firmado y poder exhibirlo es responsabilidad del Cliente. El esquema de datos prevé campos para registrar esa autorización, pero ninguna función del servicio los escribe todavía.

---

## 5. Escaneo activo — consentimiento diferenciado

1. El escaneo activo envía peticiones de ataque reales (inyección SQL, *cross-site scripting*, entre otras) contra el objetivo. Es materialmente distinto del escaneo pasivo y requiere consentimiento propio y separado.
2. El Cliente reconoce y acepta que el escaneo activo **puede causar lentitud, errores, comportamiento inesperado o interrupción** del sistema evaluado, incluso ejecutándose conforme a las mejores prácticas. El Titular no garantiza cero interrupciones.
3. Se recomienda al Cliente: respaldar los sistemas en alcance antes de la ventana de pruebas, mantener personal técnico disponible durante ella y programarla en horarios de menor tráfico.
4. El Cliente puede solicitar la detención inmediata de cualquier prueba activa en curso por el canal más rápido disponible; el Titular se compromete a detenerla al recibir la notificación y a colaborar de buena fe en el diagnóstico.
5. El servicio **no ejecuta** ataques de denegación de servicio, pruebas de carga, ingeniería social ni ninguna acción contra sistemas de terceros. Ver la [Política de Uso Aceptable](politica-uso-aceptable.md).

---

## 6. Análisis de código fuente por modelos de lenguaje de terceros

1. La revisión de código con inteligencia artificial es **opcional** y exige **doble consentimiento explícito** en cada solicitud: la autorización general de análisis y, adicionalmente, la autorización específica de enviar el contenido del código a un modelo de lenguaje de terceros. Si falta cualquiera de las dos, la solicitud se rechaza con error `403` antes de que archivo alguno se escriba en disco.
2. Los proveedores de modelos de lenguaje (Anthropic y/u OpenAI, según la configuración del despliegue) actúan como **encargados del tratamiento** y reciben el contenido del código enviado. Ver el [Aviso de Privacidad](aviso-de-privacidad.md), Sección de transferencias internacionales.
3. El código fuente subido se elimina del disco del servidor al terminar la revisión, con éxito o con error. Solo se conservan los hallazgos derivados del análisis, no el código.
4. El Cliente declara tener los derechos necesarios sobre el código que sube y garantiza que su envío a un tercero no infringe obligaciones de confidencialidad frente a terceros.

---

## 7. Ausencia de garantía de detección total

1. Ningún sistema de seguridad detecta todas las vulnerabilidades ni todas las suplantaciones. El Titular **no garantiza** que el servicio identifique la totalidad de los riesgos que afectan al Cliente.
2. Los resultados dependen de herramientas de terceros, de fuentes públicas de datos y de modelos de lenguaje, todos ellos con falsos positivos y falsos negativos. La plataforma incorpora verificación de hallazgos para reducir falsos positivos, pero no los elimina.
3. La ausencia de hallazgos **no constituye** una certificación de seguridad, ni un certificado de conformidad, ni evidencia de que el Cliente esté libre de riesgos.
4. Los reportes de cumplimiento que genera la plataforma son **orientativos**. No son una certificación ISO 27001 ni un dictamen de cumplimiento de la Ley 2573 de 2026.

---

## 8. Obligaciones legales propias del Cliente

El servicio es una herramienta de apoyo. **No sustituye ninguna obligación legal del Cliente.** En particular, frente a la **Ley 2573 de 2026** —cuya aplicación plena se proyecta hacia noviembre de 2026, según el régimen de transición aplicable— y demás normativa vigente, siguen siendo responsabilidad exclusiva del Cliente, entre otras:

- Adoptar y mantener controles de validación digital y de seguridad razonables para su actividad.
- Atender los reclamos de sus propios usuarios por fraude o suplantación, y congelar cobros cuando la norma lo exija.
- Reportar a centrales de riesgo lo que la norma le obligue a reportar, en los plazos que la norma fije.
- Denunciar penalmente los hechos que constituyan delito y colaborar con las autoridades.
- Conservar la evidencia necesaria para demostrar su diligencia, dado que la norma tiende a trasladar la carga de la prueba hacia quien esté en mejores condiciones de demostrar el fraude o la suplantación.

Los hallazgos y reportes generados por Vigia pueden servir como **parte** de la evidencia de diligencia del Cliente. No la constituyen por sí solos.

**Vigencia normativa:** las referencias normativas de este documento reflejan el estado de la legislación a la fecha de emisión. Los cambios normativos posteriores no modifican automáticamente estos Términos; el Titular los actualizará conforme a la Sección 13.

---

## 9. Confidencialidad

1. Toda la información que el Titular obtenga sobre los sistemas del Cliente —vulnerabilidades, configuraciones, credenciales expuestas, código fuente y cualquier dato sensible observado incidentalmente— es **estrictamente confidencial** y se trata como secreto empresarial del Cliente.
2. El Titular no divulgará, publicará ni compartirá esa información, salvo: (i) autorización expresa y escrita del Cliente, incluida la publicación anonimizada de casos de estudio; (ii) requerimiento de autoridad judicial o administrativa competente; o (iii) lo estrictamente necesario para prestar el servicio a través de encargados declarados en el Aviso de Privacidad.
3. Esta obligación **sobrevive indefinidamente** a la terminación del contrato.
4. La información sobre dominios *lookalike* e infraestructura de terceros detectada para el Cliente se rige adicionalmente por la [Política de Divulgación Responsable](divulgacion-responsable.md).

---

## 10. Limitación de responsabilidad

1. El servicio se presta **"tal cual" y "según disponibilidad"**, sin garantías implícitas de comerciabilidad o idoneidad para un fin particular, en la máxima medida permitida por la ley colombiana.
2. El Titular no responde por daños indirectos, lucro cesante, pérdida de datos, pérdida de oportunidad de negocio, ni daños consecuenciales derivados del uso o de la imposibilidad de uso del servicio.
3. La responsabilidad total y acumulada del Titular frente al Cliente, por cualquier causa y bajo cualquier teoría jurídica, se limita al **monto efectivamente pagado por el Cliente durante los doce (12) meses anteriores** al hecho generador.
4. El Titular no responde por interrupciones de servicio en los sistemas del Cliente derivadas de escaneos activos ejecutados dentro del alcance y condiciones autorizados por el propio Cliente.
5. El Titular no responde por daños derivados de la conducta de terceros (suplantadores, atacantes, registradores, proveedores de hosting), ni por decisiones de negocio que el Cliente tome con base en los reportes.
6. **Ninguna de estas limitaciones aplica** en caso de dolo o culpa grave del Titular, ni respecto de aquello que la ley colombiana declare irrenunciable.

---

## 11. Indemnidad

El Cliente mantendrá indemne al Titular frente a reclamaciones, sanciones, multas, costas y honorarios de defensa que provengan de:

1. El uso del servicio contra activos sobre los que el Cliente no tenía propiedad ni autorización escrita, en violación de la [Política de Uso Aceptable](politica-uso-aceptable.md).
2. La declaración falsa o inexacta del indicador de autorización firmada (Sección 4.5).
3. El envío a análisis de código fuente sobre el cual el Cliente no tenía derechos suficientes.
4. El registro de marcas vigiladas sobre las que el Cliente no tiene titularidad ni legitimación.
5. Cualquier incumplimiento por parte del Cliente de la normativa aplicable, incluidas las Leyes 1273 de 2009, 1581 de 2012 y 2573 de 2026.

---

## 12. Suspensión y terminación

1. El Titular puede **suspender de inmediato y sin preaviso** la cuenta del Cliente ante indicios razonables de violación de la Política de Uso Aceptable, en particular el intento de escanear activos ajenos o de eludir la verificación de control de dominio.
2. Cualquiera de las partes puede terminar el contrato mediante comunicación escrita, con treinta (30) días de preaviso, sin perjuicio de las obligaciones ya causadas.
3. Terminado el contrato, la supresión de datos se rige por la [Política de Retención](politica-de-retencion.md).
4. Sobreviven a la terminación: las Secciones 9 (confidencialidad), 10 (limitación de responsabilidad), 11 (indemnidad) y 14 (ley aplicable).

---

## 13. Modificaciones

El Titular puede modificar estos Términos. Los cambios sustanciales se comunicarán al correo registrado del Cliente con al menos **quince (15) días** de antelación a su entrada en vigor. El uso del servicio con posterioridad a esa fecha implica aceptación. Si el Cliente no acepta, puede terminar el contrato sin penalidad antes de la entrada en vigor.

---

## 14. Ley aplicable y jurisdicción

Estos Términos se rigen por la ley de la República de Colombia. Las partes se someten a la jurisdicción de los jueces y tribunales colombianos, salvo pacto expreso de un mecanismo alternativo de resolución de controversias.

---

## 15. Documentos relacionados

- `LICENSE` — licencia Apache 2.0 del código fuente.
- [`legal/politica-uso-aceptable.md`](politica-uso-aceptable.md)
- [`legal/aviso-de-privacidad.md`](aviso-de-privacidad.md)
- [`legal/politica-de-retencion.md`](politica-de-retencion.md)
- [`legal/divulgacion-responsable.md`](divulgacion-responsable.md)
- [`legal/autorizacion-pruebas-seguridad.md`](autorizacion-pruebas-seguridad.md) — plantilla de autorización de pruebas (Rules of Engagement).

---

Documento de trabajo. Requiere revisión de abogado colombiano especializado en derecho digital antes de uso contractual formal.
