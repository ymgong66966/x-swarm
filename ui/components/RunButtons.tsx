"use client";

import { useActionState } from "react";

import { runPipeline, type Result } from "@/app/actions";

export function RunButtons() {
  const [state, action, pending] = useActionState<Result, FormData>(runPipeline, {});
  return (
    <form action={action}>
      <div className="row">
        <button name="stream" value="ml" type="submit" disabled={pending}>
          Run ML
        </button>
        <button name="stream" value="care" type="submit" disabled={pending}>
          Run Care
        </button>
      </div>
      {state.error ? <p className="error">{state.error}</p> : null}
      {state.message ? <p className="ok">{state.message}</p> : null}
    </form>
  );
}
