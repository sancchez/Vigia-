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

/** Miniatura de la captura del sitio clon.
 *
 * `disponible` viene del backend (`DominioClonOut.screenshot` es `null` cuando
 * el hallazgo no tiene captura persistida). Antes este componente ofrecía
 * "Ver screenshot" en TODAS las filas y recién tras el fetch fallido mostraba
 * "Sin screenshot disponible" -- con N hallazgos sin captura eso son N
 * peticiones 404 y N mensajes con pinta de error. Ahora, si el backend ya dijo
 * que no hay captura, no se ofrece el botón y se explica el estado como lo que
 * es (falta de evidencia visual, no un fallo). */
function ScreenshotThumbnail({ findingId, disponible }: { findingId: string; disponible: boolean }) {
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

  if (!disponible || (intentado && !cargando)) {
    return (
      <span
        style={{ fontSize: 12, color: "var(--text-muted)", maxWidth: 160, display: "inline-block" }}
        title="La captura se toma cuando el sitio clon responde y el navegador headless está instalado en el servidor."
      >
        Sin captura del sitio
      </span>
    );
  }

  return (
    <button onClick={cargar} disabled={cargando} style={{ fontSize: 12 }}>
      {cargando ? "Cargando…" : "Ver screenshot"}
    </button>
  );
}

/** Chip de la etapa del ciclo de vida de marca. `etapa_marca` la calcula el
 * backend (`api/main.py::_etapa_marca`) a partir del `estado` genérico -- acá
 * solo se elige el color. Ver ese docstring para por qué no se muestra el
 * estado crudo ("En progreso" no le dice nada a quien mandó una carta a un
 * registrador). */
function claseEtapa(d: DominioClon): string {
  if (d.estado === "cerrado" || d.estado === "verificado") return "verificado";
  if (d.estado === "aceptado_riesgo") return "info";
  if (d.registrador_sin_respuesta > 0) return "high";
  return "info";
}

type Props = { refreshKey?: number };

export default function BrandProtectionPanel({ refreshKey }: Props) {
  const [dominios, setDominios] = useState<DominioClon[]>([]);
  const [kpis, setKpis] = useState<AntisuplantacionKpis | null>(null);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [copiado, setCopiado] = useState<string | null>(null);
  const [accionando, setAccionando] = useState<string | null>(null);

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

  /** Aplica una acción del ciclo de vida y refleja la fila devuelta por el
   * backend, sin re-fetchear toda la lista: la respuesta ES el `DominioClon`
   * actualizado (mismo contrato que `GET /antisuplantacion/dominios`), así que
   * refrescar entero solo agregaría latencia y un parpadeo. */
  const aplicarAccion = async (
    findingId: string,
    accion: (id: string) => Promise<DominioClon>,
  ) => {
    setAccionando(findingId);
    setError(null);
    try {
      const actualizado = await accion(findingId);
      setDominios((prev) => prev.map((d) => (d.finding_id === findingId ? actualizado : d)));
      // Los KPIs (TTTD) dependen de las transiciones que esta acción acaba de
      // escribir, así que sí hace falta refrescarlos.
      try {
        setKpis(await api.getAntisuplantacionKpis());
      } catch {
        /* el KPI es secundario -- no invalida la acción que sí se aplicó */
      }
    } catch (e) {
      const mensaje = e instanceof Error ? e.message : "acción rechazada";
      setError(`No se pudo registrar la acción: ${mensaje}`);
    } finally {
      setAccionando(null);
    }
  };

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
        <div style={{ fontSize: 14, color: "var(--text-secondary)" }}>
          <p style={{ marginBottom: 6 }}>
            Todavía no se detectaron dominios clon ni perfiles falsos. Usá "Proteger marca" sobre un dominio verificado
            para correr la búsqueda.
          </p>
          {/* Expectativa honesta: medido contra un dominio real con 45 lookalikes,
              la corrida bajo demanda supera los 15 minutos (ver la advertencia en
              `api/main.py::ejecutar_antisuplantacion`). Desde que el endpoint es
              asíncrono (202 + polling) ya no hay timeout del proxy que confundir
              con "no encontró nada", pero la espera real sigue existiendo. */}
          <p style={{ fontSize: 12.5, color: "var(--text-muted)" }}>
            La primera búsqueda sobre un dominio con muchas variantes puede tardar varios minutos: se verifica
            infraestructura y se captura evidencia de cada dominio sospechoso, con pausas deliberadas entre consultas.
            La corrida sigue en el servidor aunque cierres la pestaña — los hallazgos aparecen acá al terminar.
          </p>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          {dominios.map((d) => (
            <div key={d.finding_id} style={{ borderTop: "0.5px solid var(--border)", paddingTop: 10 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", marginBottom: 6 }}>
                <span style={{ fontFamily: "var(--font-mono)", fontSize: 13.5, wordBreak: "break-all" }}>
                  {d.endpoint}
                </span>
                <span className={`chip ${d.severidad}`}>{SEV_LABEL[d.severidad] ?? d.severidad}</span>
                {/* El score 0-4 solo tiene sentido sobre un dominio: un perfil de red
                    social no tiene MX ni certificado TLS propio, así que un "0/4" ahí
                    se leería como "inofensivo" cuando la métrica simplemente no aplica. */}
                {d.tipo !== "perfil_red_social" && (
                  <span className={`chip ${claseScore(d.score)}`} title="Señales de infraestructura activa: DNS-A + MX + SSL + HTTP">
                    score {d.score}/4
                  </span>
                )}
                {d.tipo === "perfil_red_social" && (
                  <span className="chip info" title="Perfil con el mismo nombre de marca. Sherlock confirma que existe, no que suplante — requiere revisión humana.">
                    perfil · revisión humana
                  </span>
                )}
                <span
                  className={`chip ${claseEtapa(d)}`}
                  title={`Estado interno del ciclo de vida: ${d.estado}`}
                >
                  {d.etapa_marca}
                </span>
              </div>

              {/* Acciones del ciclo de vida de marca. Antes de esto el panel
                  terminaba en "Copiar" el borrador: el cliente se llevaba la
                  carta y el producto no volvía a saber nada del caso, así que
                  TTTD nunca tenía datos y "¿y después qué?" no tenía respuesta
                  dentro de la herramienta. Solo se ofrecen sobre dominios: un
                  perfil de red social no se da de baja escribiéndole a un
                  registrador. */}
              {d.tipo !== "perfil_red_social" && (
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 8 }}>
                  {!d.takedown_enviado_en && (
                    <button
                      onClick={() => aplicarAccion(d.finding_id, (id) => api.marcarTakedownEnviado(id))}
                      disabled={accionando === d.finding_id}
                      style={{ fontSize: 11.5 }}
                      title="Registra que ya enviaste la solicitud al registrador. Arranca el reloj de TTTD."
                    >
                      {accionando === d.finding_id ? "Guardando…" : "Marcar takedown enviado"}
                    </button>
                  )}
                  {d.takedown_enviado_en && (
                    <button
                      onClick={() => aplicarAccion(d.finding_id, (id) => api.marcarSinRespuesta(id))}
                      disabled={accionando === d.finding_id}
                      style={{ fontSize: 11.5 }}
                      title="El registrador no contestó. Queda auditado para justificar el escalamiento."
                    >
                      {accionando === d.finding_id ? "Guardando…" : "Sin respuesta del registrador"}
                    </button>
                  )}
                  {d.takedown_enviado_en && (
                    <span style={{ fontSize: 11.5, color: "var(--text-muted)", alignSelf: "center" }}>
                      Enviado {new Date(d.takedown_enviado_en).toLocaleDateString()}
                      {d.takedown_destinatario ? ` a ${d.takedown_destinatario}` : ""}
                    </span>
                  )}
                </div>
              )}

              <div style={{ display: "flex", gap: 16, flexWrap: "wrap", alignItems: "flex-start" }}>
                {d.tipo !== "perfil_red_social" && (
                  <ScreenshotThumbnail findingId={d.finding_id} disponible={d.screenshot != null} />
                )}

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
