import { redirect } from "next/navigation";

import { signedIn } from "@/lib/auth";
import { LoginForm } from "@/components/LoginForm";

export const dynamic = "force-dynamic";

export default async function LoginPage() {
  if (await signedIn()) redirect("/");
  return (
    <main>
      <h1>x-swarm review</h1>
      <div className="card">
        <LoginForm />
      </div>
    </main>
  );
}
