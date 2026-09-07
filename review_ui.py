"""Streamlit UI for reviewing x-swarm drafts, styled like X/Twitter cards.

Run with:  .venv/bin/streamlit run review_ui.py
"""

from __future__ import annotations

import base64
import html as html_mod
import os
from pathlib import Path

import streamlit as st
from sqlalchemy import select

from xswarm.agents import publisher
from xswarm.agents.writer import revise
from xswarm.care import promoter
from xswarm.db import init_db, session_scope
from xswarm.llm import LLM
from xswarm.models import Draft, Publication

st.set_page_config(
    page_title="x-swarm review",
    layout="centered",
    initial_sidebar_state="collapsed",
)


def locked() -> bool:
    """Whether to stop here and ask for the passphrase.

    Approving a draft sends it to a real account, so a UI reachable from the internet
    needs a door. Set `XSWARM_UI_PASSWORD` to hang one; leave it unset to run open, which
    is what a laptop wants.
    """
    expected = os.getenv("XSWARM_UI_PASSWORD", "")
    if not expected or st.session_state.get("unlocked"):
        return False
    typed = st.text_input("Passphrase", type="password")
    if typed and typed == expected:
        st.session_state["unlocked"] = True
        return False
    if typed:
        st.error("Not that one.")
    return True


if locked():
    st.stop()

init_db()

MAX_CHARS = 270
# A LinkedIn post is written to a different budget than a non-premium X post, so showing
# both against 270 marks every LinkedIn draft as OVER when it is inside its own limit.
LINKEDIN_MAX_CHARS = promoter.MAX_LINKEDIN_CHARS


def char_limit(draft: Draft | None) -> int:
    if draft is not None and (draft.features or {}).get("channel") == "linkedin":
        return LINKEDIN_MAX_CHARS
    return MAX_CHARS


# ---------------------------------------------------------------------------
# Sidebar filters
# ---------------------------------------------------------------------------

with st.sidebar:
    st.markdown("### Generate")
    col_ml, col_care = st.columns(2)
    with col_ml:
        run_ml = st.button("Run ML", use_container_width=True)
    with col_care:
        run_care = st.button("Run Care", use_container_width=True)

    if run_ml or run_care:
        stream_label = "ML" if run_ml else "Care"
        with st.spinner(f"Running {stream_label} pipeline (this takes a few minutes)..."):
            try:
                if run_ml:
                    from xswarm.graph import run_pipeline

                    result = run_pipeline()
                    cost = result.get("cost_usd", 0)
                    ready = len(result.get("ready_ids", []))
                    st.success(f"ML pipeline done! {ready} drafts ready, ${cost:.3f} spent")
                else:
                    from xswarm.care.graph import run_pipeline as run_care_pipeline

                    result = run_care_pipeline()
                    st.success("Care pipeline done!")
            except Exception as e:
                st.error(f"Pipeline failed: {e}")
        st.rerun()

    st.divider()
    st.markdown("### Filters")
    STATUS_OPTIONS = ["ready_for_review", "approved", "blocked", "rejected", "drafted"]
    selected_status = st.multiselect("Status", STATUS_OPTIONS, default=["ready_for_review"])
    stream_filter = st.selectbox("Stream", ["all", "ml", "care", "own"])
    limit = st.slider("Max drafts", 5, 100, 30)

# ---------------------------------------------------------------------------
# Load drafts
# ---------------------------------------------------------------------------


def load_drafts() -> list[Draft]:
    with session_scope() as session:
        query = (
            select(Draft)
            .where(Draft.status.in_(selected_status))
            .order_by(Draft.created_at.desc())
            .limit(limit)
        )
        if stream_filter != "all":
            query = query.where(Draft.stream == stream_filter)
        drafts = list(session.scalars(query).all())
        for d in drafts:
            _ = d.brief
            if d.brief:
                _ = d.brief.candidate
                if d.brief.candidate:
                    _ = d.brief.candidate.item
            _ = d.assets
            _ = d.article
        session.expunge_all()
    return drafts


drafts = load_drafts()

if not drafts:
    st.html('<p style="text-align:center;color:#71767b;padding:60px;">No drafts match.</p>')
    st.stop()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def esc(text: str) -> str:
    return html_mod.escape(text)


def badge_color(status: str) -> tuple[str, str]:
    return {
        "ready_for_review": ("#f5a623", "#000"),
        "approved": ("#00ba7c", "#fff"),
        "blocked": ("#f4212e", "#fff"),
        "rejected": ("#71767b", "#fff"),
        "drafted": ("#1d9bf0", "#fff"),
    }.get(status, ("#1d9bf0", "#fff"))


def source_title(draft: Draft) -> str:
    try:
        if draft.brief and draft.brief.candidate and draft.brief.candidate.item:
            return draft.brief.candidate.item.title
    except Exception:
        pass
    return ""


def _data_uri(path: str) -> str | None:
    p = Path(path)
    if not p.exists() or p.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
        return None
    data = base64.b64encode(p.read_bytes()).decode()
    ext = p.suffix.lower().lstrip(".")
    return f"data:image/{'jpeg' if ext == 'jpg' else ext};base64,{data}"


def image_for_draft(draft: Draft) -> str | None:
    for asset in draft.assets or []:
        # The local file when this is the machine that drew it, the uploaded copy when
        # it is not.
        src = _data_uri(asset.path) or (asset.url or None)
        if src:
            return src
    # A care promo carries no asset of its own: the picture it ships with is the hero of
    # the article it links to, which X pulls into the link card.
    if draft.article is not None and draft.article.hero_path:
        return _data_uri(draft.article.hero_path) or (draft.article.hero_url or None)
    return None


def render_post_html(
    body: str,
    *,
    is_main: bool = True,
    thread_label: str = "",
    image_src: str | None = None,
    link_reply: str = "",
    draft: Draft | None = None,
    show_connector_above: bool = False,
) -> str:
    features = (draft.features or {}) if draft else {}
    hook = features.get("hook_style", "")
    pillar = features.get("pillar", "")
    stream = draft.stream if draft else "ml"
    status = draft.status if draft else ""
    created = draft.created_at.strftime("%b %d") if draft and draft.created_at else ""
    title = source_title(draft) if draft else ""

    avatar_letter = "M" if stream == "ml" else ("A" if stream == "care" else "Y")
    avatar_bg = "#1d9bf0" if stream == "ml" else ("#00ba7c" if stream == "care" else "#f5a623")
    handle = "@ml_frontier" if stream == "ml" else ("@alverna" if stream == "care" else "@you")
    name = (
        "ML Frontier" if stream == "ml" else ("Alverna Health" if stream == "care" else "Your Post")
    )

    chars = len(body)
    limit = char_limit(draft)
    over = chars > limit
    chars_color = "#f4212e" if over else "#71767b"

    bg, fg = badge_color(status)

    parts = []

    # Connector line above
    if show_connector_above:
        parts.append('<div style="width:2px;height:20px;background:#2f3336;margin:0 auto;"></div>')

    # Card wrapper
    if is_main:
        border_radius = "16px"
    else:
        border_radius = "0 0 16px 16px"

    parts.append(
        f'<div style="background:#000;border:1px solid #2f3336;border-radius:{border_radius};'
        f"padding:16px;max-width:598px;margin:0 auto;"
        f'font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif;color:#e7e9ea;">'
    )

    # Header (main post only)
    if is_main and draft:
        parts.append(
            f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;">'
            f'  <div style="width:40px;height:40px;border-radius:50%;background:{avatar_bg};'
            f"    display:flex;align-items:center;justify-content:center;font-weight:700;"
            f'    font-size:18px;color:#fff;flex-shrink:0;">{avatar_letter}</div>'
            f'  <div style="flex:1;">'
            f'    <div style="font-weight:700;font-size:15px;color:#e7e9ea;">{esc(name)}</div>'
            f'    <div style="font-size:13px;color:#71767b;">{esc(handle)}</div>'
            f"  </div>"
            f'  <div style="font-size:13px;color:#71767b;">{esc(created)}</div>'
            f"</div>"
        )
        # Badge + tags
        parts.append(
            f'<div style="margin-bottom:8px;">'
            f'  <span style="display:inline-block;padding:2px 10px;border-radius:12px;'
            f'    font-size:12px;font-weight:600;background:{bg};color:{fg};">'
            f"    {esc(status.replace('_', ' '))}</span>"
        )
        if hook:
            parts.append(
                f'  <span style="display:inline-block;padding:2px 8px;border-radius:10px;'
                f"    font-size:11px;background:#16181c;color:#71767b;border:1px solid #2f3336;"
                f'    margin-left:4px;">{esc(hook)}</span>'
            )
        if pillar:
            parts.append(
                f'  <span style="display:inline-block;padding:2px 8px;border-radius:10px;'
                f"    font-size:11px;background:#16181c;color:#71767b;border:1px solid #2f3336;"
                f'    margin-left:4px;">{esc(pillar)}</span>'
            )
        parts.append(
            f'  <span style="display:inline-block;padding:2px 8px;border-radius:10px;'
            f"    font-size:11px;background:#16181c;color:#71767b;border:1px solid #2f3336;"
            f'    margin-left:4px;">#{draft.id}</span>'
            f"</div>"
        )
        # Source reference
        if title:
            parts.append(
                f'<div style="font-size:12px;color:#71767b;margin-bottom:6px;">'
                f"Re: {esc(title)}</div>"
            )

    # Thread label
    if thread_label:
        parts.append(
            f'<div style="font-size:13px;color:#1d9bf0;'
            f'margin-bottom:6px;">{esc(thread_label)}</div>'
        )

    # Body
    parts.append(
        f'<div style="font-size:15px;line-height:1.5;color:#e7e9ea;white-space:pre-wrap;'
        f'word-wrap:break-word;margin-bottom:8px;">{esc(body)}</div>'
    )

    # Char count
    parts.append(
        f'<div style="font-size:12px;color:{chars_color};text-align:right;'
        f'margin-bottom:6px;">{chars}/{limit}{"  OVER" if over else ""}</div>'
    )

    # Image
    if image_src:
        parts.append(
            f'<img src="{image_src}" style="border-radius:16px;border:1px solid #2f3336;'
            f'max-width:100%;margin-bottom:10px;" />'
        )

    # Link reply content
    if link_reply:
        parts.append(
            f'<div style="font-size:14px;color:#1d9bf0;background:#16181c;border-radius:12px;'
            f'padding:10px 14px;word-break:break-all;">{esc(link_reply)}</div>'
        )

    parts.append("</div>")
    return "\n".join(parts)


def render_notes_html(notes: list[str]) -> str:
    items = "".join(
        f'<div style="background:#2c1215;border:1px solid #67000d;border-radius:8px;'
        f"padding:8px 12px;font-size:13px;color:#f4212e;margin:4px 0;"
        f'font-family:-apple-system,sans-serif;">{esc(note)}</div>'
        for note in notes
    )
    return f'<div style="max-width:598px;margin:6px auto;">{items}</div>'


# ---------------------------------------------------------------------------
# Title
# ---------------------------------------------------------------------------

st.html(
    f'<div style="max-width:598px;margin:20px auto 8px auto;font-size:20px;'
    f'font-weight:700;color:#e7e9ea;font-family:-apple-system,sans-serif;">'
    f"Review &middot; {len(drafts)} drafts</div>"
)

# ---------------------------------------------------------------------------
# Render each draft
# ---------------------------------------------------------------------------

for draft in drafts:
    image_src = image_for_draft(draft)

    # Main post card
    st.html(
        render_post_html(
            draft.body,
            is_main=True,
            image_src=image_src,
            draft=draft,
        )
    )

    # Thread posts
    if draft.thread:
        for i, post in enumerate(draft.thread):
            st.html(
                render_post_html(
                    post,
                    is_main=False,
                    thread_label=f"Thread {i + 2}/{len(draft.thread) + 1}",
                    show_connector_above=True,
                )
            )

    # Link reply
    if draft.link_reply:
        st.html(
            render_post_html(
                draft.link_reply,
                is_main=False,
                thread_label="Link reply",
                show_connector_above=True,
            )
        )

    # Editor notes
    if draft.editor_notes:
        st.html(render_notes_html(draft.editor_notes))

    # Brief context
    if draft.brief:
        with st.expander("Brief context (reviewer only)"):
            b = draft.brief
            st.markdown(
                f"**What's new:** {b.whats_new}  \n"
                f"**Key number:** {b.key_number}  \n"
                f"**Caveat:** {b.caveat}  \n"
                f"**Grounded claims:** {', '.join(b.grounded_claims or [])}"
            )

    # Action row
    col_fb = st.columns([1])[0]
    with col_fb:
        feedback = st.text_input(
            "feedback",
            key=f"fb_{draft.id}",
            placeholder="Type feedback, then Revise to rewrite or Approve to ship...",
            label_visibility="collapsed",
        )

    col_revise, col_approve, col_reject, col_publish = st.columns([1, 1, 1, 1])
    failure = st.session_state.pop(f"err_{draft.id}", "")
    if failure:
        # Outside the button columns, or the message wraps at fifteen characters.
        st.error(failure)
    with col_revise:
        can_revise = bool(draft.brief) and draft.status not in ("approved",)
        if st.button(
            "Revise",
            key=f"rv_{draft.id}",
            disabled=not can_revise or not feedback,
            use_container_width=True,
        ):
            with st.spinner("Rewriting with your feedback..."):
                llm = LLM()
                with session_scope() as session:
                    d = session.get(Draft, draft.id)
                    revise(session, d, llm, feedback)
            st.rerun()
    with col_approve:
        if st.button(
            "Approve" if draft.status != "approved" else "Approved",
            key=f"ap_{draft.id}",
            type="primary",
            disabled=draft.status == "approved",
            use_container_width=True,
        ):
            # Approving is the whole gate: the post goes straight onto the Typefully
            # queue, scheduled to send itself, with no second click anywhere.
            with st.spinner("Approving and scheduling in Typefully..."):
                with session_scope() as session:
                    d = session.get(Draft, draft.id)
                    d.status = "approved"
                    if feedback:
                        d.editor_notes = [*d.editor_notes, f"human: {feedback}"]
                try:
                    with session_scope() as session:
                        publisher.run(session, draft_ids=[draft.id])
                except Exception as e:
                    # The draft stays approved and unsent, so Publish can try again.
                    st.session_state[f"err_{draft.id}"] = (
                        f"Approved, but Typefully refused it: {e}. Press Publish to try again."
                    )
            st.rerun()
    with col_reject:
        if st.button(
            "Reject",
            key=f"rj_{draft.id}",
            disabled=draft.status == "rejected",
            use_container_width=True,
        ):
            with session_scope() as session:
                d = session.get(Draft, draft.id)
                d.status = "rejected"
                reason = feedback or "rejected without reason"
                d.editor_notes = [*d.editor_notes, f"human: {reason}"]
            st.rerun()
    with col_publish:
        # Check if already scheduled
        already_scheduled = False
        with session_scope() as session:
            pub = session.query(Publication).filter(Publication.draft_id == draft.id).first()
            if pub:
                already_scheduled = True
        if already_scheduled:
            st.button(
                "Scheduled",
                key=f"pub_{draft.id}",
                disabled=True,
                use_container_width=True,
            )
        elif st.button(
            "Publish",
            key=f"pub_{draft.id}",
            disabled=draft.status != "approved",
            use_container_width=True,
        ):
            with st.spinner("Sending to Typefully..."):
                try:
                    with session_scope() as session:
                        publisher.run(session, dry_run=False, draft_ids=[draft.id])
                except Exception as e:
                    st.session_state[f"err_{draft.id}"] = f"Publish failed: {e}"
                st.rerun()

    st.html('<div style="height:24px;"></div>')

# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------

with session_scope() as session:
    total = session.query(Draft).count()
    ready = session.query(Draft).filter(Draft.status == "ready_for_review").count()
    approved = session.query(Draft).filter(Draft.status == "approved").count()
    blocked = session.query(Draft).filter(Draft.status == "blocked").count()

st.html(
    f'<div style="text-align:center;color:#71767b;font-size:13px;padding:20px 0 40px 0;'
    f'font-family:-apple-system,sans-serif;">'
    f"Total {total} &middot; Ready {ready} &middot; "
    f"Approved {approved} &middot; Blocked {blocked}</div>"
)
