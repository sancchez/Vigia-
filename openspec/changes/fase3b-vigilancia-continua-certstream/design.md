# Design: Fase 3b+3c — Vigilancia continua (panel CertStream + feed self-hosted)

## Technical Approach

Tres piezas aditivas: (1) CRUD de `watched_brands` en `api/main.py` +
consumo en el listener; (2) panel frontend nuevo que filtra `GET /findings`
por `tipo='dominio_variante_certstream'` reusando el lenguaje visual de
`RemediationBoard.tsx`; (3) infra Docker para el feed real. Cero cambios de
schema (`watched_brands` y las columnas de ciclo de vida ya existen desde
Fase 0/2a). Cero librerías nuevas en frontend.

## Architecture Decisions

### Decisión central: ¿dominio clon = finding, o modelo propio?

| Opción | Tradeoff | |
|---|---|---|
| Modelo/tabla `clone_domains` propia | Duplica ciclo de vida (estado/owner/SLA/transiciones), nuevo PATCH, nueva auditoría — todo lo que Fase 2a ya construyó | rechazada |
| **Reusar `findings`** | El listener YA escribe `tipo='dominio_variante_certstream'` con las columnas de ciclo de vida; el panel es una vista filtrada + `api.patchFinding` ya existente | **elegida** |

**Rationale**: un dominio clon detectado ya es un finding en la DB con
`estado`/`owner_user_id`/`sla_due_at`/`finding_transiciones`. Introducir un
modelo aparte re-implementaría triage, asignación, SLA y MTTR que Fase 2a/2c ya
resolvieron. El panel es presentación, no un dominio de datos nuevo. `endpoint`
= dominio observado; `raw_json.dominio_base` = dominio/marca vigilada;
`created_at` = cuándo. Todo ya disponible en `FindingOut`.

### Semántica de generación desde `watched_brands`

| Opción | Tradeoff | |
|---|---|---|
| Guardar marca sin consumirla | Config muerta (anti-patrón que el plan advierte) | rechazada |
| Tratar marca como dominio y correr dnstwist | `generate_domain_variants(base)` espera un dominio; una marca "acme" sin TLD produce basura | rechazada solo |
| **Normalizar marca → dominio candidato, luego dnstwist** | Si la marca ya tiene punto se usa tal cual; si no, se le agrega un TLD default (`.com`) antes de generar variantes; errores por-marca aislados | **elegida** |

**Rationale**: mantiene un único motor de variantes (dnstwist) y hace que la
config sea funcional. `_build_variant_map()` ya itera activos de dominio con
`try/except` por-activo; se agrega un segundo bucle equivalente sobre
`watched_brands` activas por tenant, con la misma degradación (log + continuar).
El gate de autorización (`asset_autorizado_para_escanear`) NO aplica a marcas
(no son un asset con prueba de propiedad); se documenta que vigilar una marca
propia es legítimo (es TU marca, buscás quién la clona) — a diferencia de
escanear activamente un dominio ajeno.

### Ubicación del docker-compose (3c)

**Choice**: `deploy/certstream/docker-compose.yml` (directorio nuevo propio).
**Alternatives**: root `docker-compose.yml`; extender `eval/docker-compose.yml`.
**Rationale**: hay changes hermanos de despliegue (fase6/fase7) que
probablemente quieran un compose global en root — colocarlo en un subdirectorio
propio evita la colisión y mantiene el feed como infra opcional aislada.
`eval/` es para el harness de evaluación (Juice Shop), semánticamente distinto.

## Data Flow

```
CertStream server-go (Docker, deploy/certstream) ──ws──> certstream_listener
    (VIGIA_CERTSTREAM_URL apunta aquí)                        │
                                                              ▼
watched_brands (UI ─POST─> api/main.py) ──> _build_variant_map()
                                                              │ match permutación
                                                              ▼
                                             findings (tipo=dominio_variante_certstream)
                                                              │ GET /findings
                                                              ▼
                          VigilanciaPanel.tsx (filtra por tipo, reusa badges SLA)
```

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `api/main.py` | Modify | 3 endpoints `GET/POST/DELETE /watched-brands` + modelos Pydantic `WatchedBrandOut`/`WatchedBrandIn`; patrón de aislamiento por `tenant_id` idéntico a `/tenant/invitations` |
| `api/certstream_listener.py` | Modify | `_build_variant_map()` suma bucle sobre `watched_brands` activas (normaliza marca→dominio, dnstwist, try/except por-marca) |
| `frontend/src/api.ts` | Modify | Tipos `WatchedBrand`/`WatchedBrandBody`; funcs `listWatchedBrands`/`createWatchedBrand`/`deleteWatchedBrand`; helper para filtrar findings CertStream (reusa tipo `Finding` existente) |
| `frontend/src/components/VigilanciaPanel.tsx` | Create | Tabla de dominios clon (base, observado, cuándo, severidad, estado) + filtros + badge SLA reusado; reusa `api.patchFinding` para estado/owner |
| `frontend/src/components/WatchedBrandsConfig.tsx` | Create | Input + lista + baja de marcas vigiladas |
| `frontend/src/pages/Dashboard.tsx` | Modify | Montar `<VigilanciaPanel>` y `<WatchedBrandsConfig>`; fetch de `listWatchedBrands` en `cargarTodo()` |
| `frontend/src/index.css` | Modify | Solo si el panel necesita clases nuevas (reusa `.chip`/`.sla-*` de Fase 2b si es posible) |
| `deploy/certstream/docker-compose.yml` | Create | Servicio `certstream-server-go` (imagen de terceros), red interna, `restart: unless-stopped` |
| `deploy/certstream/README.md` | Create | Cómo levantarlo y apuntar `VIGIA_CERTSTREAM_URL` |
| `.env.example` | Modify | Documentar `VIGIA_CERTSTREAM_URL` self-hosted (si el archivo existe) |

## Coordinación con changes hermanos (LEER — el orquestador secuencia)

Estos changes se planifican en paralelo; no veo sus diseños. Archivos que mi
`sdd-apply` toca y su riesgo de colisión:

- **`api/main.py`** (ALTO): agrego un bloque de 3 endpoints `/watched-brands` +
  2 modelos Pydantic al final de la zona de rutas (antes del catch-all SPA en
  la línea ~2432). No modifico endpoints existentes. fase3a/3e/4/5 casi seguro
  también tocan este archivo → aplicar en serie, no en paralelo, sobre este file.
- **`frontend/src/api.ts`** (ALTO): agrego tipos y funcs nuevas al objeto `api`
  y tipos nuevos; no cambio firmas existentes. Colisión de merge probable con
  cualquier fase que agregue endpoints → serializar.
- **`frontend/src/pages/Dashboard.tsx`** (ALTO): agrego 2 imports + 2 bloques de
  montaje + 1 fetch en `cargarTodo()`. Es el punto de integración común de todas
  las fases de UI → serializar y re-verificar tras cada merge.
- **`frontend/src/index.css`** (MEDIO): solo si hace falta; preferir reusar.
- **`api/certstream_listener.py`** (BAJO): probablemente solo mío. fase3a es
  anti-suplantación *bajo demanda* (agents/tools), no el listener.
- **`deploy/certstream/docker-compose.yml`** (MEDIO con fase6/7): es un compose
  NUEVO en subdirectorio propio, NO el root. Si fase6/fase7 crea un
  `docker-compose.yml` global, el orquestador debe decidir si el servicio
  `certstream-server-go` se fusiona ahí o queda como compose separado. Mi diseño
  asume separado para no bloquear.

## Interfaces / Contracts

```python
# api/main.py
class WatchedBrandIn(BaseModel):
    marca: str  # validado no-vacío tras strip → 422 si vacío
class WatchedBrandOut(BaseModel):
    id: str; marca: str; is_active: bool; created_at: str
# GET  /watched-brands        -> list[WatchedBrandOut]  (WHERE tenant_id, ORDER BY created_at DESC)
# POST /watched-brands        -> WatchedBrandOut        (dedup marca activa del tenant)
# DELETE /watched-brands/{id} -> 204                    (404 si es de otro tenant)
```

```typescript
// frontend/src/api.ts
export type WatchedBrand = { id: string; marca: string; is_active: boolean; created_at: string };
// api.listWatchedBrands / createWatchedBrand(marca) / deleteWatchedBrand(id)
// Panel: findings.filter(f => f.tipo === "dominio_variante_certstream")
```

## Testing Strategy

| Layer | What | Approach |
|---|---|---|
| Unit (pytest) | CRUD `/watched-brands`: aislamiento por tenant, 422 vacío, 404 ajeno | `tests/` con TestClient (patrón existente) |
| Unit (pytest) | `_build_variant_map()` incluye marcas activas; marca inválida no rompe | test directo con DB fixture + mock de `generate_domain_variants` |
| Manual/E2E | Panel muestra solo findings CertStream; alta/baja de marca refresca | verificación manual (sin infra de test frontend, gap preexistente) |
| Infra | `certstream-server-go` levanta y el listener se conecta | solo donde haya Docker (esta máquina de dev NO tiene Docker) |

## Migration / Rollout

Sin migración de datos: todas las tablas ya existen (Fase 0/2a). Rollout
aditivo. El feed self-hosted es opcional: sin él, `VIGIA_CERTSTREAM_URL` queda
en el default muerto y el listener degrada con gracia (ya implementado). Sin
`watched_brands` configuradas, el listener sigue vigilando los dominios-asset
como hoy.

## Open Questions

- [ ] TLD default para marcas sin punto: ¿solo `.com` o un set (`.com`,`.co`)?
  Empezar con `.com`; ampliar es trivial y aislado.
- [ ] Imagen concreta de `certstream-server-go` (repo/tag) — fijar en apply,
  verificable solo con Docker disponible.
- [ ] ¿El panel de vigilancia va en el Dashboard o en su propia ruta? Diseño
  asume bloque en Dashboard (como RemediationBoard); una ruta propia es una
  iteración posterior aislada.
