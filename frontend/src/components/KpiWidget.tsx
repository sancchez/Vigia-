import { useEffect, useState } from "react";
import { api, CLASE_EXPLICACION, CLASE_LABEL, type Asset, type KpiSummary } from "../api";

// Fase 2c (openspec/changes/fase2b-tablero-remediacion... continúa fase2b):
// widget simple (no BI) de los 3 KPIs de `GET /kpis` -- MTTR por severidad
// (con badge de precisión), % de hallazgos abiertos dentro de SLA, y una
// mini-serie de tendencia de riesgo por activo. Se busca en `assets` (ya
// cargado por `Dashboard.tsx`) el `valor` legible de cada `asset_id` -- si
// no hay match (asset borrado, u otro tenant en teoría imposible) degrada
// a los primeros 8 caracteres del id, nunca revienta.

const SEV_ORDEN = ["critical", "high", "medium", "low", "info"] as const;
const SEV_LABEL: Record<string, string> = {
  critical: "Crítico",
  high: "Alto",
  medium: "Medio",
  low: "Bajo",
  info: "Info",
};

type Props = { assets: Asset[] };

export default function KpiWidget({ assets }: Props) {
  const [kpis, setKpis] = useState<KpiSummary | null>(null);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let activo = true;
    (async () => {
      try {
        const data = await api.getKpis();
        if (activo) setKpis(data);
      } catch {
        if (activo) setError("No se pudieron cargar los KPIs de remediación.");
      } finally {
        if (activo) setCargando(false);
      }
    })();
    return () => {
      activo = false;
    };
  }, []);

  const nombreAsset = (assetId: string) => assets.find((a) => a.id === assetId)?.valor ?? assetId.slice(0, 8);

  if (cargando) {
    return (
      <div className="card" style={{ marginBottom: "1.25rem" }}>
        <p style={{ fontSize: 14, color: "var(--text-secondary)" }}>Cargando KPIs de remediación…</p>
      </div>
    );
  }

  if (error || !kpis) {
    return (
      <div className="card" style={{ marginBottom: "1.25rem" }}>
        <h2 style={{ fontSize: 17, marginBottom: 8 }}>KPIs de remediación</h2>
        <p style={{ fontSize: 14, color: "var(--text-secondary)" }}>
          {error ?? "Todavía no hay datos suficientes."}
        </p>
      </div>
    );
  }

  const slaClase =
    kpis.porcentaje_dentro_sla == null
      ? "info"
      : kpis.porcentaje_dentro_sla >= 90
        ? "verificado"
        : kpis.porcentaje_dentro_sla >= 70
          ? "medium"
          : "critical";

  return (
    <div className="card" style={{ marginBottom: "1.25rem" }}>
      <h2 style={{ fontSize: 17, marginBottom: 14 }}>KPIs de remediación</h2>

      <div style={{ marginBottom: 18 }}>
        <h3 style={{ fontSize: 13.5, marginBottom: 8, color: "var(--text-secondary)" }}>
          MTTR por severidad (tiempo medio de remediación)
        </h3>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          {kpis.mttr_por_severidad.map((m) => (
            <div
              key={m.severidad}
              style={{ padding: "0.5rem 0.75rem", background: "var(--bg-sunken)", borderRadius: 8, fontSize: 13 }}
            >
              <strong>{SEV_LABEL[m.severidad] ?? m.severidad}:</strong>{" "}
              {m.mttr_dias == null ? "sin datos" : `${m.mttr_dias.toFixed(1)} día(s)`}
              {m.mttr_dias != null && (
                <span className="chip verificado" style={{ marginLeft: 6, fontSize: 10 }}>
                  {m.precision}
                </span>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Fase 7 (señal vs. ruido): conteo de hallazgos abiertos por CLASE.
          Sin esto, el KPI de SLA de abajo cambia de significado (la higiene ya
          no hereda plazos de 7/30 días) sin que nada en la UI explique por
          qué. Aditivo y tolerante a un backend anterior a esta fase. */}
      {kpis.hallazgos_abiertos_por_clase && kpis.hallazgos_abiertos_por_clase.length > 0 && (
        <div style={{ marginBottom: 18 }}>
          <h3 style={{ fontSize: 13.5, marginBottom: 8, color: "var(--text-secondary)" }}>
            Hallazgos abiertos por tipo de problema
          </h3>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            {kpis.hallazgos_abiertos_por_clase.map((c) => (
              <div
                key={c.clase}
                title={CLASE_EXPLICACION[c.clase]}
                style={{ padding: "0.5rem 0.75rem", background: "var(--bg-sunken)", borderRadius: 8, fontSize: 13 }}
              >
                <strong>{CLASE_LABEL[c.clase] ?? c.clase}:</strong> {c.total}
              </div>
            ))}
          </div>
          <p style={{ fontSize: 11.5, color: "var(--text-muted)", marginTop: 6, lineHeight: 1.5 }}>
            La higiene de configuración no lleva plazos de 7 o 30 días: exigirle el mismo SLA que a una
            vulnerabilidad explotable llenaría el tablero de vencidos por cosas que nadie prioriza
            sobre una puerta abierta real.
          </p>
        </div>
      )}

      <div style={{ marginBottom: 18 }}>
        <h3 style={{ fontSize: 13.5, marginBottom: 8, color: "var(--text-secondary)" }}>
          % de hallazgos abiertos dentro de SLA
        </h3>
        {kpis.porcentaje_dentro_sla == null ? (
          <p style={{ fontSize: 13, color: "var(--text-secondary)" }}>Sin hallazgos abiertos en este momento.</p>
        ) : (
          <span className={`chip ${slaClase}`} style={{ fontSize: 15, padding: "0.4rem 0.9rem" }}>
            {kpis.porcentaje_dentro_sla}%
          </span>
        )}
      </div>

      <div>
        <h3 style={{ fontSize: 13.5, marginBottom: 8, color: "var(--text-secondary)" }}>
          Tendencia de riesgo por activo
        </h3>
        {kpis.tendencia_por_activo.length === 0 ? (
          <p style={{ fontSize: 13, color: "var(--text-secondary)" }}>
            Todavía no hay suficiente historial de escaneos por activo.
          </p>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            {kpis.tendencia_por_activo.map((serie) => {
              const ultimoPunto = serie.puntos[serie.puntos.length - 1];
              const resumenUltimo = SEV_ORDEN.filter((sev) => Number(ultimoPunto?.[sev]) > 0)
                .map((sev) => `${SEV_LABEL[sev]} ${ultimoPunto[sev]}`)
                .join(", ");
              return (
                <div key={serie.asset_id}>
                  <p style={{ fontSize: 13, fontFamily: "var(--font-mono)", marginBottom: 4 }}>
                    {nombreAsset(serie.asset_id)}
                  </p>
                  <div style={{ display: "flex", gap: 4, alignItems: "flex-end", height: 32 }}>
                    {serie.puntos.map((punto, i) => {
                      const total = SEV_ORDEN.reduce((acc, sev) => acc + (Number(punto[sev]) || 0), 0);
                      return (
                        <div
                          key={i}
                          title={`${String(punto.fecha)} — ${total} abierto(s)`}
                          style={{
                            width: 10,
                            height: Math.max(4, Math.min(32, total * 6)),
                            background: "var(--accent)",
                            borderRadius: 2,
                          }}
                        />
                      );
                    })}
                  </div>
                  <p style={{ fontSize: 11.5, color: "var(--text-muted)", marginTop: 4 }}>
                    {serie.puntos.length} escaneo(s) — más reciente: {resumenUltimo || "sin hallazgos abiertos"}
                  </p>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
