# Proposal: Fase 3a — Anti-suplantación bajo demanda + scoring de dominios clon

## Intent

`agents/antisuplantacion.py` (dnstwist + Sherlock + análisis IA + borrador de
takedown) ya produce señales reales y, desde Fase 2a, `POST /scan` con
`antisuplantacion_habilitado=true` ya las persiste como `findings`
(`tipo=dominio_variante`/`perfil_red_social`) con ciclo de vida completo. Pero
sigue siendo **invisible**: no hay forma de dispararlo por sí solo desde la UI,
no hay panel de "Protección de marca", y los dominios clon detectados no traen
evidencia de qué tan peligrosos son (¿infraestructura activa? ¿contenido
clonado?). Esta fase lo convierte en producto visible y accionable.

## Scope

### In Scope
- `POST /antisuplantacion`: dispara la rama anti-suplantación sola sobre un
  asset dominio verificado, crea un `scan` real, enriquece y persiste hallazgos.
- **Scoring de infraestructura activa (0-4)**: por cada dominio lookalike,
  contar cuántos de {DNS-A, MX, SSL, HTTP} responden (fórmula transparente).
- **Screenshot best-effort** del dominio clon (evidencia de contenido clonado).
- `GET /antisuplantacion/dominios`: lista de dominios clon con score, evidencia
  y borrador de takedown copiable, scoped a tenant.
- `GET /antisuplantacion/kpis`: **MTTD** (best-effort, vía SSL NotBefore) y
  **TTTD** (exacto, reusa `finding_transiciones` de Fase 2a).
- UI: sección "Protección de marca" (dominios similares, perfiles, screenshots,
  score, takedown copiable, tiles MTTD/TTTD).

### Out of Scope
- Vigilancia continua CertStream y panel de watched_brands — Fase 3b.
- Feed CertStream self-hosted — Fase 3c. Monitoreo DMARC — Fase 3e.
- Envío real de takedown / notificaciones — Fase 5.

## Approach

Modelar dominios clon como `findings` (ya es el caso — no tabla nueva), reusando
lifecycle/SLA/drift/transiciones de Fase 2a → TTTD sale gratis con el patrón MTTR
de Fase 2c. Endpoint dedicado que llama el nodo `agents/antisuplantacion.py`
directo (sin el pipeline de escaneo técnico), luego un paso determinista de
enriquecimiento (scoring + screenshot) antes de `_registrar_findings_con_ciclo_de_vida`.
Sin columnas nuevas: score/screenshot/SSL-NotBefore viajan en `findings.raw_json`.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `tools/antisuplantacion.py` | Modified | Probes DNS/MX/SSL/HTTP + wrapper screenshot |
| `api/main.py` | Modified | 3 endpoints nuevos + helper de enriquecimiento + modelos |
| `frontend/src/api.ts` | Modified | Tipos + 3 funciones de API |
| `frontend/src/components/BrandProtectionPanel.tsx` | New | Sección Protección de marca |
| `frontend/src/pages/Dashboard.tsx` | Modified | Botón "Proteger marca" + montaje del panel |
| `tests/` | New | Scoring, KPIs, endpoint auth/tenant |

**Open-core**: NO toca `vigia_core_private/`. El scoring es una cuenta 0-4
transparente (no IP); el takedown reusa el prompt público ya existente. Ver
design.md, sección "Open-core routing" (incluye el seam privado documentado pero
no construido).

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Playwright (screenshot) no instalado | High | `ToolNotInstalledError` graceful, mismo patrón que `check_certthreat`; score y KPIs funcionan sin screenshot |
| Probes a dominios de terceros (recon pasivo) | Low | Solo info pública (DNS/SSL/HTTP GET); nota legal, gate = ser dueño del brand asset |
| Colisión de archivo con fases paralelas en `api/main.py`/`Dashboard.tsx` | Med | Endpoints/panel aditivos; scope de archivos declarado en design.md para serializar apply si hace falta |

## Rollback Plan

Todo aditivo: 3 endpoints, 1 componente, probes en tools. Sin migración de DB
(cero columnas/tablas nuevas). `git revert` sin downtime.

## Dependencies

- Fase 2a (mergeada): `_registrar_findings_con_ciclo_de_vida`, `finding_transiciones`.
- Fase 2c (mergeada): patrón de cálculo MTTR en Python (reusado para TTTD).

## Success Criteria

- [ ] `POST /antisuplantacion` sobre un dominio verificado devuelve dominios clon con score 0-4.
- [ ] TTTD calculado exacto vía primera transición a `cerrado`; MTTD best-effort con precisión declarada.
- [ ] Panel "Protección de marca" muestra clones, score, screenshot y takedown copiable.
- [ ] Sin instalar Playwright/dnspython la feature degrada, no rompe. `pytest tests/` verde.
