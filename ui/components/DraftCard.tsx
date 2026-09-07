"use client";

import { useActionState, useState } from "react";

import { approve, reject, revise, schedule, type Result } from "@/app/actions";
import type { DraftRow } from "@/lib/db";

const MAX_CHARS = 270;
// A LinkedIn post is written to a different budget than a non-premium X post.
const LINKEDIN_MAX_CHARS = 650;

function limitFor(draft: DraftRow): number {
  return draft.features?.channel === "linkedin" ? LINKEDIN_MAX_CHARS : MAX_CHARS;
}

export function DraftCard({ draft }: { draft: DraftRow }) {
  const [feedback, setFeedback] = useState("");
  const [state, act, pending] = useActionState<Result, FormData>(
    async (previous: Result, form: FormData) => {
      const intent = String(form.get("intent"));
      if (intent === "approve") return approve(previous, form);
      if (intent === "reject") return reject(previous, form);
      if (intent === "revise") return revise(previous, form);
      return schedule(previous, form);
    },
    {},
  );

  const limit = limitFor(draft);
  const chars = draft.body.length;
  const channel = (draft.features?.channel as string) ?? "x";

  return (
    <form className="card" action={act}>
      <input type="hidden" name="id" value={draft.id} />
      <input type="hidden" name="feedback" value={feedback} />
      <div className="meta">
        <span>#{draft.id}</span>
        <span>{draft.stream}</span>
        <span>{channel}</span>
        <span>{draft.status}</span>
        <span className={chars > limit ? "over" : undefined}>
          {chars}/{limit}
          {chars > limit ? " OVER" : ""}
        </span>
        {draft.publication_status ? (
          <span>
            {draft.publication_status}
            {draft.scheduled_for ? ` ${new Date(draft.scheduled_for).toLocaleString()}` : ""}
          </span>
        ) : null}
      </div>

      {draft.figure_url ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img className="figure" src={draft.figure_url} alt={draft.alt_text} />
      ) : (
        <p className="notice">
          No figure a browser can load. Images drawn by a run live on that machine&apos;s disk.
        </p>
      )}

      <div className="body">{draft.body}</div>
      {draft.thread.map((tweet, index) => (
        <div className="reply" key={index}>
          {tweet}
        </div>
      ))}
      {draft.link_reply ? <div className="reply">{draft.link_reply}</div> : null}
      {draft.article_title ? (
        <p className="meta">
          {draft.article_url ? (
            <a href={draft.article_url} target="_blank" rel="noreferrer">
              {draft.article_title}
            </a>
          ) : (
            draft.article_title
          )}
        </p>
      ) : null}
      {draft.editor_notes.length ? (
        <p className="notice">{draft.editor_notes.join(" · ")}</p>
      ) : null}

      <textarea
        rows={2}
        placeholder="What to change, or why you are rejecting it"
        value={feedback}
        onChange={(e) => setFeedback(e.target.value)}
      />
      <div className="row">
        <button className="primary" name="intent" value="approve" disabled={pending}>
          Approve &amp; schedule
        </button>
        <button name="intent" value="revise" disabled={pending}>
          Revise
        </button>
        <button name="intent" value="reject" disabled={pending}>
          Reject
        </button>
        {draft.status === "approved" ? (
          <button name="intent" value="schedule" disabled={pending}>
            Schedule again
          </button>
        ) : null}
      </div>
      {state.error ? <p className="error">{state.error}</p> : null}
      {state.message ? <p className="ok">{state.message}</p> : null}
    </form>
  );
}
