import { useEffect, useRef, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../AuthContext";
import {
  api,
  ApiError,
  claseDeFinding,
  CLASE_EXPLICACION,
  CLASE_LABEL,
  esRiesgoReal,
  type Asset,
  type ClaseHallazgo,
  type Costos,
  type CumplimientoReport,
  type Finding,
  type Member,
  type ScanDetail,
  type ScanHistoryItem,
  type WatchedBrand,
} from "../api";
import ScanHistoryChart from "../components/ScanHistoryChart";
import Equipo from "../components/Equipo";
import NotificationRules from "../components/NotificationRules";
import FindingDetailPanel from "../components/FindingDetailPanel";
import RemediationBoard from "../components/RemediationBoard";
import KpiWidget from "../components/KpiWidget";
import RevisionIA from "../components/RevisionIA";
import DmarcPanel from "../components/DmarcPanel";
import VigilanciaPanel from "../components/VigilanciaPanel";
import WatchedBrandsConfig from "../components/WatchedBrandsConfig";
import BrandProtectionPanel from "../components/BrandProtectionPanel";
import OnboardingWizard from "../components/OnboardingWizard";
import AuditLog from "../components/AuditLog";

const PLAN_LABEL: Record<string, string> = {
  trial: "Prueba",
  costero: "Costero",
  flota: "Flota",
  armada: "Armada",
};

/** Nivel de riesgo del panel.
 *
 * Fase 7 — antes de este cambio contaba críticos/altos SIN distinguir clase,
 * y por eso un escaneo baseline de ZAP contra una app normal pintaba el panel
 * de rojo con cabeceras faltantes. Ahora solo mira las clases de RIESGO REAL
 * (`vulnerabilidad_explotable`/`amenaza_externa`): la higiene de
 * configuración sigue completamente visible más abajo (agrupada y colapsada,
 * nunca borrada), pero no infla la cifra que el cliente lee primero.
 *
 * `findings` ya llega filtrado a hallazgos abiertos por el llamador.
 */
function nivelDeRiesgo(findings: Finding[]): { nivel: "verde" | "amarillo" | "rojo"; texto: string } {
  const reales = findings.filter(esRiesgoReal);
  const criticos = reales.filter((f) => f.severidad === "critical").length;
  const altos = reales.filter((f) => f.severidad === "high").length;
  const higiene = findings.length - reales.length;
  const colaHigiene =
    higiene > 0 ? ` Además hay ${higiene} punto(s) de higiene de configuración, listados abajo.` : "";
  if (criticos > 0)
    return {
      nivel: "rojo",
      texto: `Atención inmediata: hay ${criticos} vulnerabilidad(es) crítica(s) explotable(s).${colaHigiene}`,
    };
  if (altos > 0)
    return {
      nivel: "amarillo",
      texto: `Hay ${altos} vulnerabilidad(es) de severidad alta por revisar.${colaHigiene}`,
    };
  if (reales.length > 0)
    return {
      nivel: "amarillo",
      texto: `Sin nada crítico ni alto. Quedan ${reales.length} hallazgo(s) de riesgo real de menor severidad.${colaHigiene}`,
    };
  return {
    nivel: "verde",
    texto: `No encontramos ninguna vulnerabilidad explotable en este momento.${colaHigiene}`,
  };
}

// Fase 7: orden en que se muestran los grupos del bloque colapsado.
const CLASES_SECUNDARIAS: ClaseHallazgo[] = ["higiene_configuracion", "informativo"];

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
  // Fase 2b (openspec/changes/fase2b-tablero-remediacion): dueños posibles
  // para el tablero de remediación. Se pide por fuera del `Promise.all`
  // crítico de abajo (ver `cargarTodo`) para no tumbar el resto del
  // dashboard si `/tenant/members` responde 403 a un rol sin acceso.
  const [members, setMembers] = useState<Member[]>([]);
  // Fase 3b (openspec/changes/fase3b-vigilancia-continua-certstream): marcas
  // vigiladas del tenant, mismo patrón que `members` -- se piden por fuera
  // del `Promise.all` crítico para no tumbar el resto del dashboard si el
  // endpoint no está disponible en un despliegue parcial.
  const [watchedBrands, setWatchedBrands] = useState<WatchedBrand[]>([]);
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
  // Fase 6c (openspec/changes/fase6-endurecimiento-produccion): activo cuyo
  // wizard de onboarding de verificación está abierto (null = cerrado).
  // Reemplaza el bloque inline de expandir/verificar que había acá antes --
  // ver `components/OnboardingWizard.tsx`, misma lógica de backend
  // (`GET /assets` + `POST /assets/{id}/verify`), solo UI más guiada.
  const [wizardAsset, setWizardAsset] = useState<Asset | null>(null);
  // Fase 1 (openspec/changes/fase1-deteccion-visible): id del hallazgo cuyo
  // panel de detalle está abierto (null = cerrado). Compartido por ambas
  // tablas de hallazgos (general y detalle de scan).
  const [findingIdAbierto, setFindingIdAbierto] = useState<string | null>(null);
  // Fase 3a (openspec/changes/fase3a-antisuplantacion-y-scoring): estado del
  // botón "Proteger marca" por asset (`valor` del asset corriendo, o null),
  // y un contador que fuerza a `BrandProtectionPanel` a refrescar sus datos
  // tras una corrida nueva (mismo patrón que `onChanged` de RemediationBoard,
  // pero como prop simple en vez de callback porque el panel se auto-alimenta).
  const [protegiendoMarca, setProtegiendoMarca] = useState<string | null>(null);
  const [refrescarProteccion, setRefrescarProteccion] = useState(0);
  // Fase 6a (openspec/changes/fase6-endurecimiento-produccion): programación
  // de escaneo por activo (frecuencia/pausa) y widget de costo de LLM.
  const [costos, setCostos] = useState<Costos | null>(null);
  const [frecuenciaEditando, setFrecuenciaEditando] = useState<Record<string, string>>({});
  const [guardandoProgramacion, setGuardandoProgramacion] = useState<string | null>(null);
  // Ancla para "Ir a activar escaneo" al cerrar el wizard con éxito -- el
  // formulario de abajo ya trae el checkbox de autorización de pruebas.
  const formAutorizacionRef = useRef<HTMLFormElement>(null);

  const cargarTodo = async () => {
    setCargando(true);
    let sigueAutenticado = true;
    try {
      const [a, s, f] = await Promise.all([api.listAssets(), api.listScans(), api.listFindings()]);
      setAssets(a);
      setScans(s);
      setFindings(f);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        sigueAutenticado = false;
        logout();
        navigate("/");
      }
    } finally {
      setCargando(false);
    }
    // Deliberadamente fuera del Promise.all de arriba: si `/tenant/members`
    // no es accesible para el rol actual (ver design.md de fase2b, Open
    // Question sobre roles), el tablero de remediación degrada su filtro
    // de dueño en vez de dejar todo el dashboard sin datos.
    if (sigueAutenticado) {
      try {
        setMembers(await api.listMembers());
      } catch {
        setMembers([]);
      }
      try {
        setWatchedBrands(await api.listWatchedBrands());
      } catch {
        setWatchedBrands([]);
      }
      try {
        setCostos(await api.getCostos());
      } catch {
        setCostos(null);
      }
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

  const irAEscanear = () => {
    formAutorizacionRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
    setMensaje('Marca "Autorización de pruebas firmada" y presiona "Escanear ahora" para el análisis completo.');
  };

  // Fase 6a: guarda frecuencia (horas, vacío = usar el intervalo global) y/o
  // pausa del ciclo recurrente para un activo. `pausar` llega explícito
  // (true/false) en vez de leerse de un estado local porque el checkbox de
  // pausa dispara el guardado inmediatamente al togglearse.
  const guardarProgramacion = async (asset: Asset, pausar?: boolean) => {
    setGuardandoProgramacion(asset.id);
    setMensaje(null);
    try {
      const texto = frecuenciaEditando[asset.id];
      const body: { scan_frequency_hours?: number | null; scan_paused?: boolean } = {};
      if (texto !== undefined) {
        body.scan_frequency_hours = texto.trim() === "" ? null : Number(texto);
      }
      if (pausar !== undefined) {
        body.scan_paused = pausar;
      }
      await api.updateAssetSchedule(asset.id, body);
      await cargarTodo();
    } catch (err) {
      setMensaje(err instanceof ApiError ? err.message : "No se pudo guardar la programación del escaneo.");
    } finally {
      setGuardandoProgramacion(null);
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

  // Fase 3a: dispara SOLO la rama anti-suplantación sobre un asset dominio
  // ya verificado (o exento) -- síncrono (segundos, no los minutos de un
  // escaneo activo ZAP), así que no hace falta el patrón de polling de
  // `correrScan` de arriba.
  const protegerMarca = async (asset: Asset) => {
    setProtegiendoMarca(asset.valor);
    setMensaje(null);
    try {
      const resultado = await api.runAntisuplantacion(asset.id);
      setMensaje(
        `${asset.valor}: protección de marca ejecutada — ${resultado.dominios.length} dominio(s) clon y ` +
          `${resultado.perfiles.length} perfil(es) detectados.`
      );
      setRefrescarProteccion((n) => n + 1);
    } catch (err) {
      setMensaje(err instanceof ApiError ? err.message : "No se pudo ejecutar la protección de marca.");
    } finally {
      setProtegiendoMarca(null);
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

  // Fase 6c: export ejecutivo consolidado (postura por activo + KPIs de
  // `GET /kpis`), mismo patrón de descarga que `descargarCumplimiento`.
  const descargarEjecutivo = async (formato: "pdf" | "docx") => {
    const clave = `ejecutivo-${formato}`;
    setDescargando(clave);
    setMensaje(null);
    try {
      await api.downloadEjecutivo(formato);
    } catch (err) {
      setMensaje(err instanceof ApiError ? err.message : "No se pudo descargar el reporte ejecutivo.");
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
  // Fase 7: el eje principal del panel pasa a ser la CLASE. `findingsReales`
  // es lo que lidera (vulnerabilidades explotables + suplantación de marca);
  // `findingsSecundarios` es higiene/informativo, que sigue estando entero y
  // auditable pero agrupado y colapsado más abajo.
  const findingsReales = findings.filter(esRiesgoReal);
  const findingsSecundarios = findings.filter((f) => !esRiesgoReal(f));
  // Los conteos de severidad de la cabecera y de la barra de distribución se
  // calculan SOBRE `findingsReales`, no sobre el total -- ese era exactamente
  // el número que inflaba la higiene de configuración.
  const porSeveridad = (sev: string) => findingsReales.filter((f) => f.severidad === sev).length;
  const ordenarPorSev = (lista: Finding[]) =>
    [...lista].sort(
      (a, b) => SEV_ORDEN.indexOf(a.severidad as (typeof SEV_ORDEN)[number]) - SEV_ORDEN.indexOf(b.severidad as (typeof SEV_ORDEN)[number])
    );
  const findingsFiltrados = ordenarPorSev(
    findingsReales.filter((f) => filtroSev === "todos" || f.severidad === filtroSev)
  );
  const secundariosPorClase = CLASES_SECUNDARIAS.map((clase) => ({
    clase,
    lista: ordenarPorSev(findingsSecundarios.filter((f) => claseDeFinding(f) === clase)),
  })).filter((grupo) => grupo.lista.length > 0);

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
            <div style={{ display: "flex", flexDirection: "column", gap: 6, alignItems: "flex-end" }}>
              <div style={{ display: "flex", gap: 8 }}>
                <span className="chip critical">{porSeveridad("critical")} críticos</span>
                <span className="chip high">{porSeveridad("high")} altos</span>
                <span className="chip low">{porSeveridad("medium") + porSeveridad("low")} otros</span>
              </div>
              {/* Fase 7: la cifra de higiene se muestra, pero deliberadamente
                  en segundo plano y por separado -- nunca sumada a los chips
                  de arriba. Es la diferencia entre "tenés 97 problemas" y
                  "tenés 2 problemas y 95 mejoras pendientes". */}
              <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
                {findingsReales.length} de riesgo real · {findingsSecundarios.length} de higiene/informativos
              </span>
            </div>
          </div>

          {findingsReales.length > 0 && (
            <div className="card" style={{ marginBottom: "1.25rem" }}>
              <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 10 }}>
                <h2 style={{ fontSize: 17 }}>Distribución por severidad</h2>
                <span style={{ fontSize: 13, color: "var(--text-secondary)" }}>
                  {findingsReales.length} hallazgo(s) de riesgo real
                </span>
              </div>
              <div style={{ display: "flex", height: 14, borderRadius: 99, overflow: "hidden", background: "var(--bg-sunken)" }}>
                {SEV_ORDEN.map((sev) => {
                  const n = porSeveridad(sev);
                  if (n === 0) return null;
                  return (
                    <div
                      key={sev}
                      title={`${SEV_LABEL[sev]}: ${n}`}
                      style={{ width: `${(n / findingsReales.length) * 100}%`, background: SEV_BAR_COLOR[sev] }}
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
                          <button onClick={() => setWizardAsset(a)}>
                            Verificar propiedad
                          </button>
                        )}
                        <button
                          onClick={() => correrScan(a.valor)}
                          disabled={escaneando === a.valor || !puedeEscanear}
                          title={puedeEscanear ? undefined : "Verifica la propiedad del dominio antes de escanear"}
                        >
                          {escaneando === a.valor ? "Escaneando…" : "Escanear ahora"}
                        </button>
                        {a.tipo === "dominio" && (
                          <button
                            onClick={() => protegerMarca(a)}
                            disabled={protegiendoMarca === a.valor || !puedeEscanear}
                            title={
                              puedeEscanear
                                ? "Busca dominios clon (dnstwist) y perfiles falsos (Sherlock) de este dominio"
                                : "Verifica la propiedad del dominio antes de proteger la marca"
                            }
                          >
                            {protegiendoMarca === a.valor ? "Protegiendo…" : "Proteger marca"}
                          </button>
                        )}
                      </div>
                    </div>

                    <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 8, fontSize: 12.5, color: "var(--text-secondary)" }}>
                      <span>Escaneo automático:</span>
                      <input
                        type="number"
                        min={1}
                        placeholder="horas (global)"
                        value={frecuenciaEditando[a.id] ?? (a.scan_frequency_hours != null ? String(a.scan_frequency_hours) : "")}
                        onChange={(e) => setFrecuenciaEditando((prev) => ({ ...prev, [a.id]: e.target.value }))}
                        style={{ width: 120, fontSize: 12.5, padding: "0.25rem 0.5rem" }}
                      />
                      <button
                        onClick={() => guardarProgramacion(a)}
                        disabled={guardandoProgramacion === a.id}
                        style={{ fontSize: 12, padding: "0.25rem 0.6rem" }}
                      >
                        {guardandoProgramacion === a.id ? "…" : "Guardar frecuencia"}
                      </button>
                      <label style={{ display: "flex", alignItems: "center", gap: 4 }}>
                        <input
                          type="checkbox"
                          checked={a.scan_paused}
                          disabled={guardandoProgramacion === a.id}
                          onChange={(e) => guardarProgramacion(a, e.target.checked)}
                          style={{ width: "auto" }}
                        />
                        Pausado
                      </label>
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

                  </div>
                );
              })}
            </div>
            <form ref={formAutorizacionRef} onSubmit={agregarDominio} style={{ display: "flex", gap: 8, alignItems: "center" }}>
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
              <h2 style={{ fontSize: 17 }}>Lo que hay que arreglar</h2>
              <span style={{ fontSize: 13, color: "var(--text-secondary)" }}>
                {findingsReales.length} de riesgo real · {porSeveridad("critical")} críticos · {porSeveridad("high")} altos
              </span>
            </div>
            {/* Fase 7: la explicación va acá arriba, antes de la tabla, en
                lenguaje de dueño de negocio -- es lo que hace que la separación
                se lea como honestidad y no como que escondimos hallazgos. */}
            <p style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 12, lineHeight: 1.6 }}>
              Esta tabla muestra solo lo que alguien podría usar para entrar a tus sistemas o para hacerse
              pasar por tu negocio. Los avisos de buenas prácticas que faltan están completos más abajo, en
              su propio bloque — no borramos nada, solo dejamos de mezclarlos con lo urgente.
            </p>

            <div style={{ display: "flex", gap: 6, marginBottom: 14, flexWrap: "wrap" }}>
              {(["todos", ...SEV_ORDEN] as const).map((sev) => {
                const activo = filtroSev === sev;
                const n = sev === "todos" ? findingsReales.length : porSeveridad(sev);
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
                  : findingsReales.length === 0
                    ? "No encontramos ninguna vulnerabilidad explotable ni suplantación de marca. Lo que detectamos es higiene de configuración: está listado abajo."
                    : "No hay hallazgos de riesgo real con esa severidad."}
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
                      <tr
                        key={f.id}
                        onClick={() => setFindingIdAbierto(f.id)}
                        style={{ borderTop: "0.5px solid var(--border)", cursor: "pointer" }}
                        title="Ver detalle del hallazgo"
                      >
                        <td style={{ padding: "0.5rem 0.5rem" }}>
                          <span className={`chip ${f.severidad}`}>{SEV_LABEL[f.severidad] ?? f.severidad}</span>
                        </td>
                        <td style={{ padding: "0.5rem 0.5rem" }}>
                          {f.tipo}
                          {/* Fase 7: dentro del bloque de riesgo real todavía
                              conviven dos cosas distintas (una falla EN tus
                              sistemas vs. alguien de afuera imitándote), así
                              que la clase se nombra fila por fila. */}
                          <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginTop: 2 }}>
                            {CLASE_LABEL[claseDeFinding(f)]}
                          </div>
                        </td>
                        <td style={{ padding: "0.5rem 0.5rem", fontFamily: "var(--font-mono)", fontSize: 12.5, color: "var(--text-secondary)", wordBreak: "break-all" }}>
                          {f.endpoint || "—"}
                        </td>
                        <td style={{ padding: "0.5rem 0.5rem" }}>
                          {/* GET /findings (esta tabla) sólo trae `confirmado` --
                              expuesto a propósito para no inflar el payload de la
                              lista con estado_verificacion/evidencia (ver design.md,
                              Decision "FindingDetailOut como modelo nuevo"). Cuando
                              confirmado=true SIEMPRE es porque estado_verificacion
                              == confirmado_via_exploitdb (ver agents/verificacion.py),
                              así que ese texto es exacto sin necesitar el detalle. El
                              caso false invita a abrir el panel para ver CUÁL de los
                              otros 2 estados aplica -- 3 estados explicados en vivo
                              en el panel (GET /findings/{id}), no aquí. */}
                          <span className={`chip ${f.confirmado ? "verificado" : "info"}`}>
                            {f.confirmado ? "confirmado vía Exploit-DB" : "sin confirmar — ver detalle"}
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

          {/* Fase 7 -- bloque de higiene/informativos: COLAPSADO por default,
              pero completo y auditable al abrirlo. La decisión de producto es
              deliberada: nada se borra ni se filtra del backend (GET /findings
              sigue devolviendo todo), simplemente deja de competir por la
              atención con lo que sí es una puerta abierta. Un `<details>`
              nativo en vez de estado en React: es un bloque de solo lectura,
              no necesita re-render ni sincronización. */}
          {secundariosPorClase.length > 0 && (
            <details className="card" style={{ marginBottom: "1.25rem" }}>
              <summary style={{ cursor: "pointer", listStyle: "revert" }}>
                <span style={{ fontSize: 17, fontWeight: 600 }}>
                  Higiene de configuración y avisos informativos
                </span>
                <span style={{ fontSize: 13, color: "var(--text-secondary)", marginLeft: 8 }}>
                  ({findingsSecundarios.length})
                </span>
              </summary>
              <p style={{ fontSize: 13, color: "var(--text-secondary)", margin: "12px 0", lineHeight: 1.6 }}>
                Esto no son puertas abiertas: son buenas prácticas que faltan. Vale la pena arreglarlas
                — suman defensa y suelen pedirlas las auditorías — pero nadie entra a tu sistema
                solo por esto, así que no cuentan para tu nivel de riesgo. Las dejamos acá completas
                para que puedas revisarlas y para que quede constancia de que las detectamos.
              </p>
              {secundariosPorClase.map((grupo) => (
                <div key={grupo.clase} style={{ marginTop: 16 }}>
                  <h3 style={{ fontSize: 14, marginBottom: 4 }}>
                    {CLASE_LABEL[grupo.clase]}{" "}
                    <span style={{ color: "var(--text-muted)", fontWeight: 400 }}>({grupo.lista.length})</span>
                  </h3>
                  <p style={{ fontSize: 12.5, color: "var(--text-muted)", marginBottom: 8, lineHeight: 1.5 }}>
                    {CLASE_EXPLICACION[grupo.clase]}
                  </p>
                  <div style={{ overflowX: "auto" }}>
                    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                      <thead>
                        <tr style={{ textAlign: "left", color: "var(--text-muted)", fontSize: 12 }}>
                          <th style={{ padding: "0.35rem 0.5rem" }}>Severidad</th>
                          <th style={{ padding: "0.35rem 0.5rem" }}>Tipo</th>
                          <th style={{ padding: "0.35rem 0.5rem" }}>Endpoint / Objetivo</th>
                        </tr>
                      </thead>
                      <tbody>
                        {grupo.lista.slice(0, 100).map((f) => (
                          <tr
                            key={f.id}
                            onClick={() => setFindingIdAbierto(f.id)}
                            style={{ borderTop: "0.5px solid var(--border)", cursor: "pointer" }}
                            title="Ver detalle del hallazgo"
                          >
                            <td style={{ padding: "0.45rem 0.5rem" }}>
                              <span className={`chip ${f.severidad}`}>{SEV_LABEL[f.severidad] ?? f.severidad}</span>
                            </td>
                            <td style={{ padding: "0.45rem 0.5rem" }}>{f.tipo}</td>
                            <td
                              style={{
                                padding: "0.45rem 0.5rem",
                                fontFamily: "var(--font-mono)",
                                fontSize: 12,
                                color: "var(--text-secondary)",
                                wordBreak: "break-all",
                              }}
                            >
                              {f.endpoint || "—"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    {grupo.lista.length > 100 && (
                      <p style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 8 }}>
                        Mostrando los primeros 100 de {grupo.lista.length}. Descargá el reporte para el detalle completo.
                      </p>
                    )}
                  </div>
                </div>
              ))}
            </details>
          )}

          <KpiWidget assets={assets} />

          <DmarcPanel />

          {costos && (
            <div className="card" style={{ marginBottom: "1.25rem" }}>
              <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 10 }}>
                <h2 style={{ fontSize: 17 }}>Costo de análisis con IA</h2>
                <span style={{ fontSize: 13, color: "var(--text-secondary)" }}>
                  {costos.total_costo_estimado != null ? `USD ${costos.total_costo_estimado.toFixed(4)}` : "sin datos medibles"} · {costos.total_llamadas} llamada(s)
                </span>
              </div>
              {costos.por_scan.length === 0 ? (
                <p style={{ fontSize: 14, color: "var(--text-secondary)" }}>Todavía no se ha registrado uso de IA.</p>
              ) : (
                <div style={{ overflowX: "auto" }}>
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                    <thead>
                      <tr style={{ textAlign: "left", color: "var(--text-muted)", fontSize: 12 }}>
                        <th style={{ padding: "0.3rem 0.5rem" }}>Scan</th>
                        <th style={{ padding: "0.3rem 0.5rem" }}>Llamadas</th>
                        <th style={{ padding: "0.3rem 0.5rem" }}>Tokens in/out</th>
                        <th style={{ padding: "0.3rem 0.5rem" }}>Costo estimado</th>
                      </tr>
                    </thead>
                    <tbody>
                      {costos.por_scan.slice(0, 20).map((c) => (
                        <tr key={c.scan_id ?? "sin-scan"} style={{ borderTop: "0.5px solid var(--border)" }}>
                          <td style={{ padding: "0.4rem 0.5rem", fontFamily: "var(--font-mono)", fontSize: 12 }}>
                            {c.scan_id ? c.scan_id.slice(0, 8) : "—"}
                          </td>
                          <td style={{ padding: "0.4rem 0.5rem" }}>{c.llamadas}</td>
                          <td style={{ padding: "0.4rem 0.5rem" }}>{c.tokens_in} / {c.tokens_out}</td>
                          <td style={{ padding: "0.4rem 0.5rem" }}>
                            {c.costo_estimado != null ? `USD ${c.costo_estimado.toFixed(4)}` : "no medible (CLI)"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <p style={{ fontSize: 11.5, color: "var(--text-muted)", marginTop: 10 }}>{costos.nota}</p>
            </div>
          )}

          {findings.length > 0 && (
            <RemediationBoard findings={findings} members={members} onChanged={cargarTodo} />
          )}

          <VigilanciaPanel findings={findings} members={members} onChanged={cargarTodo} />
          <WatchedBrandsConfig brands={watchedBrands} assets={assets} onChanged={cargarTodo} />
          <BrandProtectionPanel refreshKey={refrescarProteccion} />

          {/* Fase 4 (openspec/changes/fase4-revision-codigo-ia): sección
              independiente, sin acople con el resto del dashboard -- no
              depende de `findings`/`assets` para renderizarse. */}
          <RevisionIA />

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

          <AuditLog />

          <div className="card" style={{ marginTop: "1.25rem" }}>
            <h2 style={{ fontSize: 17, marginBottom: 4 }}>Export ejecutivo</h2>
            <p style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 12 }}>
              Postura consolidada de todos tus activos (hallazgos abiertos por severidad) más los
              KPIs de remediación (MTTR, % dentro de SLA) en un solo documento.
            </p>
            <div style={{ display: "flex", gap: 8 }}>
              <button onClick={() => descargarEjecutivo("pdf")} disabled={descargando === "ejecutivo-pdf"}>
                {descargando === "ejecutivo-pdf" ? "Generando…" : "Descargar PDF"}
              </button>
              <button onClick={() => descargarEjecutivo("docx")} disabled={descargando === "ejecutivo-docx"}>
                {descargando === "ejecutivo-docx" ? "Generando…" : "Descargar DOCX"}
              </button>
            </div>
          </div>

          <div className="card" style={{ marginTop: "1.25rem" }}>
            <h2 style={{ fontSize: 17, marginBottom: 12 }}>Equipo</h2>
            <Equipo />
          </div>

          <div className="card" style={{ marginTop: "1.25rem" }}>
            <h2 style={{ fontSize: 17, marginBottom: 12 }}>Alertas y notificaciones</h2>
            <NotificationRules />
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
              {/* Fase 7: el detalle de un escaneo sí lista TODO (es el registro
                  crudo de esa corrida), pero el encabezado ya separa la cuenta
                  para que el número grande no se lea como "N problemas". */}
              <span style={{ fontSize: 13, color: "var(--text-muted)" }}>
                {scanVisto.findings.filter(esRiesgoReal).length} de riesgo real ·{" "}
                {scanVisto.findings.filter((f) => !esRiesgoReal(f)).length} de higiene/informativos
              </span>
            </div>

            {scanVisto.findings.length > 0 && (
              <div style={{ overflowX: "auto", marginBottom: 18 }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                  <tbody>
                    {[...scanVisto.findings]
                      .sort(
                        (a, b) =>
                          Number(esRiesgoReal(b)) - Number(esRiesgoReal(a)) ||
                          SEV_ORDEN.indexOf(a.severidad as (typeof SEV_ORDEN)[number]) -
                            SEV_ORDEN.indexOf(b.severidad as (typeof SEV_ORDEN)[number])
                      )
                      .map((f) => (
                      <tr
                        key={f.id}
                        onClick={() => setFindingIdAbierto(f.id)}
                        style={{ borderTop: "0.5px solid var(--border)", cursor: "pointer" }}
                        title="Ver detalle del hallazgo"
                      >
                        <td style={{ padding: "0.45rem 0.5rem" }}>
                          <span className={`chip ${f.severidad}`}>{SEV_LABEL[f.severidad] ?? f.severidad}</span>
                        </td>
                        <td style={{ padding: "0.45rem 0.5rem" }}>
                          {f.tipo}
                          <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginTop: 2 }}>
                            {CLASE_LABEL[claseDeFinding(f)]}
                          </div>
                        </td>
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

      <FindingDetailPanel findingId={findingIdAbierto} onClose={() => setFindingIdAbierto(null)} />

      {wizardAsset && (
        <OnboardingWizard
          asset={wizardAsset}
          onClose={() => setWizardAsset(null)}
          onVerificado={cargarTodo}
          onIrAEscanear={irAEscanear}
        />
      )}
    </div>
  );
}
