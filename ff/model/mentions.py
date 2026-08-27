"""Find what podcasts said about specific players.

A deliberate choice: this retrieves and quotes, it does not score. Collapsing
"I'm a little worried about his workload" into a numeric projection delta is
where this kind of feature usually goes wrong -- the extraction is noisy, there
is no ground truth to validate it against, and the resulting number gets blended
into projections as though it were measured.

What is genuinely useful is retrieval: surface every mention of the players on
your roster or waiver shortlist, with enough surrounding context to judge for
yourself, and flag the ones that sound injury-related.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from ..players import Player, PlayerUniverse
from ..util import norm_name

# Words that suggest a mention concerns availability rather than opinion.
INJURY_TERMS = re.compile(
    r"\b(injur\w*|hurt|questionable|doubtful|out\b|inactive|ir\b|"
    r"hamstring|ankle|knee|groin|shoulder|concussion|calf|quad|hip|foot|"
    r"practice|limited|dnp|snap count|workload|timeshare|committee|"
    r"return\w*|activated|designated)\b", re.I)

OPINION_TERMS = re.compile(
    r"\b(love|like|start\w*|sit\b|bench\w*|buy|sell|fade|avoid|target\w*|"
    r"breakout|bust|sleeper|smash|must[- ]start|league[- ]winner|"
    r"concern\w*|worried|upside|floor|ceiling)\b", re.I)


@dataclass
class Mention:
    player: Player
    show: str
    episode: str
    published: Optional[str]
    timestamp: float
    context: str
    injury_related: bool = False
    opinion_related: bool = False

    @property
    def clock(self) -> str:
        m, s = divmod(int(self.timestamp), 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _name_patterns(players: Sequence[Player]) -> List[Tuple[re.Pattern, Player]]:
    """Regexes for each player: full name always, surname when unambiguous.

    Surnames like Brown, Allen, and Smith collide constantly, so a surname is
    only used when exactly one player of interest carries it.
    """
    surnames: Dict[str, List[Player]] = {}
    for p in players:
        parts = p.name.split()
        if len(parts) >= 2:
            surnames.setdefault(parts[-1].lower(), []).append(p)

    patterns: List[Tuple[re.Pattern, Player]] = []
    for p in players:
        parts = [re.escape(x) for x in p.name.split() if x]
        if not parts:
            continue
        if len(parts) >= 2:
            # Allow flexible whitespace and optional middle tokens.
            full = r"\b" + parts[0] + r"\W+(?:\w+\W+){0,1}" + parts[-1] + r"\b"
            patterns.append((re.compile(full, re.I), p))
            surname = p.name.split()[-1]
            if len(surnames.get(surname.lower(), [])) == 1 and len(surname) > 4:
                patterns.append((re.compile(r"\b" + re.escape(surname) + r"\b", re.I), p))
        else:
            patterns.append((re.compile(r"\b" + parts[0] + r"\b", re.I), p))
    return patterns


def find_mentions(transcripts: Iterable[Dict], players: Sequence[Player],
                  context_seconds: float = 25.0,
                  max_context_chars: int = 420,
                  dedupe_window: float = 60.0) -> List[Mention]:
    """Locate every mention of the given players across transcripts."""
    patterns = _name_patterns(players)
    if not patterns:
        return []

    out: List[Mention] = []
    for transcript in transcripts:
        segments = transcript.get("segments") or []
        if not segments:
            continue
        for i, segment in enumerate(segments):
            text = segment.get("text") or ""
            if not text:
                continue
            for pattern, player in patterns:
                if not pattern.search(text):
                    continue
                # Widen to neighbouring segments so the quote is readable.
                start_time = segment.get("start", 0.0)
                window = [text]
                j = i - 1
                while j >= 0 and start_time - segments[j].get("start", 0.0) <= context_seconds:
                    window.insert(0, segments[j].get("text") or "")
                    j -= 1
                j = i + 1
                while j < len(segments) and segments[j].get("start", 0.0) - start_time <= context_seconds:
                    window.append(segments[j].get("text") or "")
                    j += 1
                context = " ".join(w for w in window if w).strip()
                if len(context) > max_context_chars:
                    # Centre the excerpt on the mention itself.
                    match = pattern.search(context)
                    if match:
                        mid = (match.start() + match.end()) // 2
                        lo = max(0, mid - max_context_chars // 2)
                        context = ("..." if lo else "") + context[lo:lo + max_context_chars] + "..."
                out.append(Mention(
                    player=player,
                    show=transcript.get("show", "?"),
                    episode=transcript.get("title", "?"),
                    published=transcript.get("published"),
                    timestamp=float(start_time),
                    context=context,
                    injury_related=bool(INJURY_TERMS.search(context)),
                    opinion_related=bool(OPINION_TERMS.search(context)),
                ))
                break  # one mention per segment per player
    # Consecutive segments about the same player are one discussion, not many.
    # Without this a single passage yields a mention per segment it spans.
    out.sort(key=lambda m: (m.player.name, m.episode, m.timestamp))
    deduped: List[Mention] = []
    for m in out:
        prev = deduped[-1] if deduped else None
        if (prev and prev.player.name == m.player.name
                and prev.episode == m.episode
                and m.timestamp - prev.timestamp <= dedupe_window):
            # Keep whichever excerpt carries more signal.
            if (m.injury_related or m.opinion_related) and not (
                    prev.injury_related or prev.opinion_related):
                deduped[-1] = m
            continue
        deduped.append(m)
    return deduped


def summarize(mentions: Sequence[Mention]) -> Dict[str, Dict]:
    """Per-player mention counts, for a quick view of who is being discussed."""
    out: Dict[str, Dict] = {}
    for m in mentions:
        entry = out.setdefault(m.player.name, {
            "player": m.player, "total": 0, "injury": 0,
            "opinion": 0, "shows": set()})
        entry["total"] += 1
        entry["injury"] += int(m.injury_related)
        entry["opinion"] += int(m.opinion_related)
        entry["shows"].add(m.show)
    return out
