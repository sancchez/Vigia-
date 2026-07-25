import { useEffect, useState } from "react";
import {
  api,
  ApiError,
  claseDeFinding,
  CLASE_EXPLICACION,
  CLASE_LABEL,
  type FindingDetail,
} from "../api";

// Fase 1 (openspec/changes/fase1-deteccion-visible), Task 0.3 (confirmado
// con el usuario antes de sdd-apply): reusa `.chip.info` (no verificable) y
// `.chip.verificado` (confirmado vía Exploit-DB), y agrega `.chip.sin-poc`
// para el tercer estado ("CVE real, sin PoC pública indexada").
const ESTADO_VERIFICACION_LABEL: Record<string, string> = {
  confirmado_via_exploitdb: "confirmado vía Exploit-DB",
  no_verificable_sin_cve: "no verificable (sin CVE)",
  no_confirmado_cve_sin_poc_publica: "CVE sin PoC pública",
};

const ESTADO_VERIFICACION_CLASE: Record<string, string> = {
  confirmado_via_exploitdb: "verificado",
  no_verificable_sin_cve: "info",
  no_confirmado_cve_sin_poc_publica: "sin-poc",
};

/** Texto y clase del chip de verificación explicado, a partir de
 * `estado_verificacion` (3 estados posibles). Si un finding viejo no trae
 * `estado_verificacion` embebido en `raw_json` (insertado antes de esta
 * fase), se degrada con gracia al criterio binario anterior en vez de
 * mostrar un estado inventado. Exportado para reusarse también en la fila
 * de la tabla de `Dashboard.tsx`. */
export function chipVerificacion(
  estado: string | null,
  confirmado: boolean
): { texto: string; clase: string } {
  if (estado && ESTADO_VERIFICACION_LABEL[estado]) {
    return { texto: ESTADO_VERIFICACION_LABEL[estado], clase: ESTADO_VERIFICACION_CLASE[estado] };
  }
  return confirmado
    ? { texto: "confirmado", clase: "verificado" }
    : { texto: "sin confirmar", clase: "info" };
}

type Props = {
  findingId: string | null;
  onClose: () => void;
};

export default function FindingDetailPanel({ findingId, onClose }: Props) {
  const [detalle, setDetalle] = useState<FindingDetail | null>(null);
  const [cargando, setCargando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!findingId) {
      setDetalle(null);
      setError(null);
      return;
    }
    let cancelado = false;
    setCargando(true);
    setError(null);
    setDetalle(null);
    api
      .getFinding(findingId)
      .then((d) => {
        if (!cancelado) setDetalle(d);
      })
      .catch((err) => {
        if (!cancelado) {
          setError(err instanceof ApiError ? err.message : "No se pudo cargar el detalle del hallazgo.");
        }
      })
      .finally(() => {
        if (!cancelado) setCargando(false);
      });
    return () => {
      cancelado = true;
    };
  }, [findingId]);

  if (!findingId) return null;

  const chip = detalle ? chipVerificacion(detalle.estado_verificacion, detalle.confirmado) : null;

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(16, 22, 28, 0.45)",
        display: "flex",
        alignItems: "flex-start",
        justifyContent: "center",
        padding: "3rem 1.5rem",
        overflowY: "auto",
        zIndex: 60,
      }}
    >
      <div
        className="card"
        onClick={(e) => e.stopPropagation()}
        style={{ maxWidth: 640, width: "100%", maxHeight: "85vh", overflowY: "auto" }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 12 }}>
          <h2 style={{ fontSize: 18 }}>Detalle del hallazgo</h2>
          <button onClick={onClose} style={{ fontSize: 13 }}>Cerrar</button>
        </div>

        {cargando && <p style={{ color: "var(--text-secondary)", fontSize: 14 }}>Cargando…</p>}
        {error && <p style={{ color: "var(--alert)", fontSize: 14 }}>{error}</p>}

        {detalle && (
          <>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 4 }}>
              <span className={`chip ${detalle.severidad}`}>{detalle.severidad}</span>
              {chip && <span className={`chip ${chip.clase}`}>{chip.texto}</span>}
            </div>

            <p style={{ fontSize: 13.5, margin: "10px 0 2px" }}>
              <strong>{detalle.tipo}</strong>
            </p>
            <p
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 12.5,
                color: "var(--text-secondary)",
                wordBreak: "break-all",
                marginBottom: 14,
              }}
            >
              {detalle.endpoint || "—"}
            </p>

            {/* Fase 7 (señal vs. ruido): el panel de detalle es donde el
                cliente viene a entender POR QUÉ un hallazgo está donde está.
                La explicación va en lenguaje de dueño de negocio y siempre se
                muestra -- también para las vulnerabilidades explotables, para
                que la separación no se lea como una excusa que solo aparece
                cuando conviene minimizar algo. */}
            <div
              style={{
                background: "var(--bg-sunken)",
                borderRadius: 8,
                padding: "0.7rem 0.85rem",
                marginBottom: 14,
              }}
            >
              <p style={{ fontSize: 12.5, color: "var(--text-muted)", margin: "0 0 4px" }}>
                {CLASE_LABEL[claseDeFinding(detalle)]}
              </p>
              <p style={{ fontSize: 13, color: "var(--text-secondary)", lineHeight: 1.6, margin: 0 }}>
                {CLASE_EXPLICACION[claseDeFinding(detalle)]}
              </p>
            </div>

            {detalle.descripcion && (
              <div style={{ marginBottom: 14 }}>
                <h3 style={{ fontSize: 14, marginBottom: 6 }}>Descripción</h3>
                <p style={{ fontSize: 13.5, color: "var(--text-secondary)", lineHeight: 1.6, margin: 0 }}>
                  {detalle.descripcion}
                </p>
              </div>
            )}

            {detalle.solucion && (
              <div style={{ marginBottom: 14 }}>
                <h3 style={{ fontSize: 14, marginBottom: 6 }}>Solución sugerida</h3>
                <p style={{ fontSize: 13.5, color: "var(--text-secondary)", lineHeight: 1.6, margin: 0 }}>
                  {detalle.solucion}
                </p>
              </div>
            )}

            {(detalle.cwe || detalle.referencia) && (
              <div style={{ marginBottom: 14, fontSize: 13, color: "var(--text-secondary)" }}>
                {detalle.cwe && (
                  <p style={{ margin: "0 0 4px" }}>
                    <strong>CWE:</strong> {detalle.cwe}
                  </p>
                )}
                {detalle.referencia && (
                  <p style={{ margin: 0, wordBreak: "break-all" }}>
                    <strong>Referencia:</strong> {detalle.referencia}
                  </p>
                )}
              </div>
            )}

            {!detalle.descripcion && !detalle.solucion && !detalle.cwe && !detalle.referencia && (
              <p style={{ fontSize: 13, color: "var(--text-muted)", marginBottom: 14 }}>
                Este hallazgo no tiene descripción/solución nativa disponible (dato ausente en el
                reporte original de la herramienta).
              </p>
            )}

            {detalle.evidencia_exploitdb.length > 0 && (
              <div>
                <h3 style={{ fontSize: 14, marginBottom: 8 }}>Evidencia de explotación (Exploit-DB)</h3>
                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                  {detalle.evidencia_exploitdb.map((ev, i) => (
                    <div
                      key={`${ev.edb_id}-${i}`}
                      style={{ padding: "0.6rem 0.75rem", background: "var(--bg-sunken)", borderRadius: 8, fontSize: 13 }}
                    >
                      <strong style={{ fontFamily: "var(--font-mono)" }}>EDB-ID {ev.edb_id}</strong>
                      <span style={{ color: "var(--text-secondary)" }}> · {ev.cve}</span>
                      <p style={{ marginTop: 4, marginBottom: 0, color: "var(--text-secondary)" }}>
                        {ev.descripcion}
                      </p>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
