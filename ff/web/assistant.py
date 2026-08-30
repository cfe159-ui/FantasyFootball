"""Conversational draft assistant, backed by the Claude Messages API.

The orb's voice loop is three pieces, and only the middle one is Anthropic's:

    speech in   -- the browser's SpeechRecognition, or local Whisper
    reasoning   -- this module: Messages API, with the live draft as context
    speech out  -- the browser's speechSynthesis

There is no realtime/voice endpoint to connect to; the API is text in, text out.
Latency therefore comes down to keeping the request small and the answer short,
which is why this runs at low effort with a tight token cap and streams.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Iterator, List, Optional

MODEL = os.environ.get("FF_ASSISTANT_MODEL", "claude-opus-5")

# Spoken answers must be short. A long reply is worse than a wrong one when a
# text-to-speech voice is reading it aloud on the clock.
MAX_TOKENS = 700

SYSTEM = """You are a fantasy football draft assistant speaking aloud to one \
manager during a live draft. Your answers are read out by a speech synthesizer, \
so they must be brief and plain.

Rules:
- Two or three sentences at most. No lists, no markdown, no headings.
- Lead with the recommendation, then one reason. Numbers are fine; keep them round.
- You have the manager's live board below. Use it. Never invent a player, a \
projection, or an injury.
- If the board does not answer the question, say so plainly rather than guessing.
- The manager can see the table on screen, so do not read it back to them.
"""


class AssistantUnavailable(RuntimeError):
    pass


def api_key() -> Optional[str]:
    return os.environ.get("ANTHROPIC_API_KEY") or None


def available() -> bool:
    return bool(api_key())


def _client():
    try:
        import anthropic
    except ImportError as exc:  # noqa: BLE001
        raise AssistantUnavailable("anthropic SDK not installed") from exc
    if not api_key():
        raise AssistantUnavailable(
            "No ANTHROPIC_API_KEY. Add one to .env to enable the voice assistant."
        )
    return anthropic.Anthropic()


def _fmt_player(p: Dict) -> str:
    bits = [f"{p.get('name')} ({p.get('position')}-{p.get('team')})"]
    if p.get("vor") is not None:
        bits.append(f"VOR {p['vor']:+.0f}")
    if p.get("points") is not None:
        bits.append(f"{p['points']:.0f}pts")
    if p.get("position_rank"):
        bits.append(f"{p.get('position')}{p['position_rank']}")
    if p.get("tier"):
        bits.append(f"tier {p['tier']}")
    if p.get("injury"):
        bits.append(str(p["injury"]))
    if p.get("rookie"):
        bits.append("rookie")
    return ", ".join(bits)


def build_context(status: Dict, draft: Optional[Dict],
                  candidates: Optional[List[Dict]] = None,
                  roster: Optional[List[Dict]] = None,
                  rankings: Optional[Dict[str, List[Dict]]] = None,
                  scarcity: Optional[Dict[str, Dict]] = None) -> str:
    """Compact snapshot of the league and board for the system prompt.

    The rankings are included whether or not a draft is running -- most
    questions ("who is the best tight end", "is Nacua worth it") are about the
    board itself, not about a pick on the clock.

    Deliberately terse: every token here is paid for on each turn of the
    conversation, and a voice exchange has many turns.
    """
    lines: List[str] = []
    lg = status.get("league", {})
    lines.append(
        f"LEAGUE: {lg.get('teams')} teams, {lg.get('ppr')} points per reception"
        + (", superflex" if lg.get("superflex") else "")
        + f", starters {lg.get('slots')}"
    )

    if draft and draft.get("active"):
        lines.append(
            f"DRAFT: pick {draft.get('pick_number')} "
            f"(round {draft.get('round')}.{draft.get('slot')}), "
            + ("YOU ARE ON THE CLOCK" if draft.get("my_turn")
               else f"{draft.get('picks_until_mine')} picks until your turn")
        )
        mine = draft.get("my_roster") or []
        lines.append(f"YOUR PICKS SO FAR: {', '.join(mine) if mine else 'none'}")
        needs = {k: v for k, v in (draft.get("needs") or {}).items() if v > 0}
        if needs:
            lines.append("STARTING SLOTS STILL EMPTY: "
                         + ", ".join(f"{k} x{v}" for k, v in needs.items()))
        runs = {k: v for k, v in (draft.get("runs") or {}).items() if v >= 3}
        if runs:
            lines.append("POSITIONAL RUN IN LAST 8 PICKS: "
                         + ", ".join(f"{k} x{v}" for k, v in runs.items()))
        recent = [t.get("name") for t in (draft.get("taken") or [])[-6:]]
        if recent:
            lines.append(f"LAST PICKS OFF THE BOARD: {', '.join(reversed(recent))}")

    if candidates:
        lines.append("BEST AVAILABLE (name, pos, team, value over replacement, "
                     "points added to your starting lineup, players left in his tier):")
        for c in candidates[:12]:
            lines.append(
                f"  {c.get('name')}, {c.get('position')}, {c.get('team')}, "
                f"VOR {c.get('vor'):+.0f}, lineup {c.get('lineup_gain'):+.0f}, "
                f"tier has {c.get('tier_remaining')} left"
                + (", will not last to your next pick"
                   if (c.get("survival") or 1) < 0.3 else "")
            )

    if scarcity:
        lines.append("POSITIONAL SCARCITY (cliff = value lost by waiting; "
                     "replacement rank = where the position becomes streamable):")
        for pos, d in sorted(scarcity.items(),
                             key=lambda kv: -(kv[1].get("cliff") or 0)):
            lines.append(f"  {pos}: cliff {d.get('cliff'):.0f}, "
                         f"replacement at #{d.get('replacement_rank')}")

    if rankings:
        overall = rankings.get("overall") or []
        if overall:
            lines.append("TOP OF THE BOARD (value over replacement, "
                         "under this league's rules):")
            for i, p in enumerate(overall, 1):
                lines.append(f"  {i}. {_fmt_player(p)}")
        for pos in ("QB", "RB", "WR", "TE", "K", "DST"):
            group = rankings.get(pos) or []
            if group:
                lines.append(f"BEST {pos}: " + "; ".join(
                    _fmt_player(p) for p in group))

    if roster:
        lines.append("YOUR ROSTER: " + ", ".join(
            f"{p.get('name')} ({p.get('position')}, {p.get('ppg')} ppg)"
            for p in roster[:20]))

    return "\n".join(lines)


def stream_reply(question: str, context: str,
                 history: Optional[List[Dict[str, str]]] = None,
                 usage_sink: Optional[Dict[str, Any]] = None) -> Iterator[str]:
    """Stream Claude's spoken answer, token by token.

    Caching note: the breakpoint sits after the board, not after the static
    instructions. Those instructions are only ~206 tokens -- below Claude Opus
    5's 512-token minimum cacheable prefix -- so a breakpoint there creates no
    cache entry at all and fails silently. Including the board brings the prefix
    to ~1000 tokens, which does cache, and the board is identical across every
    question asked on the same pick. That is the case worth optimising: asking
    two or three follow-ups while on the clock.

    The cache is invalidated on the next pick, which is correct -- the board
    genuinely changed.
    """
    client = _client()
    messages: List[Dict[str, Any]] = []
    for turn in (history or [])[-6:]:      # a short memory keeps latency down
        role = turn.get("role")
        text = (turn.get("content") or "").strip()
        if role in ("user", "assistant") and text:
            messages.append({"role": role, "content": text})
    messages.append({"role": "user", "content": question})

    with client.messages.stream(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=[
            {"type": "text", "text": SYSTEM},
            # Breakpoint after the board: the instructions alone are too short
            # to reach the cacheable minimum, and the board is stable for every
            # question asked on the same pick.
            {"type": "text", "text": "CURRENT BOARD\n" + context,
             "cache_control": {"type": "ephemeral"}},
        ],
        # Speed matters more than depth for a spoken answer on the clock.
        output_config={"effort": "low"},
        messages=messages,
    ) as stream:
        for chunk in stream.text_stream:
            yield chunk
        if usage_sink is not None:
            u = stream.get_final_message().usage
            usage_sink.update({
                "input_tokens": getattr(u, "input_tokens", 0),
                "output_tokens": getattr(u, "output_tokens", 0),
                "cache_creation_input_tokens":
                    getattr(u, "cache_creation_input_tokens", 0) or 0,
                "cache_read_input_tokens":
                    getattr(u, "cache_read_input_tokens", 0) or 0,
            })
