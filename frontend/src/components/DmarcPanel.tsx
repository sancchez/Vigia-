import { useEffect, useRef, useState } from "react";
import { api, ApiError, type DmarcReport, type DmarcSender } from "../api";

// Fase 3e (openspec/changes/fase3e-monitoreo-dmarc): panel de monitoreo
// DMARC -- upload manual de un reporte agregado (RUA, RFC 7489) que el
// usuario ya recibe en su propio buzón `rua=`, lista de reportes
// ingeridos, y tabla de remitentes por IP de origen con las no
// autorizadas (fallan alineación SPF/DKIM) resaltadas. Mismo estilo de
// card que `KpiWidget`/`RemediationBoard`.

export default function DmarcPanel() {
  const [reports, setReports] = useState<DmarcReport[]>([]);
  const [senders, setSenders] = useState<DmarcSender[]>([]);
  const [cargando, setCargando] = useState(true);
  const [subiendo, setSubiendo] = useState(false);
  const [mensaje, setMensaje] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const cargar = async () => {
    setCargando(true);
    try {
      const [r, s] = await Promise.all([api.getDmarcReports(), api.getDmarcSummary()]);
      setReports(r);
      setSenders(s);
    } catch {
      // Degrada con gracia -- el resto del dashboard no depende de esto.
      setMensaje("No se pudieron cargar los datos de monitoreo DMARC.");
    } finally {
      setCargando(false);
    }
  };

  useEffect(() => {
    cargar();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const subirArchivo = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setSubiendo(true);
    setMensaje(null);
    try {
      const resultado = await api.uploadDmarcReport(file);
      setMensaje(
        `Reporte de ${resultado.dominio} ingerido (${resultado.n_records} registro(s)).`
      );
      await cargar();
    } catch (err) {
      setMensaje(err instanceof ApiError ? err.message : "No se pudo ingerir el reporte.");
    } finally {
      setSubiendo(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const noAutorizados = senders.filter((s) => !s.autorizado).length;

  return (
    <div className="card" style={{ marginBottom: "1.25rem" }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", flexWrap: "wrap", gap: 8, marginBottom: 12 }}>
        <h2 style={{ fontSize: 17 }}>Monitoreo DMARC</h2>
        {senders.length > 0 && (
          <span style={{ fontSize: 13, color: "var(--text-secondary)" }}>
            {senders.length} remitente(s) · {noAutorizados} no autorizado(s)
          </span>
        )}
      </div>
      <p style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 12 }}>
        Sube el reporte agregado (RUA) que ya recibes en tu propio buzón <code>rua=</code> — Vigia
        detecta quién está enviando correo haciéndose pasar por tu dominio.
      </p>

      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 14 }}>
        <input
          ref={fileInputRef}
          type="file"
          accept=".xml,.gz,.zip"
          onChange={subirArchivo}
          disabled={subiendo}
          style={{ fontSize: 13 }}
        />
        {subiendo && <span style={{ fontSize: 13, color: "var(--text-secondary)" }}>Procesando…</span>}
      </div>

      {mensaje && (
        <p style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 12 }}>{mensaje}</p>
      )}

      {cargando ? (
        <p style={{ fontSize: 14, color: "var(--text-secondary)" }}>Cargando…</p>
      ) : (
        <>
          <h3 style={{ fontSize: 13.5, marginBottom: 8, color: "var(--text-secondary)" }}>
            Remitentes por IP de origen
          </h3>
          {senders.length === 0 ? (
            <p style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 16 }}>
              Todavía no hay reportes ingeridos.
            </p>
          ) : (
            <div style={{ overflowX: "auto", marginBottom: 16 }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13.5 }}>
                <thead>
                  <tr style={{ textAlign: "left", color: "var(--text-muted)", fontSize: 12 }}>
                    <th style={{ padding: "0.4rem 0.5rem" }}>IP de origen</th>
                    <th style={{ padding: "0.4rem 0.5rem" }}>Mensajes</th>
                    <th style={{ padding: "0.4rem 0.5rem" }}>SPF pass</th>
                    <th style={{ padding: "0.4rem 0.5rem" }}>DKIM pass</th>
                    <th style={{ padding: "0.4rem 0.5rem" }}>Estado</th>
                  </tr>
                </thead>
                <tbody>
                  {senders.map((s) => (
                    <tr
                      key={s.source_ip}
                      style={{
                        borderTop: "0.5px solid var(--border)",
                        background: s.autorizado ? undefined : "var(--alert-bg)",
                      }}
                    >
                      <td style={{ padding: "0.5rem 0.5rem", fontFamily: "var(--font-mono)" }}>{s.source_ip}</td>
                      <td style={{ padding: "0.5rem 0.5rem" }}>{s.mensajes}</td>
                      <td style={{ padding: "0.5rem 0.5rem" }}>{s.spf_pass}</td>
                      <td style={{ padding: "0.5rem 0.5rem" }}>{s.dkim_pass}</td>
                      <td style={{ padding: "0.5rem 0.5rem" }}>
                        <span className={`chip ${s.autorizado ? "verificado" : "critical"}`}>
                          {s.autorizado ? "autorizado" : "no autorizado"}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <h3 style={{ fontSize: 13.5, marginBottom: 8, color: "var(--text-secondary)" }}>
            Reportes ingeridos
          </h3>
          {reports.length === 0 ? (
            <p style={{ fontSize: 13, color: "var(--text-secondary)" }}>Ninguno todavía.</p>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {reports.map((r) => (
                <div
                  key={r.id}
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    fontSize: 13,
                    padding: "0.4rem 0",
                    borderBottom: "0.5px solid var(--border)",
                  }}
                >
                  <span style={{ fontFamily: "var(--font-mono)" }}>{r.dominio}</span>
                  <span style={{ color: "var(--text-secondary)" }}>{r.n_records} registro(s)</span>
                  <span style={{ color: "var(--text-muted)" }}>
                    {r.rango_inicio ? new Date(r.rango_inicio).toLocaleDateString("es-CO") : "—"}
                    {" – "}
                    {r.rango_fin ? new Date(r.rango_fin).toLocaleDateString("es-CO") : "—"}
                  </span>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
