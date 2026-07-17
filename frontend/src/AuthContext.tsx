import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api, setToken, type Me } from "./api";

type AuthState = {
  me: Me | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (nombreNegocio: string, email: string, password: string) => Promise<void>;
  logout: () => void;
  refresh: () => Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = async () => {
    try {
      const data = await api.me();
      setMe(data);
    } catch {
      setToken(null);
      setMe(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (localStorage.getItem("vigia_token")) {
      refresh();
    } else {
      setLoading(false);
    }
  }, []);

  const login = async (email: string, password: string) => {
    const { access_token } = await api.login(email, password);
    setToken(access_token);
    await refresh();
  };

  const register = async (nombreNegocio: string, email: string, password: string) => {
    const { access_token } = await api.register(nombreNegocio, email, password);
    setToken(access_token);
    await refresh();
  };

  const logout = () => {
    setToken(null);
    setMe(null);
  };

  return (
    <AuthContext.Provider value={{ me, loading, login, register, logout, refresh }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth debe usarse dentro de <AuthProvider>");
  return ctx;
}
