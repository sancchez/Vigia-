import { useState } from "react";
import { api, ApiError, type Asset } from "../api";

// Fase 6c (openspec/changes/fase6-endurecimiento-produccion): wizard de
// onboarding de verificación de dominio. Frontend puro sobre la lógica que
// ya existe (`tools/asset_verification.py` vía `GET /assets` +
// `POST /assets/{id}/verify`, ver design.md Decisión 6c.1) -- este
// componente no reimplementa ninguna verificación, solo orquesta los
// mismos dos endpoints en un paso a paso más guiado que el bloque inline
// que había antes en `Dashboard.tsx`.

type Paso = "elegir" | "confirmar" | "listo";

type Props = {
  asset: Asset;
  onClose: () => void;
  /** Recarga la lista de activos del dashboard tras verificar con éxito. */
  onVerificado: () => Promise<void>;
  /** Lleva al usuario al siguiente paso de valor: autorizar escaneo activo. */
  onIrAEscanear: () => void;
};

export default function OnboardingWizard({ asset, onClose, onVerificado, onIrAEscanear }: Props) {
  const [paso, setPaso] = useState<Paso>("elegir");
  const [metodo, setMetodo] = useState<"dns_txt" | "http_file">("dns_txt");
  const [verificando, setVerificando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const instrucciones = asset.instrucciones_verificacion;

  const confirmarVerificacion = async () => {
    setVerificando(true);
    setError(null);
    try {
      const resultado = await api.verifyAsset(asset.id, metodo);
      if (resultado.verificado) {
        await onVerificado();
        setPaso("listo");
      } else {
        setError(resultado.detalle || "Todavía no detectamos el cambio. Revisa y volvé a intentar.");
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudo comprobar la verificación.");
    } finally {
      setVerificando(false);
    }
  };

  const pasos: { id: Paso; label: string }[] = [
    { id: "elegir", label: "1. Elegir método" },
    { id: "confirmar", label: "2. Publicar y confirmar" },
    { id: "listo", label: "3. Listo" },
  ];

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
        style={{ maxWidth: 560, width: "100%", maxHeight: "85vh", overflowY: "auto" }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 4 }}>
          <h2 style={{ fontSize: 18 }}>Verificar propiedad de {asset.valor}</h2>
          <button onClick={onClose} style={{ fontSize: 13 }}>Cerrar</button>
        </div>

        <div style={{ display: "flex", gap: 6, margin: "0.75rem 0 1.25rem" }}>
          {pasos.map((p) => {
            const activo = p.id === paso;
            const completado = pasos.findIndex((x) => x.id === paso) > pasos.findIndex((x) => x.id === p.id);
            return (
              <span
                key={p.id}
                style={{
                  fontSize: 11.5,
                  padding: "0.25rem 0.6rem",
                  borderRadius: 99,
                  border: activo ? "1px solid var(--accent)" : "0.5px solid var(--border)",
                  background: activo ? "var(--accent-bg)" : completado ? "var(--verified-bg)" : "transparent",
                  color: activo ? "var(--accent-ink)" : completado ? "var(--verified)" : "var(--text-secondary)",
                }}
              >
                {p.label}
              </span>
            );
          })}
        </div>

        {paso === "elegir" && instrucciones && (
          <>
            <p style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 14 }}>
              Demuestra que controlas <strong>{asset.valor}</strong> con uno de estos dos métodos.
              Elige el que te resulte más fácil de publicar.
            </p>
            <div style={{ display: "flex", flexDirection: "column", gap: 10, marginBottom: 16 }}>
              <label
                style={{
                  display: "flex",
                  gap: 10,
                  alignItems: "flex-start",
                  padding: "0.75rem 0.9rem",
                  borderRadius: 8,
                  background: metodo === "dns_txt" ? "var(--accent-bg)" : "var(--bg-sunken)",
                  border: metodo === "dns_txt" ? "1px solid var(--accent)" : "0.5px solid var(--border)",
                  cursor: "pointer",
                }}
              >
                <input
                  type="radio"
                  checked={metodo === "dns_txt"}
                  onChange={() => setMetodo("dns_txt")}
                  style={{ width: "auto", marginTop: 3 }}
                />
                <div style={{ fontSize: 13 }}>
                  <strong>Registro DNS TXT</strong> — recomendado si administras el DNS del dominio.
                  <p style={{ fontFamily: "var(--font-mono)", fontSize: 12, margin: "6px 0 0", wordBreak: "break-all", color: "var(--text-secondary)" }}>
                    Nombre: {instrucciones.dns_txt.registro}
                    <br />
                    Valor: {instrucciones.dns_txt.valor}
                  </p>
                </div>
              </label>
              <label
                style={{
                  display: "flex",
                  gap: 10,
                  alignItems: "flex-start",
                  padding: "0.75rem 0.9rem",
                  borderRadius: 8,
                  background: metodo === "http_file" ? "var(--accent-bg)" : "var(--bg-sunken)",
                  border: metodo === "http_file" ? "1px solid var(--accent)" : "0.5px solid var(--border)",
                  cursor: "pointer",
                }}
              >
                <input
                  type="radio"
                  checked={metodo === "http_file"}
                  onChange={() => setMetodo("http_file")}
                  style={{ width: "auto", marginTop: 3 }}
                />
                <div style={{ fontSize: 13 }}>
                  <strong>Archivo bien-conocido</strong> — recomendado si tienes acceso al servidor web.
                  <p style={{ fontFamily: "var(--font-mono)", fontSize: 12, margin: "6px 0 0", wordBreak: "break-all", color: "var(--text-secondary)" }}>
                    URL: {instrucciones.http_file.url}
                    <br />
                    Contenido: {instrucciones.http_file.contenido}
                  </p>
                </div>
              </label>
            </div>
            <button className="primary" onClick={() => setPaso("confirmar")} style={{ width: "100%" }}>
              Ya lo publiqué, continuar
            </button>
          </>
        )}

        {paso === "confirmar" && (
          <>
            <p style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 14 }}>
              Confirmamos ahora mismo, en vivo, que{" "}
              {metodo === "dns_txt" ? "el registro TXT" : "el archivo"} está publicado en{" "}
              <strong>{asset.valor}</strong>. Si acabas de publicarlo, la propagación DNS puede tardar
              unos minutos — está bien reintentar.
            </p>
            {error && (
              <div className="card" style={{ marginBottom: 14, fontSize: 13, background: "var(--alert-bg)", color: "var(--alert)" }}>
                {error}
              </div>
            )}
            <div style={{ display: "flex", gap: 8 }}>
              <button onClick={() => setPaso("elegir")} disabled={verificando}>
                Volver
              </button>
              <button className="primary" onClick={confirmarVerificacion} disabled={verificando} style={{ flex: 1 }}>
                {verificando ? "Comprobando…" : "Comprobar ahora"}
              </button>
            </div>
          </>
        )}

        {paso === "listo" && (
          <>
            <div className="card" style={{ marginBottom: 14, fontSize: 14, background: "var(--verified-bg)", color: "var(--verified)" }}>
              {asset.valor} quedó verificado. Ya puedes correr escaneos sobre este activo.
            </div>
            <p style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 14 }}>
              Próximo paso recomendado: autoriza el escaneo activo (firma la autorización de pruebas)
              para desbloquear el análisis completo, no solo el pasivo.
            </p>
            <div style={{ display: "flex", gap: 8 }}>
              <button onClick={onClose}>Cerrar</button>
              <button
                className="primary"
                style={{ flex: 1 }}
                onClick={() => {
                  onIrAEscanear();
                  onClose();
                }}
              >
                Ir a activar escaneo
              </button>
            </div>
          </>
        )}

        {paso === "elegir" && !instrucciones && (
          <p style={{ fontSize: 14, color: "var(--text-secondary)" }}>
            Este activo no tiene instrucciones de verificación pendientes (ya está verificado o
            exento).
          </p>
        )}
      </div>
    </div>
  );
}
