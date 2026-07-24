import { useState } from "react";
import { api, ApiError, type EstadoHallazgo, type Finding, type Member } from "../api";

// Fase 2b (openspec/changes/fase2b-tablero-remediacion): tablero de
// remediación -- tabla + filtros (estado/dueño/SLA) + selects inline de
// estado/dueño. Ver design.md para la decisión "tabla con filtros vs.
// kanban" y el contrato real (corregido) de `fase2a-ciclo-vida-hallazgo`.

export const ESTADOS: EstadoHallazgo[] = [
  "descubierto",
  "triage",
  "asignado",
  "en_progreso",
  "mitigado",
  "verificado",
  "cerrado",
  "aceptado_riesgo",
];

export const ESTADO_LABEL: Record<EstadoHallazgo, string> = {
  descubierto: "Descubierto",
  triage: "Triage",
  asignado: "Asignado",
  en_progreso: "En progreso",
  mitigado: "Mitigado",
  verificado: "Verificado",
  cerrado: "Cerrado",
  aceptado_riesgo: "Riesgo aceptado",
};

// Contrato REAL confirmado de fase2a (`TRANSICIONES_VALIDAS` en el
// backend). Se usa para limitar las opciones del <select> de estado a
// transiciones válidas -- evita que la UI ofrezca una transición que el
// backend va a rechazar con 409 de forma predecible. "cerrado" y
// "aceptado_riesgo" son estados terminales (conjunto vacío).
export const TRANSICIONES_VALIDAS: Record<EstadoHallazgo, EstadoHallazgo[]> = {
  descubierto: ["triage", "aceptado_riesgo"],
  triage: ["asignado", "aceptado_riesgo"],
  asignado: ["en_progreso", "aceptado_riesgo"],
  en_progreso: ["mitigado", "aceptado_riesgo"],
  mitigado: ["verificado", "aceptado_riesgo"],
  verificado: ["cerrado", "aceptado_riesgo"],
  cerrado: [],
  aceptado_riesgo: [],
};

export type SlaBadge = { texto: string; clase: string };

/** Clasifica `sla_due_at` en un badge visual. Sin SLA o fecha inválida
 * degradan a "sin SLA" (nunca lanzan, nunca se muestran como vencido).
 * Umbral "próximo" = 3 días (ver design.md). Exportada para tests puros. */
export function badgeSla(slaDueAt: string | null | undefined, ahora: Date = new Date()): SlaBadge {
  if (!slaDueAt) return { texto: "sin SLA", clase: "info" };
  const vence = new Date(slaDueAt);
  if (Number.isNaN(vence.getTime())) return { texto: "sin SLA", clase: "info" };
  const diffMs = vence.getTime() - ahora.getTime();
  const diffDias = Math.ceil(diffMs / (1000 * 60 * 60 * 24));
  if (diffDias < 0) return { texto: "SLA vencido", clase: "sla-vencido" };
  if (diffDias <= 3) return { texto: `vence en ${diffDias}d`, clase: "sla-proximo" };
  return { texto: `vence en ${diffDias}d`, clase: "sla-ok" };
}

/** Categoría de SLA para el filtro de tres valores ("vencido"/"proximo"/
 * "ok", donde "ok" incluye "sin SLA" -- ver spec.md). Deriva de `badgeSla`
 * para no duplicar el umbral. */
export function categoriaSla(slaDueAt: string | null | undefined, ahora: Date = new Date()): "vencido" | "proximo" | "ok" {
  const clase = badgeSla(slaDueAt, ahora).clase;
  if (clase === "sla-vencido") return "vencido";
  if (clase === "sla-proximo") return "proximo";
  return "ok";
}

/** Findings sin `estado` (contrato de lectura aún no extendido por
 * fase2a) se tratan como "descubierto" solo a efectos de filtro/badge --
 * nunca como dato confirmado (ver spec.md, degradación). */
export function filtrarPorEstado(findings: Finding[], filtro: string): Finding[] {
  if (filtro === "todos") return findings;
  return findings.filter((f) => (f.estado ?? "descubierto") === filtro);
}

export function filtrarPorDueno(findings: Finding[], filtro: string): Finding[] {
  if (filtro === "todos") return findings;
  if (filtro === "sin_asignar") return findings.filter((f) => !f.owner_user_id);
  return findings.filter((f) => f.owner_user_id === filtro);
}

export function filtrarPorSla(findings: Finding[], filtro: string, ahora: Date = new Date()): Finding[] {
  if (filtro === "todos") return findings;
  return findings.filter((f) => categoriaSla(f.sla_due_at, ahora) === filtro);
}

/** Mensaje de error específico por código de estado real del backend de
 * fase2a -- NO es un catch-all genérico. 409 = transición de estado no
 * permitida (`TRANSICIONES_VALIDAS`); 422 = falta `nota` al cambiar
 * estado, `owner_user_id` de otro tenant, o body vacío; 404 = finding
 * inexistente o de otro tenant; 401 = sesión expirada. */
function mensajeDeError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 409) return `Transición de estado no permitida: ${err.message}`;
    if (err.status === 422) return `No se pudo guardar el cambio: ${err.message}`;
    if (err.status === 404) return "Este hallazgo ya no existe o no pertenece a tu organización.";
    if (err.status === 401) return "Tu sesión expiró. Volvé a iniciar sesión.";
    return err.message;
  }
  return "No se pudo actualizar el hallazgo (error de red).";
}

const SLA_FILTROS = ["todos", "vencido", "proximo", "ok"] as const;
const SLA_FILTRO_LABEL: Record<(typeof SLA_FILTROS)[number], string> = {
  todos: "Todos",
  vencido: "Vencido",
  proximo: "Próximo",
  ok: "OK",
};

type Props = {
  findings: Finding[];
  members: Member[];
  onChanged: () => void;
};

export default function RemediationBoard({ findings, members, onChanged }: Props) {
  const [filtroEstado, setFiltroEstado] = useState<string>("todos");
  const [filtroDueno, setFiltroDueno] = useState<string>("todos");
  const [filtroSla, setFiltroSla] = useState<string>("todos");
  const [guardando, setGuardando] = useState<string | null>(null);
  const [mensaje, setMensaje] = useState<string | null>(null);

  const filtrados = filtrarPorSla(filtrarPorDueno(filtrarPorEstado(findings, filtroEstado), filtroDueno), filtroSla);

  const cambiarEstado = async (findingId: string, nuevoEstado: EstadoHallazgo) => {
    // Contrato real de fase2a: `nota` es obligatoria (min_length 1) al
    // mandar `estado`. No hay campo de nota en la fila (tabla compacta),
    // así que se pide con un prompt nativo -- sin librería nueva, sin
    // modal adicional para un caso que ya es de baja frecuencia.
    const nota = window.prompt("El cambio de estado requiere una nota breve (obligatoria):");
    if (nota === null) return; // cancelado -- el select permanece en el valor confirmado
    if (nota.trim().length === 0) {
      setMensaje("Se requiere una nota para cambiar el estado (mínimo 1 carácter).");
      return;
    }
    setGuardando(findingId);
    setMensaje(null);
    try {
      await api.patchFinding(findingId, { estado: nuevoEstado, nota: nota.trim() });
      onChanged();
    } catch (err) {
      setMensaje(mensajeDeError(err));
    } finally {
      setGuardando(null);
    }
  };

  const cambiarDueno = async (findingId: string, nuevoOwnerId: string | null) => {
    setGuardando(findingId);
    setMensaje(null);
    try {
      await api.patchFinding(findingId, { owner_user_id: nuevoOwnerId });
      onChanged();
    } catch (err) {
      setMensaje(mensajeDeError(err));
    } finally {
      setGuardando(null);
    }
  };

  // Fase 2c (openspec/changes/fase2c-verificacion-cierre-y-kpis): re-escanea
  // solo el endpoint del hallazgo y cierra automáticamente si ya no
  // reproduce. `verificando` es un estado propio (distinto de `guardando`)
  // porque esta llamada puede tardar bastante más (levanta un daemon de
  // ZAP) que un PATCH normal -- no queremos que las otras filas se vean
  // "bloqueadas" por el `disabled` de `guardando` mientras esto corre.
  const [verificando, setVerificando] = useState<string | null>(null);

  const verificarCierre = async (findingId: string) => {
    setVerificando(findingId);
    setMensaje(null);
    try {
      const resultado = await api.verificarCierre(findingId);
      setMensaje(resultado.detalle);
      onChanged();
    } catch (err) {
      setMensaje(mensajeDeError(err));
    } finally {
      setVerificando(null);
    }
  };

  const porEstado = (estado: string) =>
    estado === "todos" ? findings.length : findings.filter((f) => (f.estado ?? "descubierto") === estado).length;

  return (
    <div className="card" style={{ marginBottom: "1.25rem" }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", flexWrap: "wrap", gap: 8, marginBottom: 12 }}>
        <h2 style={{ fontSize: 17 }}>Tablero de remediación</h2>
        <span style={{ fontSize: 13, color: "var(--text-secondary)" }}>{findings.length} hallazgo(s)</span>
      </div>

      {mensaje && (
        <p style={{ fontSize: 13.5, color: "var(--alert)", marginBottom: 10 }}>{mensaje}</p>
      )}

      <div style={{ display: "flex", gap: 6, marginBottom: 10, flexWrap: "wrap" }}>
        {(["todos", ...ESTADOS] as const).map((estado) => {
          const activo = filtroEstado === estado;
          return (
            <button
              key={estado}
              onClick={() => setFiltroEstado(estado)}
              style={{
                fontSize: 12,
                padding: "0.3rem 0.7rem",
                borderRadius: 99,
                border: activo ? "1px solid var(--accent)" : "0.5px solid var(--border)",
                background: activo ? "var(--accent-bg)" : "transparent",
                color: activo ? "var(--accent-ink)" : "var(--text-secondary)",
              }}
            >
              {estado === "todos" ? "Todos" : ESTADO_LABEL[estado]} ({porEstado(estado)})
            </button>
          );
        })}
      </div>

      <div style={{ display: "flex", gap: 10, marginBottom: 14, flexWrap: "wrap", alignItems: "center" }}>
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 13, color: "var(--text-secondary)" }}>
          Dueño:
          <select value={filtroDueno} onChange={(e) => setFiltroDueno(e.target.value)} style={{ fontSize: 12.5 }}>
            <option value="todos">Todos</option>
            <option value="sin_asignar">Sin asignar</option>
            {members.map((m) => (
              <option key={m.id} value={m.id}>
                {m.email}
              </option>
            ))}
          </select>
        </label>

        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {SLA_FILTROS.map((v) => {
            const activo = filtroSla === v;
            return (
              <button
                key={v}
                onClick={() => setFiltroSla(v)}
                style={{
                  fontSize: 12,
                  padding: "0.3rem 0.7rem",
                  borderRadius: 99,
                  border: activo ? "1px solid var(--accent)" : "0.5px solid var(--border)",
                  background: activo ? "var(--accent-bg)" : "transparent",
                  color: activo ? "var(--accent-ink)" : "var(--text-secondary)",
                }}
              >
                SLA: {SLA_FILTRO_LABEL[v]}
              </button>
            );
          })}
        </div>
      </div>

      {filtrados.length === 0 ? (
        <p style={{ fontSize: 14, color: "var(--text-secondary)" }}>
          {findings.length === 0
            ? "Todavía no hay hallazgos. Corré un escaneo para poblar el tablero."
            : "Ningún hallazgo coincide con estos filtros."}
        </p>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13.5 }}>
            <thead>
              <tr style={{ textAlign: "left", color: "var(--text-muted)", fontSize: 12 }}>
                <th style={{ padding: "0.4rem 0.5rem" }}>Severidad</th>
                <th style={{ padding: "0.4rem 0.5rem" }}>Tipo</th>
                <th style={{ padding: "0.4rem 0.5rem" }}>Endpoint</th>
                <th style={{ padding: "0.4rem 0.5rem" }}>Estado</th>
                <th style={{ padding: "0.4rem 0.5rem" }}>Dueño</th>
                <th style={{ padding: "0.4rem 0.5rem" }}>SLA</th>
                <th style={{ padding: "0.4rem 0.5rem" }}>Acciones</th>
              </tr>
            </thead>
            <tbody>
              {filtrados.slice(0, 100).map((f) => {
                const estadoActual = f.estado ?? "descubierto";
                const opcionesEstado = [estadoActual, ...TRANSICIONES_VALIDAS[estadoActual]];
                const sinTransiciones = TRANSICIONES_VALIDAS[estadoActual].length === 0;
                const sla = badgeSla(f.sla_due_at);
                const guardandoEsta = guardando === f.id;
                // Fase 2c: solo tiene sentido re-escanear el endpoint puntual
                // una vez que alguien ya reportó una remediación
                // ('mitigado') o ya lo verificó a mano ('verificado') --
                // antes de eso no hay nada que confirmar todavía.
                const puedeVerificarCierre = estadoActual === "mitigado" || estadoActual === "verificado";
                const verificandoEsta = verificando === f.id;

                const opcionesDueno = [{ id: "", label: "Sin asignar" }, ...members.map((m) => ({ id: m.id, label: m.email }))];
                if (f.owner_user_id && !members.some((m) => m.id === f.owner_user_id)) {
                  opcionesDueno.push({ id: f.owner_user_id, label: f.owner_email ?? f.owner_user_id });
                }

                return (
                  <tr key={f.id} style={{ borderTop: "0.5px solid var(--border)" }}>
                    <td style={{ padding: "0.5rem 0.5rem" }}>
                      <span className={`chip ${f.severidad}`}>{f.severidad}</span>
                    </td>
                    <td style={{ padding: "0.5rem 0.5rem" }}>{f.tipo}</td>
                    <td
                      style={{
                        padding: "0.5rem 0.5rem",
                        fontFamily: "var(--font-mono)",
                        fontSize: 12.5,
                        color: "var(--text-secondary)",
                        wordBreak: "break-all",
                      }}
                    >
                      {f.endpoint || "—"}
                    </td>
                    <td style={{ padding: "0.5rem 0.5rem" }}>
                      <select
                        value={estadoActual}
                        disabled={guardandoEsta || sinTransiciones}
                        title={sinTransiciones ? "Estado terminal, sin transiciones válidas" : undefined}
                        onChange={(e) => cambiarEstado(f.id, e.target.value as EstadoHallazgo)}
                        style={{ fontSize: 12.5 }}
                      >
                        {opcionesEstado.map((estado) => (
                          <option key={estado} value={estado}>
                            {ESTADO_LABEL[estado]}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td style={{ padding: "0.5rem 0.5rem" }}>
                      <select
                        value={f.owner_user_id ?? ""}
                        disabled={guardandoEsta}
                        onChange={(e) => cambiarDueno(f.id, e.target.value || null)}
                        style={{ fontSize: 12.5 }}
                      >
                        {opcionesDueno.map((o) => (
                          <option key={o.id || "sin-asignar"} value={o.id}>
                            {o.label}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td style={{ padding: "0.5rem 0.5rem" }}>
                      <span className={`chip ${sla.clase}`}>{sla.texto}</span>
                      {guardandoEsta && (
                        <span style={{ marginLeft: 6, fontSize: 11, color: "var(--text-muted)" }}>guardando…</span>
                      )}
                    </td>
                    <td style={{ padding: "0.5rem 0.5rem" }}>
                      {puedeVerificarCierre && (
                        <button
                          onClick={() => verificarCierre(f.id)}
                          disabled={verificandoEsta}
                          title="Re-escanea solo este endpoint; cierra el hallazgo automáticamente si ya no reproduce."
                          style={{ fontSize: 12, padding: "0.3rem 0.6rem" }}
                        >
                          {verificandoEsta ? "Verificando…" : "Verificar arreglo"}
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {filtrados.length > 100 && (
            <p style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 8 }}>
              Mostrando los primeros 100 de {filtrados.length}.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
