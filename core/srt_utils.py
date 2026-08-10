"""Conversão entre a legenda SRT (fonte da verdade do tempo) e a lista de
versículos usada pelo restante da aplicação.

Cada entrada do SRT representa um versículo e tem o texto prefixado com o
número do versículo entre colchetes, ex: "[3] No princípio Deus criou..."
"""
import re
import srt as srt_lib
from datetime import timedelta

VERSE_PREFIX_RE = re.compile(r"^\s*\[(\d+)\]\s*(.*)$", re.DOTALL)


def seconds_to_timedelta(seconds: float) -> timedelta:
    return timedelta(seconds=seconds)


def timedelta_to_seconds(td: timedelta) -> float:
    return td.total_seconds()


def srt_text_to_segments(srt_text: str) -> list[dict]:
    """Converte um conteúdo .srt em uma lista de segmentos/versículos."""
    segments = []
    for sub in srt_lib.parse(srt_text):
        match = VERSE_PREFIX_RE.match(sub.content or "")
        if match:
            verse_number = int(match.group(1))
            text = match.group(2).strip()
        else:
            verse_number = sub.index
            text = (sub.content or "").strip()

        segments.append(
            {
                "index": sub.index,
                "verse": verse_number,
                "start": timedelta_to_seconds(sub.start),
                "end": timedelta_to_seconds(sub.end),
                "text": text,
            }
        )
    segments.sort(key=lambda s: s["start"])
    return segments


def segments_to_srt_text(segments: list[dict]) -> str:
    """Converte a lista de segmentos/versículos de volta para .srt."""
    subs = []
    for i, seg in enumerate(sorted(segments, key=lambda s: s["start"]), start=1):
        content = f"[{seg['verse']}] {seg['text']}".strip()
        subs.append(
            srt_lib.Subtitle(
                index=i,
                start=seconds_to_timedelta(float(seg["start"])),
                end=seconds_to_timedelta(float(seg["end"])),
                content=content,
            )
        )
    return srt_lib.compose(subs)
