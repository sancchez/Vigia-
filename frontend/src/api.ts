const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8010";

export type Asset = {
  id: string;
  tipo: "dominio" | "app" | "ip";
  valor: string;
  notas: string;
  is_active: boolean;
  created_at: string;
};

export type ScanHistoryItem = {
  id: string;
  target: string;
  estado: string;
  autorizacion_firmada: boolean;
  total_hallazgos: number;
  created_at: string;
  completed_at: string | null;
};

export type ScanResult = {
  target: string;
  autorizacion_firmada: boolean;
  autorizacion_bloqueo_motivo: string | null;
  verified_findings: Record<string, unknown>[];
  reporte_final: string | null;
};

export type Me = {
  user_id: string;
  tenant_id: string;
  tenant_nombre: string;
  email: string;
  role: string;
  plan: string;
};

export type Finding = {
  id: string;
  scan_id: string;
  tipo: string;
  severidad: "critical" | "high" | "medium" | "low" | "info";
  endpoint: string;
  confirmado: boolean;
  created_at: string;
};

class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function getToken(): string | null {
  return localStorage.getItem("vigia_token");
}

export function setToken(token: string | null) {
  if (token) localStorage.setItem("vigia_token", token);
  else localStorage.removeItem("vigia_token");
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string> | undefined),
  };
  if (token) headers.Authorization = `Bearer ${token}`;

  const res = await fetch(`${API_URL}${path}`, { ...options, headers });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      /* respuesta sin cuerpo JSON */
    }
    throw new ApiError(res.status, typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const api = {
  register: (nombre_negocio: string, email: string, password: string) =>
    request<{ access_token: string }>("/auth/register", {
      method: "POST",
      body: JSON.stringify({ nombre_negocio, email, password }),
    }),
  login: (email: string, password: string) =>
    request<{ access_token: string }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),
  me: () => request<Me>("/me"),
  listAssets: () => request<Asset[]>("/assets"),
  createAsset: (tipo: string, valor: string, notas: string) =>
    request<Asset>("/assets", { method: "POST", body: JSON.stringify({ tipo, valor, notas }) }),
  listScans: () => request<ScanHistoryItem[]>("/scans"),
  listFindings: () => request<Finding[]>("/findings"),
  runScan: (target: string, autorizacion_firmada: boolean) =>
    request<ScanResult>("/scan", {
      method: "POST",
      body: JSON.stringify({ target, autorizacion_firmada }),
    }),
};

export { ApiError };
