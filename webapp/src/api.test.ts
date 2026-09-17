import { beforeEach, describe, expect, it, vi } from "vitest";

import { uploadResume } from "./api";

/* The token is somebody else's problem here: auth.ts gets it from the Cognito
   library now, and these tests are about the three-step upload. */
vi.mock("./auth", () => ({
  getIdToken: async () => "id-token",
  signOutLocally: () => undefined,
}));

describe("resume upload", () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it("asks for a ticket, sends the file straight to storage, then has the API read it", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      if (url.endsWith("/api/v1/me/resume")) {
        return {
          ok: true,
          status: 201,
          json: async () => ({ resume_id: 7, version: 2, upload: { url: "https://bucket.s3.amazonaws.com/", fields: { key: "users/1/cv.pdf", policy: "p" }, expires_in: 300 } }),
        } as Response;
      }
      if (url.startsWith("https://bucket.s3")) return { ok: true, status: 204 } as Response;
      return { ok: true, status: 200, json: async () => ({ id: 7, version: 2, filename: "cv.pdf", skills_extracted: ["Python"], skills_confirmed: null }) } as Response;
    });

    const resume = await uploadResume(new File(["hello"], "cv.pdf", { type: "application/pdf" }), fetchMock as unknown as typeof fetch);

    const [ticket, storage, complete] = calls;
    expect(ticket.url).toMatch(/\/api\/v1\/me\/resume$/);
    expect(storage.url).toBe("https://bucket.s3.amazonaws.com/");
    const form = storage.init!.body as FormData;
    expect(form.get("key")).toBe("users/1/cv.pdf");         // the ticket's fields are sent back
    expect((form.get("file") as File).name).toBe("cv.pdf");
    expect(storage.init!.headers).toBeUndefined();          // no Authorization header to storage
    expect(complete.url).toMatch(/\/api\/v1\/me\/resume\/2\/complete$/);
    expect(resume.skills_extracted).toEqual(["Python"]);
  });

  it("stops if storage refuses the file", async () => {
    const fetchMock = vi.fn(async (url: string) => {
      if (url.endsWith("/api/v1/me/resume")) {
        return { ok: true, status: 201, json: async () => ({ resume_id: 1, version: 1, upload: { url: "https://bucket.s3.amazonaws.com/", fields: {}, expires_in: 300 } }) } as Response;
      }
      return { ok: false, status: 403, json: async () => ({}) } as Response;
    });

    await expect(uploadResume(new File(["x"], "cv.pdf"), fetchMock as unknown as typeof fetch)).rejects.toThrow(/did not reach storage/);
  });
});
