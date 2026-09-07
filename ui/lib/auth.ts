import { createHmac, timingSafeEqual } from "node:crypto";
import { cookies } from "next/headers";

const COOKIE = "xswarm_review";

function password(): string {
  const value = process.env.XSWARM_UI_PASSWORD;
  // Approving a draft schedules a real post, so a deployment without a door is a bug,
  // not a convenience.
  if (!value) throw new Error("XSWARM_UI_PASSWORD is not set");
  return value;
}

function token(): string {
  return createHmac("sha256", password()).update(COOKIE).digest("hex");
}

function sameToken(candidate: string): boolean {
  const expected = Buffer.from(token());
  const given = Buffer.from(candidate);
  return expected.length === given.length && timingSafeEqual(expected, given);
}

export async function signedIn(): Promise<boolean> {
  const jar = await cookies();
  const value = jar.get(COOKIE)?.value;
  return value !== undefined && sameToken(value);
}

/** Set the session cookie when the passphrase matches. */
export async function signIn(typed: string): Promise<boolean> {
  if (typed !== password()) return false;
  const jar = await cookies();
  jar.set(COOKIE, token(), {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    maxAge: 60 * 60 * 24 * 30,
    path: "/",
  });
  return true;
}
