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

# Rótulos padrão exibidos na UI para cada status interno. Podem ser
# sobrescritos em Configurações > Preferências (chave "video_status_labels"
# no app_settings) sem alterar os valores internos usados no código/CSS.
STATUS_LABELS_DEFAULT = {
    STATUS_UPLOADED: "Enviado",
    STATUS_TRANSCRIBING: "Transcrevendo",
    STATUS_REVIEW: "Em revisão",
    STATUS_SOUNDTRACK: "Trilha sonora",
    STATUS_RENDERING: "Renderizando",
    STATUS_DONE: "Concluído",
    STATUS_ERROR: "Erro",
}


def get_status_labels() -> dict:
    """Rótulos efetivos de cada status: padrão mesclado com overrides salvos."""
    from core.database import AppSettingsDB
    overrides = AppSettingsDB.get("video_status_labels", {}) or {}
    labels = dict(STATUS_LABELS_DEFAULT)
    for key, value in overrides.items():
        if key in labels and value:
            labels[key] = value
    return labels


def status_label(status: str) -> str:
    return get_status_labels().get(status, status)


def _job_dir(job_id: str) -> Path:
    d = JOBS_DIR / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _job_file(job_id: str) -> Path:
    return _job_dir(job_id) / "job.json"


def create_job(
    title: str,
    video_format: str,
    template: str,
    audio_filename: str,
    intro_subtitle: str = "",
    outro_text: str = "",
) -> dict:
    job_id = uuid.uuid4().hex[:12]
    job = {
        "id": job_id,
        "title": title,
        "video_format": video_format,  # "landscape" | "vertical"
        "template": template,
        "audio_filename": audio_filename,
        "intro_subtitle": intro_subtitle,
        "outro_text": outro_text,
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
    # Ordena por data de criação (mais novo primeiro)
    jobs.sort(key=lambda j: j.get("created_at", ""), reverse=True)
    return jobs

def update_job_audio_settings(job_id: str, soundtrack: str, bg_volume: float, voice_volume: float):
    job = load_job(job_id)
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

    # 4. Remove a thumbnail do YouTube (se existir) — fica em output/ junto
    # com o vídeo, fora da pasta principal do job removida no passo 1.
    thumbnail_path = youtube_thumbnail_path(job_id)
    if thumbnail_path.exists():
        thumbnail_path.unlink()

    return True


def update_job_youtube_settings(
    job_id: str,
    youtube_title: str = None,
    youtube_description: str = None,
    youtube_visibility: str = None,
    youtube_playlist: str = None,
    youtube_keywords: str = None,
    youtube_video_id: str = None,
    youtube_thumbnail_path: str = None,
) -> dict:
    """
    Atualiza as configurações do YouTube para um job, mesclando com o que já
    existia (campos não informados, ou seja None, são preservados).

    Args:
        job_id: ID do job
        youtube_title: Título do vídeo no YouTube
        youtube_description: Descrição do vídeo
        youtube_visibility: Visibilidade ('private', 'public', 'unlisted')
        youtube_playlist: ID da playlist (opcional)
        youtube_keywords: Keywords separadas por vírgula
        youtube_video_id: ID do vídeo já enviado ao YouTube
        youtube_thumbnail_path: Caminho da thumbnail configurada
    """
    job = load_job(job_id)
    settings = job.get('youtube_settings') or {}

    updates = {
        'title': youtube_title,
        'description': youtube_description,
        'visibility': youtube_visibility,
        'playlist_id': youtube_playlist,
        'keywords': youtube_keywords,
        'video_id': youtube_video_id,
        'thumbnail_path': youtube_thumbnail_path,
    }
    for key, value in updates.items():
        if value is not None:
            settings[key] = value

    settings['updated_at'] = datetime.now(timezone.utc).isoformat()
    job['youtube_settings'] = settings
    save_job(job)
    return job


def update_job_youtube_upload_status(
    job_id: str,
    status: str,
    progress: int = 0,
    error: str | None = None,
    published_info: dict | None = None,
) -> dict:
    """
    Atualiza o progresso do upload de um vídeo para o YouTube, usado pelo
    endpoint de polling para alimentar o feedback visual do upload.

    Args:
        job_id: ID do job
        status: 'uploading' | 'done' | 'error'
        progress: percentual de 0 a 100
        error: mensagem de erro, quando status == 'error'
        published_info: informações do vídeo publicado, quando status == 'done'
    """
    job = load_job(job_id)
    settings = job.get('youtube_settings') or {}
    settings['upload'] = {
        'status': status,
        'progress': progress,
        'error': error,
        'published_info': published_info,
    }
    job['youtube_settings'] = settings
    save_job(job)
    return job


def youtube_thumbnail_path(job_id: str) -> Path:
    """
    Retorna o caminho da thumbnail do YouTube. Fica salva junto com o vídeo
    renderizado em output/, e é removida de lá quando o upload é concluído
    (assim como o próprio vídeo) para não ocupar espaço em disco à toa.
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR / f"{job_id}_thumbnail.png"


def youtube_published_info(job: dict) -> dict | None:
    """
    Retorna as informações do vídeo publicado no YouTube, se este job já
    tiver sido enviado com sucesso — usado para trocar a exibição do vídeo
    renderizado localmente pelas informações do vídeo no YouTube.
    """
    settings = job.get('youtube_settings') or {}
    upload = settings.get('upload') or {}

    if upload.get('status') == 'done' and upload.get('published_info'):
        return upload['published_info']

    # Fallback para jobs com um video_id salvo por fora do fluxo de upload
    # com acompanhamento de progresso (ex: registros antigos).
    video_id = settings.get('video_id')
    if video_id:
        return {
            'id': video_id,
            'url': f"https://www.youtube.com/watch?v={video_id}",
            'title': settings.get('title') or job.get('title', ''),
            'thumbnail_url': None,
            'view_count': None,
            'uploaded_at': settings.get('updated_at'),
        }

    return None