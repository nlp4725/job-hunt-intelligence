import { beforeEach, describe, expect, it, vi } from "vitest";

import { authorizeUrl, completeSignIn, getIdToken, isSignedIn, pkcePair, signOutLocally } from "./auth";

type FetchMock = ReturnType<typeof vi.fn<typeof fetch>>;

const bodyOf = (mock: FetchMock) => new URLSearchParams((mock.mock.calls[0]![1] as RequestInit).body as string);

const token = (seconds: number) => ({
  ok: true,
  status: 200,
  json: async () => ({ id_token: "id-token", refresh_token: "refresh-token", expires_in: seconds }),
});

describe("sign-in", () => {
  beforeEach(() => {
    sessionStorage.clear();
    signOutLocally();
  });

  it("makes a verifier and its SHA-256 challenge, both url-safe", async () => {
    const { verifier, challenge } = await pkcePair(new Uint8Array(32).fill(7));
    expect(verifier).toMatch(/^[A-Za-z0-9_-]+$/);
    expect(challenge).toMatch(/^[A-Za-z0-9_-]+$/);
    expect(challenge).not.toEqual(verifier);
    const again = await pkcePair(new Uint8Array(32).fill(7));
    expect(again.challenge).toEqual(challenge); // same verifier → same challenge
  });

  it("asks Cognito for a code, never a token, and names the exact redirect", () => {
    const url = new URL(authorizeUrl("challenge-value"));
    expect(url.searchParams.get("response_type")).toBe("code");
    expect(url.searchParams.get("code_challenge_method")).toBe("S256");
    expect(url.searchParams.get("redirect_uri")).toBe(`${window.location.origin}/auth/callback`);
    expect(url.pathname).toBe("/login");
    expect(new URL(authorizeUrl("c", "signup")).pathname).toBe("/signup");
  });

  it("swaps the code for tokens, sending the verifier it kept", async () => {
    const { verifier } = await pkcePair();
    sessionStorage.setItem("pkce_verifier", verifier);
    const fetchMock = vi.fn(async () => token(3600) as unknown as Response) as unknown as FetchMock;

    await completeSignIn("the-code", fetchMock);

    const body = bodyOf(fetchMock);
    expect(body.get("grant_type")).toBe("authorization_code");
    expect(body.get("code")).toBe("the-code");
    expect(body.get("code_verifier")).toBe(verifier);
    expect(sessionStorage.getItem("pkce_verifier")).toBeNull(); // single use
    expect(await getIdToken(fetchMock)).toBe("id-token");
    expect(fetchMock).toHaveBeenCalledTimes(1); // still valid: no refresh
  });

  it("refuses a code for a sign-in that started elsewhere", async () => {
    await expect(completeSignIn("code")).rejects.toThrow(/did not start here/);
  });

  it("refreshes an expired token", async () => {
    sessionStorage.setItem("refresh_token", "refresh-token");
    const fetchMock = vi.fn(async () => token(3600) as unknown as Response) as unknown as FetchMock;

    const idToken = await getIdToken(fetchMock);

    const body = bodyOf(fetchMock);
    expect(body.get("grant_type")).toBe("refresh_token");
    expect(idToken).toBe("id-token");
  });

  it("signs out when the refresh token is rejected", async () => {
    sessionStorage.setItem("refresh_token", "stale");
    const fetchMock = vi.fn(async () => ({ ok: false, status: 400, json: async () => ({}) }) as unknown as Response) as unknown as FetchMock;

    expect(await getIdToken(fetchMock)).toBeNull();
    expect(isSignedIn()).toBe(false);
  });

  it("keeps tokens out of anything that outlives the tab", async () => {
    sessionStorage.setItem("refresh_token", "refresh-token");
    await getIdToken(vi.fn(async () => token(3600) as unknown as Response) as unknown as typeof fetch);
    expect(Object.keys(localStorage)).toEqual([]);
    expect(document.cookie).toBe("");
  });
});
