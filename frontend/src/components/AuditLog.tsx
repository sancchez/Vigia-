import { useEffect, useState } from "react";
import { api, type AuditEvent } from "../api";

// Fase 7 (openspec/changes/fase7-confianza-calidad): timeline de solo
// lectura del registro de auditoría del tenant (`GET /audit`), que expande
// el `trace_log` ya persistido en cada scan (`scans.trace_log_json`) --
// nunca escrito ni mutado desde acá, mismo criterio que `KpiWidget.tsx`
// (auto-fetch, sin props obligatorios). Sin ningún control de edición o
// borrado -- ver spec.md "Vista de auditoría de solo lectura".

const AGENTE_LABEL: Record<string, string> = {
  orquestador: "Orquestador",
  recon: "Reconocimiento",
  escaneo: "Escaneo",
  verificacion: "Verificación",
  priorizacion: "Priorización",
  remediacion: "Remediación",
  reporteria: "Reportería",
  antisuplantacion: "Anti-suplantación",
  revision_ia: "Revisión de código IA",
};

export default function AuditLog() {
  const [eventos, setEventos] = useState<AuditEvent[]>([]);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let activo = true;
    (async () => {
      try {
        const data = await api.getAudit();
        if (activo) setEventos(data);
      } catch {
        if (activo) setError("No se pudo cargar el registro de auditoría.");
      } finally {
        if (activo) setCargando(false);
      }
    })();
    return () => {
      activo = false;
    };
  }, []);

  if (cargando) {
    return (
      <div className="card" style={{ marginTop: "1.25rem" }}>
        <h2 style={{ fontSize: 17, marginBottom: 8 }}>Registro de auditoría</h2>
        <p style={{ fontSize: 14, color: "var(--text-secondary)" }}>Cargando traza…</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="card" style={{ marginTop: "1.25rem" }}>
        <h2 style={{ fontSize: 17, marginBottom: 8 }}>Registro de auditoría</h2>
        <p style={{ fontSize: 14, color: "var(--text-secondary)" }}>{error}</p>
      </div>
    );
  }

  return (
    <div className="card" style={{ marginTop: "1.25rem" }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 12 }}>
        <h2 style={{ fontSize: 17 }}>Registro de auditoría</h2>
        <span style={{ fontSize: 13, color: "var(--text-secondary)" }}>
          {eventos.length} evento(s) · solo lectura
        </span>
      </div>

      {eventos.length === 0 ? (
        <p style={{ fontSize: 14, color: "var(--text-secondary)" }}>
          Sin traza registrada todavía — corré un escaneo para empezar a ver eventos acá.
        </p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 0, maxHeight: 420, overflowY: "auto" }}>
          {eventos.map((ev, i) => (
            <div
              key={`${ev.scan_id}-${i}`}
              style={{
                display: "flex",
                gap: 12,
                padding: "0.6rem 0",
                borderTop: i === 0 ? undefined : "0.5px solid var(--border)",
                fontSize: 13,
              }}
            >
              <span style={{ color: "var(--text-muted)", fontFamily: "var(--font-mono)", fontSize: 12, whiteSpace: "nowrap" }}>
                {new Date(ev.timestamp).toLocaleString("es-CO")}
              </span>
              <span className="chip ok" style={{ flexShrink: 0 }}>
                {AGENTE_LABEL[ev.agente] ?? ev.agente}
              </span>
              <span style={{ color: "var(--text-secondary)", flexShrink: 0 }}>{ev.accion}</span>
              <span style={{ color: "var(--text-secondary)", flex: 1, wordBreak: "break-word" }}>
                {ev.resultado}
              </span>
              <span
                title={ev.target}
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 11.5,
                  color: "var(--text-muted)",
                  flexShrink: 0,
                  maxWidth: 160,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {ev.target}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
