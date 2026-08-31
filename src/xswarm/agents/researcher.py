"""Autonomous research agent with tool use.

Given a candidate (title + abstract), the agent decides what to investigate:
read the paper PDF, explore a GitHub repo, search the web, or fetch a page.
It calls tools in whatever order makes sense and stops when it has enough
context, or when it runs out of steps.

The result is stored on `item.signals["research_context"]` so the Analyst
can write a much richer brief than the abstract alone would allow.
"""

from __future__ import annotations

import io
import json
import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy.orm import Session

from ..config import settings
from ..llm import LLM
from ..models import Candidate

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_STEPS = 8  # tool calls per candidate before we force a summary
PDF_MAX_BYTES = 20 * 1024 * 1024
PDF_MAX_CHARS = 30_000
REPO_MAX_CHARS = 15_000
PAGE_MAX_CHARS = 12_000

ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,5})")

TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "read_pdf",
            "description": (
                "Download and extract text from a PDF URL. Best for arXiv papers. "
                "Returns the full text of the paper (up to ~30k chars)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Direct URL to the PDF file.",
                    }
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "explore_repo",
            "description": (
                "Clone a GitHub repository and read its README, config files, "
                "and key source files. Returns the concatenated content."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "GitHub repository URL.",
                    }
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web for discussions, benchmarks, or related work. "
                "Returns titles, URLs, and snippets of the top results."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query.",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_page",
            "description": (
                "Fetch a web page and extract its text content. "
                "Useful for blog posts, announcements, or documentation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL of the page to fetch.",
                    }
                },
                "required": ["url"],
            },
        },
    },
]

SYSTEM_PROMPT = """\
You are a research assistant preparing deep context for a social media writer.

Given a candidate item (title, abstract, URL), investigate it thoroughly using
the tools available to you. Your goal is to gather concrete, specific information
that goes beyond the abstract: exact numbers from experiments, how the method
actually works, what the code looks like, what the community thinks.

Strategy:
- For arXiv papers: read the PDF to get methods, results, and limitations.
- For GitHub repos: explore the code to understand architecture and maturity.
- For any item: search the web if you want community reactions or comparisons.
- Follow interesting leads: if the paper mentions a repo, explore it. If the
  repo references a paper, read it.

When you have gathered enough context, respond with your final research summary.
Do NOT call any more tools once you are ready to summarize. Just write the summary.

Your summary should include:
1. CORE METHOD: How it actually works (not just claims).
2. KEY RESULTS: Specific numbers, comparisons to baselines.
3. CODE/IMPLEMENTATION: What the code reveals (if you explored a repo).
4. COMMUNITY SIGNAL: What others are saying (if you searched).
5. LIMITATIONS: What the authors admit or what you noticed.
6. SURPRISING FINDINGS: Anything unexpected.

Be specific. Include numbers. Max 800 words."""


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def _tool_read_pdf(url: str) -> str:
    """Download and extract text from a PDF."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return "Error: pypdf not installed."

    # Normalize arXiv URLs
    if "arxiv.org/abs/" in url:
        match = ARXIV_ID_RE.search(url)
        if match:
            url = f"https://arxiv.org/pdf/{match.group(1)}"
    elif "arxiv.org" in url and not url.endswith(".pdf"):
        match = ARXIV_ID_RE.search(url)
        if match:
            url = f"https://arxiv.org/pdf/{match.group(1)}"

    try:
        with httpx.Client(timeout=60, follow_redirects=True) as client:
            resp = client.get(
                url, headers={"User-Agent": "xswarm/0.1 research-agent"}
            )
            resp.raise_for_status()
            if len(resp.content) > PDF_MAX_BYTES:
                return f"Error: PDF too large ({len(resp.content)} bytes)."

        reader = PdfReader(io.BytesIO(resp.content))
        pages: list[str] = []
        chars = 0
        for page in reader.pages:
            text = page.extract_text() or ""
            pages.append(text)
            chars += len(text)
            if chars > PDF_MAX_CHARS:
                break
        result = "\n\n".join(pages)[:PDF_MAX_CHARS]
        return result if result.strip() else "Error: PDF contained no extractable text."
    except Exception as exc:
        return f"Error fetching/parsing PDF: {exc}"


def _tool_explore_repo(url: str) -> str:
    """Clone and read key files from a GitHub repo."""
    match = re.search(r"github\.com/([\w.-]+/[\w.-]+)", url)
    if not match:
        return "Error: not a valid GitHub URL."

    clone_url = f"https://github.com/{match.group(1)}.git"
    tmpdir = Path(tempfile.mkdtemp(prefix="xswarm_repo_"))

    try:
        result = subprocess.run(
            ["git", "clone", "--depth", "1", "--single-branch",
             clone_url, str(tmpdir)],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            return f"Error cloning: {result.stderr[:500]}"

        parts: list[str] = []
        chars = 0

        # README
        for name in ["README.md", "readme.md", "README.rst"]:
            readme = tmpdir / name
            if readme.exists():
                text = readme.read_text(errors="replace")[:8000]
                parts.append(f"=== {name} ===\n{text}")
                chars += len(text)
                break

        # Build config
        for name in ["pyproject.toml", "setup.py", "Cargo.toml",
                      "package.json"]:
            cfg = tmpdir / name
            if cfg.exists():
                text = cfg.read_text(errors="replace")[:2000]
                parts.append(f"=== {name} ===\n{text}")
                chars += len(text)
                break

        # Key source files
        src_patterns = [
            "main.py", "app.py", "run.py", "train.py", "inference.py",
            "src/**/main.py", "src/**/__init__.py", "*.py",
        ]
        seen: set[str] = set()
        for pattern in src_patterns:
            if chars > REPO_MAX_CHARS:
                break
            for f in sorted(tmpdir.glob(pattern))[:3]:
                rel = str(f.relative_to(tmpdir))
                if rel in seen or f.stat().st_size > 50_000:
                    continue
                seen.add(rel)
                try:
                    text = f.read_text(errors="replace")[:3000]
                    parts.append(f"=== {rel} ===\n{text}")
                    chars += len(text)
                except Exception:
                    continue
                if chars > REPO_MAX_CHARS:
                    break

        if not parts:
            return "Repo cloned but no readable files found."
        return "\n\n".join(parts)[:REPO_MAX_CHARS]
    except subprocess.TimeoutExpired:
        return "Error: git clone timed out."
    except Exception as exc:
        return f"Error exploring repo: {exc}"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _tool_web_search(query: str) -> str:
    """Search the web. Requires XSWARM_TAVILY_API_KEY."""
    api_key = settings.tavily_api_key
    if not api_key:
        return (
            "Web search unavailable (no XSWARM_TAVILY_API_KEY configured). "
            "Try using fetch_page on a specific URL instead."
        )
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": api_key,
                    "query": query,
                    "max_results": 5,
                    "include_raw_content": False,
                },
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
            if not results:
                return "No results found."
            lines = []
            for r in results[:5]:
                lines.append(
                    f"- {r.get('title', '(no title)')}\n"
                    f"  URL: {r.get('url', '')}\n"
                    f"  {r.get('content', '')[:400]}"
                )
            return "\n\n".join(lines)
    except Exception as exc:
        return f"Error searching: {exc}"


def _tool_fetch_page(url: str) -> str:
    """Fetch a web page and extract readable text."""
    try:
        with httpx.Client(
            timeout=20, follow_redirects=True,
            headers={"User-Agent": "xswarm/0.1 research-agent"},
        ) as client:
            resp = client.get(url)
            resp.raise_for_status()

        ct = resp.headers.get("content-type", "")
        if "pdf" in ct:
            return _tool_read_pdf(url)

        html = resp.text
        # Strip scripts, styles, nav, footer
        html = re.sub(
            r"<(script|style|nav|footer|header)[^>]*>.*?</\1>",
            "", html, flags=re.DOTALL | re.IGNORECASE,
        )
        # Extract block text
        blocks: list[str] = []
        for tag in ["h1", "h2", "h3", "p", "li", "blockquote", "pre"]:
            for m in re.finditer(
                rf"<{tag}[^>]*>(.*?)</{tag}>",
                html, flags=re.DOTALL | re.IGNORECASE,
            ):
                text = re.sub(r"<[^>]+>", "", m.group(1)).strip()
                if len(text) > 20:
                    blocks.append(text)
        result = "\n\n".join(blocks)[:PAGE_MAX_CHARS]
        return result if result.strip() else "Page fetched but no readable text extracted."
    except Exception as exc:
        return f"Error fetching page: {exc}"


TOOL_DISPATCH: dict[str, Any] = {
    "read_pdf": lambda args: _tool_read_pdf(args["url"]),
    "explore_repo": lambda args: _tool_explore_repo(args["url"]),
    "web_search": lambda args: _tool_web_search(args["query"]),
    "fetch_page": lambda args: _tool_fetch_page(args["url"]),
}


# ---------------------------------------------------------------------------
# Tool-use agent loop (OpenAI function calling)
# ---------------------------------------------------------------------------


def _run_agent_loop(
    llm: LLM,
    candidate: Candidate,
) -> str | None:
    """Run the ReAct loop: the model calls tools until it produces a summary."""
    if llm.dry_run or llm.provider != "openai":
        return None

    item = candidate.item
    user_msg = (
        f"Research this item and produce a deep summary.\n\n"
        f"Title: {item.title}\n"
        f"Source: {item.source}\n"
        f"URL: {item.url}\n"
        f"Authors: {', '.join(item.authors[:8])}\n\n"
        f"Abstract:\n{item.summary[:3000]}"
    )

    model = settings.openai_strong_model
    client = llm._openai()

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    for step in range(MAX_STEPS):
        response = client.chat.completions.create(
            model=model,
            max_tokens=2000,
            messages=messages,
            tools=TOOL_DEFS,
        )
        llm._track("researcher", model, response.usage)
        choice = response.choices[0]

        # If the model is done (no tool calls), return its text
        if choice.finish_reason == "stop" or not choice.message.tool_calls:
            return choice.message.content

        # Process tool calls
        messages.append(choice.message)  # assistant message with tool_calls
        for tool_call in choice.message.tool_calls:
            fn_name = tool_call.function.name
            try:
                fn_args = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError:
                fn_args = {}

            log.info(
                "researcher step %d: %s(%s)",
                step + 1, fn_name,
                ", ".join(f"{k}={v!r}" for k, v in fn_args.items()),
            )

            handler = TOOL_DISPATCH.get(fn_name)
            if handler:
                result = handler(fn_args)
            else:
                result = f"Unknown tool: {fn_name}"

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result[:20000],  # cap tool output
            })

    # Exceeded max steps; ask for a final summary
    messages.append({
        "role": "user",
        "content": "You've used all your research steps. Write your final summary now.",
    })
    response = client.chat.completions.create(
        model=model,
        max_tokens=1500,
        messages=messages,
    )
    llm._track("researcher", model, response.usage)
    return response.choices[0].message.content


# ---------------------------------------------------------------------------
# Fallback for dry-run / Anthropic
# ---------------------------------------------------------------------------


def _fallback_research(candidate: Candidate) -> dict:
    """Minimal research without an LLM: just note what could be researched."""
    context: dict = {}
    url = candidate.item.url
    if "arxiv.org" in url:
        match = ARXIV_ID_RE.search(url)
        if match:
            pdf_text = _tool_read_pdf(url)
            if not pdf_text.startswith("Error"):
                context["pdf_raw"] = pdf_text[:5000]
    if "github.com" in url:
        repo_text = _tool_explore_repo(url)
        if not repo_text.startswith("Error"):
            context["repo_raw"] = repo_text[:5000]
    return context


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def research_candidate(candidate: Candidate, llm: LLM) -> dict:
    """Run the autonomous research agent on one candidate."""
    summary = _run_agent_loop(llm, candidate)
    if summary:
        return {"research_summary": summary}
    # Fallback: raw content without LLM summarization
    return _fallback_research(candidate)


def run(
    session: Session, llm: LLM, candidates: list[Candidate],
) -> list[Candidate]:
    """Enrich candidates with deep research context."""
    for candidate in candidates:
        context = research_candidate(candidate, llm)
        if context:
            signals = dict(candidate.item.signals or {})
            signals["research_context"] = context
            candidate.item.signals = signals
            log.info(
                "researched candidate %d: %s",
                candidate.id, ", ".join(context.keys()),
            )
        else:
            log.info(
                "candidate %d: no research context gathered",
                candidate.id,
            )
    session.flush()
    log.info("researched %d candidates", len(candidates))
    return candidates
