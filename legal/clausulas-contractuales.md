# Cláusulas Contractuales — Contrato de Prestación de Servicios de Seguridad Informática (Vigia)

> **Uso previsto.** Estas nueve cláusulas son el núcleo jurídico del contrato de
> prestación de servicios entre **Vigia** (en adelante, "el Prestador") y su
> cliente (en adelante, "el Cliente"). Están redactadas para insertarse en un
> contrato marco, y se complementan con:
>
> - [`legal/autorizacion-pruebas-seguridad.md`](./autorizacion-pruebas-seguridad.md) — Rules of Engagement (RoE) / Anexo de Alcance, que se firma **por cada** ciclo de pruebas.
> - [`docs/cumplimiento-del-proveedor.md`](../docs/cumplimiento-del-proveedor.md) — dossier de diligencia debida del Prestador, con el estado real verificado de cada control.
> - [`legal/terminos-de-servicio.md`](./terminos-de-servicio.md), [`legal/politica-uso-aceptable.md`](./politica-uso-aceptable.md), [`legal/aviso-de-privacidad.md`](./aviso-de-privacidad.md), [`legal/politica-de-retencion.md`](./politica-de-retencion.md), [`legal/divulgacion-responsable.md`](./divulgacion-responsable.md) *(en preparación)*.
>
> Los campos entre `[corchetes]` son los únicos que quedan por negociar por
> contrato. Las notas al margen (marcadas **Nota**) explican el porqué de cada
> decisión de redacción y **no forman parte del texto contractual**.

---

## 3.1 Alcance y autorización previa

**3.1.1.** El Prestador únicamente ejecutará pruebas de seguridad sobre los
activos digitales que el Cliente haya enumerado de forma expresa, individual e
inequívoca en el **Anexo de Alcance** (Rules of Engagement), suscrito por un
representante del Cliente con facultad legal para autorizar dichas pruebas sobre
esos activos.

**3.1.2.** **Todo activo, dominio, subdominio, dirección IP, aplicación,
repositorio de código o imagen de contenedor que no esté expresamente listado en
el Anexo de Alcance queda excluido del servicio** y no será objeto de prueba
alguna, aun cuando resulte técnicamente alcanzable desde un activo sí
autorizado, y aun cuando el Cliente sea su titular.

**3.1.3.** El Anexo de Alcance especificará, como mínimo: (i) la lista
enumerada de activos; (ii) la ventana horaria autorizada; (iii) los tipos de
prueba permitidos, distinguiendo expresamente entre reconocimiento pasivo y
escaneo activo; (iv) el contacto técnico de emergencia del Cliente, disponible
durante toda la ventana; y (v) las exclusiones explícitas y los sistemas
críticos a los que se refiere la Cláusula 3.3.

**3.1.4.** El Cliente declara ser titular de los activos listados o contar con
autorización escrita de su titular. La Plataforma del Prestador exige,
adicionalmente y por medios técnicos, que el Cliente **demuestre control** de
cada dominio antes de permitir su escaneo, mediante publicación de un token
único en un registro DNS TXT o en una ruta `.well-known` del propio dominio. La
sola declaración de titularidad no habilita el escaneo.

**3.1.5.** Cualquier ampliación del alcance requiere un Anexo de Alcance nuevo o
un otrosí escrito, firmado antes de la ejecución. **No se admite ampliación
verbal, por correo sin firma ni por conducta concluyente.**

> **Nota (3.1).** La regla del 3.1.2 —lo no listado queda fuera— es la que
> convierte las pruebas en lícitas frente a la Ley 1273 de 2009. Está además
> respaldada técnicamente y no solo contractualmente: ver
> `docs/cumplimiento-del-proveedor.md` §0.6.

---

## 3.2 No garantía de detección total

**3.2.1.** El Cliente reconoce y acepta que **ninguna herramienta, metodología o
profesional de seguridad informática puede detectar la totalidad de las
vulnerabilidades existentes** en un sistema. El servicio produce, por su
naturaleza, resultados no exhaustivos.

**3.2.2.** La obligación del Prestador es una **obligación de medio y no de
resultado**: se obliga a ejecutar las pruebas con la diligencia, la metodología
y el estado del arte propios de un profesional de la seguridad informática, no a
garantizar la detección de toda vulnerabilidad ni la ausencia de incidentes
futuros.

**3.2.3.** En particular, el Prestador **no garantiza**: (i) la detección de
vulnerabilidades desconocidas públicamente al momento de la prueba (*zero-day*);
(ii) la detección de vulnerabilidades en componentes fuera del alcance
autorizado; (iii) la ausencia de falsos negativos; ni (iv) que la remediación de
los hallazgos reportados haga al Cliente inmune a un ataque.

**3.2.4.** Los informes reflejan el estado de los activos **en la fecha y hora
de la prueba**. Cualquier cambio posterior en la infraestructura, el código o
las dependencias del Cliente puede introducir vulnerabilidades no cubiertas por
un informe previo.

**3.2.5.** El servicio puede producir **falsos positivos**. La validación final
de cada hallazgo en el contexto operativo del Cliente, y la decisión sobre su
remediación, corresponden al Cliente.

---

## 3.3 Prohibición de explotación y no afectación de sistemas críticos

**3.3.1.** Salvo lo previsto en 3.3.3, el Prestador **se abstendrá de explotar**
las vulnerabilidades detectadas. Las pruebas se limitan a la identificación y a
la verificación no intrusiva, sin extracción de datos, sin escalamiento de
privilegios, sin persistencia y sin movimiento lateral.

**3.3.2.** El Prestador **no ejecutará** pruebas de denegación de servicio,
pruebas de carga o estrés, ataques de fuerza bruta contra credenciales de
usuarios reales, ni ingeniería social sobre el personal del Cliente, salvo
autorización expresa, específica y por escrito en el Anexo de Alcance.

**3.3.3. Explotación controlada.** El Prestador podrá realizar explotación
controlada de una vulnerabilidad **únicamente** cuando concurran todas las
condiciones siguientes: (i) autorización **expresa y específica** para ese tipo
de explotación, otorgada por escrito en el Anexo de Alcance —una autorización
genérica de "pruebas de seguridad" **no** es suficiente—; (ii) el objetivo se
limite a confirmar la explotabilidad real, evitando falsos positivos; (iii) no
se extraigan, alteren ni destruyan datos del Cliente ni de terceros más allá de
lo estrictamente necesario para esa confirmación; y (iv) toda acción quede
registrada en la bitácora de la Cláusula 3.4.4.

**3.3.4. Sistemas críticos.** El Anexo de Alcance identificará los sistemas
críticos del Cliente. Sobre ellos el Prestador aplicará configuraciones
conservadoras (concurrencia y agresividad reducidas) y **no** ejecutará
explotación controlada, aun mediando la autorización de 3.3.3, salvo mención
nominal de ese sistema específico.

**3.3.5. Suspensión inmediata ante riesgo.** Si durante la ejecución el
Prestador detecta o razonablemente presume que una prueba está degradando la
disponibilidad, la integridad o el desempeño de un sistema del Cliente,
**suspenderá inmediatamente esa prueba**, sin esperar instrucción del Cliente, y
activará el protocolo de la Cláusula 3.4. La suspensión no constituye
incumplimiento del Prestador ni da lugar a descuento en la contraprestación.

**3.3.6.** El Cliente podrá ordenar en cualquier momento, y sin expresar causa,
la suspensión o terminación de las pruebas en curso, por el canal previsto en
3.4.2.

---

## 3.4 Protocolo de incidentes accidentales

**3.4.1.** Se entiende por **incidente accidental** toda degradación,
interrupción, alteración de datos o comportamiento anómalo de un sistema del
Cliente que sea causado —o razonablemente atribuible— a la ejecución de las
pruebas contratadas.

**3.4.2. Canal de comunicación acordado.** Salvo que las Partes designen otro
canal en el Anexo de Alcance, el canal de comunicación para incidentes será, en
este orden y de forma **acumulativa** (no alternativa):

1. **Llamada telefónica** al contacto técnico de emergencia designado por el
   Cliente en el Anexo de Alcance, disponible durante toda la ventana de
   pruebas; **y**
2. **Correo electrónico** de constancia a la dirección de incidentes designada
   por el Cliente, con copia a los representantes contractuales de ambas Partes,
   enviado dentro de la hora siguiente a la llamada, con la información del
   numeral 3.4.3.

El correo electrónico **no sustituye la llamada**: sirve como constancia
escrita, no como notificación primaria. Ambos datos de contacto —número
telefónico y dirección de correo— se consignan en el Anexo de Alcance y son
**configurables por contrato**; su ausencia o desactualización no libera al
Cliente de la carga de mantenerlos vigentes durante la ventana de pruebas.

**3.4.3. Contenido y plazo de la notificación.** El Prestador notificará el
incidente **dentro de la hora siguiente** a su detección, informando: (i) hora
de inicio y de detección; (ii) activo afectado; (iii) prueba y herramienta
involucradas; (iv) medidas de contención ya adoptadas, incluida la suspensión de
3.3.5; y (v) recomendación inmediata al Cliente.

**3.4.4. Preservación de evidencia.** El Prestador conservará íntegros y sin
alteración los registros de trazabilidad de la corrida involucrada —incluidos
alcance, marcas de tiempo y resultado de cada etapa— y los pondrá a disposición
del Cliente. Dichos registros son generados automáticamente por la Plataforma
para toda ejecución.

**3.4.5. Informe post-incidente.** Dentro de los **cinco (5) días hábiles**
siguientes, el Prestador entregará un informe escrito con la causa raíz, la
cronología, el impacto observado y las medidas correctivas adoptadas para evitar
su repetición.

**3.4.6.** La notificación de un incidente conforme a esta cláusula **no
constituye reconocimiento de responsabilidad** por parte del Prestador. Las
Partes acuerdan que la transparencia inmediata prevalece sobre la discusión de
responsabilidad, que se resolverá conforme a la Cláusula 3.5.

> **Nota (3.4.2).** El canal se propone acumulativo —teléfono primero, correo
> como constancia— porque un incidente de disponibilidad es, por definición,
> urgente: el correo solo garantiza registro, no atención. Es un canal concreto
> y razonable, y queda expresamente marcado como configurable por contrato para
> clientes que operen un SOC con su propio sistema de tickets o un canal
> dedicado de mensajería.

---

## 3.5 Limitación de responsabilidad

**3.5.1.** La responsabilidad total y acumulada del Prestador frente al Cliente,
por cualquier causa y bajo cualquier teoría jurídica —contractual,
extracontractual o de otra índole— derivada de o relacionada con este contrato,
**no excederá el valor total efectivamente pagado por el Cliente al Prestador
durante los seis (6) meses inmediatamente anteriores** al hecho generador del
daño.

**3.5.2.** El Prestador **no será responsable, en ningún caso**, por daños
indirectos, lucro cesante, pérdida o corrupción de datos, interrupción del
negocio, pérdida de oportunidad comercial, daño reputacional, ni por sanciones
administrativas o multas impuestas al Cliente por autoridad competente, aun
cuando el Prestador hubiera sido advertido de la posibilidad de tales daños.

**3.5.3.** Las limitaciones de 3.5.1 y 3.5.2 **no aplican** en casos de **dolo o
culpa grave** del Prestador, ni en aquellos supuestos en que la ley colombiana
prohíba expresamente la limitación de responsabilidad. Se entiende por culpa
grave, entre otras, la ejecución deliberada de pruebas sobre activos no
autorizados o la omisión deliberada del protocolo de la Cláusula 3.4.

**3.5.4.** El Prestador no responde por daños derivados de: (i) información
inexacta o incompleta suministrada por el Cliente en el Anexo de Alcance; (ii)
la decisión del Cliente de no remediar un hallazgo reportado, o de remediarlo
fuera de plazo; (iii) vulnerabilidades introducidas por cambios del Cliente
posteriores a la prueba; ni (iv) actos de terceros ajenos al Prestador.

**3.5.5.** El Cliente reconoce que la contraprestación pactada se calculó
considerando esta distribución de riesgos, y que sin ella el precio del servicio
sería sustancialmente distinto.

> **Nota al margen (3.5.1) — por qué seis (6) meses y no tres.** Un tope de tres
> meses es habitual en contratos de software de bajo ticket, pero frente a un
> cliente corporativo —y con mayor razón frente a un cliente regulado cuyo
> departamento jurídico revisará esta cláusula— un tope de tres meses tiende a
> leerse como una limitación desproporcionada y es más probable que sea
> rechazado en negociación o incluso cuestionado judicialmente por afectar la
> esencia de la obligación. Seis meses es **más defendible**: refleja un
> compromiso económico real y proporcional al servicio prestado, señala
> confianza del Prestador en su propia diligencia, y **sigue siendo un tope
> acotado y previsible** que protege al Prestador de una exposición ilimitada.
> El equilibrio adicional se logra vía seguro de responsabilidad civil
> profesional (E&O / cyber liability), que es la vía correcta para absorber el
> riesgo residual — no un tope artificialmente bajo. Ver
> `docs/cumplimiento-del-proveedor.md` §4, punto 3.

---

## 3.6 El servicio no sustituye las obligaciones legales del Cliente

**3.6.1.** El Cliente reconoce que **el Prestador no es sujeto obligado** de la
**Ley 2573 de 2026**. Dicha ley impone obligaciones a operadores de
telecomunicaciones, entidades financieras y crediticias, y comercios con
competencia crediticia. El Prestador actúa exclusivamente como **proveedor
tecnológico y tercero** de dichos sujetos obligados.

**3.6.2.** En consecuencia, **la contratación del servicio no traslada al
Prestador ninguna obligación legal del Cliente**, ni exime al Cliente de
cumplirlas por sí mismo. En particular, y a título enunciativo, **corresponde
exclusivamente al Cliente**:

1. Congelar, suspender o revertir cobros, obligaciones o reportes derivados de
   una suplantación de identidad.
2. Marcar a una persona como **"Víctima de Falsedad Personal"** ante centrales
   de riesgo, operadores de información o cualquier registro crediticio.
3. Atender, tramitar y resolver los reportes de presunta suplantación
   presentados por sus usuarios, dentro de los plazos legales.
4. Adoptar y mantener los mecanismos de validación de identidad que la ley le
   exija, y conservar la evidencia de su aplicación.
5. Reportar a las autoridades competentes los incidentes que la ley le obligue a
   reportar.

**3.6.3.** El servicio del Prestador consiste en **detectar, documentar y
priorizar** vulnerabilidades técnicas y señales de suplantación de marca, y en
entregar informes que el Cliente **puede utilizar como insumo probatorio** de su
propia diligencia. Ese insumo **no equivale al cumplimiento**: la valoración de
suficiencia corresponde a la autoridad competente.

**3.6.4.** El Prestador **no emite certificaciones de cumplimiento** de la Ley
2573 de 2026 ni de ISO/IEC 27001, ni está certificado en dicha norma. Todo
mapeo normativo contenido en sus informes constituye una **interpretación
razonada de carácter técnico**, sujeta a validación por asesor jurídico del
Cliente y, en el caso de ISO/IEC 27001, por auditor certificado. Los informes
generados por la Plataforma incluyen esta advertencia de forma automática y en
todos los casos.

**3.6.5.** El Cliente informará al Prestador, en el Anexo de Alcance, si es o no
sujeto obligado de la Ley 2573 de 2026 y bajo qué categoría, con el único fin de
ajustar el alcance técnico del servicio. La clasificación es responsabilidad del
Cliente; el Prestador no la determina ni la verifica.

---

## 3.7 Confidencialidad

**3.7.1.** Toda información a la que el Prestador acceda en ejecución del
contrato —arquitectura, código fuente, credenciales de prueba, datos personales,
y de manera destacada **las vulnerabilidades detectadas y no remediadas**— tiene
carácter de **información confidencial de alto riesgo**.

**3.7.2.** El Prestador se obliga a: (i) no divulgarla a terceros sin
autorización escrita previa del Cliente; (ii) limitar su acceso interno al
personal que necesite conocerla para prestar el servicio; (iii) aplicarle
medidas de seguridad no inferiores a las que aplica a su propia información
confidencial; y (iv) no utilizarla para fin distinto de la prestación del
servicio.

**3.7.3. Canal de entrega.** Los informes se entregarán exclusivamente a través
de la Plataforma del Prestador, bajo autenticación individual y con acceso
restringido al Cliente. **Queda prohibida la remisión de informes completos, o
de detalles técnicos explotables, por correo electrónico no cifrado, mensajería
instantánea no corporativa o servicios públicos de almacenamiento.** Cualquier
canal alternativo requiere acuerdo escrito y deberá ofrecer cifrado en tránsito
y control de acceso individual.

**3.7.4. Vigencia.** La obligación de confidencialidad subsiste durante la
vigencia del contrato y por **[cinco (5)] años** contados desde su terminación.
Respecto de vulnerabilidades no remediadas al momento de la terminación, la
obligación subsiste **indefinidamente** o hasta que el Cliente acredite su
remediación, lo que ocurra primero.

**3.7.5. Excepciones.** No se considera confidencial la información que sea o
llegue a ser de dominio público sin culpa del Prestador, la que el Prestador ya
poseía legítimamente antes de su revelación, o aquella cuya divulgación sea
exigida por autoridad competente —en cuyo caso el Prestador notificará al
Cliente previamente, salvo prohibición legal.

**3.7.6. Uso agregado y anonimizado.** El Prestador podrá utilizar métricas
agregadas y **anonimizadas** (volúmenes, tipos de hallazgo, tiempos de
remediación) para mejorar el servicio y elaborar estadísticas sectoriales,
siempre que sea **imposible identificar al Cliente, sus activos o sus
vulnerabilidades específicas** a partir de dicha información.

**3.7.7. Tratamiento de datos personales.** Respecto de los datos personales a
los que acceda, el Prestador actúa como **Encargado del Tratamiento** en los
términos de la Ley 1581 de 2012, y los tratará únicamente conforme a las
instrucciones del Cliente, que actúa como Responsable. Ver
[`legal/aviso-de-privacidad.md`](./aviso-de-privacidad.md) y
[`legal/politica-de-retencion.md`](./politica-de-retencion.md).

**3.7.8. Devolución y supresión.** Terminado el contrato, y a solicitud escrita
del Cliente, el Prestador suprimirá o devolverá la información confidencial en
los plazos de la política de retención, salvo aquella que deba conservar por
obligación legal o como evidencia de trazabilidad de las pruebas ejecutadas.

---

## 3.8 Indemnidad

**3.8.1. Indemnidad a cargo del Cliente.** El Cliente mantendrá indemne al
Prestador —y a sus socios, administradores, empleados y contratistas— frente a
toda reclamación, demanda, sanción, multa o perjuicio, incluidos honorarios de
defensa razonables, que se derive de:

1. La **inexactitud de la declaración de titularidad** del Anexo de Alcance, o
   la inclusión en él de activos sobre los cuales el Cliente no tenía facultad
   para autorizar pruebas, incluidos activos de terceros, en la nube o
   compartidos.
2. Reclamaciones de terceros —proveedores de hosting, nube, socios o clientes
   del Cliente— cuyos sistemas resultaran afectados por pruebas ejecutadas sobre
   activos que el Cliente declaró como propios.
3. El **incumplimiento por el Cliente de sus propias obligaciones legales**,
   incluidas las de la Ley 2573 de 2026 conforme a la Cláusula 3.6.
4. La divulgación o filtración de los informes por parte del Cliente o de su
   personal.
5. El uso de los entregables para fin distinto del pactado.

**3.8.2. Indemnidad a cargo del Prestador.** El Prestador mantendrá indemne al
Cliente frente a reclamaciones derivadas de: (i) la ejecución de pruebas sobre
activos **no incluidos** en el Anexo de Alcance por causa imputable al
Prestador; (ii) la violación de la Cláusula 3.7 por el Prestador o su personal;
y (iii) la infracción de derechos de propiedad intelectual de terceros por las
herramientas o metodologías empleadas. Esta indemnidad está sujeta a la
Cláusula 3.5, salvo dolo o culpa grave.

**3.8.3. Procedimiento.** La Parte que reciba una reclamación cubierta por esta
cláusula notificará a la otra dentro de los **[diez (10)] días hábiles**
siguientes, le permitirá participar en la defensa y no transigirá sin su
consentimiento escrito, que no podrá negarse sin causa razonable.

---

## 3.9 Vigencia normativa y adaptación

**3.9.1.** Las Partes reconocen que la **Ley 2573 de 2026** tiene **entrada en
vigor plena prevista hacia noviembre de 2026**, y que su reglamentación,
interpretación administrativa y desarrollo jurisprudencial se encuentran en
evolución. Las referencias normativas de este contrato reflejan el estado de la
regulación a la fecha de su suscripción.

**3.9.2.** El Prestador se obliga a **revisar periódicamente**, y **al menos una
vez por trimestre**, los cambios normativos aplicables al servicio, y a informar
al Cliente de aquellos que afecten materialmente el alcance, la metodología o
los entregables pactados.

**3.9.3.** Si una modificación normativa —o su reglamentación— hace necesario
ajustar el servicio, las Partes negociarán de buena fe un otrosí dentro de los
**[treinta (30)] días calendario** siguientes a la notificación. Si no se
alcanza acuerdo, cualquiera de las Partes podrá terminar el contrato sin
penalidad, con preaviso de **[treinta (30)] días**, liquidándose los servicios
efectivamente prestados.

**3.9.4.** Si una disposición de este contrato deviene contraria a norma
imperativa posterior, se entenderá **modificada en lo estrictamente necesario**
para ajustarse a ella, conservando las demás disposiciones su plena validez.

**3.9.5.** La actualización de las referencias normativas por parte del
Prestador **no constituye asesoría jurídica** al Cliente ni sustituye su propia
asesoría legal. El Cliente es el único responsable de determinar qué normas le
son aplicables y cómo cumplirlas.

**3.9.6.** El presente contrato se rige por la **ley colombiana**. Le son
aplicables, entre otras, la Ley 1273 de 2009 (delitos informáticos), la Ley 1581
de 2012 y el Decreto 1074 de 2015 (protección de datos personales), y la Ley
2573 de 2026 en lo pertinente conforme a la Cláusula 3.6.

---

## Campos por resolver antes de la firma

| Campo | Valor propuesto | Estado |
|---|---|---|
| 3.4.2 — canal de incidentes | Llamada al contacto de emergencia + correo de constancia dentro de la hora | Propuesto; configurable en el Anexo de Alcance |
| 3.5.1 — tope de responsabilidad | **Seis (6) meses** de lo efectivamente pagado | Propuesto; ver nota al margen de 3.5 |
| 3.7.4 — vigencia de confidencialidad | Cinco (5) años; indefinida para vulnerabilidades no remediadas | Por negociar |
| 3.8.3 — plazo de notificación de reclamaciones | Diez (10) días hábiles | Por negociar |
| 3.9.3 — plazos de renegociación y preaviso | Treinta (30) días | Por negociar |
| Datos de las Partes, precio, vigencia y jurisdicción | — | Fuera del alcance de este documento |

---

*Documento de trabajo. Requiere revisión de abogado colombiano especializado en derecho digital antes de uso contractual formal.*
