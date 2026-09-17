import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  AuthError,
  asAuthError,
  authorizeUrl,
  completeSignIn,
  confirmPasswordReset,
  confirmSignUp,
  getIdToken,
  isSignedIn,
  passwordProblem,
  pkcePair,
  requestPasswordReset,
  signInWithPassword,
  signOutLocally,
  signUpWithPassword,
  socialProviders,
} from "./auth";

/* The Cognito library is replaced wholesale: these tests are about what our
   module does with a session, never about reaching AWS. Each fake class hands
   its call to a spy the test can drive. */

type Callbacks = Record<string, (...args: unknown[]) => void>;

const calls = {
  pools: [] as { UserPoolId?: string; ClientId?: string; Storage?: unknown }[],
  users: [] as { Username: string; Storage?: unknown }[],
  details: [] as { Username: string; Password?: string }[],
  authenticate: vi.fn<(details: unknown, callbacks: Callbacks) => void>(),
  refresh: vi.fn<(token: { getToken(): string }, callback: (err: unknown, session: unknown) => void) => void>(),
  signUp: vi.fn<(username: string, password: string, attributes: { Name: string; Value: string }[], validation: unknown, callback: (err: unknown, result?: unknown) => void) => void>(),
  confirmRegistration: vi.fn<(code: string, force: boolean, callback: (err: unknown) => void) => void>(),
  resend: vi.fn<(callback: (err: unknown) => void) => void>(),
  forgotPassword: vi.fn<(callbacks: Callbacks) => void>(),
  confirmPassword: vi.fn<(code: string, password: string, callbacks: Callbacks) => void>(),
};

vi.mock("amazon-cognito-identity-js", () => ({
  CognitoUserPool: class {
    constructor(data: { UserPoolId?: string; ClientId?: string; Storage?: unknown }) {
      calls.pools.push(data);
    }
    signUp(...args: Parameters<typeof calls.signUp>) {
      calls.signUp(...args);
    }
  },
  CognitoUser: class {
    constructor(data: { Username: string; Storage?: unknown }) {
      calls.users.push(data);
    }
    authenticateUser(...args: Parameters<typeof calls.authenticate>) {
      calls.authenticate(...args);
    }
    refreshSession(...args: Parameters<typeof calls.refresh>) {
      calls.refresh(...args);
    }
    confirmRegistration(...args: Parameters<typeof calls.confirmRegistration>) {
      calls.confirmRegistration(...args);
    }
    resendConfirmationCode(...args: Parameters<typeof calls.resend>) {
      calls.resend(...args);
    }
    forgotPassword(...args: Parameters<typeof calls.forgotPassword>) {
      calls.forgotPassword(...args);
    }
    confirmPassword(...args: Parameters<typeof calls.confirmPassword>) {
      calls.confirmPassword(...args);
    }
  },
  AuthenticationDetails: class {
    constructor(data: { Username: string; Password?: string }) {
      calls.details.push(data);
    }
  },
  CognitoRefreshToken: class {
    constructor(private data: { RefreshToken: string }) {}
    getToken() {
      return this.data.RefreshToken;
    }
  },
  CognitoUserAttribute: class {
    constructor(public data: { Name: string; Value: string }) {
      Object.assign(this, data);
    }
  },
}));

/** A session shaped like the library's: an ID token that knows its own expiry
 *  in seconds, and a refresh token. */
const session = (secondsLeft: number, refresh: string | null = "refresh-token") => ({
  getIdToken: () => ({
    getJwtToken: () => "id-token",
    getExpiration: () => Math.floor(Date.now() / 1000) + secondsLeft,
  }),
  getRefreshToken: () => (refresh ? { getToken: () => refresh } : undefined),
});

/** An unsigned JWT good enough for the claim-reading the callback path does. */
const jwt = (payload: Record<string, unknown>) => `header.${btoa(JSON.stringify(payload))}.signature`;

const failure = (code: string) => ({ code, name: code, message: `raw ${code} text` });

beforeEach(() => {
  sessionStorage.clear();
  signOutLocally();
  calls.pools.length = 0;
  calls.users.length = 0;
  calls.details.length = 0;
  vi.clearAllMocks();
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("password sign-in", () => {
  it("signs in over SRP and keeps the ID token out of anything that outlives the tab", async () => {
    calls.authenticate.mockImplementation((_details, callbacks) => callbacks.onSuccess!(session(3600)));

    const tokens = await signInWithPassword(" nasi@example.com ", "Sup3rSecretPass");

    expect(tokens.idToken).toBe("id-token");
    expect(calls.details[0]).toMatchObject({ Username: "nasi@example.com", Password: "Sup3rSecretPass" });
    expect(sessionStorage.getItem("refresh_token")).toBe("refresh-token");
    expect(sessionStorage.getItem("auth_user")).toBe("nasi@example.com");
    // Nothing that survives the tab, and nothing the library cached for itself:
    // it was handed a memory-only Storage.
    expect(Object.keys(localStorage)).toEqual([]);
    expect(document.cookie).toBe("");
    expect(sessionStorage.getItem("id_token")).toBeNull();
    expect(JSON.stringify(sessionStorage)).not.toContain("id-token");
    expect(calls.users[0]!.Storage).toBeDefined();
    expect(calls.pools[0]!.Storage).toBeDefined();
    expect(isSignedIn()).toBe(true);
  });

  it("serves the token it already has until a minute before it expires", async () => {
    calls.authenticate.mockImplementation((_details, callbacks) => callbacks.onSuccess!(session(3600)));
    await signInWithPassword("nasi@example.com", "Sup3rSecretPass");

    expect(await getIdToken()).toBe("id-token");
    expect(calls.refresh).not.toHaveBeenCalled();
  });

  it("refreshes a token inside the expiry margin, naming the user it signed in", async () => {
    calls.authenticate.mockImplementation((_details, callbacks) => callbacks.onSuccess!(session(30))); // < 60s left
    calls.refresh.mockImplementation((_token, callback) => callback(null, session(3600, "rotated-token")));
    await signInWithPassword("nasi@example.com", "Sup3rSecretPass");

    expect(await getIdToken()).toBe("id-token");
    expect(calls.refresh).toHaveBeenCalledTimes(1);
    expect(calls.refresh.mock.calls[0]![0].getToken()).toBe("refresh-token");
    expect(calls.users.at(-1)!.Username).toBe("nasi@example.com");
    expect(sessionStorage.getItem("refresh_token")).toBe("rotated-token"); // rotation is kept
  });

  it("refreshes from sessionStorage alone after a reload", async () => {
    sessionStorage.setItem("refresh_token", "refresh-token");
    sessionStorage.setItem("auth_user", "nasi@example.com");
    calls.refresh.mockImplementation((_token, callback) => callback(null, session(3600)));

    expect(isSignedIn()).toBe(true);
    expect(await getIdToken()).toBe("id-token");
  });

  it("has nobody signed in when the tab kept no refresh token", async () => {
    expect(isSignedIn()).toBe(false);
    expect(await getIdToken()).toBeNull();
    expect(calls.refresh).not.toHaveBeenCalled();
  });

  it("signs out locally when the refresh token is rejected", async () => {
    sessionStorage.setItem("refresh_token", "stale");
    sessionStorage.setItem("auth_user", "nasi@example.com");
    calls.refresh.mockImplementation((_token, callback) => callback(failure("NotAuthorizedException"), null));

    expect(await getIdToken()).toBeNull();
    expect(isSignedIn()).toBe(false);
    expect(sessionStorage.getItem("refresh_token")).toBeNull();
  });

  it("drops every trace of the session on signOutLocally", async () => {
    calls.authenticate.mockImplementation((_details, callbacks) => callbacks.onSuccess!(session(3600)));
    await signInWithPassword("nasi@example.com", "Sup3rSecretPass");

    signOutLocally();

    expect(isSignedIn()).toBe(false);
    expect(sessionStorage.getItem("auth_user")).toBeNull();
    expect(await getIdToken()).toBeNull();
  });
});

describe("errors the user can act on", () => {
  it("explains a wrong password in plain English and keeps the code", async () => {
    calls.authenticate.mockImplementation((_details, callbacks) => callbacks.onFailure!(failure("NotAuthorizedException")));

    const error = await signInWithPassword("nasi@example.com", "wrong").catch((err: AuthError) => err);

    expect(error).toBeInstanceOf(AuthError);
    expect((error as AuthError).code).toBe("NotAuthorizedException");
    expect((error as AuthError).message).toMatch(/do not match an account/);
    expect((error as AuthError).message).not.toContain("raw");
  });

  it("marks an unconfirmed account so the page can ask for the code instead", async () => {
    calls.authenticate.mockImplementation((_details, callbacks) => callbacks.onFailure!(failure("UserNotConfirmedException")));

    await expect(signInWithPassword("nasi@example.com", "Sup3rSecretPass")).rejects.toMatchObject({
      code: "UserNotConfirmedException",
    });
  });

  it("never leaves a challenge hanging", async () => {
    calls.authenticate.mockImplementation((_details, callbacks) => callbacks.newPasswordRequired!({}, {}));

    await expect(signInWithPassword("nasi@example.com", "Sup3rSecretPass")).rejects.toThrow(/Forgot password/);
  });

  it("keeps an unmapped message rather than inventing one, and survives a non-error", () => {
    expect(asAuthError({ code: "WeirdException", message: "something specific" }).message).toBe("something specific");
    expect(asAuthError(null).code).toBe("UnknownError");
    const already = new AuthError("CodeMismatchException", "mine");
    expect(asAuthError(already)).toBe(already);
  });
});

describe("the pool's password policy", () => {
  it("names exactly what is missing", () => {
    expect(passwordProblem("short")).toMatch(/12 characters/);
    expect(passwordProblem("alllowercase123")).toMatch(/upper-case/);
    expect(passwordProblem("ALLUPPERCASE123")).toMatch(/lower-case/);
    expect(passwordProblem("NoDigitsInHere")).toMatch(/number/);
    expect(passwordProblem("Sup3rSecretPass")).toBeNull();
  });

  it("refuses a weak password before troubling Cognito with it", async () => {
    await expect(signUpWithPassword("nasi@example.com", "weak")).rejects.toMatchObject({ code: "InvalidPasswordException" });
    await expect(confirmPasswordReset("nasi@example.com", "123456", "weak")).rejects.toMatchObject({
      code: "InvalidPasswordException",
    });
    expect(calls.signUp).not.toHaveBeenCalled();
    expect(calls.confirmPassword).not.toHaveBeenCalled();
  });
});

describe("sign-up and the emailed code", () => {
  it("creates the account with the email as an attribute and reports that a code is needed", async () => {
    calls.signUp.mockImplementation((_u, _p, _a, _v, callback) => callback(null, { userConfirmed: false }));

    expect(await signUpWithPassword("nasi@example.com", "Sup3rSecretPass")).toEqual({ confirmed: false });
    const [username, password, attributes] = calls.signUp.mock.calls[0]!;
    expect(username).toBe("nasi@example.com");
    expect(password).toBe("Sup3rSecretPass");
    expect(attributes[0]).toMatchObject({ Name: "email", Value: "nasi@example.com" });
  });

  it("points an existing account at signing in", async () => {
    calls.signUp.mockImplementation((_u, _p, _a, _v, callback) => callback(failure("UsernameExistsException")));

    await expect(signUpWithPassword("nasi@example.com", "Sup3rSecretPass")).rejects.toThrow(/already an account/);
  });

  it("spends the code without letting it create an alias", async () => {
    calls.confirmRegistration.mockImplementation((_code, _force, callback) => callback(null));

    await confirmSignUp("nasi@example.com", " 123456 ");

    expect(calls.confirmRegistration.mock.calls[0]!.slice(0, 2)).toEqual(["123456", false]);
  });

  it("says plainly when a code is wrong or stale", async () => {
    calls.confirmRegistration.mockImplementation((_code, _force, callback) => callback(failure("ExpiredCodeException")));
    await expect(confirmSignUp("nasi@example.com", "123456")).rejects.toThrow(/expired/);

    calls.confirmRegistration.mockImplementation((_code, _force, callback) => callback(failure("CodeMismatchException")));
    await expect(confirmSignUp("nasi@example.com", "123456")).rejects.toThrow(/not right/);
  });
});

describe("password reset", () => {
  it("resolves on the verification-code reply Cognito normally sends", async () => {
    calls.forgotPassword.mockImplementation((callbacks) => callbacks.inputVerificationCode!({}));
    await expect(requestPasswordReset("nasi@example.com")).resolves.toBeUndefined();
  });

  it("resolves on a bare success too", async () => {
    calls.forgotPassword.mockImplementation((callbacks) => callbacks.onSuccess!({}));
    await expect(requestPasswordReset("nasi@example.com")).resolves.toBeUndefined();
  });

  it("reports a rate limit as something to wait out", async () => {
    calls.forgotPassword.mockImplementation((callbacks) => callbacks.onFailure!(failure("LimitExceededException")));
    await expect(requestPasswordReset("nasi@example.com")).rejects.toThrow(/Wait a few minutes/);
  });

  it("sets the new password with the code", async () => {
    calls.confirmPassword.mockImplementation((_code, _password, callbacks) => callbacks.onSuccess!("SUCCESS"));
    await confirmPasswordReset("nasi@example.com", " 123456 ", "Sup3rSecretPass");
    expect(calls.confirmPassword.mock.calls[0]!.slice(0, 2)).toEqual(["123456", "Sup3rSecretPass"]);
  });
});

describe("federated providers", () => {
  it("offers none until the deploy says which the pool has", () => {
    expect(socialProviders()).toEqual([]);
  });

  it("offers only the ones named, under the Cognito provider names", () => {
    vi.stubEnv("VITE_SOCIAL_PROVIDERS", "google, apple ,nonsense");
    const providers = socialProviders();
    expect(providers.map((provider) => provider.id)).toEqual(["google", "apple"]);
    expect(providers[1]!.name).toBe("SignInWithApple"); // what /oauth2/authorize wants
  });

  it("asks Cognito for a code, never a token, and names the exact redirect", () => {
    const url = new URL(authorizeUrl("challenge-value"));
    expect(url.searchParams.get("response_type")).toBe("code");
    expect(url.searchParams.get("code_challenge_method")).toBe("S256");
    expect(url.searchParams.get("redirect_uri")).toBe(`${window.location.origin}/auth/callback`);
    expect(url.pathname).toBe("/login");
    expect(new URL(authorizeUrl("c", "signup")).pathname).toBe("/signup");
  });

  it("sends a named provider straight to the authorize endpoint", () => {
    const url = new URL(authorizeUrl("challenge-value", "login", "Google"));
    expect(url.pathname).toBe("/oauth2/authorize");
    expect(url.searchParams.get("identity_provider")).toBe("Google");
  });

  it("makes a verifier and its SHA-256 challenge, both url-safe", async () => {
    const { verifier, challenge } = await pkcePair(new Uint8Array(32).fill(7));
    expect(verifier).toMatch(/^[A-Za-z0-9_-]+$/);
    expect(challenge).toMatch(/^[A-Za-z0-9_-]+$/);
    expect(challenge).not.toEqual(verifier);
    const again = await pkcePair(new Uint8Array(32).fill(7));
    expect(again.challenge).toEqual(challenge); // same verifier → same challenge
  });

  it("swaps the code for tokens, sending the verifier it kept, and learns the username", async () => {
    const { verifier } = await pkcePair();
    sessionStorage.setItem("pkce_verifier", verifier);
    const idToken = jwt({ "cognito:username": "google_1234", email: "nasi@example.com" });
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => ({
      ok: true,
      status: 200,
      json: async () => ({ id_token: idToken, refresh_token: "refresh-token", expires_in: 3600 }),
    }) as unknown as Response);

    await completeSignIn("the-code", fetchMock as unknown as typeof fetch);

    const body = new URLSearchParams((fetchMock.mock.calls[0]![1] as RequestInit).body as string);
    expect(body.get("grant_type")).toBe("authorization_code");
    expect(body.get("code")).toBe("the-code");
    expect(body.get("code_verifier")).toBe(verifier);
    expect(sessionStorage.getItem("pkce_verifier")).toBeNull(); // single use
    // The username is what a later refresh will have to name itself with.
    expect(sessionStorage.getItem("auth_user")).toBe("google_1234");
    expect(await getIdToken()).toBe(idToken);
    expect(calls.refresh).not.toHaveBeenCalled(); // still valid: no refresh
  });

  it("refuses a code for a sign-in that started elsewhere", async () => {
    await expect(completeSignIn("code")).rejects.toThrow(/did not start here/);
  });
});
