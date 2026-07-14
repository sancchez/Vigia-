# Research de Mercado — Proyecto Ciberseguridad IA

*Investigación de información pública únicamente. Sin outreach, sin contacto con terceros. Última actualización: julio 2026.*

## Resumen ejecutivo

El mercado internacional de Attack Surface Management (ASM) y Brand Protection está validado, es de suscripción (no proyecto único), y sus jugadores más grandes (Detectify, Intruder.io) **construyen sobre motores open source** (Nuclei, ZAP, OpenVAS) sin regalar ese hecho como ventaja de marketing — lo tratan como implementación interna, no como propuesta de valor. Esto confirma el ángulo del plan: ser abiertos sobre el motor no es un riesgo competitivo, es diferenciación, porque nadie más en este espacio lo comunica así.

En Colombia/LATAM **no existe un competidor directo** que combine ASM + anti-suplantación de marca + cumplimiento normativo local en un solo producto para pyme. Los jugadores regionales (Hackmetrix, Delta Protect) hacen compliance/GRC (ISO 27001, SOC2, PCI DSS) con servicio humano intensivo, no productos self-service baratos ni monitoreo de suplantación de marca. Nadie está usando la Ley 2573 de 2026 como gancho de venta todavía — es una ventana abierta y con urgencia legal real, no inventada.

**Precio ancla recomendado para pyme colombiana:** un rango de USD $49–$299/mes (aprox. $200.000–$1.200.000 COP/mes) posiciona el producto muy por debajo de Detectify/Intruder (que empiezan en cientos a miles de USD/mes, pensados para empresas con equipo de seguridad) y muy por encima de "gratis", capturando el hueco de pymes que hoy no pagan nada porque todo lo disponible las excede en precio o complejidad.

## 1. Competencia directa — pricing y target

| Competidor | Modelo de pricing | Rango de precio (USD) | Qué incluye por tier | Target | LATAM/local pricing |
|---|---|---|---|---|---|
| **BrandShield** | Cotización a medida, no publicado | No público (industria: ~$5K–$50K+/año) | Monitoreo de marca, dominios, redes sociales, dark web; tiers varían en frecuencia de escaneo y takedowns | Empresas medianas-grandes con marca reconocida | No — precio en USD estándar, sin tier LATAM |
| **Bolster AI** | Cotización modular por dominios/keywords monitoreados | Benchmark de industria: ~$4K–$12K/mes; deals mid-market $25K–$75K/año | Detección de phishing/fraude en tiempo real, takedowns asistidos o "white-glove" en tiers superiores | Mid-market y enterprise con equipo de seguridad dedicado | No — sin tier regional, descuentos solo por prepago anual (5–10%) |
| **PhishFort** | Suscripción + pago por takedown (con descuento por volumen) | Contrato promedio ~$24K–$26K/año | Takedowns individuales o paquetes "ilimitados" a medida | Empresas SaaS/fintech, MSPs/MSSPs | No |
| **Detectify** | Tiers publicados + cotización enterprise | Starter gratis (5 usuarios); Standard €2.500/año; Professional €5.000/año; Enterprise €15.000/año + fees por dominio/target | EASM (surface monitoring), DAST de aplicaciones, API scanning; onboarding y soporte crecen por tier | Equipos de AppSec con presupuesto medio-alto; deals reales $12K–$25K/año para 5–10 assets | No — precios en EUR estándar, sin ajuste regional |
| **Intruder.io** | Tiers publicados + add-ons | Free (para siempre, limitado); Essential desde ~$99–149/mes; Cloud ~$180–299/mes; Pro ~$240–499/mes; Enterprise a medida; Pentest AI add-on desde $3.500/test | Escaneo externo, checks de nube, contenedores, motores Nuclei/ZAP/OpenVAS integrados; capas superiores agregan escaneo interno, más puertos, más "AI credits" | Startups (Essential) hasta empresas con superficie compleja (Enterprise) | No — plan Free es la única vía de entrada barata, sin tier regional |
| **Astra Security** | Por "target" (activo/dominio), anual o mensual | Scanner $199/mes o $1.999/año por target; Pentest $5.999/año por target; Enterprise desde $3.999/año por target | Scanner: 9.300+ tests automatizados; Pentest: prueba manual + certificado público + compliance; Enterprise: multi-target + CSM | PYMEs tech-savvy hasta enterprise, con foco fuerte en compliance (PCI, SOC2, ISO) | No, pero es el más accesible en precio de punto de entrada de todo el grupo |

**Patrón común:** ninguno de los seis tiene tier explícito para LATAM ni precios en pesos/moneda local — todos cotizan en USD/EUR con supuestos de mercado desarrollado. Esto por sí solo ya es una barrera de entrada para pyme colombiana (conversión de moneda + percepción de "no es para mí").

## 2. Competencia adyacente en LATAM/Colombia

No se encontró ningún competidor colombiano o latinoamericano que combine ASM + anti-suplantación de marca + IA en un producto self-service de bajo ticket. Lo que sí existe:

- **Hackmetrix** (Chile/México/Colombia, ~100 clientes LATAM, ronda de $1.3M): plataforma SaaS + servicio para compliance (ISO 27001, SOC2, PCI DSS) dirigida a startups/pymes. Incluye monitoreo de superficie de ataque y pentesting como parte del bundle de compliance, no como producto independiente. Precio no público — modelo de "contactar para cotización".
- **Delta Protect** (México, con presencia regional incluida Colombia y Argentina): "centro de comando de ciberseguridad" con IA + ejecución humana. Su línea dAttack ofrece pentesting avanzado y evaluación continua de vulnerabilidades, pero como parte de un bundle de servicios (dSOC, dCloud, dCISO), vendido con ticket de consultoría, no de SaaS accesible. Comunican que su plataforma Apolo es "6 veces más económica que un equipo interno" — mensaje de posicionamiento contra contratar personal, no contra otro SaaS.
- **T.I. Rescue, Cyberseguro, Background Colombia** y otras firmas locales: servicios tradicionales de consultoría/pentesting manual o protección de marca vía investigación privada (no producto de software recurrente ni IA).
- **GatekeeperX** (startup colombiana, fraude/AML, no ciberseguridad técnica): ataca el problema de fraude financiero/lavado con IA, adyacente pero no competidor directo — confirma que sí hay apetito de inversión en Colombia para "IA + fraude/seguridad", pero en el segmento financiero, no en ASM de pyme.

**Conclusión de la sección:** el hueco es real. Nadie en Colombia vende "vigilancia de superficie de ataque + anti-suplantación de marca" como producto de suscripción barato y self-service para pyme. Los jugadores locales son firmas de servicios/consultoría con ticket alto (orientadas a mediana-grande empresa que necesita certificación), no SaaS de bajo costo para el segmento que describe el caso miempresa.

## 3. Contexto regulatorio — Ley 2573 de 2026

- **Promulgada:** 19 de mayo de 2026. Vigencia escalonada: algunas disposiciones rigen desde la promulgación, otras a los 6 meses.
- **Objeto:** proteger a las personas de reportes negativos y cobros indebidos derivados de suplantación de identidad, y obligar a las entidades a demostrar que aplicaron controles adecuados de validación digital antes de generar una obligación a nombre de alguien.
- **Sujetos obligados explícitos:** entidades financieras y crediticias, operadores de telecomunicaciones que activan líneas/servicios asociados a identidad, y establecimientos comerciales cuya contratación genere obligaciones crediticias (financiamiento, planes, etc.).
- **Obligaciones concretas:**
  - Adoptar medidas de seguridad digital "suficientes y razonables" para validar identidad de usuarios.
  - Atender oportunamente reportes de presunta suplantación.
  - Conservar evidencia de los procedimientos de validación aplicados (trazabilidad).
  - Poder demostrar, si se les exige, que sus mecanismos de prevención de fraude eran adecuados.
- **Cambio de paradigma — inversión de la carga de la prueba:** si una empresa no puede demostrar que su proceso de vinculación/validación fue correcto, pierde el derecho a cobrar la obligación y absorbe la pérdida ella misma. Este es el gancho de venta más fuerte del plan: ya no es "deberías cuidarte", es "si no lo demuestras con evidencia, pagas tú el fraude".
- **Relevancia directa para el producto:** el módulo de "Cumplimiento normativo" de la sección 4 del plan (mapear hallazgos a Ley 2573/ISO 27001) tiene encaje casi literal — la ley pide evidencia y trazabilidad de controles, que es exactamente lo que el pipeline de agentes ya genera como registro de auditoría (sección 8.1 del plan, "trazabilidad completa").
- **Advertencia:** el alcance textual de la ley, según las fuentes públicas revisadas, apunta primero a bancos, telcos y comercio con crédito — no a "toda pyme" de forma automática. El discurso comercial debe evitar sobre-prometer aplicabilidad legal directa a cualquier pyme; el ángulo correcto es "esta ley marca el estándar de lo que un regulador/juez va a considerar razonable" y sirve como argumento de buenas prácticas incluso para quien no está en el listado explícito de obligados.

## 4. Validación del ángulo open-core

- **Detectify:** 100% propietario y cerrado. No comunica ningún componente open source. El plan "Starter" es gratis pero limitado (5 usuarios, 1 team) — es una puerta de entrada freemium clásica, no una narrativa de "motor abierto + capa paga".
- **Intruder.io:** sí construye explícitamente sobre motores open source — usan Nuclei, ZAP (Checkmarx) y OpenVAS como backend de sus escaneos, y hasta tienen un post de blog explicando qué es Nuclei. Pero **no lo vende como ventaja de transparencia/auditabilidad**; lo mencionan como detalle técnico de producto, no como argumento de confianza. El plan Free (para siempre, targets de infraestructura ilimitados, 5 apps web) es la única concesión "gratis", y no incluye los motores más potentes (Nuclei entra recién en Cloud/Premium).
- **Astra, Bolster, PhishFort, BrandShield:** cero mención de componentes open source en su comunicación pública — todo se presenta como tecnología propietaria "de la casa".

**Conclusión:** ningún competidor de la lista comunica activamente "esto es lo que es open source, esto es lo que cobramos" como estrategia de marketing/confianza — todos usan open source puertas adentro (cuando lo usan) sin decirlo como diferencial. Esto **confirma y refuerza** la apuesta de la sección 6 del plan: comunicar abiertamente la división open-core (motor auditable en GitHub vs. capa de priorización/reportes/monitoreo de pago) sería un ángulo de posicionamiento que ningún competidor de este research está usando, y encaja con el argumento del propio plan de que "nadie confía en una herramienta de seguridad que no se puede auditar".

## Recomendaciones concretas de pricing/posicionamiento

1. **Precio de entrada explícito en pesos, no en USD.** Todos los competidores directos cotizan en USD/EUR sin tier regional — es una barrera de fricción y de percepción ("esto no es para mí"). Un tier de entrada publicado en COP (ej. ~$200.000–$400.000 COP/mes, equivalente a Essential de Intruder pero mostrado en moneda local) es un diferencial inmediato frente a los 6 competidores internacionales revisados, ninguno de los cuales lo hace.

2. **Vender el compliance de la Ley 2573 como el gancho principal, no el escaneo técnico.** El mercado de ASM/vulnerability scanning ya está transitado (Astra desde $199/mes, Intruder Free/Essential) y la pyme colombiana no entiende "CVE" ni "superficie de ataque". Sí entiende "si te estafan y no puedes demostrar que validaste la identidad del cliente, pagas tú" — ese es el mensaje de la Ley 2573 y ningún competidor local (Hackmetrix, Delta Protect) lo está usando como ángulo de venta todavía. Reporte de cumplimiento + evidencia trazable como producto de entrada, con el escaneo técnico como el motor debajo, invierte el orden de venta de los competidores internacionales (que venden escaneo primero, compliance como upsell).

3. **Publicar el repo del motor (Nuclei/ZAP/dnstwist wrappeados) en GitHub y comunicarlo activamente como "auditable", replicando la confianza que XBOW/PentAGI generan en el espacio técnico pero que ningún competidor B2B (Detectify, Intruder, Astra, BrandShield) comunica hacia su cliente final.** Ninguno de los 6 competidores usa la transparencia del motor como argumento de marketing directo al comprador pyme — todos lo tratan como detalle interno o lo ocultan del todo. Ser el único que dice "así es exactamente como te escaneamos, revísalo tú mismo" es un diferencial de confianza barato de producir (ya está en el plan) y ausente en todo el research.

## Fuentes consultadas

- BrandShield: Capterra, G2, brandshield.com blog, SoftwareSuggest, Krowdbase, Gartner Peer Insights, SoftwareWorld, SaaSCounter, Axencis
- Bolster AI: Vendr, SaaSWorthy, CheckThat.ai, bolster.ai, Software Finder, Capterra, Software Advice, G2
- PhishFort: phishfort.com, Vendr, G2, Gartner Peer Insights, phishprotection.com
- Detectify: detectify.com/pricing, Vendr, G2, Capterra, WeavAI blog, Software Finder, CircleID, AppSec Santa
- Intruder.io: intruder.io/pricing, SaaSWorthy, G2, Attaxion, AppSec Santa, Capterra, Gartner, SelectHub, intruder.io/blog (Nuclei)
- Astra Security: Capterra, getastra.com/pricing, G2, Software Advice, GetApp, SoftwareSuggest, TechRepublic, Techjockey, getastra.com/blog (costo de pentest)
- Colombia/LATAM: deltaprotect.com, pmoinformatica.com, eldiario.com.co, connectasec.com, pentestingteam.com, hackmetrix.com, cibersare.com, pentestinglatam.com, tuconsultorti.com, nsit.com.co, colombiaone.com (GatekeeperX), ensun.io
- Ley 2573 de 2026: alcaldiabogota.gov.co (texto normativo), compliance.com.co, valoraanalitik.com, eltiempo.com, lanotaeconomica.com.co, dapre.presidencia.gov.co (PDF oficial), facephi.com, ecosdelcombeima.com, datacredito.com.co
