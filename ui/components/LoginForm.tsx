"use client";

import { useActionState } from "react";

import { signInAction, type Result } from "@/app/actions";

export function LoginForm() {
  const [state, action, pending] = useActionState<Result, FormData>(signInAction, {});
  return (
    <form action={action}>
      <input name="passphrase" type="password" placeholder="Passphrase" autoFocus />
      <div className="row">
        <button className="primary" type="submit" disabled={pending}>
          Enter
        </button>
      </div>
      {state.error ? <p className="error">{state.error}</p> : null}
    </form>
  );
}
