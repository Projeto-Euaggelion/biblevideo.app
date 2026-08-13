import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from core.config import READINGS_DIR, UPLOADS_DIR

# Reconhece linhas de versículo no formato "1. Texto do versículo...". O
# número identifica o versículo para o revisor de legendas e para a
# renderização final, mas não é enviado para a API do ElevenLabs.
VERSE_LINE_RE = re.compile(r"^(\d+)\.\s*(.+)$")


def parse_reading_markdown(content_markdown: str) -> dict:
    """
    Extrai o título (primeira linha não vazia) e os versículos numerados
    (demais linhas no formato "N. texto") do markdown editado pelo usuário.
    """
    lines = [line.strip() for line in content_markdown.splitlines()]
    lines = [line for line in lines if line]

    title = lines[0] if lines else ""
    verses = []
    for line in lines[1:]:
        match = VERSE_LINE_RE.match(line)
        if match:
            verses.append({"number": int(match.group(1)), "text": match.group(2).strip()})

    return {"title": title, "verses": verses}


def _reading_dir(reading_id: str) -> Path:
    d = READINGS_DIR / reading_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _reading_file(reading_id: str) -> Path:
    return _reading_dir(reading_id) / "reading.json"


def create_reading(title: str, content_markdown: str, verses: list) -> dict:
    reading_id = uuid.uuid4().hex[:12]
    reading = {
        "id": reading_id,
        "title": title,
        "content_markdown": content_markdown,
        "verses": verses,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    save_reading(reading)
    return reading


def save_reading(reading: dict) -> None:
    reading["updated_at"] = datetime.now(timezone.utc).isoformat()
    with open(_reading_file(reading["id"]), "w", encoding="utf-8") as f:
        json.dump(reading, f, ensure_ascii=False, indent=2)


def load_reading(reading_id: str) -> dict | None:
    path = _reading_file(reading_id)
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def list_readings() -> list[dict]:
    readings = []
    for d in READINGS_DIR.iterdir():
        if d.is_dir():
            reading = load_reading(d.name)
            if reading:
                readings.append(reading)
    readings.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return readings


def update_reading(reading_id: str, title: str, content_markdown: str, verses: list) -> dict:
    reading = load_reading(reading_id)
    reading["title"] = title
    reading["content_markdown"] = content_markdown
    reading["verses"] = verses
    save_reading(reading)
    return reading


def delete_reading(reading_id: str) -> bool:
    reading_dir = _reading_dir(reading_id)
    if reading_dir.exists():
        shutil.rmtree(reading_dir)

    audio_dir_path = UPLOADS_DIR / reading_id
    if audio_dir_path.exists():
        shutil.rmtree(audio_dir_path)

    return True


def verses_to_speech_text(verses: list) -> str:
    """
    Junta o texto dos versículos em um único texto para a API do ElevenLabs,
    sem a numeração (que é usada só pelo revisor de legendas e pelo vídeo).
    """
    return "\n".join(v["text"] for v in verses)


def audio_dir(reading_id: str) -> Path:
    """
    Diretório onde o áudio gerado para esta leitura é salvo, seguindo a
    mesma lógica de pastas em uploads/ usada para o áudio dos jobs de vídeo.
    """
    d = UPLOADS_DIR / reading_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def audio_file_path(reading_id: str) -> Path:
    return audio_dir(reading_id) / "audio.mp3"


def update_reading_audio_status(reading_id: str, status: str, error: str | None = None) -> dict:
    """
    Atualiza o estado da geração de áudio de uma leitura (idle | generating |
    done | error), usado pelo endpoint de polling da tela de progresso.
    """
    reading = load_reading(reading_id)
    audio = reading.get("audio") or {}
    audio["status"] = status
    audio["error"] = error
    if status == "done":
        audio["generated_at"] = datetime.now(timezone.utc).isoformat()
    reading["audio"] = audio
    save_reading(reading)
    return reading
