# Proposal: Fase 3b+3c — Vigilancia continua (panel CertStream + feed self-hosted)

## Intent

`api/certstream_listener.py` ya escribe findings reales de dominios clon
(`tipo='dominio_variante_certstream'`) cuando detecta un certificado SSL de una
permutación de un dominio vigilado — pero hoy nada en la UI los muestra, así que
la "vigilancia continua" es invisible (mismo "dato muerto" que Fase 2b cerró
para el ciclo de vida). Además el feed público `certstream.calidog.io` está
muerto (confirmado en el docstring del listener): sin un feed vivo el listener
nunca recibe eventos, así que ni siquiera hay findings que mostrar. Esta fase
hace visible la vigilancia (3b) y la vuelve real (3c self-hosted).

## Scope

### In Scope
- **3b Panel de vigilancia**: vista de dominios clon detectados (lista +
  filtros), consumiendo `GET /findings` filtrado por
  `tipo='dominio_variante_certstream'` — reusa ciclo de vida de Fase 2a
  (`estado`/`owner`/`sla_due_at`), NO un modelo nuevo (justificado en design.md).
- **3b Config de `watched_brands`**: CRUD backend (`/watched-brands`) + UI para
  que el tenant agregue/desactive marcas, y wiring del listener para que esas
  marcas realmente se vigilen (no config muerta).
- **3c Feed self-hosted**: `docker-compose` para `certstream-server-go` +
  documentación de `VIGIA_CERTSTREAM_URL` apuntando a esa instancia.

### Out of Scope
- Scoring/screenshot de dominios clon y KPIs MTTD/TTTD (Fase 3d).
- Notificaciones ante dominio clon nuevo (Fase 5 — este panel es solo visual).
- DMARC/SPF/DKIM (Fase 3e).
- Cambios en `vigia_core_private/`.
- Reescribir el motor de detección del listener (dnstwist ya existe).

## Approach

El panel es un componente frontend nuevo (`VigilanciaPanel.tsx`) que reusa el
lenguaje visual de `RemediationBoard.tsx` (tabla + filtros-chip + badges de SLA)
pero filtra a los findings CertStream y muestra columnas propias del dominio
(dominio base, dominio observado, cuándo, estado). Config de `watched_brands`:
endpoints CRUD nuevos en `api/main.py` + sección de UI, y extensión de
`certstream_listener._build_variant_map()` para generar variantes también desde
las marcas configuradas. El feed real se levanta con un `docker-compose` propio
para `certstream-server-go`; `VIGIA_CERTSTREAM_URL` (ya configurable) apunta ahí.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `api/main.py` | Modified | CRUD `GET/POST/DELETE /watched-brands` (tabla ya existe) |
| `api/certstream_listener.py` | Modified | `_build_variant_map()` consume `watched_brands` |
| `frontend/src/api.ts` | Modified | Tipos + funcs de `watched_brands`; helper de vista CertStream |
| `frontend/src/components/VigilanciaPanel.tsx` | New | Tabla de dominios clon + filtros + estado |
| `frontend/src/components/WatchedBrandsConfig.tsx` | New | Alta/baja de marcas vigiladas |
| `frontend/src/pages/Dashboard.tsx` | Modified | Montar los dos componentes nuevos |
| `frontend/src/index.css` | Modified | Estilos de la tabla/sección si hace falta |
| `deploy/certstream/docker-compose.yml` | New | `certstream-server-go` self-hosted (3c) |
| `.env.example` / docs | Modified | Documentar `VIGIA_CERTSTREAM_URL` self-hosted |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Colisión en `api/main.py`/`Dashboard.tsx`/`api.ts` con changes hermanos (3a,3e,4,5,6,7) | High | design.md lista archivos+símbolos exactos; endpoints/funcs con nombres propios (`/watched-brands`, `VigilanciaPanel`) para minimizar solapamiento; el orquestador secuencia |
| Colisión de `docker-compose` con un change de despliegue (3c vs. fase6/7) | Medium | Se ubica en `deploy/certstream/` propio, no root, para no chocar con un compose global |
| Imagen Docker de `certstream-server-go` no verificable sin Docker (esta máquina no lo tiene) | Medium | Compose documentado y probado en apply solo si Docker disponible; degradación ya existe en el listener (feed inalcanzable → se apaga solo) |
| `watched_brands` como keyword (no dominio) rompe `generate_domain_variants` | Medium | design.md define la semántica exacta de generación desde marca; entrada tolerada, errores por-marca aislados |

## Rollback Plan

Todo aditivo. Frontend: `git revert` del commit (componentes nuevos + montaje).
Backend: los endpoints `/watched-brands` son nuevos, revert sin tocar schema
(la tabla ya existía desde Fase 0). El wiring del listener degrada solo si falla.
`deploy/certstream/docker-compose.yml` es infra opcional: si no se levanta,
`VIGIA_CERTSTREAM_URL` queda en el default y el listener se apaga con gracia —
cero impacto sobre el resto de la API.

## Dependencies

- Ciclo de vida de finding de Fase 2a (`estado`/`owner`/`sla_due_at`,
  `PATCH /findings/{id}`) — ya mergeado, base del panel.
- Tabla `watched_brands` de Fase 0 — ya creada.
- `certstream-server-go` (imagen Docker de terceros) para el feed real (3c).
- Docker/Docker Compose en el host de despliegue (no en esta máquina de dev).

## Success Criteria

- [ ] El Dashboard muestra un panel de vigilancia con los findings
  `dominio_variante_certstream` (dominio base, observado, cuándo, estado).
- [ ] El tenant puede agregar/desactivar `watched_brands` desde la UI y quedan
  persistidas.
- [ ] Las marcas configuradas se incorporan al mapa de variantes del listener.
- [ ] Existe un `docker-compose` que levanta `certstream-server-go` y el flujo
  de apuntar `VIGIA_CERTSTREAM_URL` a él queda documentado.
- [ ] Cero cambios en `vigia_core_private/`.
