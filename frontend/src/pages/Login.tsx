import { useEffect, useState, type FormEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useAuth } from "../AuthContext";
import { api, ApiError, type InvitationPreview } from "../api";

export default function Login() {
  const [searchParams] = useSearchParams();
  const inviteToken = searchParams.get("invite");

  const [modo, setModo] = useState<"login" | "registro">(inviteToken ? "registro" : "login");
  const [nombreNegocio, setNombreNegocio] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);
  const [invitacion, setInvitacion] = useState<InvitationPreview | null>(null);
  const { login, register } = useAuth();
  const navigate = useNavigate();

  useEffect(() => {
    if (!inviteToken) return;
    api
      .previewInvitation(inviteToken)
      .then((preview) => {
        setInvitacion(preview);
        if (preview.valido && preview.email) setEmail(preview.email);
      })
      .catch(() => setInvitacion({ valido: false, motivo: "No se pudo verificar la invitación." }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inviteToken]);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setEnviando(true);
    try {
      if (modo === "login") {
        await login(email, password);
      } else {
        await register(nombreNegocio, email, password, inviteToken ?? undefined);
      }
      navigate("/panel");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudo conectar con el servicio.");
    } finally {
      setEnviando(false);
    }
  };

  const esInvitacionValida = Boolean(inviteToken) && invitacion?.valido === true;

  return (
    <div
      style={{
        minHeight: "100%",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: "1.5rem",
      }}
    >
      <div className="card" style={{ width: "100%", maxWidth: 400 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: "1.5rem" }}>
          <span
            style={{
              width: 8,
              height: 8,
              borderRadius: "50%",
              background: "var(--accent)",
              display: "inline-block",
            }}
          />
          <span style={{ fontFamily: "var(--font-mono)", fontWeight: 500, letterSpacing: "0.06em" }}>
            VIGIA
          </span>
        </div>

        {inviteToken && invitacion === null && (
          <p style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: "1rem" }}>
            Verificando invitación…
          </p>
        )}
        {inviteToken && invitacion && !invitacion.valido && (
          <div
            style={{
              fontSize: 13,
              color: "var(--alert)",
              background: "var(--alert-bg)",
              padding: "0.6rem 0.8rem",
              borderRadius: 8,
              marginBottom: "1rem",
            }}
          >
            {invitacion.motivo ?? "Esta invitación no es válida."} Puedes crear una cuenta nueva abajo.
          </div>
        )}
        {esInvitacionValida && (
          <div
            style={{
              fontSize: 13,
              color: "var(--verified)",
              background: "var(--verified-bg)",
              padding: "0.6rem 0.8rem",
              borderRadius: 8,
              marginBottom: "1rem",
            }}
          >
            Te invitaron a unirte a <strong>{invitacion?.tenant_nombre}</strong> como {invitacion?.role}.
          </div>
        )}

        <h1 style={{ fontSize: 22, marginBottom: "0.35rem" }}>
          {modo === "login" ? "Ingresa a tu panel" : esInvitacionValida ? "Únete al equipo" : "Crea tu cuenta"}
        </h1>
        <p style={{ color: "var(--text-secondary)", fontSize: 14, marginBottom: "1.5rem" }}>
          {modo === "login"
            ? "Vigilancia de superficie de ataque para tu negocio."
            : esInvitacionValida
              ? "Crea tu contraseña para empezar a colaborar en este panel."
              : "Registra tu negocio para empezar a vigilar tus dominios."}
        </p>

        <form onSubmit={onSubmit} style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
          {modo === "registro" && !esInvitacionValida && (
            <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 13 }}>
              Nombre del negocio
              <input
                required
                value={nombreNegocio}
                onChange={(e) => setNombreNegocio(e.target.value)}
                placeholder="Mi Pyme S.A.S."
              />
            </label>
          )}
          <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 13 }}>
            Correo
            <input
              required
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="tucorreo@negocio.com"
              readOnly={esInvitacionValida}
            />
          </label>
          <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 13 }}>
            Contraseña
            <input
              required
              type="password"
              minLength={8}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="mínimo 8 caracteres"
            />
          </label>

          {error && (
            <div style={{ fontSize: 13, color: "var(--alert)", background: "var(--alert-bg)", padding: "0.5rem 0.75rem", borderRadius: 8 }}>
              {error}
            </div>
          )}

          <button className="primary" type="submit" disabled={enviando} style={{ marginTop: "0.5rem" }}>
            {enviando ? "Procesando…" : modo === "login" ? "Ingresar" : esInvitacionValida ? "Unirme" : "Crear cuenta"}
          </button>
        </form>

        {!esInvitacionValida && (
          <button
            onClick={() => {
              setError(null);
              setModo(modo === "login" ? "registro" : "login");
            }}
            style={{ marginTop: "1rem", width: "100%", background: "transparent", border: "none" }}
          >
            {modo === "login" ? "¿No tienes cuenta? Regístrate" : "¿Ya tienes cuenta? Ingresa"}
          </button>
        )}
      </div>
    </div>
  );
}
