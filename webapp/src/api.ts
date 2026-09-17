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

/** Every skill name the matcher knows. The API rejects anything outside this
 *  list, so the Skills step suggests from it as you type rather than letting
 *  you spell a name it will refuse. Small enough (~170 names) to fetch whole
 *  and filter in the browser. */
export const listSkillVocabulary = () => api<{ skills: string[]; taxonomy_version: string }>("/api/v1/skills");

// --- profile --------------------------------------------------------------

export const SENIORITY_LEVELS = ["intern", "entry", "mid_senior", "senior", "staff_principal"] as const;
export type SeniorityLevel = (typeof SENIORITY_LEVELS)[number];
export type ScoreTable = Record<SeniorityLevel | "not_a_fit" | "unknown", number>;

/** You may aim at up to three levels at once — a senior engineer open to staff
 *  roles is aiming at both, and both should score full marks. */
export const MAX_SENIORITY_TARGETS = 3;

export type Profile = {
  version: number;
  seniority_targets: SeniorityLevel[];
  seniority_scores: ScoreTable | null;
  proposed_scores: ScoreTable;
  target_roles: string[] | null;
  note: string | null;
  resume_id: number | null;
};

export const getProfile = () => api<{ profile: Profile | null }>("/api/v1/me/profile");
export const pickLevels = (levels: SeniorityLevel[]) =>
  api<Profile>("/api/v1/me/profile/level", { method: "PUT", body: JSON.stringify({ levels }) });
export const confirmScores = (scores: ScoreTable) =>
  api<Profile>("/api/v1/me/profile/scores", { method: "PUT", body: JSON.stringify({ scores }) });

// --- board -----------------------------------------------------------------

export type Tracking = {
  applied: boolean;
  applied_at: string | null;
  applied_resume_version: string | null;
  not_interested: boolean;
  not_interested_note: string | null;
  note: string | null;
};

export type BoardJob = {
  id: number;
  job_id: string;
  title: string;
  company: string | null;
  location: string | null;
  workplace_type: string | null;
  posted_date: string | null;
  url: string;
  first_seen_at: string | null;
  level: SeniorityLevel | null;
  is_contract: boolean;
  scores: {
    skill_score: number | null;
    seniority_fit: number | null;
    total_score: number | null;
    skill_matched: string[] | null;
    skill_group_matched: string[] | null;
    skill_missing: string[] | null;
  } | null;
  expertise: {
    expertise_score: number;
    domain: number;
    capability: number;
    dream: number;
    evidence: Record<string, string | null> | null;
    stale: boolean;
  } | null;
  tracking: Tracking | null;
  company_applied_count: number;
};

export const listJobs = (days: number | null = 14, limit = 200, offset = 0) =>
  api<{ jobs: BoardJob[]; limit: number; offset: number; days: number | null }>(
    `/api/v1/jobs?limit=${limit}&offset=${offset}${days ? `&days=${days}` : ""}`,
  );

export const saveTracking = (jobId: number, changes: Partial<Tracking>) =>
  api<Tracking>(`/api/v1/me/tracking/${jobId}`, { method: "PUT", body: JSON.stringify(changes) });

// --- public (no sign-in) ----------------------------------------------------

export type PublicStats = {
  jobs: number;
  remote_jobs: number;
  collected_today: number;
  jobs_with_level: number;
  companies: number;
  last_collected_at: string | null;
};

/** The landing page's numbers. No token: this endpoint is public. */
export async function getPublicStats(fetchImpl = fetch): Promise<PublicStats> {
  const response = await fetchImpl(`${BASE}/api/public/stats`);
  if (!response.ok) throw new ApiError(response.status, "could not load the numbers");
  return response.json();
}

export type PublicJob = {
  title: string;
  company: string | null;
  url: string;
  industry: string | null;
  size: string | null;
  workplace_type: string | null;
  location: string | null;
  posted_date: string | null;
  first_seen_at: string | null;
  level: SeniorityLevel | null;
  is_contract: boolean | null;
};

/** Recent real postings for the landing page. No token: public facts only. */
export async function getRecentJobs(days = 14, limit = 8, fetchImpl = fetch): Promise<PublicJob[]> {
  const response = await fetchImpl(`${BASE}/api/public/recent-jobs?days=${days}&limit=${limit}`);
  if (!response.ok) throw new ApiError(response.status, "could not load recent jobs");
  return (await response.json()).jobs;
}
