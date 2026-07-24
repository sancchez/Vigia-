import { useRef, useState } from "react";
import { api, ApiError, type HallazgoLogica, type RevisionIAResult } from "../api";

// Fase 4 (openspec/changes/fase4-revision-codigo-ia): UI de
// `POST /reports/revision-ia` -- subir código + los DOS consentimientos por
// separado (ver spec.md, Requirement "Doble consentimiento") + ver los
// hallazgos de LÓGICA DE NEGOCIO que `agents/revision_ia.py::revisar_codigo()`
// complementa a Semgrep. El botón de enviar permanece deshabilitado hasta
// que ambos checkboxes estén marcados -- la UI nunca deja intentar un envío
// que el backend va a rechazar con 403 de forma predecible.

const SEV_LABEL: Record<string, string> = {
  alta: "Alta",
  media: "Media",
  baja: "Baja",
};

const SEV_CHIP: Record<string, string> = {
  alta: "critical",
  media: "medium",
  baja: "low",
};

export default function RevisionIA() {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [archivos, setArchivos] = useState<File[]>([]);
  const [autorizacionFirmada, setAutorizacionFirmada] = useState(false);
  const [envioLLM, setEnvioLLM] = useState(false);
  const [contexto, setContexto] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [resultado, setResultado] = useState<RevisionIAResult | null>(null);

  const puedeEnviar = autorizacionFirmada && envioLLM && archivos.length > 0 && !enviando;

  const onSeleccionarArchivos = (e: React.ChangeEvent<HTMLInputElement>) => {
    setArchivos(Array.from(e.target.files ?? []));
  };

  const enviar = async () => {
    if (!puedeEnviar) return;
    setEnviando(true);
    setError(null);
    setResultado(null);
    try {
      const r = await api.revisarCodigo(archivos, autorizacionFirmada, envioLLM, contexto);
      setResultado(r);
    } catch (err) {
      if (err instanceof ApiError) {
        // 403 con motivo distinto por cada consentimiento faltante (ver
        // backend) -- se muestra tal cual, ya es un texto explicativo.
        setError(err.message);
      } else {
        setError("No se pudo completar la revisión de código.");
      }
    } finally {
      setEnviando(false);
    }
  };

  const limpiar = () => {
    setArchivos([]);
    setResultado(null);
    setError(null);
    if (inputRef.current) inputRef.current.value = "";
  };

  return (
    <div className="card" style={{ marginTop: "1.25rem" }}>
      <h2 style={{ fontSize: 17, marginBottom: 4 }}>Revisión de código con IA</h2>
      <p style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 14 }}>
        Complementa a Semgrep: encuentra fallas de <strong>lógica de negocio</strong> (control de
        acceso, aislamiento multi-tenant, confianza en datos del cliente) que un analizador
        sintáctico no puede detectar. Sube el código puntual a revisar (un módulo, un endpoint) —
        no un repositorio completo.
      </p>

      <div style={{ display: "flex", flexDirection: "column", gap: 10, marginBottom: 14 }}>
        <input ref={inputRef} type="file" multiple onChange={onSeleccionarArchivos} />
        {archivos.length > 0 && (
          <p style={{ fontSize: 12.5, color: "var(--text-secondary)" }}>
            {archivos.length} archivo(s) seleccionado(s): {archivos.map((f) => f.name).join(", ")}
          </p>
        )}
        <input
          value={contexto}
          onChange={(e) => setContexto(e.target.value)}
          placeholder="Contexto de negocio (opcional): ej. 'este módulo maneja pagos'"
        />
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 14 }}>
        <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13.5 }}>
          <input
            type="checkbox"
            checked={autorizacionFirmada}
            onChange={(e) => setAutorizacionFirmada(e.target.checked)}
            style={{ width: "auto" }}
          />
          Tengo autorización de pruebas firmada para analizar este código.
        </label>
        <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13.5 }}>
          <input
            type="checkbox"
            checked={envioLLM}
            onChange={(e) => setEnvioLLM(e.target.checked)}
            style={{ width: "auto" }}
          />
          Autorizo específicamente que el CONTENIDO de este código se envíe a un LLM de terceros
          (API de Anthropic) para su análisis.
        </label>
      </div>

      <div style={{ display: "flex", gap: 8 }}>
        <button className="primary" onClick={enviar} disabled={!puedeEnviar}>
          {enviando ? "Revisando…" : "Revisar código"}
        </button>
        {(archivos.length > 0 || resultado) && (
          <button onClick={limpiar} disabled={enviando}>
            Limpiar
          </button>
        )}
      </div>

      {error && (
        <p style={{ fontSize: 13, color: "var(--alert)", marginTop: 12 }}>{error}</p>
      )}

      {resultado && (
        <div style={{ marginTop: 18 }}>
          <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 10 }}>
            <h3 style={{ fontSize: 15 }}>Hallazgos de lógica de negocio</h3>
            <span style={{ fontSize: 12.5, color: "var(--text-secondary)" }}>
              {resultado.archivos_revisados.length} archivo(s) revisado(s)
              {resultado.archivos_omitidos.length > 0 && ` · ${resultado.archivos_omitidos.length} omitido(s)`}
            </span>
          </div>

          {resultado.hallazgos_logica.length === 0 ? (
            <p style={{ fontSize: 13.5, color: "var(--text-secondary)" }}>
              No se encontraron fallas de lógica de negocio en el código revisado.
            </p>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {resultado.hallazgos_logica.map((h: HallazgoLogica, i: number) => (
                <div key={i} style={{ padding: "0.8rem 0.9rem", background: "var(--bg-sunken)", borderRadius: 8 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8, marginBottom: 6 }}>
                    <span style={{ fontFamily: "var(--font-mono)", fontSize: 13 }}>
                      {h.archivo}
                      {h.linea_aprox != null ? `:${h.linea_aprox}` : ""}
                    </span>
                    <span className={`chip ${SEV_CHIP[h.severidad] ?? "info"}`}>
                      {SEV_LABEL[h.severidad] ?? h.severidad}
                    </span>
                  </div>
                  <p style={{ fontSize: 13.5, marginBottom: 6 }}>{h.descripcion}</p>
                  {h.evidencia && (
                    <pre
                      style={{
                        whiteSpace: "pre-wrap",
                        wordBreak: "break-word",
                        fontFamily: "var(--font-mono)",
                        fontSize: 12,
                        background: "var(--bg-base)",
                        padding: "0.5rem 0.6rem",
                        borderRadius: 6,
                        margin: "0 0 6px",
                      }}
                    >
                      {h.evidencia}
                    </pre>
                  )}
                  <p style={{ fontSize: 12.5, color: "var(--text-secondary)" }}>
                    <strong>Recomendación:</strong> {h.recomendacion}
                  </p>
                </div>
              ))}
            </div>
          )}

          {resultado.archivos_omitidos.length > 0 && (
            <details style={{ marginTop: 10 }}>
              <summary style={{ cursor: "pointer", fontSize: 12.5, color: "var(--text-secondary)" }}>
                Archivos omitidos ({resultado.archivos_omitidos.length})
              </summary>
              <ul style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 6 }}>
                {resultado.archivos_omitidos.map((o, i) => (
                  <li key={i}>{o}</li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
    </div>
  );
}
