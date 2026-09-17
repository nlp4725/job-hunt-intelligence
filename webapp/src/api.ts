/** The API client: every call carries the Cognito ID token. */

import { getIdToken, signOutLocally } from "./auth";

const BASE = import.meta.env.VITE_API_BASE;

export class ApiError extends Error {
  constructor(readonly status: number, message: string, readonly requestId?: string) {
    super(message);
  }
}

export async function api<T>(path: string, options: RequestInit = {}, fetchImpl = fetch): Promise<T> {
  const token = await getIdToken(fetchImpl);
  if (!token) throw new ApiError(401, "sign in required");
  const response = await fetchImpl(`${BASE}${path}`, {
    ...options,
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...options.headers,
      Authorization: `Bearer ${token}`,
    },
  });
  if (response.status === 401) {
    signOutLocally();
    throw new ApiError(401, "your session has expired");
  }
  if (response.status === 204) return undefined as T;
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new ApiError(response.status, data.error ?? `request failed (${response.status})`, data.request_id);
  }
  return data as T;
}

export type Onboarding = {
  resume: boolean;
  skills_confirmed: boolean;
  level: boolean;
  scores_confirmed: boolean;
  complete: boolean;
  board_scored: boolean;
  expertise: "locked" | "pending" | "skipped" | "done";
};

export type Me = {
  id: number;
  email: string;
  display_name: string | null;
  role: "user" | "admin";
  plan: "free" | "paid";
  onboarding: Onboarding;
};

export const getMe = () => api<Me>("/api/v1/me");

// --- resume ---------------------------------------------------------------

export type Resume = {
  id: number;
  version: number;
  filename: string | null;
  skills_extracted: string[] | null;
  skills_confirmed: string[] | null;
  uploaded_at: string | null;
};

type UploadTicket = { resume_id: number; version: number; upload: { url: string; fields: Record<string, string>; expires_in: number } };

/** Three steps: ask for a ticket, send the file straight to storage, then let
 *  the API read it. The file never passes through the API. */
export async function uploadResume(file: File, fetchImpl = fetch): Promise<Resume> {
  const ticket = await api<UploadTicket>("/api/v1/me/resume", { method: "POST", body: JSON.stringify({ filename: file.name }) }, fetchImpl);
  const form = new FormData();
  for (const [key, value] of Object.entries(ticket.upload.fields)) form.append(key, value);
  form.append("file", file);
  const stored = await fetchImpl(ticket.upload.url, { method: "POST", body: form });
  if (!stored.ok) throw new ApiError(stored.status, "the upload did not reach storage");
  return api<Resume>(`/api/v1/me/resume/${ticket.version}/complete`, { method: "POST" }, fetchImpl);
}

export const confirmSkills = (resumeId: number, skills: string[]) =>
  api<Resume>(`/api/v1/me/resume/${resumeId}/skills`, { method: "PUT", body: JSON.stringify({ skills }) });

export const listResumes = () => api<{ versions: Resume[]; active_resume_id: number | null }>("/api/v1/me/resume");

// --- profile --------------------------------------------------------------

export const SENIORITY_LEVELS = ["intern", "entry", "mid_senior", "senior", "staff_principal"] as const;
export type SeniorityLevel = (typeof SENIORITY_LEVELS)[number];
export type ScoreTable = Record<SeniorityLevel | "not_a_fit" | "unknown", number>;

export type Profile = {
  version: number;
  seniority_target: SeniorityLevel;
  seniority_scores: ScoreTable | null;
  proposed_scores: ScoreTable;
  target_roles: string[] | null;
  note: string | null;
  resume_id: number | null;
};

export const getProfile = () => api<{ profile: Profile | null }>("/api/v1/me/profile");
export const pickLevel = (level: SeniorityLevel) =>
  api<Profile>("/api/v1/me/profile/level", { method: "PUT", body: JSON.stringify({ level }) });
export const confirmScores = (scores: ScoreTable) =>
  api<Profile>("/api/v1/me/profile/scores", { method: "PUT", body: JSON.stringify({ scores }) });
