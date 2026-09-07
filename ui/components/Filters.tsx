"use client";

import { useRouter } from "next/navigation";

const STATUSES = ["ready_for_review", "approved", "scheduled", "rejected", "all"];
const STREAMS = ["all", "ml", "care"];

export function Filters({ status, stream }: { status: string; stream: string }) {
  const router = useRouter();
  const go = (next: { status?: string; stream?: string }) =>
    router.push(
      `/?status=${next.status ?? status}&stream=${next.stream ?? stream}`,
    );
  return (
    <div className="row">
      <select value={status} onChange={(e) => go({ status: e.target.value })}>
        {STATUSES.map((value) => (
          <option key={value} value={value}>
            {value}
          </option>
        ))}
      </select>
      <select value={stream} onChange={(e) => go({ stream: e.target.value })}>
        {STREAMS.map((value) => (
          <option key={value} value={value}>
            {value}
          </option>
        ))}
      </select>
    </div>
  );
}
