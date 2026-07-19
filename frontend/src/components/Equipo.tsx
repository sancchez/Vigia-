import { useEffect, useState, type FormEvent } from "react";
import { useAuth } from "../AuthContext";
import { api, ApiError, type Invitation, type Member } from "../api";

// Feature "invitar más usuarios al mismo tenant" (item transversal de
// HANDOFF.md). Sin envío de email real: el owner/admin comparte el link de
// invitación manualmente (copiar/pegar) — más simple y honesto que fingir
// un flujo de email sin proveedor configurado en el proyecto.

const ROLE_LABEL: Record<string, string> = { owner: "Owner", admin: "Admin", member: "Miembro" };

export default function Equipo() {
  const { me } = useAuth();
  const [members, setMembers] = useState<Member[]>([]);
  const [invitations, setInvitations] = useState<Invitation[]>([]);
  const [cargando, setCargando] = useState(true);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("member");
  const [enviando, setEnviando] = useState(false);
  const [mensaje, setMensaje] = useState<string | null>(null);
  const [ultimoLink, setUltimoLink] = useState<string | null>(null);

  const puedeInvitar = me?.role === "owner" || me?.role === "admin";

  const cargar = async () => {
    setCargando(true);
    try {
      const m = await api.listMembers();
      setMembers(m);
      if (puedeInvitar) {
        const i = await api.listInvitations();
        setInvitations(i);
      }
    } catch {
      /* silencioso: Dashboard ya maneja el 401 global de sesión expirada */
    } finally {
      setCargando(false);
    }
  };

  useEffect(() => {
    cargar();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const invitar = async (e: FormEvent) => {
    e.preventDefault();
    if (!email.trim()) return;
    setEnviando(true);
    setMensaje(null);
    try {
      const inv = await api.createInvitation(email.trim(), role);
      setUltimoLink(`${window.location.origin}/?invite=${inv.token}`);
      setMensaje(`Invitación creada para ${inv.email}. Comparte el link de abajo con esa persona.`);
      setEmail("");
      await cargar();
    } catch (err) {
      setMensaje(err instanceof ApiError ? err.message : "No se pudo crear la invitación.");
    } finally {
      setEnviando(false);
    }
  };

  const revocar = async (id: string) => {
    try {
      await api.revokeInvitation(id);
      await cargar();
    } catch (err) {
      setMensaje(err instanceof ApiError ? err.message : "No se pudo revocar la invitación.");
    }
  };

  if (cargando) return <p style={{ fontSize: 14, color: "var(--text-secondary)" }}>Cargando equipo…</p>;

  const pendientes = invitations.filter((i) => i.estado === "pendiente");

  return (
    <div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: puedeInvitar ? 16 : 0 }}>
        {members.map((m) => (
          <div
            key={m.id}
            style={{
              display: "flex",
              justifyContent: "space-between",
              fontSize: 13.5,
              padding: "0.5rem 0",
              borderBottom: "0.5px solid var(--border)",
            }}
          >
            <span>{m.email}</span>
            <span className="chip ok">{ROLE_LABEL[m.role] ?? m.role}</span>
          </div>
        ))}
      </div>

      {puedeInvitar && (
        <>
          <form onSubmit={invitar} style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 12 }}>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="correo@delinvitado.com"
              style={{ flex: 1 }}
              required
            />
            <select value={role} onChange={(e) => setRole(e.target.value)}>
              <option value="member">Miembro</option>
              <option value="admin">Admin</option>
            </select>
            <button className="primary" type="submit" disabled={enviando}>
              {enviando ? "Invitando…" : "Invitar"}
            </button>
          </form>

          {mensaje && <p style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 8 }}>{mensaje}</p>}
          {ultimoLink && (
            <div
              className="card"
              style={{ marginBottom: 12, fontSize: 12.5, fontFamily: "var(--font-mono)", wordBreak: "break-all" }}
            >
              {ultimoLink}
            </div>
          )}

          {pendientes.length > 0 && (
            <div>
              <h3 style={{ fontSize: 14, marginBottom: 8, color: "var(--text-secondary)" }}>Invitaciones pendientes</h3>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {pendientes.map((i) => (
                  <div
                    key={i.id}
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      fontSize: 13,
                      padding: "0.4rem 0",
                      borderBottom: "0.5px solid var(--border)",
                    }}
                  >
                    <span>{i.email}</span>
                    <span className="chip low">{ROLE_LABEL[i.role] ?? i.role}</span>
                    <button onClick={() => revocar(i.id)}>Revocar</button>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
