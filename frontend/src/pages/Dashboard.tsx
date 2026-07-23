import { useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../AuthContext";
import { api, ApiError, type Asset, type CumplimientoReport, type Finding, type ScanDetail, type ScanHistoryItem } from "../api";
import ScanHistoryChart from "../components/ScanHistoryChart";
import Equipo from "../components/Equipo";

const PLAN_LABEL: Record<string, string> = {
  trial: "Prueba",
  costero: "Costero",
  flota: "Flota",
  armada: "Armada",
};

function nivelDeRiesgo(findings: Finding[]): { nivel: "verde" | "amarillo" | "rojo"; texto: string } {
  const criticos = findings.filter((f) => f.severidad === "critical").length;
  const altos = findings.filter((f) => f.severidad === "high").length;
  if (criticos > 0) return { nivel: "rojo", texto: "Atención inmediata: hay hallazgos críticos abiertos." };
  if (altos > 0) return { nivel: "amarillo", texto: "Hay hallazgos de severidad alta por revisar." };
  return { nivel: "verde", texto: "Sin hallazgos críticos ni altos en este momento." };
}

const NIVEL_COLOR: Record<string, string> = {
  verde: "var(--verified)",
  amarillo: "var(--accent)",
  rojo: "var(--alert)",
};
const NIVEL_BG: Record<string, string> = {
  verde: "var(--verified-bg)",
  amarillo: "var(--accent-bg)",
  rojo: "var(--alert-bg)",
};

const SEV_ORDEN = ["critical", "high", "medium", "low", "info"] as const;
const SEV_LABEL: Record<string, string> = {
  critical: "Crítico",
  high: "Alto",
  medium: "Medio",
  low: "Bajo",
  info: "Info",
};
const SEV_BAR_COLOR: Record<string, string> = {
  critical: "var(--alert)",
  high: "var(--accent)",
  medium: "#c9862b",
  low: "#7f97a0",
  info: "#b8c2c7",
};

export default function Dashboard() {
  const { me, logout } = useAuth();
  const navigate = useNavigate();
  const [assets, setAssets] = useState<Asset[]>([]);
  const [scans, setScans] = useState<ScanHistoryItem[]>([]);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [cargando, setCargando] = useState(true);
  const [nuevoDominio, setNuevoDominio] = useState("");
  const [autorizado, setAutorizado] = useState(false);
  const [escaneando, setEscaneando] = useState<string | null>(null);
  const [mensaje, setMensaje] = useState<string | null>(null);
  const [descargando, setDescargando] = useState<string | null>(null);
  const [progreso, setProgreso] = useState<string | null>(null);
  const [filtroSev, setFiltroSev] = useState<string>("todos");
  const [scanVisto, setScanVisto] = useState<ScanDetail | null>(null);
  const [viendoScan, setViendoScan] = useState<string | null>(null);
  const [cumplimiento, setCumplimiento] = useState<CumplimientoReport | null>(null);
  const [cargandoCumpl, setCargandoCumpl] = useState(false);
  const [assetVerificando, setAssetVerificando] = useState<string | null>(null);
  const [assetExpandido, setAssetExpandido] = useState<string | null>(null);
  const [metodoElegido, setMetodoElegido] = useState<Record<string, "dns_txt" | "http_file">>({});

  const cargarTodo = async () => {
    setCargando(true);
    try {
      const [a, s, f] = await Promise.all([api.listAssets(), api.listScans(), api.listFindings()]);
      setAssets(a);
      setScans(s);
      setFindings(f);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        logout();
        navigate("/");
      }
    } finally {
      setCargando(false);
    }
  };

  useEffect(() => {
    cargarTodo();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const agregarDominio = async (e: FormEvent) => {
    e.preventDefault();
    if (!nuevoDominio.trim()) return;
    try {
      await api.createAsset("dominio", nuevoDominio.trim(), "");
      setNuevoDominio("");
      await cargarTodo();
    } catch (err) {
      setMensaje(err instanceof ApiError ? err.message : "No se pudo registrar el dominio.");
    }
  };

  const comprobarVerificacion = async (assetId: string) => {
    const metodo = metodoElegido[assetId] ?? "dns_txt";
    setAssetVerificando(assetId);
    setMensaje(null);
    try {
      const resultado = await api.verifyAsset(assetId, metodo);
      setMensaje(resultado.detalle);
      await cargarTodo();
    } catch (err) {
      setMensaje(err instanceof ApiError ? err.message : "No se pudo comprobar la verificación.");
    } finally {
      setAssetVerificando(null);
    }
  };

  const correrScan = async (valor: string) => {
    if (!autorizado) {
      setMensaje(
        `Para correr un escaneo activo sobre ${valor}, marca primero "Autorización de pruebas firmada".`
      );
      return;
    }
    setEscaneando(valor);
    setMensaje(null);
    setProgreso("Iniciando escaneo activo…");
    try {
      const { scan_id } = await api.startActiveScan(valor);
      // El escaneo corre en background en el backend; consultamos su estado
      // cada 3s. `reporte_final` trae el checkpoint de progreso en vivo
      // ("[spider] Spider clásico: 57%", "[ascan] Escaneo activo: 12%")
      // mientras `estado === "corriendo"`.
      let detalle = await api.getScan(scan_id);
      while (detalle.estado === "corriendo") {
        setProgreso(detalle.reporte_final ?? "Escaneando…");
        await new Promise((r) => setTimeout(r, 3000));
        detalle = await api.getScan(scan_id);
      }
      if (detalle.estado === "completado") {
        setMensaje(`${valor}: escaneo activo completado — ${detalle.findings.length} hallazgo(s).`);
      } else {
        setMensaje(
          `${valor}: el escaneo terminó con estado "${detalle.estado}". ${detalle.reporte_final ?? ""}`.trim()
        );
      }
      await cargarTodo();
    } catch (err) {
      setMensaje(err instanceof ApiError ? err.message : "El escaneo no se pudo completar.");
    } finally {
      setEscaneando(null);
      setProgreso(null);
    }
  };

  const progresoPct = (() => {
    if (!progreso) return null;
    const m = progreso.match(/(\d+)%/);
    return m ? Math.min(100, Number(m[1])) : null;
  })();

  const descargarCumplimiento = async (formato: "pdf" | "docx") => {
    const clave = `cumplimiento-${formato}`;
    setDescargando(clave);
    setMensaje(null);
    try {
      await api.downloadCumplimiento(formato);
    } catch (err) {
      setMensaje(err instanceof ApiError ? err.message : "No se pudo descargar el reporte.");
    } finally {
      setDescargando(null);
    }
  };

  const descargarScan = async (scanId: string, formato: "pdf" | "docx") => {
    const clave = `${scanId}-${formato}`;
    setDescargando(clave);
    setMensaje(null);
    try {
      await api.downloadScanReport(scanId, formato);
    } catch (err) {
      setMensaje(err instanceof ApiError ? err.message : "No se pudo descargar el reporte.");
    } finally {
      setDescargando(null);
    }
  };

  const verScan = async (scanId: string) => {
    setViendoScan(scanId);
    try {
      const detalle = await api.getScan(scanId);
      setScanVisto(detalle);
    } catch (err) {
      setMensaje(err instanceof ApiError ? err.message : "No se pudo abrir el reporte del escaneo.");
    } finally {
      setViendoScan(null);
    }
  };

  const verCumplimiento = async () => {
    setCargandoCumpl(true);
    setMensaje(null);
    try {
      const reporte = await api.getCumplimiento();
      setCumplimiento(reporte);
    } catch (err) {
      setMensaje(err instanceof ApiError ? err.message : "No se pudo generar el reporte de cumplimiento.");
    } finally {
      setCargandoCumpl(false);
    }
  };

  if (!me) return null;

  const riesgo = nivelDeRiesgo(findings);
  const porSeveridad = (sev: string) => findings.filter((f) => f.severidad === sev).length;
  const ordenarPorSev = (lista: Finding[]) =>
    [...lista].sort(
      (a, b) => SEV_ORDEN.indexOf(a.severidad as (typeof SEV_ORDEN)[number]) - SEV_ORDEN.indexOf(b.severidad as (typeof SEV_ORDEN)[number])
    );
  const findingsFiltrados = ordenarPorSev(
    findings.filter((f) => filtroSev === "todos" || f.severidad === filtroSev)
  );

  return (
    <div style={{ maxWidth: 960, margin: "0 auto", padding: "2rem 1.5rem" }}>
      <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "2rem" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ width: 8, height: 8, borderRadius: "50%", background: "var(--accent)", display: "inline-block" }} />
          <span style={{ fontFamily: "var(--font-mono)", fontWeight: 500, letterSpacing: "0.06em" }}>VIGIA</span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span style={{ fontSize: 14, color: "var(--text-secondary)" }}>{me.tenant_nombre}</span>
          <span className="chip ok">{PLAN_LABEL[me.plan] ?? me.plan}</span>
          <button onClick={() => { logout(); navigate("/"); }}>Salir</button>
        </div>
      </header>

      {cargando ? (
        <p style={{ color: "var(--text-secondary)" }}>Cargando tu panel…</p>
      ) : assets.length === 0 ? (
        <div className="card">
          <h2 style={{ fontSize: 18, marginBottom: 8 }}>Empecemos a vigilar tu negocio</h2>
          <p style={{ color: "var(--text-secondary)", fontSize: 14, marginBottom: "1.25rem" }}>
            Todavía no tienes dominios registrados. Sigue estos pasos:
          </p>
          <ol style={{ fontSize: 14, color: "var(--text-secondary)", paddingLeft: "1.2rem", lineHeight: 1.9 }}>
            <li>Registra el primer dominio o app de tu negocio abajo.</li>
            <li>Corre tu primer escaneo — el pasivo no necesita autorización.</li>
            <li>Confirma la autorización de pruebas para desbloquear el escaneo activo completo.</li>
          </ol>
          <form onSubmit={agregarDominio} style={{ display: "flex", gap: 8, marginTop: "1rem" }}>
            <input
              value={nuevoDominio}
              onChange={(e) => setNuevoDominio(e.target.value)}
              placeholder="midominio.com"
              style={{ flex: 1 }}
            />
            <button className="primary" type="submit">Registrar dominio</button>
          </form>
        </div>
      ) : (
        <>
          <div className="card" style={{ display: "flex", alignItems: "center", gap: "1.25rem", marginBottom: "1.25rem" }}>
            <div
              style={{
                width: 56,
                height: 56,
                borderRadius: "50%",
                background: NIVEL_BG[riesgo.nivel],
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                flexShrink: 0,
              }}
            >
              <span style={{ width: 16, height: 16, borderRadius: "50%", background: NIVEL_COLOR[riesgo.nivel] }} />
            </div>
            <div style={{ flex: 1 }}>
              <h2 style={{ fontSize: 17, marginBottom: 4 }}>Nivel de riesgo actual</h2>
              <p style={{ fontSize: 14, color: "var(--text-secondary)" }}>{riesgo.texto}</p>
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <span className="chip critical">{porSeveridad("critical")} críticos</span>
              <span className="chip high">{porSeveridad("high")} altos</span>
              <span className="chip low">{porSeveridad("medium") + porSeveridad("low")} otros</span>
            </div>
          </div>

          {findings.length > 0 && (
            <div className="card" style={{ marginBottom: "1.25rem" }}>
              <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 10 }}>
                <h2 style={{ fontSize: 17 }}>Distribución por severidad</h2>
                <span style={{ fontSize: 13, color: "var(--text-secondary)" }}>{findings.length} hallazgo(s)</span>
              </div>
              <div style={{ display: "flex", height: 14, borderRadius: 99, overflow: "hidden", background: "var(--bg-sunken)" }}>
                {SEV_ORDEN.map((sev) => {
                  const n = porSeveridad(sev);
                  if (n === 0) return null;
                  return (
                    <div
                      key={sev}
                      title={`${SEV_LABEL[sev]}: ${n}`}
                      style={{ width: `${(n / findings.length) * 100}%`, background: SEV_BAR_COLOR[sev] }}
                    />
                  );
                })}
              </div>
              <div style={{ display: "flex", gap: 16, flexWrap: "wrap", marginTop: 12 }}>
                {SEV_ORDEN.map((sev) => (
                  <div key={sev} style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 13 }}>
                    <span style={{ width: 10, height: 10, borderRadius: 3, background: SEV_BAR_COLOR[sev], display: "inline-block" }} />
                    <span style={{ color: "var(--text-secondary)" }}>{SEV_LABEL[sev]}</span>
                    <strong>{porSeveridad(sev)}</strong>
                  </div>
                ))}
              </div>
            </div>
          )}

          {mensaje && (
            <div className="card" style={{ marginBottom: "1.25rem", fontSize: 14 }}>
              {mensaje}
            </div>
          )}

          <div className="card" style={{ marginBottom: "1.25rem" }}>
            <h2 style={{ fontSize: 17, marginBottom: 12 }}>Dominios protegidos</h2>
            <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 12 }}>
              {assets.map((a) => {
                const puedeEscanear = a.verificado || a.exento_de_verificacion;
                return (
                  <div key={a.id} style={{ borderBottom: "0.5px solid var(--border)", padding: "0.6rem 0" }}>
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                      <div>
                        <span style={{ fontFamily: "var(--font-mono)", fontSize: 13.5 }}>{a.valor}</span>
                        <span className="chip ok" style={{ marginLeft: 8 }}>
                          {a.is_active ? "activo" : "inactivo"}
                        </span>
                        {a.exento_de_verificacion ? (
                          <span className="chip verificado" style={{ marginLeft: 8 }}>
                            exento (local)
                          </span>
                        ) : a.verificado ? (
                          <span className="chip verificado" style={{ marginLeft: 8 }}>
                            verificado ({a.verification_method})
                          </span>
                        ) : (
                          <span className="chip medium" style={{ marginLeft: 8 }}>
                            sin verificar
                          </span>
                        )}
                      </div>
                      <div style={{ display: "flex", gap: 8 }}>
                        {!puedeEscanear && (
                          <button
                            onClick={() => setAssetExpandido(assetExpandido === a.id ? null : a.id)}
                          >
                            {assetExpandido === a.id ? "Ocultar" : "Verificar propiedad"}
                          </button>
                        )}
                        <button
                          onClick={() => correrScan(a.valor)}
                          disabled={escaneando === a.valor || !puedeEscanear}
                          title={puedeEscanear ? undefined : "Verifica la propiedad del dominio antes de escanear"}
                        >
                          {escaneando === a.valor ? "Escaneando…" : "Escanear ahora"}
                        </button>
                      </div>
                    </div>

                    {escaneando === a.valor && (
                      <div style={{ marginTop: 10 }}>
                        <div
                          style={{
                            height: 8,
                            borderRadius: 999,
                            background: "var(--bg-sunken)",
                            overflow: "hidden",
                          }}
                        >
                          <div
                            style={{
                              height: "100%",
                              width: progresoPct !== null ? `${progresoPct}%` : "40%",
                              background: "var(--accent)",
                              borderRadius: 999,
                              transition: "width 0.4s ease",
                              animation: progresoPct === null ? "vigia-pulse 1.2s ease-in-out infinite" : undefined,
                            }}
                          />
                        </div>
                        <p
                          style={{
                            marginTop: 6,
                            fontSize: 12.5,
                            fontFamily: "var(--font-mono)",
                            color: "var(--text-secondary)",
                          }}
                        >
                          {progreso ?? "Escaneando…"}
                        </p>
                      </div>
                    )}

                    {assetExpandido === a.id && a.instrucciones_verificacion && (
                      <div
                        style={{
                          marginTop: 10,
                          padding: "0.75rem 0.9rem",
                          background: "var(--bg-sunken)",
                          borderRadius: 8,
                          fontSize: 13,
                        }}
                      >
                        <p style={{ marginBottom: 8, color: "var(--text-secondary)" }}>
                          Demuestra que controlas <strong>{a.valor}</strong> con uno de estos dos métodos, luego
                          confirma abajo.
                        </p>
                        <p style={{ marginBottom: 4 }}>
                          <strong>Opción 1 — registro DNS TXT:</strong>
                        </p>
                        <p style={{ fontFamily: "var(--font-mono)", fontSize: 12, marginBottom: 8, wordBreak: "break-all" }}>
                          Nombre: {a.instrucciones_verificacion.dns_txt.registro}
                          <br />
                          Valor: {a.instrucciones_verificacion.dns_txt.valor}
                        </p>
                        <p style={{ marginBottom: 4 }}>
                          <strong>Opción 2 — archivo bien-conocido:</strong>
                        </p>
                        <p style={{ fontFamily: "var(--font-mono)", fontSize: 12, marginBottom: 10, wordBreak: "break-all" }}>
                          URL: {a.instrucciones_verificacion.http_file.url}
                          <br />
                          Contenido: {a.instrucciones_verificacion.http_file.contenido}
                        </p>
                        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                          <select
                            value={metodoElegido[a.id] ?? "dns_txt"}
                            onChange={(e) =>
                              setMetodoElegido((prev) => ({ ...prev, [a.id]: e.target.value as "dns_txt" | "http_file" }))
                            }
                          >
                            <option value="dns_txt">Ya publiqué el TXT</option>
                            <option value="http_file">Ya publiqué el archivo</option>
                          </select>
                          <button onClick={() => comprobarVerificacion(a.id)} disabled={assetVerificando === a.id}>
                            {assetVerificando === a.id ? "Comprobando…" : "Comprobar ahora"}
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
            <form onSubmit={agregarDominio} style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <input
                value={nuevoDominio}
                onChange={(e) => setNuevoDominio(e.target.value)}
                placeholder="agregar otro dominio…"
                style={{ flex: 1 }}
              />
              <button type="submit">Agregar</button>
              <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 13, color: "var(--text-secondary)" }}>
                <input type="checkbox" checked={autorizado} onChange={(e) => setAutorizado(e.target.checked)} style={{ width: "auto" }} />
                Autorización de pruebas firmada
              </label>
            </form>
          </div>

          <div className="card" style={{ marginBottom: "1.25rem" }}>
            <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", flexWrap: "wrap", gap: 8, marginBottom: 12 }}>
              <h2 style={{ fontSize: 17 }}>Hallazgos detectados</h2>
              <span style={{ fontSize: 13, color: "var(--text-secondary)" }}>
                {findings.length} en total · {porSeveridad("critical")} críticos · {porSeveridad("high")} altos
              </span>
            </div>

            <div style={{ display: "flex", gap: 6, marginBottom: 14, flexWrap: "wrap" }}>
              {(["todos", ...SEV_ORDEN] as const).map((sev) => {
                const activo = filtroSev === sev;
                const n = sev === "todos" ? findings.length : porSeveridad(sev);
                return (
                  <button
                    key={sev}
                    onClick={() => setFiltroSev(sev)}
                    style={{
                      fontSize: 12,
                      padding: "0.3rem 0.7rem",
                      borderRadius: 99,
                      border: activo ? "1px solid var(--accent)" : "0.5px solid var(--border)",
                      background: activo ? "var(--accent-bg)" : "transparent",
                      color: activo ? "var(--accent-ink)" : "var(--text-secondary)",
                    }}
                  >
                    {sev === "todos" ? "Todos" : SEV_LABEL[sev]} ({n})
                  </button>
                );
              })}
            </div>

            {findingsFiltrados.length === 0 ? (
              <p style={{ fontSize: 14, color: "var(--text-secondary)" }}>
                {findings.length === 0
                  ? "Todavía no hay hallazgos. Corré un escaneo para poblar esta tabla."
                  : "No hay hallazgos con esa severidad."}
              </p>
            ) : (
              <div style={{ overflowX: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13.5 }}>
                  <thead>
                    <tr style={{ textAlign: "left", color: "var(--text-muted)", fontSize: 12 }}>
                      <th style={{ padding: "0.4rem 0.5rem" }}>Severidad</th>
                      <th style={{ padding: "0.4rem 0.5rem" }}>Tipo</th>
                      <th style={{ padding: "0.4rem 0.5rem" }}>Endpoint / Objetivo</th>
                      <th style={{ padding: "0.4rem 0.5rem" }}>Estado</th>
                    </tr>
                  </thead>
                  <tbody>
                    {findingsFiltrados.slice(0, 100).map((f) => (
                      <tr key={f.id} style={{ borderTop: "0.5px solid var(--border)" }}>
                        <td style={{ padding: "0.5rem 0.5rem" }}>
                          <span className={`chip ${f.severidad}`}>{SEV_LABEL[f.severidad] ?? f.severidad}</span>
                        </td>
                        <td style={{ padding: "0.5rem 0.5rem" }}>{f.tipo}</td>
                        <td style={{ padding: "0.5rem 0.5rem", fontFamily: "var(--font-mono)", fontSize: 12.5, color: "var(--text-secondary)", wordBreak: "break-all" }}>
                          {f.endpoint || "—"}
                        </td>
                        <td style={{ padding: "0.5rem 0.5rem" }}>
                          <span className={`chip ${f.confirmado ? "verificado" : "info"}`}>
                            {f.confirmado ? "confirmado" : "sin confirmar"}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {findingsFiltrados.length > 100 && (
                  <p style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 8 }}>
                    Mostrando los primeros 100 de {findingsFiltrados.length}. Descargá el reporte para el detalle completo.
                  </p>
                )}
              </div>
            )}
          </div>

          <div className="card">
            <h2 style={{ fontSize: 17, marginBottom: 12 }}>Actividad reciente</h2>
            {scans.length === 0 ? (
              <p style={{ fontSize: 14, color: "var(--text-secondary)" }}>Todavía no se ha corrido ningún escaneo.</p>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {scans.map((s) => (
                  <div
                    key={s.id}
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      fontSize: 13.5,
                      padding: "0.5rem 0",
                      borderBottom: "0.5px solid var(--border)",
                    }}
                  >
                    <span style={{ fontFamily: "var(--font-mono)" }}>{s.target}</span>
                    <span className="chip ok">{s.estado}</span>
                    <span style={{ color: "var(--text-secondary)" }}>{s.total_hallazgos} hallazgo(s)</span>
                    <span style={{ color: "var(--text-muted)" }}>{new Date(s.created_at).toLocaleString("es-CO")}</span>
                    {s.estado === "completado" && (
                      <div style={{ display: "flex", gap: 6 }}>
                        <button
                          onClick={() => verScan(s.id)}
                          disabled={viendoScan === s.id}
                          style={{ fontSize: 12, padding: "0.3rem 0.6rem" }}
                        >
                          {viendoScan === s.id ? "…" : "Ver"}
                        </button>
                        <button
                          onClick={() => descargarScan(s.id, "pdf")}
                          disabled={descargando === `${s.id}-pdf`}
                          style={{ fontSize: 12, padding: "0.3rem 0.6rem" }}
                        >
                          {descargando === `${s.id}-pdf` ? "…" : "PDF"}
                        </button>
                        <button
                          onClick={() => descargarScan(s.id, "docx")}
                          disabled={descargando === `${s.id}-docx`}
                          style={{ fontSize: 12, padding: "0.3rem 0.6rem" }}
                        >
                          {descargando === `${s.id}-docx` ? "…" : "DOCX"}
                        </button>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="card" style={{ marginTop: "1.25rem" }}>
            <h2 style={{ fontSize: 17, marginBottom: 4 }}>Comparar escaneos en el tiempo</h2>
            <p style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 16 }}>
              Hallazgos por escaneo completado, agrupados por severidad.
            </p>
            <ScanHistoryChart scans={scans} findings={findings} />
          </div>

          <div className="card" style={{ marginTop: "1.25rem" }}>
            <h2 style={{ fontSize: 17, marginBottom: 4 }}>Reporte de cumplimiento</h2>
            <p style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 12 }}>
              Evidencia acumulada de todo tu historial de escaneos, mapeada a ISO 27001 y Ley 2573.
            </p>
            <div style={{ display: "flex", gap: 8 }}>
              <button className="primary" onClick={verCumplimiento} disabled={cargandoCumpl}>
                {cargandoCumpl ? "Generando…" : "Ver en pantalla"}
              </button>
              <button onClick={() => descargarCumplimiento("pdf")} disabled={descargando === "cumplimiento-pdf"}>
                {descargando === "cumplimiento-pdf" ? "Generando…" : "Descargar PDF"}
              </button>
              <button onClick={() => descargarCumplimiento("docx")} disabled={descargando === "cumplimiento-docx"}>
                {descargando === "cumplimiento-docx" ? "Generando…" : "Descargar DOCX"}
              </button>
            </div>
          </div>

          <div className="card" style={{ marginTop: "1.25rem" }}>
            <h2 style={{ fontSize: 17, marginBottom: 12 }}>Equipo</h2>
            <Equipo />
          </div>
        </>
      )}

      {scanVisto && (
        <div
          onClick={() => setScanVisto(null)}
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(16, 22, 28, 0.45)",
            display: "flex",
            alignItems: "flex-start",
            justifyContent: "center",
            padding: "3rem 1.5rem",
            overflowY: "auto",
            zIndex: 50,
          }}
        >
          <div
            className="card"
            onClick={(e) => e.stopPropagation()}
            style={{ maxWidth: 720, width: "100%", maxHeight: "85vh", overflowY: "auto" }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 4 }}>
              <h2 style={{ fontSize: 18 }}>Reporte del escaneo</h2>
              <button onClick={() => setScanVisto(null)} style={{ fontSize: 13 }}>Cerrar</button>
            </div>
            <p style={{ fontFamily: "var(--font-mono)", fontSize: 13, color: "var(--text-secondary)", marginBottom: 4 }}>
              {scanVisto.target}
            </p>
            <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
              <span className={`chip ${scanVisto.estado}`}>{scanVisto.estado}</span>
              <span style={{ fontSize: 13, color: "var(--text-muted)" }}>
                {scanVisto.findings.length} hallazgo(s)
              </span>
            </div>

            {scanVisto.findings.length > 0 && (
              <div style={{ overflowX: "auto", marginBottom: 18 }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                  <tbody>
                    {ordenarPorSev(scanVisto.findings).map((f) => (
                      <tr key={f.id} style={{ borderTop: "0.5px solid var(--border)" }}>
                        <td style={{ padding: "0.45rem 0.5rem" }}>
                          <span className={`chip ${f.severidad}`}>{SEV_LABEL[f.severidad] ?? f.severidad}</span>
                        </td>
                        <td style={{ padding: "0.45rem 0.5rem" }}>{f.tipo}</td>
                        <td style={{ padding: "0.45rem 0.5rem", fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--text-secondary)", wordBreak: "break-all" }}>
                          {f.endpoint || "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            <h3 style={{ fontSize: 14, marginBottom: 8 }}>Análisis técnico</h3>
            <pre
              style={{
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
                fontFamily: "var(--font-mono)",
                fontSize: 12.5,
                lineHeight: 1.7,
                background: "var(--bg-sunken)",
                padding: "0.9rem 1rem",
                borderRadius: 8,
                margin: 0,
              }}
            >
              {scanVisto.reporte_final || "Este escaneo no tiene un reporte técnico generado."}
            </pre>
          </div>
        </div>
      )}

      {cumplimiento && (
        <div
          onClick={() => setCumplimiento(null)}
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(16, 22, 28, 0.45)",
            display: "flex",
            alignItems: "flex-start",
            justifyContent: "center",
            padding: "3rem 1.5rem",
            overflowY: "auto",
            zIndex: 50,
          }}
        >
          <div
            className="card"
            onClick={(e) => e.stopPropagation()}
            style={{ maxWidth: 760, width: "100%", maxHeight: "85vh", overflowY: "auto" }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 4 }}>
              <h2 style={{ fontSize: 19 }}>Reporte de cumplimiento</h2>
              <button onClick={() => setCumplimiento(null)} style={{ fontSize: 13 }}>Cerrar</button>
            </div>
            <p style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 18 }}>
              ISO 27001 · Ley 2573 de 2026 — evidencia acumulada de {cumplimiento.total_hallazgos} hallazgo(s).
              Generado {new Date(cumplimiento.generado_en).toLocaleString("es-CO")}.
            </p>

            <h3 style={{ fontSize: 15, marginBottom: 10 }}>Hallazgos por categoría normativa</h3>
            {cumplimiento.resumen_por_categoria.length === 0 ? (
              <p style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 18 }}>
                Sin hallazgos clasificados todavía.
              </p>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 12, marginBottom: 22 }}>
                {cumplimiento.resumen_por_categoria.map((c) => (
                  <div
                    key={c.categoria}
                    style={{ padding: "0.9rem 1rem", background: "var(--bg-sunken)", borderRadius: 8 }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
                      <strong style={{ fontSize: 14.5 }}>{c.nombre}</strong>
                      <span className="chip high">{c.cantidad}</span>
                    </div>
                    <p style={{ fontSize: 13, color: "var(--text-secondary)", margin: "6px 0 10px" }}>{c.explicacion}</p>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                      {c.iso27001.map((iso) => (
                        <span key={iso} className="chip ok" style={{ fontSize: 10.5 }}>ISO {iso}</span>
                      ))}
                      {c.ley2573_obligaciones.map((o) => (
                        <span key={o.id} className="chip verificado" style={{ fontSize: 10.5 }} title={o.texto}>
                          Ley 2573 · {o.id}
                        </span>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            )}

            <h3 style={{ fontSize: 15, marginBottom: 10 }}>Cobertura Ley 2573 de 2026</h3>
            <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 22 }}>
              {Object.entries(cumplimiento.cobertura_ley2573).map(([oid, info]) => {
                const cubierta = info.categorias_relacionadas.length > 0;
                return (
                  <div key={oid} style={{ display: "flex", gap: 10, alignItems: "flex-start", fontSize: 13 }}>
                    <span className={`chip ${cubierta ? "verificado" : "info"}`} style={{ flexShrink: 0, marginTop: 2 }}>
                      {cubierta ? "con evidencia" : "sin evidencia"}
                    </span>
                    <span style={{ color: "var(--text-secondary)" }}>
                      <strong style={{ color: "var(--text-primary)" }}>{oid}</strong> — {info.texto}
                    </span>
                  </div>
                );
              })}
            </div>

            <details>
              <summary style={{ cursor: "pointer", fontSize: 14, marginBottom: 8 }}>Ver reporte completo (markdown)</summary>
              <pre
                style={{
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                  fontFamily: "var(--font-mono)",
                  fontSize: 12,
                  lineHeight: 1.7,
                  background: "var(--bg-sunken)",
                  padding: "0.9rem 1rem",
                  borderRadius: 8,
                  marginTop: 8,
                }}
              >
                {cumplimiento.reporte_markdown}
              </pre>
            </details>

            <p style={{ fontSize: 11.5, color: "var(--text-muted)", marginTop: 18, lineHeight: 1.6 }}>
              {cumplimiento.advertencia_alcance_legal}
              <br />
              {cumplimiento.advertencia_iso27001}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
