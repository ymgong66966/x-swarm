const REPO = process.env.XSWARM_GITHUB_REPO ?? "ymgong66966/x-swarm";
const REF = process.env.XSWARM_GITHUB_REF ?? "main";

/** Start a workflow and return once GitHub has accepted it.
 *
 * Scheduling a draft means talking to Typefully with the same slot rules, image upload
 * and account routing the CLI already implements, and a Vercel function is the wrong
 * place to reimplement that. The UI writes the decision to the database and asks the
 * repository's own workflow to carry it out.
 */
export async function dispatch(workflow: string, inputs: Record<string, string>): Promise<void> {
  const token = process.env.XSWARM_GITHUB_TOKEN;
  if (!token) throw new Error("XSWARM_GITHUB_TOKEN is not set");
  const response = await fetch(
    `https://api.github.com/repos/${REPO}/actions/workflows/${workflow}/dispatches`,
    {
      method: "POST",
      headers: {
        accept: "application/vnd.github+json",
        authorization: `Bearer ${token}`,
        "x-github-api-version": "2022-11-28",
      },
      body: JSON.stringify({ ref: REF, inputs }),
    },
  );
  if (!response.ok) {
    throw new Error(`${workflow}: GitHub said ${response.status} ${await response.text()}`);
  }
}

export function runsUrl(): string {
  return `https://github.com/${REPO}/actions`;
}
