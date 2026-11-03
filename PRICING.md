# Pricing & Go-to-Market — Vigia (Ciberseguridad)

Este archivo es solo para estrategia de negocio/venta. Para arquitectura y desarrollo, ver `README.md` y `plan-proyecto-ciberseguridad.md`.

## Cliente objetivo
PYMEs colombianas sin equipo de seguridad interno. Referencia de mercado (ya validada en `plan-proyecto-ciberseguridad.md`): competidores internacionales (BrandShield, Detectify, Intruder.io) cobran $5,000-$50,000+ USD/año. El ticket para PYME colombiana es mucho menor, pero el modelo (suscripción, no proyecto único) sí aplica igual.

## Restricción legal que define la oferta (no negociable)
Ningún escaneo activo puede hacerse sin `legal/autorizacion-pruebas-seguridad.md` firmado por el cliente. Esto significa que **el gancho de entrada no puede ser "escaneo gratis"** — sería ilegal ejecutarlo sin firma previa. La oferta de bajo riesgo tiene que basarse en fuentes públicas (OSINT), que no requieren autorización porque no tocan sistemas del cliente.

## Oferta de entrada (gancho, sin firma requerida)
**"Reporte de Exposición Digital" — gratuito.**
Basado 100% en información pública: dominios/subdominios expuestos, posibles dominios clon de su marca, huellas en redes sociales, filtraciones conocidas asociadas a su dominio. Entrega en 48-72h. Esto reemplaza al "diagnóstico gratis" genérico: es real, legal, y ya demuestra valor antes de pedir firma o tarjeta.

## Planes (una vez el prospecto vio el reporte)

| Plan | Precio | Incluye | Requiere autorización firmada |
|---|---|---|---|
| Monitoreo de Marca | $99-149 USD/mes | Anti-suplantación: dominios clon, phishing con su marca, redes falsas (módulo `agents/` anti-suplantación). **Barrido programado cada 24h por default**; detección en minutos solo si el despliegue levanta el feed CertStream — ver la nota de abajo | No (solo OSINT/monitoreo pasivo) |

### Qué significa exactamente "continuo" en el plan de Marca (leer antes de venderlo)

Hay dos niveles reales, y el precio de arriba asume el primero:

1. **Barrido programado (siempre activo, sin infraestructura extra).**
   `api/scheduler.py::run_brand_cycle_once()` revisa cada dominio verificado y
   cada marca vigilada cada `VIGIA_BRAND_CYCLE_HOURS` (default 24h). Latencia
   de detección: **horas**.
2. **Vigilancia en vivo por Certificate Transparency (opt-in, requiere un
   contenedor más).** Con `deploy/certstream/docker-compose.yml` levantado, un
   clon se detecta cuando le emiten el certificado SSL — a menudo antes de que
   el sitio de phishing esté armado. Latencia: **minutos**.

No prometer "tiempo real" ni "24/7 al instante" si solo está el nivel 1. La
diferencia es vendible tal cual (es un upsell honesto), pero afirmarla sin
tenerla es lo que destruye la confianza en la primera reunión de renovación.
| Attack Surface Activo | $249-499 USD/mes | Escaneo activo recurrente (Nuclei/ZAP/Amass), priorización de riesgo, remediación explicada | Sí — firma antes del primer escaneo |
| Auditoría única | $1,500-5,000 USD (proyecto) | Auditoría completa a medida para empresa más grande, informe ejecutivo | Sí |

## Cómo se conecta con el estado actual del producto
Según `plan-proyecto-ciberseguridad.md` (Fase 4), ya existe multi-tenant, dashboard, y falta conectar niveles de precio a cobro real (pasarela en pausa). Mientras no haya pasarela automática: primeros clientes se cierran manual (factura/transferencia), automatizar cobro solo después de 3+ clientes confirmados.

## Guion de outreach (primer contacto)
> "Hice un análisis rápido de la huella digital pública de [empresa] — encontré [hallazgo concreto, ej. subdominio expuesto / posible dominio clon]. Te mando el reporte completo gratis, sin compromiso. ¿Te sirve que te lo comparta?"

Entregar el hallazgo real, no genérico — esto es lo que convierte un mensaje frío en una conversación.
