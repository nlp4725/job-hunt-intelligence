// Typed client for backend/app.py. Field names mirror the JSON exactly — the
// server is the contract, so nothing is renamed on the way in.

export type Track = "ml_ai" | "pm";

export type Job = {
  id: number;
  title: string | null;
  track: Track;
  company_name: string | null;
  company_applied_count: number;
  company_industry: string | null;
  company_size: string | null;
  top500_tech: boolean;
  location: string | null;
  workplace_type: string | null;
  url: string | null;
  posted_date_raw: string | null;
  posted_at: string | null;
  skill_score: number | null;
  seniority_score: number | null;
  expertise_score: number | null;
  to_c_product_pm: boolean | null;
  total_score: number | null;
  applied: boolean;
  applied_at: string | null;
  applied_resume_version: string | null;
  expired: boolean;
  not_interested: boolean;
  not_interested_note: string | null;
  note: string | null;
  duplicate_of_job_id: number | null;
  duplicate_of_title: string | null;
  duplicate_of_applied: boolean | null;
  duplicate_of_total_score: number | null;
};

export type HealthState = "ok" | "stale" | "drift" | "failed" | "incomplete" | "unknown";

export type Health = {
  state: HealthState;
  message: string;
  hours_since_activity: number | null;
  collected_24h: number;
  last_run: Record<string, unknown> | null;
  failing_fields: string[];
  incomplete_run: { last_page: number; planned: number; session: string } | null;
};

export type JobPatch = Partial<
  Pick<Job, "applied" | "expired" | "not_interested" | "not_interested_note" | "note">
>;

// What PATCH /api/jobs/<id> echoes back — including applied_at, which the
// server stamps itself rather than taking from the request.
export type SavedJob = Pick<
  Job,
  "id" | "applied" | "applied_at" | "applied_resume_version" | "expired" | "not_interested" | "not_interested_note" | "note"
>;

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) throw new Error(`${init?.method ?? "GET"} ${url} failed: ${response.status}`);
  return response.json() as Promise<T>;
}

export async function fetchJobs(): Promise<Job[]> {
  const data = await request<{ count: number; jobs: Job[] }>("/api/jobs");
  return data.jobs;
}

export function fetchHealth(): Promise<Health> {
  return request<Health>("/api/health");
}

export function patchJob(id: number, updates: JobPatch): Promise<SavedJob> {
  return request<SavedJob>(`/api/jobs/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(updates),
  });
}
