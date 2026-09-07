import { redirect } from "next/navigation";

import { signedIn } from "@/lib/auth";
import { drafts } from "@/lib/db";
import { runsUrl } from "@/lib/github";
import { DraftCard } from "@/components/DraftCard";
import { Filters } from "@/components/Filters";
import { RunButtons } from "@/components/RunButtons";

export const dynamic = "force-dynamic";

export default async function Page({
  searchParams,
}: {
  searchParams: Promise<{ status?: string; stream?: string }>;
}) {
  if (!(await signedIn())) redirect("/login");
  const params = await searchParams;
  const status = params.status ?? "ready_for_review";
  const stream = params.stream ?? "all";
  const rows = await drafts(status, stream);
  return (
    <main>
      <header className="bar">
        <h1>Review</h1>
        <a href={runsUrl()} target="_blank" rel="noreferrer">
          runs
        </a>
      </header>
      <div className="card">
        <Filters status={status} stream={stream} />
        <RunButtons />
      </div>
      {rows.length === 0 ? <p className="notice">Nothing here.</p> : null}
      {rows.map((row) => (
        <DraftCard key={row.id} draft={row} />
      ))}
    </main>
  );
}
