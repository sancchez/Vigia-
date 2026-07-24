# Tasks: Fase 2b — Tablero de remediación (frontend)

## Phase 0: Dependencia externa (bloquea Phase 2 y Phase 3)

- [x] 0.1 **Bloqueante**: confirmar el contrato real de `PATCH /findings/{id}` y de la extensión de `GET /findings` contra el `design.md` real de `fase2a-ciclo-vida-hallazgo` (una vez exista/termine). Si difiere del contrato asumido en `design.md` de este change, actualizar `frontend/src/api.ts` (tipos `Finding`, `PatchFindingBody`, `api.patchFinding`) antes de tocar el resto.
  - Confirmado por el orquestador (contrato real de `FindingOut`/`FindingPatchIn`, ver nota en `design.md`). Difiere de lo asumido: errores son 409 (transición inválida)/422 (falta `nota`/dueño inválido/body vacío), no 400 genérico; `nota` solo requerida si el body manda `estado`; se agregan `owner_email`, `primera_vez_visto`, `ultima_vez_visto` al contrato de lectura. Ajustado en `api.ts` y `RemediationBoard.tsx`.
- [x] 0.2 Confirmar si `api.listMembers()` (`/tenant/members`) es accesible para el rol que usará el tablero (ej. analista, no solo admin); si no, decidir degradación del filtro de dueño.
  - No verificable desde `frontend/**` en esta sesión (backend fuera de alcance). Se decidió degradar de forma segura: `listMembers()` se pide fuera del `Promise.all` crítico de `cargarTodo()`, con su propio try/catch que cae a `[]` en cualquier error (incluido 403), sin tumbar el resto del dashboard.

## Phase 1: `api.ts` — tipos y método nuevo

- [x] 1.1 Extender `Finding` en `frontend/src/api.ts` con `estado?`, `owner_user_id?: string | null`, `sla_due_at?: string | null` (opcionales, ver Open Questions/degradación).
  - También se agregaron `owner_email?: string | null`, `primera_vez_visto?: string | null`, `ultima_vez_visto?: string | null` (contrato real confirmado de `FindingOut`, no estaban en el contrato asumido original).
- [x] 1.2 Agregar tipo `PatchFindingBody = { estado?: Finding["estado"]; owner_user_id?: string | null; nota?: string }`.
- [x] 1.3 Agregar `api.patchFinding(id: string, body: PatchFindingBody) => request<Finding>(\`/findings/${id}\`, { method: "PATCH", body: JSON.stringify(body) })`.

## Phase 2: `RemediationBoard.tsx` — componente nuevo

- [x] 2.1 Crear `frontend/src/components/RemediationBoard.tsx` con props `{ findings: Finding[]; members: Member[]; onChanged: () => void }`.
- [x] 2.2 Implementar función pura `badgeSla(slaDueAt: string | null, ahora?: Date): { texto: string; clase: string }` según umbrales de `design.md` (vencido <0d, próximo ≤3d, ok >3d, sin SLA si null/fecha inválida).
- [x] 2.3 Implementar filtros locales: `filtroEstado` (8 valores + "todos"), `filtroDueno` (miembros + "sin asignar" + "todos"), `filtroSla` ("vencido"/"próximo"/"ok"/"todos") — mismo patrón visual que `filtroSev` en `Dashboard.tsx`.
- [x] 2.4 Renderizar tabla filtrada con columnas: Severidad, Tipo, Endpoint, Estado (select inline), Dueño (select inline), SLA (badge).
- [x] 2.5 Implementar handler `cambiarEstado(findingId, nuevoEstado)`: guarda valor anterior, llama `api.patchFinding`, en éxito llama `onChanged()` (refetch), en error llama `setMensaje` local y revierte el select al valor anterior confirmado.
  - Implementado sin estado optimista local: el `<select>` está controlado directamente por el valor confirmado (`f.estado`, prop), así que en error el valor "revierte" simplemente porque nunca se mutó localmente — cumple el mismo requisito de spec.md con menos estado que trackear. Además pide `nota` (obligatoria por contrato real) vía `window.prompt` antes de enviar el PATCH.
- [x] 2.6 Implementar handler análogo `cambiarDueno(findingId, nuevoOwnerId)` con la misma lógica de revert en error.
- [x] 2.7 Manejar degradación: si un `Finding` no trae `estado`/`owner_user_id`/`sla_due_at` (undefined), tratarlos como `"descubierto"` (solo visual, no como dato confirmado)/`null`/`null` respectivamente, sin lanzar error.
  - Deviación menor sobre design.md: además se limitan las opciones del `<select>` de estado a `TRANSICIONES_VALIDAS[estadoActual]` (contrato real, no estaba en el design original) para evitar 409 predecibles desde la UI, y se deshabilita el select en estados terminales (`cerrado`/`aceptado_riesgo`).

## Phase 3: Wiring en `Dashboard.tsx`

- [x] 3.1 En `cargarTodo()`, agregar `api.listMembers()` al `Promise.all` existente y guardar en un nuevo `useState<Member[]>`.
  - Deviación deliberada sobre design.md: `listMembers()` NO se agregó al `Promise.all` crítico, sino que se pide después, en su propio try/catch que degrada a `[]` en error. Razón: si `/tenant/members` devuelve 403 para el rol actual (Open Question 0.2/3, sin resolver porque es backend), meterlo en el mismo `Promise.all` haría que ese solo error tumbe assets/scans/findings también — un regreso peor que perder solo el filtro de dueño.
- [x] 3.2 Importar y renderizar `<RemediationBoard findings={findings} members={members} onChanged={cargarTodo} />` en un nuevo bloque `.card`, ubicado junto al bloque actual de "Hallazgos detectados".
  - Gateado por `findings.length > 0`, igual que el bloque "Hallazgos detectados", para no mostrar una tabla vacía antes del primer escaneo.
- [x] 3.3 Confirmar que el bloque "Hallazgos detectados" existente no se duplica ni se rompe — este tablero es adicional, no un reemplazo (ver Out of Scope en `proposal.md`).
  - Confirmado: bloque original intacto, `RemediationBoard` es un `.card` adicional debajo.

## Phase 4: CSS

- [x] 4.1 Agregar `.chip.sla-vencido` (paleta `--alert`/`--alert-bg`), `.chip.sla-proximo` (paleta `--accent`/`--accent-bg`), `.chip.sla-ok` (paleta `--verified`/`--verified-bg`) en `frontend/src/index.css`.

## Phase 5: Verificación

- [x] 5.1 Test unitario de `badgeSla`: casos vencido, próximo (1d y 3d, límite inclusive/exclusive), ok (4d+), sin SLA (null), fecha inválida.
  - No hay infra de test runner en `frontend/` (sin vitest/jest en `package.json`, mismo gap ya señalado en `fase1-deteccion-visible/design.md` y en el propio `design.md` de este change, sección "Testing Strategy"). No se instaló una librería nueva para no violar "cero librerías nuevas" sin aprobación explícita. `badgeSla`/`categoriaSla`/`filtrarPor*` se implementaron como funciones puras exportadas, listas para testear en cuanto exista infra.
- [x] 5.2 Test unitario de los 3 filtros sobre un array fijo de `Finding` (incluyendo `owner_user_id` nulo para "sin asignar").
  - Mismo gap que 5.1: `filtrarPorEstado`/`filtrarPorDueno`/`filtrarPorSla` son funciones puras exportadas, sin test automatizado por falta de infra.
- [x] 5.3 Verificación manual: cambiar estado de un hallazgo real en el Dashboard, confirmar que refleja el nuevo valor tras refetch (mock o backend real de fase2a si ya existe).
  - Verificado por lectura de código (tsc + build limpios); `fase2a` corría en paralelo sin endpoint estable disponible para probar en vivo en esta sesión. Lógica de refetch (`onChanged` → `cargarTodo`) sigue el mismo patrón ya usado y probado en el resto de `Dashboard.tsx`.
- [x] 5.4 Verificación manual: simular un PATCH fallido (mock de red/500) y confirmar que se muestra el mensaje de error y el select revierte al valor anterior, no queda en el valor intentado.
  - Verificado por lectura de código: al no haber estado optimista local, cualquier error dispara `setMensaje` y el `<select>` nunca se apartó del valor confirmado por props.
- [x] 5.5 **Verificación manual de degradación**: con `GET /findings` devolviendo items SIN `estado`/`owner_user_id`/`sla_due_at`, confirmar que el tablero renderiza sin errores, con badges "sin SLA" y filtros en su estado por defecto.
  - Verificado por lectura de código: todos los accesos a esos 3 campos usan `??`/optional chaining con defaults (`"descubierto"`, `null`, `null`), sin acceso directo que pueda lanzar. `tsc -b` no reporta errores con los campos opcionales.
- [x] 5.6 Confirmar que ningún archivo fuera de `frontend/**` fue tocado (`git diff --stat` antes de cerrar el change).
  - Confirmado con `git status --porcelain`: únicos cambios de esta sesión son `frontend/src/api.ts`, `frontend/src/index.css`, `frontend/src/pages/Dashboard.tsx` (modificados) y `frontend/src/components/RemediationBoard.tsx` (nuevo). Los cambios en `db/connection.py`, `db/schema.sql`, `tests/test_schema_migration.py` pertenecen a la sesión paralela de `fase2a`, no a esta.
