"""Generate the same drafts in each candidate voice so a human can pick one.

Not part of the pipeline: run once while choosing `voice.md`.

    .venv/bin/python scripts/voice_bakeoff.py --brief 6 --brief 5 --out voices/bakeoff.md
"""

from __future__ import annotations

import argparse
from pathlib import Path

from xswarm.agents import writer
from xswarm.config import settings
from xswarm.db import SessionLocal
from xswarm.llm import LLM
from xswarm.models import Brief

VOICES = Path(__file__).resolve().parents[1] / "voices"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--brief", type=int, action="append", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    playbook = settings.playbook_path.read_text()
    llm = LLM()
    lines: list[str] = []

    with SessionLocal() as session:
        for card in sorted(VOICES.glob("[a-z]_*.md")):
            voice = card.read_text()
            lines.append(f"\n## Voice {card.stem[0].upper()} — {card.stem.split('_', 1)[1]}\n")
            for brief_id in args.brief:
                brief = session.get(Brief, brief_id)
                if brief is None:
                    continue
                lines.append(f"**{brief.candidate.item.title}**\n")
                for draft in writer.write(brief, llm, voice, playbook):
                    hook = (draft.features or {}).get("hook_style", "?")
                    lines.append(f"- _{hook}_ — {draft.body}\n")
            session.rollback()

    args.out.write_text("\n".join(lines))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
