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
