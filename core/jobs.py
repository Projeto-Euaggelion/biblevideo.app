import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from core.config import JOBS_DIR, UPLOADS_DIR, OUTPUT_DIR

# Status possíveis de um job, nesta ordem de progressão natural
STATUS_UPLOADED = "uploaded"
STATUS_TRANSCRIBING = "transcribing"
STATUS_REVIEW = "review"
STATUS_SOUNDTRACK = "soundtrack"
STATUS_RENDERING = "rendering"
STATUS_DONE = "done"
STATUS_ERROR = "error"


def _job_dir(job_id: str) -> Path:
    d = JOBS_DIR / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _job_file(job_id: str) -> Path:
    return _job_dir(job_id) / "job.json"


def create_job(title: str, video_format: str, template: str, audio_filename: str) -> dict:
    job_id = uuid.uuid4().hex[:12]
    job = {
        "id": job_id,
        "title": title,
        "video_format": video_format,  # "landscape" | "vertical"
        "template": template,
        "audio_filename": audio_filename,
        "status": STATUS_UPLOADED,
        "error": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    save_job(job)
    return job


def save_job(job: dict) -> None:
    job["updated_at"] = datetime.now(timezone.utc).isoformat()
    with open(_job_file(job["id"]), "w", encoding="utf-8") as f:
        json.dump(job, f, ensure_ascii=False, indent=2)


def load_job(job_id: str) -> dict | None:
    path = _job_file(job_id)
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def list_jobs() -> list[dict]:
    jobs = []
    for d in sorted(JOBS_DIR.iterdir(), reverse=True):
        if d.is_dir():
            job = load_job(d.name)
            if job:
                jobs.append(job)
    return jobs

def update_job_audio_settings(job_id: str, soundtrack: str, bg_volume: float, voice_volume: float):
    job = get_job(job_id)
    job['audio_settings'] = {
        'soundtrack': soundtrack,
        'bg_volume': bg_volume,       # ex: 0.1 (10%)
        'voice_volume': voice_volume  # ex: 1.0 (100%)
    }
    save_job(job)

def audio_path(job_id: str) -> Path:
    job = load_job(job_id)
    return UPLOADS_DIR / job_id / job["audio_filename"]


def srt_path(job_id: str) -> Path:
    return _job_dir(job_id) / "captions.srt"


def frames_dir(job_id: str) -> Path:
    d = _job_dir(job_id) / "frames"
    d.mkdir(parents=True, exist_ok=True)
    return d


def concat_list_path(job_id: str) -> Path:
    return _job_dir(job_id) / "concat.txt"


def output_video_path(job_id: str) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR / f"{job_id}.mp4"


def set_status(job_id: str, status: str, error: str | None = None) -> dict:
    job = load_job(job_id)
    job["status"] = status
    job["error"] = error
    save_job(job)
    return job

def delete_job(job_id: str) -> bool:
    """
    Remove o diretório do job (metadados, SRT, frames), 
    a pasta de uploads (áudio original) e o vídeo final gerado.
    """
    # 1. Remove a pasta principal do job (onde fica o job.json, frames, concat.txt, srt)
    job_dir = _job_dir(job_id)
    if job_dir.exists():
        shutil.rmtree(job_dir)
    
    # 2. Remove a pasta de uploads (onde fica o áudio enviado)
    job_upload_dir = UPLOADS_DIR / job_id
    if job_upload_dir.exists():
        shutil.rmtree(job_upload_dir)
        
    # 3. Remove o vídeo renderizado final (se existir)
    video_path = output_video_path(job_id)
    if video_path.exists():
        video_path.unlink()
        
    return True