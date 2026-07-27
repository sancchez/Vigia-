# Aviso de Privacidad y Política de Tratamiento de Datos Personales — Vigia

**Versión:** 1.0 (borrador)
**Fecha de emisión:** 2026-07-26
**Marco normativo:** Ley 1581 de 2012, Decreto 1074 de 2015 (Título 2, Capítulo 25) y demás normas concordantes de la República de Colombia.

---

## 1. Responsable del tratamiento

| Campo | Valor |
|---|---|
| Razón social | Camilo Sánchez |
| NIT | N/A — persona natural, sin RUT/NIT comercial registrado |
| Domicilio | `[DOMICILIO — dirección física en Colombia]` |
| Correo para habeas data | `camilosanch2008@gmail.com` |
| Teléfono | `[teléfono de contacto]` |
| Sitio web | `[sitio web]` |

El Responsable opera la plataforma **Vigia**, un servicio de gestión de superficie de ataque y detección de suplantación de marca.

Cuando el Responsable trata datos por cuenta de un Cliente (por ejemplo, los datos que el Cliente sube o que la plataforma recolecta sobre los activos del Cliente), actúa como **Encargado del tratamiento** y el Cliente conserva la calidad de Responsable frente a los titulares de esos datos.

---

## 2. Finalidades del tratamiento

Los datos personales se tratan exclusivamente para:

1. Crear y administrar cuentas de usuario, autenticar el acceso y segregar la información por cliente.
2. Prestar el servicio contratado: reconocimiento de superficie expuesta, escaneo de vulnerabilidades, verificación de hallazgos, detección de dominios *lookalike* y de perfiles públicos que puedan suplantar la marca del Cliente.
3. Generar reportes, notificaciones y alertas dirigidas al Cliente.
4. Verificar el control de dominio de los activos registrados, como control de seguridad y de legalidad del escaneo.
5. Atender solicitudes, reclamos y requerimientos de autoridad.
6. Cumplir obligaciones legales, contables y tributarias.
7. Facturación y gestión de pagos.
8. Mejorar la calidad del servicio mediante métricas agregadas, sin identificar personas.

**No** se tratan datos personales con fines publicitarios de terceros, ni se venden, ceden o comercializan bases de datos.

---

## 3. Datos tratados

| Categoría | Datos concretos | Origen | Base de legitimación |
|---|---|---|---|
| **Cuenta de usuario del Cliente** | Correo electrónico (almacenado en claro), contraseña (almacenada como hash `bcrypt`, no recuperable), identificador de tenant, fecha de registro | Suministrados por el titular al registrarse | Consentimiento y ejecución del contrato |
| **Activos del Cliente** | Dominios, subdominios, aplicaciones, direcciones IP, tokens de verificación | Suministrados por el Cliente y enumeración técnica | Ejecución del contrato |
| **Hallazgos de seguridad** | Vulnerabilidades, endpoints afectados, evidencia técnica, severidad, estado del ciclo de vida | Generados por las herramientas de escaneo sobre activos verificados del Cliente | Ejecución del contrato |
| **Código fuente enviado a revisión** | Archivos subidos por el Cliente, que pueden contener incidentalmente datos personales en comentarios, datos de prueba o cadenas de conexión | Suministrado por el Cliente con doble consentimiento explícito | Consentimiento expreso y específico |
| **Dominios sospechosos de suplantación** | Nombre de dominio, registros DNS públicos, certificados TLS publicados en registros de Certificate Transparency, captura de pantalla del sitio (`data/screenshots`), señales de infraestructura | Fuentes públicas | Interés legítimo en seguridad; datos de naturaleza pública (art. 10, lit. c, Ley 1581 de 2012) |
| **Perfiles públicos de redes sociales de terceros** | URL de perfiles públicos cuyo nombre de usuario coincide o se asemeja al de la marca vigilada (`findings.raw_json`, hallazgos de tipo `perfil_red_social`) | Búsqueda automatizada en fuentes públicas (Sherlock) | Datos de acceso público; interés legítimo en seguridad — ver Sección 6 |
| **Reportes DMARC** | Informes agregados de autenticación de correo, que pueden incluir direcciones IP de remitentes | Enviados por proveedores de correo al Cliente y cargados en la plataforma | Ejecución del contrato |
| **Registros técnicos** | Registros de ejecución de escaneos, trazas del pipeline, marcas de tiempo, resultados de herramientas | Generados por la plataforma | Interés legítimo en seguridad y trazabilidad |

**Datos que NO se tratan:**

- **Registros WHOIS.** La plataforma **no consulta ni almacena WHOIS ni RDAP** de dominios de terceros. Es una decisión de diseño explícita y deliberada: esos registros suelen contener nombre, dirección, teléfono y correo del titular del dominio —datos personales de una persona que puede no tener relación alguna con un fraude—. La correlación de dominios sospechosos se hace con señales de infraestructura (DNS, certificados públicos, contenido publicado), no con datos de registro.
- **Datos sensibles.** La plataforma no solicita ni trata datos sensibles en el sentido del artículo 5 de la Ley 1581 de 2012 (salud, orientación sexual, origen étnico, convicciones políticas o religiosas, datos biométricos). Si un Cliente los incorpora incidentalmente en código fuente subido, ese contenido se elimina al terminar la revisión (Sección 8).
- **Datos de menores de edad.** El servicio está dirigido exclusivamente a personas jurídicas y a personas naturales mayores de edad en ejercicio de actividad empresarial.

---

## 4. Derechos del titular (arts. 8 y 15, Ley 1581 de 2012)

Todo titular de datos personales tiene derecho a:

1. **Conocer** los datos personales suyos que estén bajo tratamiento, de forma gratuita.
2. **Actualizar y rectificar** los datos parciales, inexactos, incompletos, fraccionados o que induzcan a error.
3. **Solicitar prueba de la autorización** otorgada, salvo cuando la ley exceptúe ese requisito (art. 10).
4. **Ser informado**, previa solicitud, sobre el uso dado a sus datos.
5. **Presentar quejas** ante la Superintendencia de Industria y Comercio por infracciones a la ley, una vez agotado el trámite de consulta o reclamo ante el Responsable.
6. **Revocar la autorización** y/o **solicitar la supresión** del dato, cuando no exista un deber legal o contractual que lo impida y siempre que la Superintendencia no haya determinado que el Responsable incurrió en conductas contrarias a la ley.
7. **Acceder de forma gratuita** a sus datos personales que hayan sido objeto de tratamiento.

---

## 5. Canal y procedimiento para ejercer los derechos

**Canal único:** `camilosanch2008@gmail.com`

La solicitud debe indicar: nombre completo del titular, documento de identificación, descripción concreta de lo que se solicita, y datos de contacto para la respuesta.

**Plazos legales:**

| Trámite | Plazo |
|---|---|
| Consulta | Diez (10) días hábiles, prorrogables por cinco (5) días hábiles más, informando al titular. |
| Reclamo | Quince (15) días hábiles, prorrogables por ocho (8) días hábiles más, informando al titular. Si el reclamo está incompleto, se requiere al interesado dentro de los cinco (5) días siguientes; si no subsana en dos (2) meses, se entiende desistido. |

Agotado el trámite ante el Responsable, el titular puede acudir ante la **Superintendencia de Industria y Comercio**.

---

## 6. Tratamiento especial de datos de TERCEROS obtenidos de fuentes públicas

Esta sección es la más delicada del servicio y se declara con precisión.

### 6.1 Qué se recolecta y por qué

Para proteger la marca de un Cliente, la plataforma consulta fuentes de acceso público y puede registrar información asociada a personas que no son clientes:

- **Dominios similares a la marca del Cliente**, obtenidos por generación de variantes y por registros de **Certificate Transparency** (registros públicos y auditables de certificados TLS emitidos) y consultas **DNS** públicas.
- **Capturas de pantalla** del contenido publicado en esos dominios, como evidencia de clonación de la identidad visual del Cliente.
- **Perfiles públicos de redes sociales** cuyo nombre de usuario coincide o se asemeja al de la marca vigilada.

### 6.2 Base de legitimación

Se invoca el **artículo 10, literal c, de la Ley 1581 de 2012**, que exceptúa de autorización previa el tratamiento de **datos de naturaleza pública**, en concordancia con el interés legítimo en la seguridad digital y la prevención del fraude. Toda la información descrita proviene de registros o publicaciones de acceso público, sin credenciales, sin elusión de controles y sin acceso a áreas privadas.

Esta base de legitimación **no ampara**: el cruce de esa información con otras fuentes para construir un perfil de una persona, su uso para fines distintos a la protección de la marca del Cliente, ni su divulgación pública.

### 6.3 Advertencia expresa sobre falsos positivos — no se atribuye conducta a nadie

**Un hallazgo de tipo `perfil_red_social` NO es una acusación de suplantación.**

La detección se basa en la coincidencia de un nombre de usuario. Un perfil detectado puede pertenecer a:

- una persona natural homónima, sin relación alguna con la marca;
- una cuenta legítima del propio Cliente;
- una cuenta de un aficionado, un empleado o un distribuidor;
- una cuenta creada para suplantar.

Por eso la plataforma clasifica estos hallazgos con severidad informativa y los marca como **pendientes de revisión humana**, nunca como amenaza confirmada. La decisión sobre qué hacer con ellos corresponde al Cliente, titular de la marca, y debe tomarse con el estándar de la [Política de Divulgación Responsable](divulgacion-responsable.md).

### 6.4 Derechos de los terceros

Cualquier persona cuyos datos hayan sido registrados en la plataforma como resultado de estos procesos puede ejercer los derechos de la Sección 4 por el canal de la Sección 5, incluida la **supresión** del hallazgo asociado. El Responsable atenderá esas solicitudes en los mismos plazos legales y las trasladará al Cliente correspondiente cuando este tenga la calidad de Responsable del dato.

---

## 7. Encargados, transferencias y transmisiones internacionales

El servicio se apoya en terceros que pueden tratar datos por cuenta del Responsable, algunos ubicados fuera de Colombia:

| Tercero | Rol | Datos que recibe | Condición |
|---|---|---|---|
| **Anthropic** (Estados Unidos) | Encargado — modelo de lenguaje | Contexto de hallazgos y, cuando el Cliente lo autoriza expresamente, contenido de código fuente | Solo si está configurada la clave del proveedor |
| **OpenAI** (Estados Unidos) | Encargado — modelo de lenguaje | Igual que el anterior | Alternativa configurable al anterior |
| **Proveedor de infraestructura / hosting** | Encargado | Alojamiento de la base de datos y de la aplicación | `[identificar el proveedor y su jurisdicción]` |
| **Wompi** (Colombia) | Encargado — procesamiento de pagos | Datos de facturación | Si se habilita el módulo de pagos |
| **Supabase** | Encargado — base de datos alternativa | Datos de la plataforma | Solo si está configurada esa opción de despliegue |

**Sobre el envío de código fuente a modelos de lenguaje:** ocurre únicamente con **doble consentimiento explícito** del Cliente en cada solicitud —una autorización para analizar y otra, separada, para enviar el contenido del código a un tercero—. Si falta cualquiera de las dos, la solicitud se rechaza antes de que ningún archivo se escriba en disco.

**Transferencias internacionales:** el artículo 26 de la Ley 1581 de 2012 prohíbe la transferencia a países que no ofrezcan niveles adecuados de protección, salvo, entre otras causales, que medie **autorización expresa e inequívoca del titular** o que la transferencia sea necesaria para la ejecución de un contrato entre el titular y el Responsable. El doble consentimiento descrito arriba busca precisamente satisfacer esa exigencia respecto del contenido enviado a los proveedores de modelos de lenguaje. Se recomienda al Responsable formalizar cláusulas contractuales de tratamiento con cada encargado.

---

## 8. Medidas de seguridad — lo que hay y lo que no

El Responsable declara con exactitud las medidas implementadas, y advierte las que aún no lo están:

**Implementadas:**

- Contraseñas almacenadas con hash `bcrypt`; el Responsable no puede conocerlas ni recuperarlas.
- Autenticación por token con expiración.
- Segregación lógica de la información por cliente, aplicada en la capa de aplicación; las consultas a datos de otro cliente responden "no encontrado".
- Verificación efectiva de control de dominio antes de permitir escaneos, con rechazo en el borde de la API.
- Eliminación del código fuente subido, del disco del servidor, al terminar la revisión, tanto si tuvo éxito como si falló.
- Puerta de autorización determinista en el pipeline, sin intervención de modelos de lenguaje.
- **Supresión automática de las capturas de pantalla de dominios *lookalike*** a los 90 días, mediante una tarea programada que corre por defecto una vez al día. Es el dato de terceros más sensible que el sistema produce y es el primero en tener vencimiento automático.
- Límites de volumen y ritmo: cuota de uso por cliente en las operaciones de escaneo, espera mínima entre sondeos a un mismo sistema de terceros y tope de pruebas simultáneas.
- Los sondeos a sistemas de terceros viajan con un identificador de bot declarado que enlaza a la política y al canal de reclamo, de modo que el administrador de ese sistema pueda reconocerlo y bloquearlo.

**No implementadas a la fecha de esta versión (se declaran por transparencia):**

- **La base de datos no está cifrada en reposo.**
- La segregación por cliente **no** usa *row-level security* del motor de base de datos; depende del filtrado correcto en la capa de aplicación.
- El correo electrónico de los usuarios se almacena en claro.
- **La supresión automática por vencimiento cubre únicamente las capturas de pantalla.** Los hallazgos, las trazas de ejecución, los reportes DMARC y los datos de cuentas terminadas se suprimen a solicitud y por revisión manual, no por un proceso programado. Ver la [Política de Retención](politica-de-retencion.md), Sección 3.2.

Ninguna medida de seguridad elimina por completo el riesgo. El Responsable se compromete a actualizar esta sección cuando las medidas cambien.

---

## 9. Vigencia

Este aviso rige desde su fecha de emisión. Las bases de datos se conservarán durante la vigencia de la relación contractual y por los plazos de la [Política de Retención](politica-de-retencion.md) y los que impongan las obligaciones legales del Responsable.

Las modificaciones sustanciales se comunicarán al correo registrado de los usuarios con al menos quince (15) días de antelación.

---

## 10. Autorización

Al registrarse y usar la plataforma, el usuario autoriza de manera previa, expresa e informada el tratamiento de sus datos personales en los términos de este aviso. La autorización puede revocarse por el canal de la Sección 5, sin perjuicio de las obligaciones legales de conservación.

---

Documento de trabajo. Requiere revisión de abogado colombiano especializado en derecho digital antes de uso contractual formal.
