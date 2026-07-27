import { useState, type FormEvent } from "react";
import { api, ApiError, type Asset, type WatchedBrand } from "../api";

// Fase 3b (openspec/changes/fase3b-vigilancia-continua-certstream): alta/
// baja de marcas vigiladas -- alimenta `_build_variant_map()` en
// `api/certstream_listener.py` (ver spec.md "Alta y baja de marcas desde
// la UI"). Patrón `cargarTodo()` vía prop `onChanged`, igual que el resto
// del dashboard.

type Props = {
  brands: WatchedBrand[];
  // Se usa para ofrecer solo dominios propios YA verificados como respaldo de
  // la marca -- el backend exige uno (403 si no) desde el cierre del hueco de
  // propiedad de marca (ver api/main.py::_dominio_verificado_para_marca).
  assets: Asset[];
  onChanged: () => void;
};

function mensajeDeError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 422) return "La marca no puede estar vacía y necesita un dominio de respaldo.";
    if (err.status === 403) return err.message;
    if (err.status === 404) return "Esa marca ya no existe.";
    if (err.status === 401) return "Tu sesión expiró. Volvé a iniciar sesión.";
    return err.message;
  }
  return "No se pudo completar la operación (error de red).";
}

export default function WatchedBrandsConfig({ brands, assets, onChanged }: Props) {
  const [nuevaMarca, setNuevaMarca] = useState("");
  const [dominio, setDominio] = useState("");
  const [creando, setCreando] = useState(false);
  const [borrando, setBorrando] = useState<string | null>(null);
  const [mensaje, setMensaje] = useState<string | null>(null);

  const activas = brands.filter((b) => b.is_active);
  const dominiosDisponibles = assets.filter(
    (a) => a.tipo === "dominio" && a.is_active && (a.verificado || a.exento_de_verificacion),
  );

  const agregarMarca = async (e: FormEvent) => {
    e.preventDefault();
    if (!nuevaMarca.trim() || !dominio) return;
    setCreando(true);
    setMensaje(null);
    try {
      await api.createWatchedBrand(nuevaMarca.trim(), dominio);
      setNuevaMarca("");
      onChanged();
    } catch (err) {
      setMensaje(mensajeDeError(err));
    } finally {
      setCreando(false);
    }
  };

  const quitarMarca = async (id: string) => {
    setBorrando(id);
    setMensaje(null);
    try {
      await api.deleteWatchedBrand(id);
      onChanged();
    } catch (err) {
      setMensaje(mensajeDeError(err));
    } finally {
      setBorrando(null);
    }
  };

  return (
    <div className="card" style={{ marginBottom: "1.25rem" }}>
      <h2 style={{ fontSize: 17, marginBottom: 4 }}>Marcas vigiladas</h2>
      <p style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 12 }}>
        Además de tus dominios registrados, podés vigilar el nombre de tu marca — cualquier certificado SSL emitido
        para una permutación de estos nombres genera una alerta de vigilancia continua. Cada marca debe respaldarse
        con un dominio propio ya verificado: es la prueba de que la marca es tuya.
      </p>

      {mensaje && <p style={{ fontSize: 13.5, color: "var(--alert)", marginBottom: 10 }}>{mensaje}</p>}

      {activas.length === 0 ? (
        <p style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 12 }}>
          Todavía no vigilas ninguna marca.
        </p>
      ) : (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 12 }}>
          {activas.map((b) => (
            <span
              key={b.id}
              className="chip info"
              style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12.5 }}
            >
              {b.marca}
              {!b.verificada && (
                <span title="Marca sin dominio de respaldo verificado: no alimenta la vigilancia continua. Volvé a agregarla eligiendo un dominio.">
                  (sin verificar)
                </span>
              )}
              <button
                onClick={() => quitarMarca(b.id)}
                disabled={borrando === b.id}
                title="Dejar de vigilar esta marca"
                style={{ fontSize: 11, padding: "0.05rem 0.4rem" }}
              >
                {borrando === b.id ? "…" : "×"}
              </button>
            </span>
          ))}
        </div>
      )}

      {dominiosDisponibles.length === 0 ? (
        <p style={{ fontSize: 13.5, color: "var(--text-secondary)" }}>
          Para vigilar una marca necesitás primero un dominio propio registrado y verificado. Registralo y verificalo
          en la sección de activos.
        </p>
      ) : (
        <form onSubmit={agregarMarca} style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <input
            value={nuevaMarca}
            onChange={(e) => setNuevaMarca(e.target.value)}
            placeholder="nombre de tu marca (ej. bancoacme)"
            style={{ flex: 1, minWidth: 160 }}
          />
          <select
            value={dominio}
            onChange={(e) => setDominio(e.target.value)}
            title="Dominio propio verificado que respalda esta marca"
          >
            <option value="">dominio de respaldo…</option>
            {dominiosDisponibles.map((a) => (
              <option key={a.id} value={a.valor}>
                {a.valor}
              </option>
            ))}
          </select>
          <button className="primary" type="submit" disabled={creando || !nuevaMarca.trim() || !dominio}>
            {creando ? "Agregando…" : "Vigilar marca"}
          </button>
        </form>
      )}
    </div>
  );
}
