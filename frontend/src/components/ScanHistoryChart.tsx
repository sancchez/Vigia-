import type { Finding, ScanHistoryItem } from "../api";

// Feature "comparar escaneos en el tiempo" (item transversal de HANDOFF.md).
// Sin dependencia de charting nueva a propósito: frontend/package.json no
// tenía ninguna (ver Dashboard.tsx / HANDOFF.md), y esto es un gráfico de
// barras apiladas simple — SVG a mano es más liviano que sumar una librería
// para esto. Los datos ya existen en /scans + /findings (agregación 100%
// client-side, sin endpoint nuevo).

const SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"] as const;

const SEVERITY_COLOR: Record<string, string> = {
  critical: "var(--alert)",
  high: "#c9702f",
  medium: "var(--accent)",
  low: "var(--verified)",
  info: "var(--text-muted)",
};

const SEVERITY_LABEL: Record<string, string> = {
  critical: "Críticos",
  high: "Altos",
  medium: "Medios",
  low: "Bajos",
  info: "Info",
};

type Props = {
  scans: ScanHistoryItem[];
  findings: Finding[];
};

export default function ScanHistoryChart({ scans, findings }: Props) {
  const porScan = new Map<string, Record<string, number>>();
  for (const f of findings) {
    const bucket = porScan.get(f.scan_id) ?? {};
    bucket[f.severidad] = (bucket[f.severidad] ?? 0) + 1;
    porScan.set(f.scan_id, bucket);
  }

  const completados = scans
    .filter((s) => s.estado === "completado")
    .slice()
    .sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime())
    .slice(-12);

  if (completados.length === 0) {
    return (
      <p style={{ fontSize: 14, color: "var(--text-secondary)" }}>
        Todavía no hay suficientes escaneos completados para comparar en el tiempo.
      </p>
    );
  }

  const totales = completados.map((s) => {
    const bucket = porScan.get(s.id) ?? {};
    return SEVERITY_ORDER.reduce((acc, sev) => acc + (bucket[sev] ?? 0), 0);
  });
  const maxTotal = Math.max(1, ...totales);

  const width = 640;
  const chartHeight = 160;
  const bottomMargin = 26;
  const height = chartHeight + bottomMargin;
  const barGap = 14;
  const barWidth = Math.min(48, (width - barGap * (completados.length + 1)) / completados.length);

  return (
    <div>
      <svg viewBox={`0 0 ${width} ${height}`} style={{ width: "100%", height: "auto", display: "block" }}>
        <line x1={0} y1={chartHeight} x2={width} y2={chartHeight} stroke="var(--border)" strokeWidth={1} />
        {completados.map((s, i) => {
          const bucket = porScan.get(s.id) ?? {};
          const x = barGap + i * (barWidth + barGap);
          let yAcumulado = chartHeight;
          return (
            <g key={s.id}>
              {SEVERITY_ORDER.map((sev) => {
                const cantidad = bucket[sev] ?? 0;
                if (cantidad === 0) return null;
                const alto = (cantidad / maxTotal) * chartHeight;
                yAcumulado -= alto;
                return (
                  <rect key={sev} x={x} y={yAcumulado} width={barWidth} height={alto} fill={SEVERITY_COLOR[sev]}>
                    <title>{`${s.target} — ${SEVERITY_LABEL[sev]}: ${cantidad}`}</title>
                  </rect>
                );
              })}
              <text x={x + barWidth / 2} y={chartHeight + 16} textAnchor="middle" fontSize="9" fill="var(--text-muted)">
                {new Date(s.created_at).toLocaleDateString("es-CO", { day: "2-digit", month: "2-digit" })}
              </text>
            </g>
          );
        })}
      </svg>
      <div style={{ display: "flex", gap: 14, flexWrap: "wrap", marginTop: 8 }}>
        {SEVERITY_ORDER.map((sev) => (
          <div key={sev} style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 12, color: "var(--text-secondary)" }}>
            <span style={{ width: 10, height: 10, borderRadius: 3, background: SEVERITY_COLOR[sev], display: "inline-block" }} />
            {SEVERITY_LABEL[sev]}
          </div>
        ))}
      </div>
    </div>
  );
}
