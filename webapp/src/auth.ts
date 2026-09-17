/**
 * Cognito hosted sign-in: authorization code + PKCE, no client secret.
 *
 *   signIn()            → sends the browser to Cognito
 *   completeSignIn()    → on /auth/callback: swaps the code for tokens
 *   getIdToken()        → a valid ID token for API calls, refreshed when it expires
 *   signOut()           → drops the tokens and ends the Cognito session
 *
 * The ID and access tokens live in memory only. The refresh token is kept in
 * sessionStorage, so it never outlives the tab and is not shared with others.
 */

const DOMAIN = import.meta.env.VITE_COGNITO_DOMAIN;
const CLIENT_ID = import.meta.env.VITE_COGNITO_CLIENT_ID;
const REDIRECT_URI = `${window.location.origin}/auth/callback`;
const SCOPE = "openid email profile";
const VERIFIER_KEY = "pkce_verifier";
const REFRESH_KEY = "refresh_token";
const EXPIRY_MARGIN_MS = 60_000; // refresh a minute before expiry

type Tokens = { idToken: string; expiresAt: number };
let current: Tokens | null = null;

function base64url(bytes: ArrayBuffer): string {
  return btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export async function pkcePair(random = crypto.getRandomValues(new Uint8Array(32))) {
  const verifier = base64url(random.buffer as ArrayBuffer);
  const challenge = base64url(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier)));
  return { verifier, challenge };
}

export function authorizeUrl(challenge: string, mode: "login" | "signup" = "login"): string {
  const params = new URLSearchParams({
    client_id: CLIENT_ID,
    response_type: "code",
    scope: SCOPE,
    redirect_uri: REDIRECT_URI,
    code_challenge: challenge,
    code_challenge_method: "S256",
  });
  return `https://${DOMAIN}/${mode}?${params}`;
}

export async function signIn(mode: "login" | "signup" = "login"): Promise<void> {
  const { verifier, challenge } = await pkcePair();
  sessionStorage.setItem(VERIFIER_KEY, verifier);
  window.location.assign(authorizeUrl(challenge, mode));
}

async function tokenRequest(body: Record<string, string>, fetchImpl = fetch): Promise<Tokens> {
  const response = await fetchImpl(`https://${DOMAIN}/oauth2/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ client_id: CLIENT_ID, ...body }).toString(),
  });
  if (!response.ok) throw new Error(`sign-in failed (${response.status})`);
  const data = await response.json();
  if (data.refresh_token) sessionStorage.setItem(REFRESH_KEY, data.refresh_token);
  current = { idToken: data.id_token, expiresAt: Date.now() + data.expires_in * 1000 };
  return current;
}

/** Called on /auth/callback with the ?code= Cognito sent back. */
export async function completeSignIn(code: string, fetchImpl = fetch): Promise<Tokens> {
  const verifier = sessionStorage.getItem(VERIFIER_KEY);
  if (!verifier) throw new Error("this sign-in did not start here");
  sessionStorage.removeItem(VERIFIER_KEY);
  return tokenRequest({ grant_type: "authorization_code", code, redirect_uri: REDIRECT_URI, code_verifier: verifier }, fetchImpl);
}

export async function getIdToken(fetchImpl = fetch): Promise<string | null> {
  if (current && current.expiresAt - EXPIRY_MARGIN_MS > Date.now()) return current.idToken;
  const refresh = sessionStorage.getItem(REFRESH_KEY);
  if (!refresh) return null;
  try {
    return (await tokenRequest({ grant_type: "refresh_token", refresh_token: refresh }, fetchImpl)).idToken;
  } catch {
    signOutLocally();
    return null;
  }
}

export function signOutLocally(): void {
  current = null;
  sessionStorage.removeItem(REFRESH_KEY);
  sessionStorage.removeItem(VERIFIER_KEY);
}

export function signOut(): void {
  signOutLocally();
  const params = new URLSearchParams({ client_id: CLIENT_ID, logout_uri: `${window.location.origin}/` });
  window.location.assign(`https://${DOMAIN}/logout?${params}`);
}

export function isSignedIn(): boolean {
  return current !== null || sessionStorage.getItem(REFRESH_KEY) !== null;
}
