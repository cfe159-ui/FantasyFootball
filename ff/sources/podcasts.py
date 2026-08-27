"""Fantasy podcast ingestion: RSS -> audio -> local transcript.

Episodes are downloaded from public RSS feeds and transcribed on this machine,
which is what any podcast app does. Nothing is redistributed. Transcripts are
copyrighted material, so `data/transcripts/` and `data/audio/` are gitignored
and must never be committed -- this repository is public.

Transcription uses faster-whisper, which bundles audio decoding through PyAV
and so needs no system ffmpeg.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import requests

from ..util import DATA_DIR, get_json

AUDIO_DIR = DATA_DIR / "audio"
TRANSCRIPT_DIR = DATA_DIR / "transcripts"

ITUNES_SEARCH = "https://itunes.apple.com/search"

# Shows worth ingesting, matched against the Apple directory by substring.
DEFAULT_SHOWS = (
    "Fantasy Footballers",
    "Fantasy Football Today",
    "Fantasy Focus Football",
    "Establish The Run",
    "FantasyPros",
    "Yahoo Fantasy Forecast",
    "Late-Round Fantasy Football",
)


@dataclass
class Episode:
    show: str
    title: str
    published: Optional[datetime]
    audio_url: str
    guid: str
    duration_seconds: Optional[int] = None

    @property
    def slug(self) -> str:
        base = re.sub(r"[^a-z0-9]+", "-", f"{self.show}-{self.title}".lower())
        return base.strip("-")[:110]


def discover_feeds(shows: Iterable[str] = DEFAULT_SHOWS,
                   limit: int = 40) -> Dict[str, str]:
    """Resolve show names to RSS feed URLs via Apple's public directory."""
    payload = get_json(ITUNES_SEARCH, params={
        "term": "fantasy football", "entity": "podcast", "limit": limit})
    wanted = [s.lower() for s in shows]
    feeds: Dict[str, str] = {}
    for result in payload.get("results", []):
        name = result.get("collectionName") or ""
        feed = result.get("feedUrl")
        if not feed:
            continue
        if any(w in name.lower() for w in wanted):
            feeds.setdefault(name, feed)
    return feeds


def recent_episodes(feed_url: str, show: str, days: int = 7,
                    limit: int = 5) -> List[Episode]:
    """Episodes published within the last `days`, newest first."""
    import feedparser

    parsed = feedparser.parse(feed_url)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out: List[Episode] = []
    for entry in parsed.entries:
        audio = None
        for link in entry.get("links", []):
            if link.get("rel") == "enclosure" and "audio" in (link.get("type") or ""):
                audio = link.get("href")
                break
        if not audio:
            continue
        published = None
        if entry.get("published_parsed"):
            published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        if published and published < cutoff:
            continue
        duration = entry.get("itunes_duration")
        seconds = None
        if duration:
            parts = str(duration).split(":")
            try:
                seconds = sum(int(p) * 60 ** i for i, p in enumerate(reversed(parts)))
            except ValueError:
                seconds = None
        out.append(Episode(show=show, title=entry.get("title", "untitled"),
                           published=published, audio_url=audio,
                           guid=entry.get("id") or audio,
                           duration_seconds=seconds))
        if len(out) >= limit:
            break
    return out


def download(episode: Episode, force: bool = False) -> Path:
    """Fetch episode audio to the local cache."""
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    path = AUDIO_DIR / f"{episode.slug}.mp3"
    if path.exists() and not force and path.stat().st_size > 0:
        return path
    with requests.get(episode.audio_url, stream=True, timeout=120) as resp:
        resp.raise_for_status()
        tmp = path.with_suffix(".part")
        with tmp.open("wb") as fh:
            for chunk in resp.iter_content(chunk_size=1 << 16):
                fh.write(chunk)
        tmp.replace(path)
    return path


def transcript_path(episode: Episode) -> Path:
    return TRANSCRIPT_DIR / f"{episode.slug}.json"


def transcribe(episode: Episode, audio_path: Path, model_size: str = "base",
               force: bool = False) -> Dict:
    """Transcribe locally with faster-whisper, caching the result.

    Runs near real-time on CPU, so a 60-minute episode costs roughly an hour.
    Smaller models trade accuracy for speed; "base" is a reasonable floor for
    catching player names.
    """
    from faster_whisper import WhisperModel

    TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = transcript_path(episode)
    if out_path.exists() and not force:
        try:
            return json.loads(out_path.read_text())
        except json.JSONDecodeError:
            pass

    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, info = model.transcribe(str(audio_path), beam_size=1,
                                      vad_filter=True)
    collected = [{"start": round(s.start, 2), "end": round(s.end, 2),
                  "text": s.text.strip()} for s in segments]
    payload = {
        "show": episode.show,
        "title": episode.title,
        "published": episode.published.isoformat() if episode.published else None,
        "guid": episode.guid,
        "model": model_size,
        "language": info.language,
        "duration": round(info.duration, 1) if info.duration else None,
        "segments": collected,
        "transcribed_at": time.time(),
    }
    out_path.write_text(json.dumps(payload))
    return payload


def load_transcripts() -> List[Dict]:
    """Every transcript already on disk."""
    if not TRANSCRIPT_DIR.exists():
        return []
    out = []
    for path in sorted(TRANSCRIPT_DIR.glob("*.json")):
        try:
            out.append(json.loads(path.read_text()))
        except json.JSONDecodeError:
            continue
    return out
