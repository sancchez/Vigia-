import { useState } from "react";
import {
  api,
  ApiError,
  dominioBaseCertstream,
  filtrarFindingsCertstream,
  type EstadoHallazgo,
  type Finding,
  type Member,
} from "../api";
import { ESTADO_LABEL, ESTADOS, TRANSICIONES_VALIDAS, badgeSla, filtrarPorEstado } from "./RemediationBoard";

// Fase 3b (openspec/changes/fase3b-vigilancia-continua-certstream): panel
// de vigilancia continua -- dominios clon detectados por
// `api/certstream_listener.py`. Reusa el ciclo de vida de fase2a/2b (el
// dominio clon ES un finding, ver design.md "Decisión central: ¿dominio
// clon = finding, o modelo propio?") y el lenguaje visual de
// `RemediationBoard.tsx` (tabla + chips + badge SLA) en vez de una UI
// nueva -- NO introduce un endpoint/tabla de "dominios" separada.

/** Mensaje de error específico por código real del backend de fase2a --
 * mismo criterio que `RemediationBoard.tsx::mensajeDeError`. */
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

type Props = {
  findings: Finding[];
  members: Member[];
  onChanged: () => void;
};

export default function VigilanciaPanel({ findings, members, onChanged }: Props) {
  const [filtroEstado, setFiltroEstado] = useState<string>("todos");
  const [guardando, setGuardando] = useState<string | null>(null);
  const [mensaje, setMensaje] = useState<string | null>(null);

  const dominiosClon = filtrarFindingsCertstream(findings);
  const filtrados = filtrarPorEstado(dominiosClon, filtroEstado);

  const cambiarEstado = async (findingId: string, nuevoEstado: EstadoHallazgo) => {
    const nota = window.prompt("El cambio de estado requiere una nota breve (obligatoria):");
    if (nota === null) return;
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

  const porEstado = (estado: string) =>
    estado === "todos"
      ? dominiosClon.length
      : dominiosClon.filter((f) => (f.estado ?? "descubierto") === estado).length;

  return (
    <div className="card" style={{ marginBottom: "1.25rem" }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", flexWrap: "wrap", gap: 8, marginBottom: 12 }}>
        <h2 style={{ fontSize: 17 }}>Vigilancia continua — dominios clon</h2>
        <span style={{ fontSize: 13, color: "var(--text-secondary)" }}>{dominiosClon.length} detectado(s)</span>
      </div>
      <p style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 12 }}>
        Dominios que registraron un certificado SSL con una permutación de tus dominios o marcas vigiladas
        (detección en tiempo real vía CertStream).
      </p>

      {mensaje && <p style={{ fontSize: 13.5, color: "var(--alert)", marginBottom: 10 }}>{mensaje}</p>}

      {dominiosClon.length > 0 && (
        <div style={{ display: "flex", gap: 6, marginBottom: 14, flexWrap: "wrap" }}>
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
      )}

      {dominiosClon.length === 0 ? (
        <p style={{ fontSize: 14, color: "var(--text-secondary)" }}>
          Sin dominios clon detectados por ahora. La vigilancia sigue corriendo en tiempo real.
        </p>
      ) : filtrados.length === 0 ? (
        <p style={{ fontSize: 14, color: "var(--text-secondary)" }}>Ningún dominio clon coincide con este filtro.</p>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13.5 }}>
            <thead>
              <tr style={{ textAlign: "left", color: "var(--text-muted)", fontSize: 12 }}>
                <th style={{ padding: "0.4rem 0.5rem" }}>Severidad</th>
                <th style={{ padding: "0.4rem 0.5rem" }}>Dominio base</th>
                <th style={{ padding: "0.4rem 0.5rem" }}>Dominio observado</th>
                <th style={{ padding: "0.4rem 0.5rem" }}>Detectado</th>
                <th style={{ padding: "0.4rem 0.5rem" }}>Estado</th>
                <th style={{ padding: "0.4rem 0.5rem" }}>Dueño</th>
              </tr>
            </thead>
            <tbody>
              {filtrados.slice(0, 100).map((f) => {
                const estadoActual = f.estado ?? "descubierto";
                const opcionesEstado = [estadoActual, ...TRANSICIONES_VALIDAS[estadoActual]];
                const sinTransiciones = TRANSICIONES_VALIDAS[estadoActual].length === 0;
                const sla = badgeSla(f.sla_due_at);
                const guardandoEsta = guardando === f.id;
                const dominioBase = dominioBaseCertstream(f);

                const opcionesDueno = [{ id: "", label: "Sin asignar" }, ...members.map((m) => ({ id: m.id, label: m.email }))];
                if (f.owner_user_id && !members.some((m) => m.id === f.owner_user_id)) {
                  opcionesDueno.push({ id: f.owner_user_id, label: f.owner_email ?? f.owner_user_id });
                }

                return (
                  <tr key={f.id} style={{ borderTop: "0.5px solid var(--border)" }}>
                    <td style={{ padding: "0.5rem 0.5rem" }}>
                      <span className={`chip ${f.severidad}`}>{f.severidad}</span>
                    </td>
                    <td style={{ padding: "0.5rem 0.5rem", fontFamily: "var(--font-mono)", fontSize: 12.5 }}>
                      {dominioBase ?? "—"}
                    </td>
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
                    <td style={{ padding: "0.5rem 0.5rem", color: "var(--text-muted)", fontSize: 12.5 }}>
                      {new Date(f.created_at).toLocaleString("es-CO")}
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
                      <span className={`chip ${sla.clase}`} style={{ marginLeft: 6 }}>
                        {sla.texto}
                      </span>
                      {guardandoEsta && (
                        <span style={{ marginLeft: 6, fontSize: 11, color: "var(--text-muted)" }}>guardando…</span>
                      )}
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
