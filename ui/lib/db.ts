import { Pool } from "pg";

// The Python side writes into its own schema, and the pooled connection string cannot
// carry `options=-csearch_path=...` reliably, so every query names the schema instead.
export const SCHEMA = process.env.XSWARM_DB_SCHEMA ?? "xswarm";

function connectionString(): string {
  const raw = process.env.XSWARM_DATABASE_URL;
  if (!raw) throw new Error("XSWARM_DATABASE_URL is not set");
  // The CLI stores a SQLAlchemy URL; node-postgres wants the plain scheme.
  return raw.replace(/^postgresql\+psycopg:\/\//, "postgresql://");
}

// One pool per lambda instance: Next.js reloads modules in development, so hang it off
// globalThis rather than opening a new pool on every edit.
const globalForPool = globalThis as unknown as { xswarmPool?: Pool };

export function pool(): Pool {
  if (!globalForPool.xswarmPool) {
    globalForPool.xswarmPool = new Pool({
      connectionString: connectionString(),
      max: 3,
      ssl: { rejectUnauthorized: false },
    });
  }
  return globalForPool.xswarmPool;
}

export type DraftRow = {
  id: number;
  stream: string;
  status: string;
  variant: number;
  body: string;
  thread: string[];
  link_reply: string;
  card_url: string;
  alt_text: string;
  features: Record<string, unknown>;
  editor_notes: string[];
  created_at: string;
  article_title: string | null;
  article_url: string | null;
  figure_url: string | null;
  figure_caption: string | null;
  publication_status: string | null;
  scheduled_for: string | null;
  post_url: string | null;
};

export async function drafts(status: string, stream: string): Promise<DraftRow[]> {
  const wheres: string[] = [];
  const params: string[] = [];
  if (status !== "all") {
    params.push(status);
    wheres.push(`d.status = $${params.length}`);
  }
  if (stream !== "all") {
    params.push(stream);
    wheres.push(`d.stream = $${params.length}`);
  }
  const where = wheres.length ? `where ${wheres.join(" and ")}` : "";
  const { rows } = await pool().query<DraftRow>(
    `select d.id, d.stream, d.status, d.variant, d.body, d.thread, d.link_reply,
            d.card_url, d.alt_text, d.features, d.editor_notes, d.created_at,
            a.title as article_title, a.published_url as article_url,
            -- The uploaded copy first: it is the image the post will actually carry.
            -- A paper's own figure already lives on the web, and a care promo that
            -- links its article as a card has no asset of its own but shows its hero.
            coalesce(nullif(asset.url, ''), asset.spec->>'source_url',
                     nullif(a.hero_url, '')) as figure_url,
            asset.spec->>'caption' as figure_caption,
            p.status as publication_status, p.scheduled_for, p.post_url
       from ${SCHEMA}.drafts d
       left join ${SCHEMA}.articles a on a.id = d.article_id
       left join ${SCHEMA}.publications p on p.draft_id = d.id
       left join lateral (
            select url, spec from ${SCHEMA}.assets
             where draft_id = d.id
               and (url <> '' or spec::jsonb ? 'source_url')
             order by (url <> '') desc, id desc limit 1
       ) asset on true
       ${where}
      order by d.created_at desc, d.id desc
      limit 100`,
    params,
  );
  return rows;
}

export async function setStatus(id: number, status: string, note: string): Promise<void> {
  await pool().query(
    `update ${SCHEMA}.drafts
        set status = $2,
            -- The column is json, which has no concatenation operator of its own.
            editor_notes = case when $3 = '' then editor_notes
                                else (editor_notes::jsonb || to_jsonb(array[$3::text]))::json
                           end
      where id = $1`,
    [id, status, note],
  );
}
