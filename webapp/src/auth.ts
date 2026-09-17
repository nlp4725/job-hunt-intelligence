/**
 * Cognito sign-in, driven from our own pages.
 *
 * Email and password go straight to the Cognito user pool over SRP (the
 * `amazon-cognito-identity-js` library's default authentication flow, which our
 * app client already allows via ALLOW_USER_SRP_AUTH), so the password itself
 * never crosses the wire and we never have to send the browser to the hosted
 * UI. The hosted UI is still used for one thing: a federated identity provider
 * (Google, Apple) can only be reached through /oauth2/authorize, so those
 * buttons keep the authorization-code + PKCE path and /auth/callback alive.
 *
 *   signInWithPassword()    → SRP sign-in on our own /signin page
 *   signUpWithPassword()    → create the account; Cognito emails a code
 *   confirmSignUp()         → spend that emailed code
 *   requestPasswordReset()  → email a reset code
 *   confirmPasswordReset()  → spend it and set the new password
 *   signInWithProvider()    → hosted /oauth2/authorize for Google/Apple
 *   completeSignIn()        → on /auth/callback: swaps the code for tokens
 *   getIdToken()            → a valid ID token for API calls, refreshed for you
 *   signOut()               → drops the tokens and ends the Cognito session
 *
 * The same two storage rules hold whichever door the user came through: the ID
 * token lives in memory only, and the refresh token lives in sessionStorage, so
 * it never outlives the tab and is not shared with another one. That is also
 * why the Cognito library is handed a memory-only Storage — left to itself it
 * would cache every token in localStorage, where any script on the origin
 * could read it long after the user walked away.
 */

import {
  AuthenticationDetails,
  CognitoRefreshToken,
  CognitoUser,
  CognitoUserAttribute,
  CognitoUserPool,
  type CognitoUserSession,
  type ICognitoStorage,
} from "amazon-cognito-identity-js";

const DOMAIN = import.meta.env.VITE_COGNITO_DOMAIN;
const CLIENT_ID = import.meta.env.VITE_COGNITO_CLIENT_ID;
const USER_POOL_ID = import.meta.env.VITE_COGNITO_USER_POOL_ID;
const REDIRECT_URI = `${window.location.origin}/auth/callback`;
const SCOPE = "openid email profile";
const VERIFIER_KEY = "pkce_verifier";
const REFRESH_KEY = "refresh_token";
const USER_KEY = "auth_user"; // the username a refresh needs to name itself
const EXPIRY_MARGIN_MS = 60_000; // refresh a minute before expiry

type Tokens = { idToken: string; expiresAt: number };
let current: Tokens | null = null;

/** Everything the Cognito library wants to persist is kept here instead, so it
 *  disappears with the page and never reaches localStorage. */
const memory = new Map<string, string>();
const memoryStorage: ICognitoStorage = {
  getItem: (key) => memory.get(key) ?? null,
  setItem: (key, value) => void memory.set(key, String(value)),
  removeItem: (key) => void memory.delete(key),
  clear: () => memory.clear(),
};

/** Built on first use, not at import: a page that never signs anybody in (and a
 *  test that never touches Cognito) should not need the pool id to be set. */
let pool: CognitoUserPool | null = null;
function userPool(): CognitoUserPool {
  if (!pool) pool = new CognitoUserPool({ UserPoolId: USER_POOL_ID, ClientId: CLIENT_ID, Storage: memoryStorage });
  return pool;
}

function cognitoUser(email: string): CognitoUser {
  return new CognitoUser({ Username: email.trim(), Pool: userPool(), Storage: memoryStorage });
}

// --- errors -----------------------------------------------------------------

/** A failure we can explain. `code` is Cognito's exception name, which the
 *  pages branch on (an unconfirmed account is a fork in the flow, not a dead
 *  end); `message` is already plain enough to render. */
export class AuthError extends Error {
  constructor(readonly code: string, message: string) {
    super(message);
    this.name = "AuthError";
  }
}

/** Cognito's own wording is mostly fine — "Incorrect username or password." is
 *  exactly right — so we only rewrite the messages that read like a stack
 *  trace or leak the API's vocabulary at the user. */
const MESSAGES: Record<string, string> = {
  NotAuthorizedException: "That email and password do not match an account.",
  UserNotFoundException: "We could not find an account with that email.",
  UserNotConfirmedException: "This account still needs the code we emailed you.",
  UsernameExistsException: "There is already an account with that email. Try signing in instead.",
  InvalidPasswordException: "That password does not meet the rules below.",
  InvalidParameterException: "Please check the details above and try again.",
  CodeMismatchException: "That code is not right. Check the email and try again.",
  ExpiredCodeException: "That code has expired. Send yourself a new one.",
  LimitExceededException: "Too many attempts. Wait a few minutes and try again.",
  TooManyRequestsException: "Too many attempts. Wait a few minutes and try again.",
  TooManyFailedAttemptsException: "Too many wrong attempts. Wait a few minutes and try again.",
  PasswordResetRequiredException: "This account needs a new password. Use “Forgot password?” below.",
  NetworkError: "We could not reach the sign-in service. Check your connection.",
};

export function asAuthError(err: unknown): AuthError {
  if (err instanceof AuthError) return err;
  const raw = err as { code?: string; name?: string; message?: string } | null;
  const code = raw?.code ?? raw?.name ?? "UnknownError";
  const message = MESSAGES[code] ?? raw?.message ?? "Something went wrong. Please try again.";
  return new AuthError(code, message);
}

// --- password policy --------------------------------------------------------

/** Said out loud on the sign-up and reset pages, so nobody meets the pool's
 *  policy for the first time as a red error after submitting. */
export const PASSWORD_RULES = "At least 12 characters, with an upper-case letter, a lower-case letter and a number.";

/** null when the password is acceptable, otherwise what is missing. Mirrors the
 *  password_policy block in terraform/app/cognito.tf. */
export function passwordProblem(password: string): string | null {
  if (password.length < 12) return "Use at least 12 characters.";
  if (!/[A-Z]/.test(password)) return "Add an upper-case letter.";
  if (!/[a-z]/.test(password)) return "Add a lower-case letter.";
  if (!/[0-9]/.test(password)) return "Add a number.";
  return null;
}

// --- the session ------------------------------------------------------------

function adopt(session: CognitoUserSession, email: string): Tokens {
  const idToken = session.getIdToken();
  current = { idToken: idToken.getJwtToken(), expiresAt: idToken.getExpiration() * 1000 };
  const refresh = session.getRefreshToken()?.getToken();
  if (refresh) sessionStorage.setItem(REFRESH_KEY, refresh);
  sessionStorage.setItem(USER_KEY, email.trim());
  return current;
}

/** SRP sign-in. Throws an AuthError; `UserNotConfirmedException` means the
 *  caller should show the emailed-code form rather than an error. */
export function signInWithPassword(email: string, password: string): Promise<Tokens> {
  const user = cognitoUser(email);
  return new Promise((resolve, reject) => {
    user.authenticateUser(new AuthenticationDetails({ Username: email.trim(), Password: password }), {
      onSuccess: (session) => resolve(adopt(session, email)),
      onFailure: (err) => reject(asAuthError(err)),
      // A pool-side admin reset or an MFA setting we do not support yet would
      // otherwise hang the promise forever, so every challenge gets an answer.
      newPasswordRequired: () =>
        reject(new AuthError("NewPasswordRequired", "This account needs a new password. Use “Forgot password?” below.")),
      mfaRequired: () => reject(new AuthError("MfaRequired", "This account uses a second factor, which this page cannot do yet.")),
      totpRequired: () => reject(new AuthError("MfaRequired", "This account uses a second factor, which this page cannot do yet.")),
    });
  });
}

/** Creates the account. `confirmed` is false in the normal case, meaning
 *  Cognito has emailed a code that confirmSignUp() must spend. */
export function signUpWithPassword(email: string, password: string): Promise<{ confirmed: boolean }> {
  const problem = passwordProblem(password);
  if (problem) return Promise.reject(new AuthError("InvalidPasswordException", problem));
  return new Promise((resolve, reject) => {
    const attributes = [new CognitoUserAttribute({ Name: "email", Value: email.trim() })];
    userPool().signUp(email.trim(), password, attributes, [], (err, result) => {
      if (err || !result) return reject(asAuthError(err));
      resolve({ confirmed: result.userConfirmed });
    });
  });
}

export function confirmSignUp(email: string, code: string): Promise<void> {
  return new Promise((resolve, reject) => {
    // forceAliasCreation false: the email is the username here, so there is no
    // alias to steal from another account.
    cognitoUser(email).confirmRegistration(code.trim(), false, (err) => (err ? reject(asAuthError(err)) : resolve()));
  });
}

export function resendConfirmationCode(email: string): Promise<void> {
  return new Promise((resolve, reject) => {
    cognitoUser(email).resendConfirmationCode((err) => (err ? reject(asAuthError(err)) : resolve()));
  });
}

/** Asks Cognito to email a reset code. Resolves either way Cognito answers:
 *  inputVerificationCode is the normal reply, onSuccess the rarer one. */
export function requestPasswordReset(email: string): Promise<void> {
  return new Promise((resolve, reject) => {
    cognitoUser(email).forgotPassword({
      onSuccess: () => resolve(),
      inputVerificationCode: () => resolve(),
      onFailure: (err) => reject(asAuthError(err)),
    });
  });
}

export function confirmPasswordReset(email: string, code: string, password: string): Promise<void> {
  const problem = passwordProblem(password);
  if (problem) return Promise.reject(new AuthError("InvalidPasswordException", problem));
  return new Promise((resolve, reject) => {
    cognitoUser(email).confirmPassword(code.trim(), password, {
      onSuccess: () => resolve(),
      onFailure: (err) => reject(asAuthError(err)),
    });
  });
}

// --- federated providers (still the hosted authorize endpoint) ---------------

export type SocialProvider = { id: string; name: string; label: string };

/** The providers we know how to draw a button for. `name` is the Cognito
 *  identity-provider name, which is what /oauth2/authorize wants. */
const KNOWN_PROVIDERS: Record<string, SocialProvider> = {
  google: { id: "google", name: "Google", label: "Continue with Google" },
  apple: { id: "apple", name: "SignInWithApple", label: "Continue with Apple" },
  facebook: { id: "facebook", name: "Facebook", label: "Continue with Facebook" },
};

/** Only providers actually wired up in the user pool, so we never render a
 *  button that would bounce the user off to an error page. The pool currently
 *  has supported_identity_providers = ["COGNITO"], so this is empty until
 *  VITE_SOCIAL_PROVIDERS is set — read at call time so a deploy, or a test, can
 *  change it without reloading the module. */
export function socialProviders(): SocialProvider[] {
  return (import.meta.env.VITE_SOCIAL_PROVIDERS ?? "")
    .split(",")
    .map((name: string) => KNOWN_PROVIDERS[name.trim().toLowerCase()])
    .filter((provider: SocialProvider | undefined): provider is SocialProvider => Boolean(provider));
}

function base64url(bytes: ArrayBuffer): string {
  return btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export async function pkcePair(random = crypto.getRandomValues(new Uint8Array(32))) {
  const verifier = base64url(random.buffer as ArrayBuffer);
  const challenge = base64url(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier)));
  return { verifier, challenge };
}

export function authorizeUrl(challenge: string, mode: "login" | "signup" = "login", provider?: string): string {
  const params = new URLSearchParams({
    client_id: CLIENT_ID,
    response_type: "code",
    scope: SCOPE,
    redirect_uri: REDIRECT_URI,
    code_challenge: challenge,
    code_challenge_method: "S256",
  });
  // A named provider must go through /oauth2/authorize: /login and /signup are
  // the hosted UI's own screens and ignore identity_provider.
  if (provider) params.set("identity_provider", provider);
  return `https://${DOMAIN}/${provider ? "oauth2/authorize" : mode}?${params}`;
}

/** Sends the browser to Cognito. Kept for the federated case (and as a working
 *  fallback to the hosted UI); email and password never come through here. */
export async function signIn(mode: "login" | "signup" = "login", provider?: string): Promise<void> {
  const { verifier, challenge } = await pkcePair();
  sessionStorage.setItem(VERIFIER_KEY, verifier);
  window.location.assign(authorizeUrl(challenge, mode, provider));
}

export const signInWithProvider = (provider: SocialProvider) => signIn("login", provider.name);

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
  // A federated sign-in has no email typed into a form, so the username for a
  // later refresh comes out of the ID token Cognito just handed us.
  const username = claimOf(data.id_token, "cognito:username") ?? claimOf(data.id_token, "email");
  if (username) sessionStorage.setItem(USER_KEY, username);
  return current;
}

function claimOf(jwt: string, claim: string): string | null {
  try {
    const payload = JSON.parse(atob(jwt.split(".")[1]!.replace(/-/g, "+").replace(/_/g, "/")));
    return typeof payload[claim] === "string" ? payload[claim] : null;
  } catch {
    return null;
  }
}

/** Called on /auth/callback with the ?code= Cognito sent back. */
export async function completeSignIn(code: string, fetchImpl = fetch): Promise<Tokens> {
  const verifier = sessionStorage.getItem(VERIFIER_KEY);
  if (!verifier) throw new Error("this sign-in did not start here");
  sessionStorage.removeItem(VERIFIER_KEY);
  return tokenRequest({ grant_type: "authorization_code", code, redirect_uri: REDIRECT_URI, code_verifier: verifier }, fetchImpl);
}

function refreshSession(email: string, refreshToken: string): Promise<CognitoUserSession> {
  return new Promise((resolve, reject) => {
    cognitoUser(email).refreshSession(new CognitoRefreshToken({ RefreshToken: refreshToken }), (err, session) => {
      if (err || !session) return reject(asAuthError(err));
      resolve(session as CognitoUserSession);
    });
  });
}

/** A valid ID token, or null when nobody is signed in. Refreshes a minute
 *  before expiry so a call never goes out with a token about to be rejected.
 *  The unused parameter keeps the signature api.ts already passes: the refresh
 *  now runs inside the Cognito library, which brings its own transport. */
export async function getIdToken(_fetchImpl?: typeof fetch): Promise<string | null> {
  if (current && current.expiresAt - EXPIRY_MARGIN_MS > Date.now()) return current.idToken;
  const refresh = sessionStorage.getItem(REFRESH_KEY);
  const email = sessionStorage.getItem(USER_KEY);
  if (!refresh || !email) return null;
  try {
    return adopt(await refreshSession(email, refresh), email).idToken;
  } catch {
    signOutLocally();
    return null;
  }
}

export function signOutLocally(): void {
  current = null;
  memoryStorage.clear();
  sessionStorage.removeItem(REFRESH_KEY);
  sessionStorage.removeItem(USER_KEY);
  sessionStorage.removeItem(VERIFIER_KEY);
}

export function signOut(): void {
  signOutLocally();
  // Still worth a trip to /logout: a federated sign-in leaves a session cookie
  // on the Cognito domain that only Cognito can clear.
  const params = new URLSearchParams({ client_id: CLIENT_ID, logout_uri: `${window.location.origin}/` });
  window.location.assign(`https://${DOMAIN}/logout?${params}`);
}

export function isSignedIn(): boolean {
  return current !== null || (sessionStorage.getItem(REFRESH_KEY) !== null && sessionStorage.getItem(USER_KEY) !== null);
}
