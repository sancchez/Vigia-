const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8010";

export type VerificationInstructions = {
  dns_txt: { registro: string; tipo: string; valor: string };
  http_file: { url: string; contenido: string };
};

export type Asset = {
  id: string;
  tipo: "dominio" | "app" | "ip";
  valor: string;
  notas: string;
  is_active: boolean;
  created_at: string;
  verificado: boolean;
  verification_method: string | null;
  verified_at: string | null;
  exento_de_verificacion: boolean;
  instrucciones_verificacion: VerificationInstructions | null;
};

export type VerifyAssetResult = {
  verificado: boolean;
  detalle: string;
  verification_method: string | null;
  verified_at: string | null;
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

export type ActiveScanAccepted = {
  scan_id: string;
  estado: string;
};

export type ScanDetail = {
  id: string;
  target: string;
  estado: string;
  autorizacion_firmada: boolean;
  reporte_final: string | null;
  created_at: string;
  completed_at: string | null;
  findings: Finding[];
};

export type CumplimientoCategoria = {
  categoria: string;
  nombre: string;
  cantidad: number;
  iso27001: string[];
  ley2573_obligaciones: { id: string; texto: string }[];
  explicacion: string;
  ejemplos: { id?: string; endpoint?: string; severidad?: string; scan_id?: string }[];
};

export type CumplimientoReport = {
  reporte_markdown: string;
  resumen_por_categoria: CumplimientoCategoria[];
  cobertura_ley2573: Record<string, { texto: string; categorias_relacionadas: string[] }>;
  advertencia_alcance_legal: string;
  advertencia_iso27001: string;
  generado_en: string;
  total_hallazgos: number;
};

export type Me = {
  user_id: string;
  tenant_id: string;
  tenant_nombre: string;
  email: string;
  role: string;
  plan: string;
};

// Fase 2b (openspec/changes/fase2b-tablero-remediacion): los 8 valores del
// CHECK de `findings.estado` en el backend de `fase2a-ciclo-vida-hallazgo`
// (`TRANSICIONES_VALIDAS`, ver api/main.py).
export type EstadoHallazgo =
  | "descubierto"
  | "triage"
  | "asignado"
  | "en_progreso"
  | "mitigado"
  | "verificado"
  | "cerrado"
  | "aceptado_riesgo";

export type Finding = {
  id: string;
  scan_id: string;
  tipo: string;
  severidad: "critical" | "high" | "medium" | "low" | "info";
  endpoint: string;
  confirmado: boolean;
  created_at: string;
  // Fase 2b: ciclo de vida agregado por `fase2a-ciclo-vida-hallazgo`
  // (backend). Contrato REAL confirmado (`FindingOut`): `estado` es
  // requerido (siempre presente una vez desplegado fase2a) -- se mantiene
  // opcional aquí solo para tolerar una ventana de despliegue parcial donde
  // el backend viejo aún no lo expone (ver spec.md, "Degradación cuando GET
  // /findings no expone los campos nuevos"). `owner_email` es derivado
  // (LEFT JOIN users en el backend). `primera_vez_visto`/`ultima_vez_visto`
  // se tipan por completitud del contrato pero no se renderizan en este
  // change (fuera de alcance -- ver proposal.md, KPIs de MTTR quedan para
  // una fase de reporting posterior).
  estado?: EstadoHallazgo;
  owner_user_id?: string | null;
  owner_email?: string | null;
  sla_due_at?: string | null;
  primera_vez_visto?: string | null;
  ultima_vez_visto?: string | null;
};

// Fase 2b: contrato REAL de `FindingPatchIn` (fase2a) -- `nota` es
// requerida por el backend (min_length 1) SOLO cuando el body incluye
// `estado`; no lo es para un cambio de solo `owner_user_id`. El backend
// responde 409 si la transición de `estado` no es válida según
// `TRANSICIONES_VALIDAS`, 422 si falta `nota` al mandar `estado` (o el
// body viene vacío, o `owner_user_id` es de otro tenant), 404 si el
// finding no existe o es de otro tenant, 401 sin auth.
export type PatchFindingBody = {
  estado?: EstadoHallazgo;
  owner_user_id?: string | null;
  nota?: string;
};

// Fase 1 (openspec/changes/fase1-deteccion-visible): superset de `Finding`
// con el detalle curado que ya calculaba `agents/verificacion.py` pero
// nunca se exponía -- descripción/solución/CWE nativos de la herramienta,
// y el estado de verificación explicado (3 estados, no binario). Backend:
// GET /findings/{id} (api/main.py::FindingDetailOut).
export type FindingDetail = Finding & {
  descripcion: string;
  solucion: string;
  referencia: string;
  cwe: string | null;
  estado_verificacion: string | null;
  cves_detectados: string[];
  evidencia_exploitdb: { cve: string; edb_id: string; descripcion: string }[];
};

// Fase 2c (openspec/changes/fase2c-verificacion-cierre-y-kpis): resultado
// de `POST /findings/{id}/verificar-cierre` -- ver `VerificarCierreOut` en
// el backend. `estado` es el estado FINAL del hallazgo tras la llamada
// (puede no haber cambiado si `reaparece=true`, o si el hallazgo ya estaba
// en un estado terminal).
export type VerificarCierreResult = {
  finding_id: string;
  estado: EstadoHallazgo;
  reaparece: boolean;
  detalle: string;
};

// Fase 7 (openspec/changes/fase7-confianza-calidad): forma de `TraceEventOut`
// / `AuditEventOut` en el backend -- proyección de solo lectura de
// `scans.trace_log_json` (ya poblado por cada nodo del grafo desde antes de
// esta fase). Sin ningún campo de mutación/edición: la UI que los consume
// (`AuditLog.tsx`) es una línea de tiempo de solo lectura.
export type TraceEvent = {
  timestamp: string;
  agente: string;
  accion: string;
  resultado: string;
};

export type AuditEvent = TraceEvent & {
  scan_id: string;
  target: string;
};

// Contrato real de `GET /kpis` (`KpiOut` en el backend). `precision` en
// `mttr_por_severidad` es "exacto" hoy (fase2a persiste
// `finding_transiciones.created_at` real) -- se tipa como `string`, no un
// literal fijo, para no romper si un backend futuro degradara a
// "aproximado" ante la ausencia de esa tabla.
export type KpiSummary = {
  mttr_por_severidad: { severidad: string; mttr_dias: number | null; precision: string }[];
  porcentaje_dentro_sla: number | null;
  tendencia_por_activo: { asset_id: string; puntos: Record<string, unknown>[] }[];
};

export type Invitation = {
  id: string;
  email: string;
  role: string;
  estado: string;
  token: string;
  created_at: string;
  expires_at: string | null;
};

export type Member = {
  id: string;
  email: string;
  role: string;
  created_at: string;
};

export type InvitationPreview = {
  valido: boolean;
  email?: string | null;
  tenant_nombre?: string | null;
  role?: string | null;
  motivo?: string | null;
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
    // Header inofensivo fuera de un túnel (cualquier backend real lo ignora):
    // localtunnel muestra una página de aviso ante el primer fetch() de un
    // origen nuevo, y ese aviso solo se puede aceptar navegando -- un
    // fetch() de JS nunca lo ve, simplemente falla. Este header es el
    // mecanismo que localtunnel documenta para saltarlo desde código.
    "bypass-tunnel-reminder": "vigia",
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

async function downloadFile(path: string, filename: string): Promise<void> {
  const token = getToken();
  const headers: Record<string, string> = { "bypass-tunnel-reminder": "vigia" };
  if (token) headers.Authorization = `Bearer ${token}`;

  const res = await fetch(`${API_URL}${path}`, { headers });
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
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export const api = {
  register: (nombre_negocio: string, email: string, password: string, invite_token?: string) =>
    request<{ access_token: string }>("/auth/register", {
      method: "POST",
      body: JSON.stringify({ nombre_negocio, email, password, invite_token }),
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
  verifyAsset: (assetId: string, metodo: "dns_txt" | "http_file") =>
    request<VerifyAssetResult>(`/assets/${assetId}/verify`, {
      method: "POST",
      body: JSON.stringify({ metodo }),
    }),
  listScans: () => request<ScanHistoryItem[]>("/scans"),
  listFindings: () => request<Finding[]>("/findings"),
  getFinding: (id: string) => request<FindingDetail>(`/findings/${id}`),
  // Fase 2b: ver `PatchFindingBody` arriba para el contrato real (409
  // transición inválida, 422 falta nota/dueño inválido/body vacío, 404,
  // 401 -- todos propagados como `ApiError` con `status` + `message`).
  patchFinding: (id: string, body: PatchFindingBody) =>
    request<Finding>(`/findings/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  // Fase 2c: re-escanea solo el endpoint puntual del hallazgo; 422 si la
  // herramienta de origen no soporta verificación automática (semgrep/
  // trivy/grype), 503 si ZAP/nuclei no están disponibles, 404 cross-tenant.
  verificarCierre: (id: string) =>
    request<VerificarCierreResult>(`/findings/${id}/verificar-cierre`, { method: "POST" }),
  getKpis: () => request<KpiSummary>("/kpis"),
  runScan: (target: string, autorizacion_firmada: boolean) =>
    request<ScanResult>("/scan", {
      method: "POST",
      body: JSON.stringify({ target, autorizacion_firmada }),
    }),
  startActiveScan: (target_url: string, minutes = 10, ajax_spider = false) =>
    request<ActiveScanAccepted>("/scan/activo", {
      method: "POST",
      body: JSON.stringify({ target_url, minutes, ajax_spider, autorizacion_firmada: true }),
    }),
  getScan: (scanId: string) => request<ScanDetail>(`/scans/${scanId}`),
  // Fase 7: registro de auditoría de solo lectura sobre `trace_log`.
  getScanTrace: (scanId: string) => request<TraceEvent[]>(`/scans/${scanId}/trace`),
  getAudit: (limit?: number) =>
    request<AuditEvent[]>(`/audit${limit != null ? `?limit=${limit}` : ""}`),
  getCumplimiento: () => request<CumplimientoReport>("/reports/cumplimiento"),
  downloadCumplimiento: (formato: "pdf" | "docx") =>
    downloadFile(`/reports/cumplimiento/download?formato=${formato}`, `reporte-cumplimiento.${formato}`),
  downloadScanReport: (scanId: string, formato: "pdf" | "docx") =>
    downloadFile(`/scans/${scanId}/report/download?formato=${formato}`, `reporte-scan-${scanId.slice(0, 8)}.${formato}`),
  listMembers: () => request<Member[]>("/tenant/members"),
  listInvitations: () => request<Invitation[]>("/tenant/invitations"),
  createInvitation: (email: string, role: string) =>
    request<Invitation>("/tenant/invitations", { method: "POST", body: JSON.stringify({ email, role }) }),
  revokeInvitation: (id: string) => request<void>(`/tenant/invitations/${id}`, { method: "DELETE" }),
  previewInvitation: (token: string) => request<InvitationPreview>(`/tenant/invitations/preview/${token}`),
};

export { ApiError };
