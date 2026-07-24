import { useEffect, useState } from "react";
import { api, type AntisuplantacionKpis, type DominioClon } from "../api";

// Fase 3a (openspec/changes/fase3a-antisuplantacion-y-scoring): panel
// "Protección de marca" -- dominios clon / perfiles falsos detectados por
// `agents/antisuplantacion.py` (dnstwist + Sherlock + IA), con score
// transparente de infraestructura activa (0-4), screenshot best-effort
// como evidencia, borrador de takedown copiable, y tiles MTTD/TTTD.
// Mismo precedente de extracción que `RemediationBoard.tsx`/`KpiWidget.tsx`:
// se auto-alimenta (fetch propio) en vez de depender de props del
// dashboard, salvo `refreshKey` para refrescar tras un `POST /antisuplantacion`.

const SEV_LABEL: Record<string, string> = {
  critical: "Crítico",
  high: "Alto",
  medium: "Medio",
  low: "Bajo",
  info: "Info",
};

/** Clasifica el score 0-4 en un badge visual -- 0-1 bajo, 2-3 medio, 4 alto
 * (ver `.chip.score-*` en index.css). Exportada para tests puros. */
export function claseScore(score: number): "score-bajo" | "score-medio" | "score-alto" {
  if (score >= 4) return "score-alto";
  if (score >= 2) return "score-medio";
  return "score-bajo";
}

function ScreenshotThumbnail({ findingId }: { findingId: string }) {
  const [url, setUrl] = useState<string | null>(null);
  const [cargando, setCargando] = useState(false);
  const [intentado, setIntentado] = useState(false);

  useEffect(() => {
    return () => {
      if (url) URL.revokeObjectURL(url);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url]);

  const cargar = async () => {
    setCargando(true);
    try {
      const objectUrl = await api.getScreenshotUrl(findingId);
      setUrl(objectUrl);
    } finally {
      setCargando(false);
      setIntentado(true);
    }
  };

  if (url) {
    return (
      <img
        src={url}
        alt="Screenshot del dominio clon"
        style={{ maxWidth: 160, maxHeight: 100, borderRadius: 6, border: "0.5px solid var(--border)" }}
      />
    );
  }

  if (intentado && !cargando) {
    return <span style={{ fontSize: 12, color: "var(--text-muted)" }}>Sin screenshot disponible</span>;
  }

  return (
    <button onClick={cargar} disabled={cargando} style={{ fontSize: 12 }}>
      {cargando ? "Cargando…" : "Ver screenshot"}
    </button>
  );
}

type Props = { refreshKey?: number };

export default function BrandProtectionPanel({ refreshKey }: Props) {
  const [dominios, setDominios] = useState<DominioClon[]>([]);
  const [kpis, setKpis] = useState<AntisuplantacionKpis | null>(null);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [copiado, setCopiado] = useState<string | null>(null);

  useEffect(() => {
    let activo = true;
    (async () => {
      setCargando(true);
      try {
        const [d, k] = await Promise.all([api.getDominiosClon(), api.getAntisuplantacionKpis()]);
        if (activo) {
          setDominios(d);
          setKpis(k);
          setError(null);
        }
      } catch {
        if (activo) setError("No se pudo cargar la protección de marca.");
      } finally {
        if (activo) setCargando(false);
      }
    })();
    return () => {
      activo = false;
    };
  }, [refreshKey]);

  const copiarTakedown = async (findingId: string, texto: string) => {
    try {
      await navigator.clipboard.writeText(texto);
      setCopiado(findingId);
      setTimeout(() => setCopiado((actual) => (actual === findingId ? null : actual)), 2000);
    } catch {
      /* clipboard no disponible (ej. contexto no seguro) -- no bloquea el resto del panel */
    }
  };

  if (cargando && dominios.length === 0) {
    return (
      <div className="card" style={{ marginBottom: "1.25rem" }}>
        <h2 style={{ fontSize: 17, marginBottom: 8 }}>Protección de marca</h2>
        <p style={{ fontSize: 14, color: "var(--text-secondary)" }}>Cargando…</p>
      </div>
    );
  }

  return (
    <div className="card" style={{ marginBottom: "1.25rem" }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", flexWrap: "wrap", gap: 8, marginBottom: 12 }}>
        <h2 style={{ fontSize: 17 }}>Protección de marca</h2>
        <span style={{ fontSize: 13, color: "var(--text-secondary)" }}>
          {kpis ? `${kpis.total_clones} detectado(s) · ${kpis.clones_activos} con infraestructura activa` : ""}
        </span>
      </div>

      {error && <p style={{ fontSize: 13.5, color: "var(--alert)", marginBottom: 10 }}>{error}</p>}

      {kpis && (
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 16 }}>
          <div style={{ padding: "0.5rem 0.75rem", background: "var(--bg-sunken)", borderRadius: 8, fontSize: 13 }}>
            <strong>TTTD (tiempo hasta takedown):</strong>{" "}
            {kpis.tttd_dias == null ? "sin datos" : `${kpis.tttd_dias.toFixed(1)} día(s)`}
            {kpis.tttd_dias != null && (
              <span className="chip verificado" style={{ marginLeft: 6, fontSize: 10 }}>
                {kpis.tttd_precision}
              </span>
            )}
          </div>
          <div style={{ padding: "0.5rem 0.75rem", background: "var(--bg-sunken)", borderRadius: 8, fontSize: 13 }}>
            <strong>MTTD (tiempo hasta detección):</strong>{" "}
            {kpis.mttd_dias == null ? "sin datos" : `${kpis.mttd_dias.toFixed(1)} día(s)`}
            {kpis.mttd_dias != null && (
              <span className="chip info" style={{ marginLeft: 6, fontSize: 10 }}>
                {kpis.mttd_precision}
              </span>
            )}
          </div>
        </div>
      )}

      {dominios.length === 0 ? (
        <p style={{ fontSize: 14, color: "var(--text-secondary)" }}>
          Todavía no se detectaron dominios clon ni perfiles falsos. Usá "Proteger marca" sobre un dominio verificado
          para correr la búsqueda.
        </p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          {dominios.map((d) => (
            <div key={d.finding_id} style={{ borderTop: "0.5px solid var(--border)", paddingTop: 10 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", marginBottom: 6 }}>
                <span style={{ fontFamily: "var(--font-mono)", fontSize: 13.5, wordBreak: "break-all" }}>
                  {d.endpoint}
                </span>
                <span className={`chip ${d.severidad}`}>{SEV_LABEL[d.severidad] ?? d.severidad}</span>
                <span className={`chip ${claseScore(d.score)}`} title="Señales de infraestructura activa: DNS-A + MX + SSL + HTTP">
                  score {d.score}/4
                </span>
                <span className="chip info">{d.estado}</span>
              </div>

              <div style={{ display: "flex", gap: 16, flexWrap: "wrap", alignItems: "flex-start" }}>
                <ScreenshotThumbnail findingId={d.finding_id} />

                {d.takedown && (
                  <div style={{ flex: 1, minWidth: 240 }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
                      <strong style={{ fontSize: 12.5, color: "var(--text-secondary)" }}>Borrador de takedown</strong>
                      <button onClick={() => copiarTakedown(d.finding_id, d.takedown ?? "")} style={{ fontSize: 11.5 }}>
                        {copiado === d.finding_id ? "Copiado" : "Copiar"}
                      </button>
                    </div>
                    <p
                      style={{
                        fontSize: 12.5,
                        color: "var(--text-secondary)",
                        background: "var(--bg-sunken)",
                        borderRadius: 8,
                        padding: "0.5rem 0.7rem",
                        whiteSpace: "pre-wrap",
                        maxHeight: 120,
                        overflowY: "auto",
                      }}
                    >
                      {d.takedown}
                    </p>
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
