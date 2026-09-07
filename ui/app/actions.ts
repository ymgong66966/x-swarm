"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";

import { signIn, signedIn } from "@/lib/auth";
import { setStatus } from "@/lib/db";
import { dispatch } from "@/lib/github";

export type Result = { error?: string; message?: string };

async function guard(): Promise<void> {
  if (!(await signedIn())) throw new Error("not signed in");
}

export async function signInAction(_state: Result, form: FormData): Promise<Result> {
  const ok = await signIn(String(form.get("passphrase") ?? ""));
  if (!ok) return { error: "Not that one." };
  redirect("/");
}

export async function approve(_state: Result, form: FormData): Promise<Result> {
  await guard();
  const id = Number(form.get("id"));
  const note = String(form.get("feedback") ?? "").trim();
  await setStatus(id, "approved", note ? `human: ${note}` : "");
  try {
    await dispatch("review-action.yml", { action: "publish", draft_id: String(id) });
  } catch (error) {
    // The approval is recorded either way; only the scheduling needs retrying.
    return { error: `Approved, but scheduling did not start: ${(error as Error).message}` };
  }
  revalidatePath("/");
  return { message: `Draft ${id} approved; scheduling it now.` };
}

export async function reject(_state: Result, form: FormData): Promise<Result> {
  await guard();
  const id = Number(form.get("id"));
  const note = String(form.get("feedback") ?? "").trim();
  await setStatus(id, "rejected", note ? `human: ${note}` : "");
  revalidatePath("/");
  return { message: `Draft ${id} rejected.` };
}

export async function revise(_state: Result, form: FormData): Promise<Result> {
  await guard();
  const id = Number(form.get("id"));
  const note = String(form.get("feedback") ?? "").trim();
  if (!note) return { error: "Say what to change first." };
  await dispatch("review-action.yml", { action: "revise", draft_id: String(id), feedback: note });
  return { message: `Rewriting draft ${id}; refresh in a minute.` };
}

export async function schedule(_state: Result, form: FormData): Promise<Result> {
  await guard();
  const id = Number(form.get("id"));
  await dispatch("review-action.yml", { action: "publish", draft_id: String(id) });
  return { message: `Scheduling draft ${id} again.` };
}

export async function runPipeline(_state: Result, form: FormData): Promise<Result> {
  await guard();
  const stream = String(form.get("stream"));
  const workflow = stream === "care" ? "care-weekly.yml" : "daily.yml";
  await dispatch(workflow, {});
  return { message: `Started the ${stream} run; drafts appear here when it finishes.` };
}
