import { useEffect, useState, type FormEvent } from "react";
import { api, ApiError, type EventoNotificacion, type NotificationRule } from "../api";

// Fase 5 (openspec/changes/fase5-notificaciones-alertas): UI de reglas de
// notificación -- qué evento dispara un email a qué destino. Solo canal
// 'email' en v1 (Slack/webhook diferidos, ver proposal.md); el select de
// abajo no ofrece esas opciones a propósito, en vez de dejar que el backend
// las rechace con 422.

const EVENTO_LABEL: Record<EventoNotificacion, string> = {
  hallazgo_critico_nuevo: "Hallazgo crítico nuevo",
  dominio_clon_detectado: "Dominio clon detectado",
  sla_escalamiento: "Escalamiento de SLA (75% y vencido)",
};

const EVENTOS: EventoNotificacion[] = [
  "hallazgo_critico_nuevo",
  "dominio_clon_detectado",
  "sla_escalamiento",
];

export default function NotificationRules() {
  const [reglas, setReglas] = useState<NotificationRule[]>([]);
  const [cargando, setCargando] = useState(true);
  const [evento, setEvento] = useState<EventoNotificacion>("hallazgo_critico_nuevo");
  const [destino, setDestino] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [mensaje, setMensaje] = useState<string | null>(null);

  const cargar = async () => {
    setCargando(true);
    try {
      const r = await api.listNotificationRules();
      setReglas(r);
    } catch {
      /* silencioso: Dashboard ya maneja el 401 global de sesión expirada */
    } finally {
      setCargando(false);
    }
  };

  useEffect(() => {
    cargar();
  }, []);

  const crear = async (e: FormEvent) => {
    e.preventDefault();
    if (!destino.trim()) return;
    setEnviando(true);
    setMensaje(null);
    try {
      await api.createNotificationRule(evento, destino.trim());
      setDestino("");
      await cargar();
    } catch (err) {
      setMensaje(err instanceof ApiError ? err.message : "No se pudo crear la regla.");
    } finally {
      setEnviando(false);
    }
  };

  const alternar = async (regla: NotificationRule) => {
    try {
      await api.toggleNotificationRule(regla.id, !regla.is_active);
      await cargar();
    } catch (err) {
      setMensaje(err instanceof ApiError ? err.message : "No se pudo actualizar la regla.");
    }
  };

  const eliminar = async (id: string) => {
    try {
      await api.deleteNotificationRule(id);
      await cargar();
    } catch (err) {
      setMensaje(err instanceof ApiError ? err.message : "No se pudo eliminar la regla.");
    }
  };

  if (cargando) return <p style={{ fontSize: 14, color: "var(--text-secondary)" }}>Cargando reglas de notificación…</p>;

  return (
    <div>
      <p style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 12 }}>
        Configura a qué email avisar cuando aparece un hallazgo crítico, un posible dominio clon, o un
        hallazgo se acerca/vence su plazo de SLA. Solo email en esta versión.
      </p>

      {reglas.length === 0 ? (
        <p style={{ fontSize: 13.5, color: "var(--text-secondary)", marginBottom: 12 }}>
          Todavía no configuraste ninguna alerta.
        </p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 16 }}>
          {reglas.map((r) => (
            <div
              key={r.id}
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                fontSize: 13.5,
                padding: "0.5rem 0",
                borderBottom: "0.5px solid var(--border)",
              }}
            >
              <span>{EVENTO_LABEL[r.evento] ?? r.evento}</span>
              <span style={{ fontFamily: "var(--font-mono)", fontSize: 12.5 }}>{r.destino}</span>
              <span className={r.is_active ? "chip ok" : "chip low"}>
                {r.is_active ? "Activa" : "Inactiva"}
              </span>
              <div style={{ display: "flex", gap: 6 }}>
                <button onClick={() => alternar(r)}>{r.is_active ? "Desactivar" : "Activar"}</button>
                <button onClick={() => eliminar(r.id)}>Eliminar</button>
              </div>
            </div>
          ))}
        </div>
      )}

      <form onSubmit={crear} style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <select value={evento} onChange={(e) => setEvento(e.target.value as EventoNotificacion)}>
          {EVENTOS.map((ev) => (
            <option key={ev} value={ev}>
              {EVENTO_LABEL[ev]}
            </option>
          ))}
        </select>
        <input
          type="email"
          value={destino}
          onChange={(e) => setDestino(e.target.value)}
          placeholder="correo-destino@tuempresa.com"
          style={{ flex: 1 }}
          required
        />
        <button className="primary" type="submit" disabled={enviando}>
          {enviando ? "Guardando…" : "Agregar regla"}
        </button>
      </form>

      {mensaje && <p style={{ fontSize: 13, color: "var(--text-secondary)", marginTop: 8 }}>{mensaje}</p>}
    </div>
  );
}
