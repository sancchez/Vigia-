import { useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../AuthContext";
import { api, ApiError, type Asset, type Finding, type ScanHistoryItem } from "../api";
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

  const correrScan = async (valor: string) => {
    setEscaneando(valor);
    setMensaje(null);
    try {
      const resultado = await api.runScan(valor, autorizado);
      if (!autorizado) {
        setMensaje(
          `${valor}: escaneo pasivo completado. Para un análisis activo completo, confirma la autorización de pruebas antes de escanear.`
        );
      } else {
        setMensaje(`${valor}: escaneo completado — ${resultado.verified_findings.length} hallazgo(s).`);
      }
      await cargarTodo();
    } catch (err) {
      setMensaje(err instanceof ApiError ? err.message : "El escaneo no se pudo completar.");
    } finally {
      setEscaneando(null);
    }
  };

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

  if (!me) return null;

  const riesgo = nivelDeRiesgo(findings);
  const porSeveridad = (sev: string) => findings.filter((f) => f.severidad === sev).length;

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

          {mensaje && (
            <div className="card" style={{ marginBottom: "1.25rem", fontSize: 14 }}>
              {mensaje}
            </div>
          )}

          <div className="card" style={{ marginBottom: "1.25rem" }}>
            <h2 style={{ fontSize: 17, marginBottom: 12 }}>Dominios protegidos</h2>
            <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 12 }}>
              {assets.map((a) => (
                <div
                  key={a.id}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    padding: "0.6rem 0",
                    borderBottom: "0.5px solid var(--border)",
                  }}
                >
                  <div>
                    <span style={{ fontFamily: "var(--font-mono)", fontSize: 13.5 }}>{a.valor}</span>
                    <span className="chip ok" style={{ marginLeft: 8 }}>
                      {a.is_active ? "activo" : "inactivo"}
                    </span>
                  </div>
                  <button onClick={() => correrScan(a.valor)} disabled={escaneando === a.valor}>
                    {escaneando === a.valor ? "Escaneando…" : "Escanear ahora"}
                  </button>
                </div>
              ))}
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
    </div>
  );
}
