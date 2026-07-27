# Política de Divulgación Responsable — Vigia

**Versión:** 1.0 (borrador)
**Fecha de emisión:** 2026-07-26
**Responsable:** `[RAZÓN SOCIAL / NIT / DOMICILIO]`
**Contacto:** `[correo de contacto de seguridad]`

---

## 1. Alcance

Esta política regula qué hace Vigia cuando, al proteger la marca de un Cliente, detecta un hallazgo que involucra **infraestructura o cuentas de un tercero**: un dominio *lookalike*, un sitio que clona la identidad visual del Cliente, o un perfil público de red social con un nombre de usuario similar al de la marca.

No regula los hallazgos sobre los propios activos verificados del Cliente, que se rigen por los [Términos de Servicio](terminos-de-servicio.md).

---

## 2. Las cuatro reglas

### 2.1 No se publica

Un hallazgo sobre infraestructura de un tercero **no se publica** en ningún canal: ni en el sitio del Responsable, ni en redes sociales, ni en listas públicas de dominios maliciosos operadas por el Responsable, ni como material de mercadeo, ni en casos de estudio identificables.

La razón no es solo prudencia comercial. Un dominio detectado por similitud tipográfica puede pertenecer a una empresa legítima con un nombre parecido, a un distribuidor autorizado, a un registro defensivo del propio Cliente, o a alguien sin relación alguna con el asunto. **Publicar un señalamiento sobre un tercero con base en una coincidencia automatizada puede afectar su buen nombre y comprometer la responsabilidad de quien lo publica.**

### 2.2 No se ataca

El Responsable **no** ejecuta ninguna acción técnica contra la infraestructura de un tercero: no la escanea, no la sondea con cargas de ataque, no intenta acceder a sus paneles, no intenta tomar el dominio, no lo desfigura, no le envía tráfico dirigido a degradarlo.

La única interacción es la **consulta de información pública**: resolución DNS, lectura del certificado TLS presentado públicamente, y captura de pantalla de la página tal como la vería cualquier visitante. Nada de eso implica acceso a áreas privadas ni elusión de controles.

El límite es el mismo que impone la **Ley 1273 de 2009**: el acceso no autorizado a un sistema informático es delito en Colombia con independencia de la intención de quien lo comete. Que un dominio esté suplantando a un cliente **no** autoriza a nadie a atacarlo. No existe la legítima defensa informática por vía de acción directa.

### 2.3 Se entrega solo al Cliente titular de la marca

El hallazgo se entrega **exclusivamente** al Cliente cuya marca vigilada lo originó, dentro de su propio espacio en la plataforma y en sus reportes. No se comparte con otros clientes, ni con terceros, ni con proveedores, salvo:

- requerimiento de autoridad judicial o administrativa competente; o
- autorización expresa y escrita del Cliente.

El acceso está segregado por cliente en la plataforma.

### 2.4 La vía correcta es el reporte, no la acción directa

Cuando el Cliente decide actuar sobre un dominio *lookalike*, las vías legítimas son:

| Vía | Cuándo | A quién |
|---|---|---|
| **Reporte al registrador del dominio** | Suplantación de marca, uso indebido de nombre comercial | Registrador acreditado del dominio, por su canal de abuso |
| **Reporte al proveedor de alojamiento** | El sitio aloja contenido clonado o de *phishing* | Proveedor de hosting o CDN, por su canal de abuso |
| **Reporte a listas de navegación segura** | El sitio captura credenciales o datos de pago | Google Safe Browsing, navegadores, proveedores antifraude |
| **Denuncia penal** | Presunta suplantación de sitio web (art. 269G, Ley 1273 de 2009) u otro delito informático | Fiscalía General de la Nación — CAI Virtual de la Policía Nacional |
| **Reporte al equipo de respuesta a incidentes** | Incidente con impacto en el sector o en infraestructura crítica | CSIRT / COLCERT, según competencia |
| **Acción marcaria** | Uso indebido de marca registrada | Superintendencia de Industria y Comercio, o procedimiento UDRP ante el registrador, según el caso |

El Responsable puede **preparar la evidencia** para cualquiera de estas vías —capturas, registros DNS, certificados, cronología— y ponerla a disposición del Cliente. **No** actúa como representante del Cliente ante registradores ni autoridades, salvo acuerdo escrito específico, y no presta asesoría jurídica.

---

## 3. Antes de reportar: verificación humana obligatoria

Un hallazgo automatizado **no es una conclusión**. Antes de que el Cliente reporte un dominio o un perfil a un tercero, debe mediar revisión humana que descarte, como mínimo:

1. **Registro defensivo propio**: dominios que el propio Cliente o su agencia registraron para protección de marca.
2. **Homonimia legítima**: otra empresa o persona con un nombre parecido y actividad no relacionada.
3. **Socio autorizado**: distribuidores, franquicias, cuentas regionales o campañas del propio Cliente.
4. **Dominio parqueado sin contenido**: registrado y sin uso, lo que por sí solo no acredita intención de suplantar.
5. **Perfil de persona natural**: en los hallazgos de redes sociales, la coincidencia es de **nombre de usuario**, no de identidad. La plataforma los clasifica como informativos y pendientes de revisión humana, nunca como amenaza confirmada.

Un reporte infundado a un registrador o una denuncia sin fundamento puede exponer al Cliente a responsabilidad. La plataforma entrega señales y evidencia; la decisión y su fundamentación son del Cliente.

---

## 4. Cuando el hallazgo afecta a un tercero identificable

Si una persona identificable resulta señalada por un hallazgo:

1. Esa persona conserva sus derechos de habeas data sobre la información registrada en la plataforma, y puede ejercerlos por el canal del [Aviso de Privacidad](aviso-de-privacidad.md).
2. El Responsable atenderá la solicitud en los plazos legales y notificará al Cliente correspondiente.
3. El Responsable **no** consulta ni almacena registros WHOIS o RDAP de dominios de terceros, precisamente para no acumular datos personales del titular de un dominio que puede no tener relación con ningún fraude.

---

## 5. Vulnerabilidades en la propia plataforma Vigia

Quien identifique una vulnerabilidad en Vigia —el servicio o el código publicado— puede reportarla a `[correo de contacto de seguridad]`. El Responsable se compromete a:

1. Acusar recibo dentro de `[plazo sugerido: 3 días hábiles]`.
2. Evaluar y comunicar un diagnóstico dentro de `[plazo sugerido: 15 días hábiles]`.
3. No emprender acciones legales contra quien reporte de buena fe, siempre que se haya limitado a lo estrictamente necesario para demostrar la falla, no haya accedido a datos de terceros, no haya degradado el servicio y conceda un plazo razonable antes de cualquier divulgación pública.

Esta salvaguarda es un compromiso del Responsable y no altera el régimen penal aplicable a quien exceda esos límites.

---

## 6. Compromiso del Responsable

El Responsable no usará los hallazgos de un Cliente para contactar comercialmente al titular de un dominio detectado, no los venderá ni los cederá, y no construirá con ellos productos de datos dirigidos a terceros distintos del Cliente titular de la marca.

---

Documento de trabajo. Requiere revisión de abogado colombiano especializado en derecho digital antes de uso contractual formal.
