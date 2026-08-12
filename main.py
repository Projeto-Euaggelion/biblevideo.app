import shutil
from pathlib import Path
import sys
import asyncio
import os
import json
import threading
from typing import Optional
from fastapi import Request
from pydantic import BaseModel
from jinja2 import Template

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import FastAPI, Request, UploadFile, Form, File, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from core import jobs as job_store
from core.config import UPLOADS_DIR, VIDEO_TEMPLATES_DIR, OUTPUT_DIR, VIDEO_DIMENSIONS
from core.srt_utils import srt_text_to_segments, segments_to_srt_text
from services.transcription import transcribe_audio_to_srt, TranscriptionError
from services.renderer import render_frames, render_edge_screens, render_screen_image, write_concat_file, EDGE_SCREEN_FADE_SECONDS
from services.video_export import export_video, VideoExportError
from services.youtube import YouTubeConfig, YouTubeService, YouTubeError
from services.thumbnail_editor import open_thumbnail_editor, ThumbnailEditorBusyError

app = FastAPI(title="Bible Video Generator")

class RenderRequest(BaseModel):
    soundtrack: str = ""
    bg_volume: float = 0.1
    voice_volume: float = 1.0


class YouTubeMetadataRequest(BaseModel):
    """Request body para configurar metadados do YouTube."""
    title: str
    description: str
    visibility: str = "private"  # public, private, unlisted
    playlist: str = ""
    keywords: str = ""


class YouTubeConfigRequest(BaseModel):
    """Request body para atualizar configurações do YouTube."""
    client_id: str = ""
    client_secret: str = ""
    api_key: str = ""
    redirect_uri: str = ""
    default_title: str = ""
    default_description: str = ""
    default_keywords: str = ""
    default_visibility: str = "private"

# Configuração dos arquivos estáticos e diretórios de templates da aplicação
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/output", StaticFiles(directory=str(OUTPUT_DIR)), name="output")

templates = Jinja2Templates(directory="templates")
templates.env.filters["tojson"] = lambda value: json.dumps(value).replace("<", "\\u003c")

class SaveTemplateRequest(BaseModel):
    name: str          # Nome da pasta do template (ex: "spotify", "manuscrito")
    html_content: str  # Conteúdo do template.html
    css_content: str   # Conteúdo do style.css

def available_video_templates() -> list[str]:
    """Retorna a lista de pastas de templates disponíveis em templates/video/."""
    if not VIDEO_TEMPLATES_DIR.exists():
        return []
    return [
        d.name for d in VIDEO_TEMPLATES_DIR.iterdir()
        if d.is_dir() and not d.name.startswith(".") and not d.name.startswith("_")
    ]

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        "app/index.html", 
        {
            "request": request, 
        }
    )

@app.get("/readings", response_class=HTMLResponse)
def index(request: Request):    
    return templates.TemplateResponse(
        "app/leituras.html",
        {
            "request": request,
        }
    )

@app.get("/soundtracks", response_class=HTMLResponse)
def soundtracks_list_page(request: Request):    
    soundtracks = list_soundtracks_with_details()
    return templates.TemplateResponse(
        "app/trilhas.html",
        {
            "request": request,
            "soundtracks": soundtracks,
        }
    )

@app.get("/videos", response_class=HTMLResponse)
async def index(request: Request):
    jobs_list = job_store.list_jobs() 
    
    return templates.TemplateResponse(
        "app/videos.html", 
        {
            "request": request, 
            "jobs": jobs_list  # <-- Enviando a lista para o HTML
        }
    )

@app.get("/videos/new", response_class=HTMLResponse)
def new_job_page(request: Request):
    return templates.TemplateResponse(
        "app/new_video.html",
        {
            "request": request,
            "video_templates": available_video_templates(),
        },
    )

@app.post("/videos")
def create_job(
    title: str = Form(...),
    video_format: str = Form(...),
    template: str = Form(...),
    audio: UploadFile = File(...),
    intro_subtitle: str = Form(""),
    outro_text: str = Form(""),
):
    if video_format not in ("landscape", "vertical"):
        raise HTTPException(400, "Formato de vídeo inválido.")
    if template not in available_video_templates():
        raise HTTPException(400, "Template de vídeo inválido.")

    job = job_store.create_job(
        title=title,
        video_format=video_format,
        template=template,
        audio_filename=audio.filename,
        intro_subtitle=intro_subtitle,
        outro_text=outro_text,
    )

    job_upload_dir = UPLOADS_DIR / job["id"]
    job_upload_dir.mkdir(parents=True, exist_ok=True)
    dest = job_upload_dir / audio.filename
    with open(dest, "wb") as f:
        shutil.copyfileobj(audio.file, f)

    return RedirectResponse(url=f"/videos/{job['id']}", status_code=303)


@app.get("/videos/{job_id}", response_class=HTMLResponse)
def job_page(request: Request, job_id: str):
    job = job_store.load_job(job_id)
    if not job:
        raise HTTPException(404, "Job não encontrado.")
    if job["status"] in (job_store.STATUS_SOUNDTRACK, job_store.STATUS_RENDERING):
        return RedirectResponse(url=f"/videos/{job_id}/soundtrack", status_code=303)
    return templates.TemplateResponse(
        "app/video.html",
        {
            "request": request,
            "job": job,
            "youtube_published": job_store.youtube_published_info(job),
        },
    )


@app.get("/videos/{job_id}/soundtrack", response_class=HTMLResponse)
def soundtrack_page(request: Request, job_id: str):
    job = job_store.load_job(job_id)
    if not job:
        raise HTTPException(404, "Job não encontrado.")
    if job["status"] not in (job_store.STATUS_SOUNDTRACK, job_store.STATUS_RENDERING):
        return RedirectResponse(url=f"/videos/{job_id}", status_code=303)
    return templates.TemplateResponse(
        "app/soundtrack.html",
        {"request": request, "job": job, "soundtracks": list_soundtracks()},
    )


@app.post("/videos/{job_id}/advance-to-soundtrack")
def advance_to_soundtrack(job_id: str):
    job = job_store.load_job(job_id)
    if not job:
        raise HTTPException(404, "Job não encontrado.")
    if job["status"] != job_store.STATUS_REVIEW:
        raise HTTPException(400, "Este job não está na etapa de revisão da legenda.")
    if not job_store.srt_path(job_id).exists():
        raise HTTPException(400, "Este job ainda não tem legendas revisadas.")
    job = job_store.set_status(job_id, job_store.STATUS_SOUNDTRACK)
    return job


@app.post("/videos/{job_id}/back-to-review")
def back_to_review(job_id: str):
    job = job_store.load_job(job_id)
    if not job:
        raise HTTPException(404, "Job não encontrado.")
    job = job_store.set_status(job_id, job_store.STATUS_REVIEW)
    return job

@app.get("/videos/{job_id}/status")
def job_status(job_id: str):
    job = job_store.load_job(job_id)
    if not job:
        raise HTTPException(404, "Job não encontrado.")
    return job

@app.get("/videos/{job_id}/audio")
def preview_audio(job_id: str):
    """Serve o áudio original enviado, para o player da tela de revisão."""
    job = job_store.load_job(job_id)
    if not job:
        raise HTTPException(404, "Job não encontrado.")
    path = job_store.audio_path(job_id)
    if not path.exists():
        raise HTTPException(404, "Áudio não encontrado.")
    return FileResponse(path, media_type="audio/mpeg")

@app.post("/videos/{job_id}/audio")
def replace_audio(job_id: str, audio: UploadFile = File(...)):
    """Substitui o áudio enviado, para quando o usuário mandou o arquivo
    errado. Só é permitido antes da transcrição ter sido gerada."""
    job = job_store.load_job(job_id)
    if not job:
        raise HTTPException(404, "Job não encontrado.")
    if job["status"] not in (job_store.STATUS_UPLOADED, job_store.STATUS_ERROR):
        raise HTTPException(
            400, "Só é possível substituir o áudio antes da transcrição."
        )

    job_upload_dir = UPLOADS_DIR / job_id
    if job_upload_dir.exists():
        shutil.rmtree(job_upload_dir)
    job_upload_dir.mkdir(parents=True, exist_ok=True)
    dest = job_upload_dir / audio.filename
    with open(dest, "wb") as f:
        shutil.copyfileobj(audio.file, f)

    # descarta uma transcrição anterior (de uma tentativa com erro, por
    # exemplo), já que ela não corresponde mais ao novo áudio
    srt_path = job_store.srt_path(job_id)
    if srt_path.exists():
        srt_path.unlink()

    job["audio_filename"] = audio.filename
    job["status"] = job_store.STATUS_UPLOADED
    job["error"] = None
    job_store.save_job(job)
    return job

@app.post("/videos/{job_id}/transcribe")
def transcribe(job_id: str):
    job = job_store.load_job(job_id)
    if not job:
        raise HTTPException(404, "Job não encontrado.")

    job_store.set_status(job_id, job_store.STATUS_TRANSCRIBING)
    try:
        srt_text = transcribe_audio_to_srt(job_store.audio_path(job_id))
        job_store.srt_path(job_id).write_text(srt_text, encoding="utf-8")
        job = job_store.set_status(job_id, job_store.STATUS_REVIEW)
    except TranscriptionError as e:
        job = job_store.set_status(job_id, job_store.STATUS_ERROR, error=str(e))
        raise HTTPException(502, str(e))
    return job

@app.post("/videos/{job_id}/segments")
def save_segments(job_id: str, payload: dict):
    job = job_store.load_job(job_id)
    if not job:
        raise HTTPException(404, "Job não encontrado.")

    segments = payload.get("segments", [])
    if not segments:
        raise HTTPException(400, "Lista de versículos vazia.")

    srt_text = segments_to_srt_text(segments)
    job_store.srt_path(job_id).write_text(srt_text, encoding="utf-8")
    return {"ok": True}

@app.post("/videos/{job_id}/render")
def render(job_id: str, payload: RenderRequest):
    job = job_store.load_job(job_id)
    if not job:
        raise HTTPException(404, "Job não encontrado.")

    srt_path = job_store.srt_path(job_id)
    if not srt_path.exists():
        raise HTTPException(400, "Este job ainda não tem legendas revisadas.")

    # Salva as preferências de áudio no job.json
    job_store.update_job_audio_settings(
        job_id, payload.soundtrack, payload.bg_volume, payload.voice_volume
    )

    job = job_store.set_status(job_id, job_store.STATUS_RENDERING)
    try:
        segments = srt_text_to_segments(srt_path.read_text(encoding="utf-8"))

        states = render_frames(
            job_id=job_id,
            frames_dir=job_store.frames_dir(job_id),
            template_name=job["template"],
            chapter_title=job["title"],
            segments=segments,
            video_format=job["video_format"],
        )

        intro_states, outro_states = render_edge_screens(
            frames_dir=job_store.frames_dir(job_id),
            template_name=job["template"],
            video_format=job["video_format"],
            chapter_title=job["title"],
            intro_subtitle=job.get("intro_subtitle", ""),
            outro_text=job.get("outro_text", ""),
        )

        all_states = intro_states + states + outro_states

        concat_path = job_store.concat_list_path(job_id)
        write_concat_file(all_states, concat_path)
        output_path = job_store.output_video_path(job_id)

        # Fade entre a tela inicial e a leitura, e entre a leitura e a tela final
        intro_duration = sum(s["duration"] for s in intro_states)
        outro_duration = sum(s["duration"] for s in outro_states)
        total_duration = sum(s["duration"] for s in all_states)

        # Cada fade usa 'enable' para agir só na sua própria janela de tempo.
        # Sem isso, o filtro "fade=t=out" do ffmpeg mantém o vídeo preto
        # indefinidamente após o fim do fade (é assim que ele funciona por
        # padrão), apagando toda a leitura do capítulo até o fade-in seguinte.
        fade_parts = []
        if intro_states:
            fade_out_start = max(intro_duration - EDGE_SCREEN_FADE_SECONDS, 0)
            fade_out_end = fade_out_start + EDGE_SCREEN_FADE_SECONDS
            fade_parts.append(
                f"fade=t=out:st={fade_out_start:.3f}:d={EDGE_SCREEN_FADE_SECONDS}:"
                f"enable='between(t,{fade_out_start:.3f},{fade_out_end:.3f})'"
            )
        if outro_states:
            fade_in_start = max(total_duration - outro_duration, 0)
            fade_in_end = fade_in_start + EDGE_SCREEN_FADE_SECONDS
            fade_parts.append(
                f"fade=t=in:st={fade_in_start:.3f}:d={EDGE_SCREEN_FADE_SECONDS}:"
                f"enable='between(t,{fade_in_start:.3f},{fade_in_end:.3f})'"
            )
        # O vídeo gerado pelo concat demuxer tem poucos frames reais (um por
        # estado, muitos durando vários segundos) — não é CFR. Se a janela do
        # fade cair inteira dentro de um desses frames longos, o filtro só é
        # avaliado uma vez no início do frame e a transição não aparece.
        # "fps=25" força uma taxa constante antes do fade para garantir
        # frames suficientes durante a janela de transição.
        video_fade_filter = "fps=25," + ",".join(fade_parts) if fade_parts else None

        # Resolve o caminho da trilha se o usuário tiver selecionado alguma
        soundtrack_path = None
        if payload.soundtrack:
            soundtrack_path = Path("static/soundtracks") / payload.soundtrack
            if not soundtrack_path.exists():
                soundtrack_path = None # Fallback seguro se o arquivo sumir

        # Passa as configurações de áudio para o export_video
        export_video(
            concat_path=concat_path,
            voice_audio_path=job_store.audio_path(job_id),
            output_path=output_path,
            soundtrack_path=soundtrack_path,
            bg_volume=payload.bg_volume,
            voice_volume=payload.voice_volume,
            video_fade_filter=video_fade_filter,
            voice_delay_seconds=intro_duration,
        )

        job = job_store.set_status(job_id, job_store.STATUS_DONE)
    except (VideoExportError, FileNotFoundError) as e:
        job = job_store.set_status(job_id, job_store.STATUS_ERROR, error=str(e))
        raise HTTPException(500, str(e))
    return job

def _missing_video_detail(job_id: str) -> str:
    job = job_store.load_job(job_id)
    if job and job_store.youtube_published_info(job):
        return "Este vídeo já foi publicado no YouTube e o arquivo local foi removido para liberar espaço."
    return "Vídeo ainda não renderizado."

@app.get("/videos/{job_id}/video")
def preview_video(job_id: str):
    """Serve o vídeo para o player de preview (streaming, sem forçar download)."""
    path = job_store.output_video_path(job_id)
    if not path.exists():
        raise HTTPException(404, _missing_video_detail(job_id))
    return FileResponse(path, media_type="video/mp4")

@app.get("/videos/{job_id}/download")
def download(job_id: str):
    path = job_store.output_video_path(job_id)
    if not path.exists():
        raise HTTPException(404, _missing_video_detail(job_id))
    return FileResponse(path, media_type="video/mp4", filename=f"{job_id}.mp4")

@app.delete("/videos/{job_id}")
def delete_job_endpoint(job_id: str):
    """Exclui um job e todos os seus arquivos físicos."""
    job = job_store.load_job(job_id)
    if not job:
        raise HTTPException(404, "Job não encontrado.")
    
    try:
        job_store.delete_job(job_id)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, f"Erro ao excluir o vídeo: {str(e)}")

@app.get("/api/template/{template_name}")
async def get_template_files(template_name: str):
    """Lê e retorna o HTML e CSS do template selecionado no disco."""
    template_dir = VIDEO_TEMPLATES_DIR / template_name
    if not template_dir.exists():
        raise HTTPException(status_code=404, detail="Template não encontrado")

    html_file = template_dir / "template.html"
    css_file = template_dir / "style.css"

    html_content = html_file.read_text(encoding="utf-8") if html_file.exists() else ""
    css_content = css_file.read_text(encoding="utf-8") if css_file.exists() else ""

    return {
        "name": template_name,
        "html": html_content,
        "css": css_content
    }

@app.post("/videos/{job_id}/reedit")
def reedit_job(job_id: str):
    """Retorna um job finalizado ou com erro de volta para a tela de edição."""
    job = job_store.load_job(job_id)
    if not job:
        raise HTTPException(404, "Job não encontrado.")

    # Se o vídeo já foi renderizado, podemos apagar o arquivo antigo para evitar lixo
    # (Opcional, mas recomendado para economizar espaço)
    video_path = job_store.output_video_path(job_id)
    if video_path.exists():
        video_path.unlink()

    # Se este job já tinha sido publicado no YouTube, o vídeo que vai ser
    # renderizado agora é diferente do que está lá — limpa o estado de
    # publicação para não mostrar o card de "já publicado" apontando para
    # a versão antiga assim que o novo render terminar.
    if job_store.youtube_published_info(job):
        job_store.update_job_youtube_upload_status(job_id, status="idle", progress=0)
        job_store.update_job_youtube_settings(job_id=job_id, youtube_video_id="")

    # Volta o status para a fase de revisão
    job = job_store.set_status(job_id, job_store.STATUS_REVIEW)
    return job

@app.get("/videos/{job_id}/segments")
def get_segments(job_id: str):
    """Retorna os segmentos (versículos) atuais do job para preencher o editor."""
    job = job_store.load_job(job_id)
    if not job:
        raise HTTPException(404, "Job não encontrado.")

    srt_path = job_store.srt_path(job_id)
    if not srt_path.exists():
        # Se por acaso o SRT não existir ainda, retorna uma lista vazia
        return {"segments": []}

    try:
        # Lê o conteúdo do SRT e converte para a lista de dicionários usando o utilitário
        srt_content = srt_path.read_text(encoding="utf-8")
        segments = srt_text_to_segments(srt_content)
        return {"segments": segments}
    except Exception as e:
        raise HTTPException(500, f"Erro ao ler as legendas: {str(e)}")


SOUNDTRACKS_DIR = Path("static/soundtracks")

def ensure_soundtracks_dir():
    """Garante que o diretório de trilhas existe."""
    SOUNDTRACKS_DIR.mkdir(parents=True, exist_ok=True)

def get_soundtrack_id(filename: str) -> str:
    """Gera um ID único para a trilha baseado no nome do arquivo."""
    import hashlib
    return hashlib.md5(filename.encode()).hexdigest()[:12]

def get_metadata_file(filename: str) -> Path:
    """Retorna o caminho do arquivo JSON de metadados para uma trilha."""
    return SOUNDTRACKS_DIR / f".{filename}.json"

def extract_audio_metadata(filepath: Path) -> dict:
    """Extrai metadados de um arquivo de áudio."""
    try:
        from mutagen.id3 import ID3
        from mutagen.wave import WAVE
        from mutagen.oggvorbis import OggVorbis
        from mutagen.mp4 import MP4
    except ImportError:
        return {}
    
    metadata = {
        "artist": None,
        "title": None,
        "album": None,
        "genre": None,
        "duration": None,
    }
    
    try:
        # Tenta diferentes formatos
        ext = filepath.suffix.lower()
        
        if ext == '.mp3':
            try:
                audio = ID3(filepath)
                metadata["artist"] = str(audio.get("TPE1", "")) or None
                metadata["title"] = str(audio.get("TIT2", "")) or None
                metadata["album"] = str(audio.get("TALB", "")) or None
                metadata["genre"] = str(audio.get("TCON", "")) or None
            except:
                pass
        elif ext == '.wav':
            try:
                audio = WAVE(filepath)
                if audio.tags:
                    metadata["artist"] = audio.tags.get("ARTIST", [None])[0]
                    metadata["title"] = audio.tags.get("TITLE", [None])[0]
                    metadata["album"] = audio.tags.get("ALBUM", [None])[0]
                    metadata["genre"] = audio.tags.get("GENRE", [None])[0]
            except:
                pass
        elif ext == '.ogg':
            try:
                audio = OggVorbis(filepath)
                metadata["artist"] = audio.get("artist", [None])[0] if audio else None
                metadata["title"] = audio.get("title", [None])[0] if audio else None
                metadata["album"] = audio.get("album", [None])[0] if audio else None
                metadata["genre"] = audio.get("genre", [None])[0] if audio else None
            except:
                pass
        elif ext == '.m4a':
            try:
                audio = MP4(filepath)
                metadata["artist"] = str(audio.get("©ART", [""])[0]) or None
                metadata["title"] = str(audio.get("©nam", [""])[0]) or None
                metadata["album"] = str(audio.get("©alb", [""])[0]) or None
                metadata["genre"] = str(audio.get("©gen", [""])[0]) or None
            except:
                pass
        
        # Tenta obter duração usando pydub como fallback
        try:
            from pydub import AudioSegment
            audio = AudioSegment.from_file(str(filepath))
            metadata["duration"] = len(audio) / 1000.0  # Converte para segundos
        except:
            pass
    
    except Exception as e:
        print(f"Erro ao extrair metadados de {filepath}: {e}")
    
    return metadata

def save_soundtrack_metadata(filename: str, data: dict) -> None:
    """Salva metadados customizados de uma trilha em JSON."""
    metadata_file = get_metadata_file(filename)
    with open(metadata_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_soundtrack_metadata(filename: str) -> dict:
    """Carrega metadados customizados de uma trilha."""
    metadata_file = get_metadata_file(filename)
    if metadata_file.exists():
        try:
            with open(metadata_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {}
    return {}

def get_soundtrack_by_id(soundtrack_id: str) -> dict | None:
    """Busca uma trilha pelo ID."""
    for st in list_soundtracks_with_details():
        if st["id"] == soundtrack_id:
            return st
    return None

def list_soundtracks() -> list[str]:
    """Retorna apenas os nomes dos arquivos (compatível com versão anterior)."""
    ensure_soundtracks_dir()
    if not SOUNDTRACKS_DIR.exists():
        return []
    return [f for f in os.listdir(SOUNDTRACKS_DIR) if f.endswith(('.mp3', '.wav', '.m4a', '.ogg'))]

def list_soundtracks_with_details() -> list[dict]:
    """Retorna lista de trilhas com detalhes (id, nome, arquivo, tamanho, data)."""
    ensure_soundtracks_dir()
    soundtracks = []
    if not SOUNDTRACKS_DIR.exists():
        return soundtracks
    
    for filename in os.listdir(SOUNDTRACKS_DIR):
        if filename.endswith(('.mp3', '.wav', '.m4a', '.ogg')):
            filepath = SOUNDTRACKS_DIR / filename
            if filepath.is_file():
                stat = filepath.stat()
                # Carrega metadados customizados ou usa padrões
                custom_metadata = load_soundtrack_metadata(filename)
                display_name = custom_metadata.get("customName") or filename.rsplit('.', 1)[0]
                
                soundtracks.append({
                    "id": get_soundtrack_id(filename),
                    "filename": filename,
                    "name": display_name,
                    "customName": custom_metadata.get("customName"),
                    "size": stat.st_size,
                    "size_mb": round(stat.st_size / (1024 * 1024), 2),
                    "modified": stat.st_mtime,
                    "artist": custom_metadata.get("artist"),
                    "album": custom_metadata.get("album"),
                    "genre": custom_metadata.get("genre"),
                    "duration": custom_metadata.get("duration"),
                })
    
    return sorted(soundtracks, key=lambda x: x["modified"], reverse=True)

@app.get("/api/soundtracks")
def get_soundtracks():
    return {"soundtracks": list_soundtracks()}

@app.post("/soundtracks/upload")
def upload_soundtrack(audio: UploadFile = File(...)):
    """Upload de uma nova trilha sonora."""
    ensure_soundtracks_dir()
    
    # Validar extensão
    allowed_extensions = ('.mp3', '.wav', '.m4a', '.ogg')
    if not any(audio.filename.lower().endswith(ext) for ext in allowed_extensions):
        raise HTTPException(400, "Formato de arquivo não suportado. Use MP3, WAV, M4A ou OGG.")
    
    # Salvar arquivo
    dest_path = SOUNDTRACKS_DIR / audio.filename
    try:
        with open(dest_path, "wb") as f:
            shutil.copyfileobj(audio.file, f)
    except Exception as e:
        raise HTTPException(500, f"Erro ao salvar o arquivo: {str(e)}")
    
    # Extrair e salvar metadados
    try:
        extracted_metadata = extract_audio_metadata(dest_path)
        # Salva metadados extraídos (sem customName inicial)
        save_soundtrack_metadata(audio.filename, extracted_metadata)
    except Exception as e:
        print(f"Aviso: não foi possível extrair metadados: {e}")
    
    # Retornar detalhes da trilha criada
    soundtrack = get_soundtrack_by_id(get_soundtrack_id(audio.filename))
    return soundtrack

@app.get("/soundtracks/{soundtrack_id}", response_class=HTMLResponse)
def soundtrack_detail_page(request: Request, soundtrack_id: str):
    """Página de detalhes de uma trilha sonora."""
    soundtrack = get_soundtrack_by_id(soundtrack_id)
    if not soundtrack:
        raise HTTPException(404, "Trilha sonora não encontrada.")
    
    return templates.TemplateResponse(
        "app/soundtrack-detail.html",
        {
            "request": request,
            "soundtrack": soundtrack,
        }
    )

@app.delete("/soundtracks/{soundtrack_id}")
def delete_soundtrack(soundtrack_id: str):
    """Deleta uma trilha sonora."""
    soundtrack = get_soundtrack_by_id(soundtrack_id)
    if not soundtrack:
        raise HTTPException(404, "Trilha sonora não encontrada.")
    
    try:
        filepath = SOUNDTRACKS_DIR / soundtrack["filename"]
        if filepath.exists():
            filepath.unlink()
        # Deleta também o arquivo de metadados
        metadata_file = get_metadata_file(soundtrack["filename"])
        if metadata_file.exists():
            metadata_file.unlink()
        return {"ok": True, "message": "Trilha sonora deletada com sucesso."}
    except Exception as e:
        raise HTTPException(500, f"Erro ao deletar a trilha: {str(e)}")

class UpdateSoundtrackRequest(BaseModel):
    customName: str = ""

@app.patch("/soundtracks/{soundtrack_id}")
def update_soundtrack(soundtrack_id: str, payload: UpdateSoundtrackRequest):
    """Atualiza informações de uma trilha sonora (ex: nome customizado)."""
    soundtrack = get_soundtrack_by_id(soundtrack_id)
    if not soundtrack:
        raise HTTPException(404, "Trilha sonora não encontrada.")
    
    try:
        # Carrega metadados atuais
        metadata = load_soundtrack_metadata(soundtrack["filename"])
        # Atualiza com novos dados
        metadata["customName"] = payload.customName if payload.customName else None
        # Salva
        save_soundtrack_metadata(soundtrack["filename"], metadata)
        # Retorna os detalhes atualizados
        return get_soundtrack_by_id(soundtrack_id)
    except Exception as e:
        raise HTTPException(500, f"Erro ao atualizar a trilha: {str(e)}")

@app.get("/settings", response_class=HTMLResponse)
async def index(request: Request):    
    return templates.TemplateResponse(
        "app/config.html", 
        {
            "request": request, 
        }
    )


# ============================================================================
# ROTAS DO YOUTUBE
# ============================================================================

@app.get("/settings/youtube", response_class=HTMLResponse)
def youtube_settings_page(request: Request):
    """Página de configurações do YouTube."""
    return templates.TemplateResponse(
        "app/youtube-settings.html",
        {
            "request": request,
        }
    )


@app.post("/settings/youtube")
def save_youtube_settings(payload: YouTubeConfigRequest):
    """Salva as configurações do YouTube no banco de dados."""
    try:
        config = YouTubeConfig()
        config.client_id = payload.client_id
        config.client_secret = payload.client_secret
        config.api_key = payload.api_key
        config.redirect_uri = payload.redirect_uri
        config.default_title = payload.default_title
        config.default_description = payload.default_description
        config.default_keywords = payload.default_keywords
        config.default_visibility = payload.default_visibility
        
        # Salva no banco de dados
        config.save()
        
        print(f"✓ Configurações do YouTube salvas no banco de dados")
        
        return {
            "ok": True,
            "message": "Configurações do YouTube salvas com sucesso."
        }
    except Exception as e:
        print(f"✗ Erro ao salvar configurações: {str(e)}")
        raise HTTPException(500, f"Erro ao salvar configurações: {str(e)}")


@app.get("/settings/youtube/config")
def get_youtube_config():
    """Retorna as configurações atuais do YouTube (sem secrets)."""
    from core.database import YouTubeConfigDB
    return YouTubeConfigDB.get_public()


@app.get("/videos/{job_id}/youtube", response_class=HTMLResponse)
def youtube_video_settings_page(request: Request, job_id: str):
    """Página para configurar metadados do vídeo antes de enviar para YouTube."""
    job = job_store.load_job(job_id)
    if not job:
        raise HTTPException(404, "Vídeo não encontrado.")
    
    if job["status"] != job_store.STATUS_DONE:
        raise HTTPException(400, "Vídeo ainda não foi renderizado.")
    
    # Carrega configurações padrão do YouTube (do banco de dados)
    youtube_config = YouTubeConfig()
    
    # Carrega configurações já salvas do vídeo, se existirem (pode ser um
    # dicionário parcial, por exemplo se só a thumbnail já foi enviada)
    youtube_settings = job.get("youtube_settings") or {}

    # Preenche os campos de metadados ainda não configurados com os valores
    # padrão da conta, sem sobrescrever o que já foi salvo (thumbnail, etc.)
    if not youtube_settings.get("title"):
        youtube_settings = {
            **youtube_settings,
            "title": youtube_config.default_title or job.get("title", ""),
            "description": youtube_settings.get("description") or youtube_config.default_description,
            "visibility": youtube_settings.get("visibility") or youtube_config.default_visibility,
            "playlist_id": youtube_settings.get("playlist_id", ""),
            "keywords": youtube_settings.get("keywords") or youtube_config.default_keywords,
        }
    
    target_size = VIDEO_DIMENSIONS.get(job["video_format"], VIDEO_DIMENSIONS["landscape"])

    return templates.TemplateResponse(
        "app/youtube.html",
        {
            "request": request,
            "job": job,
            "youtube_settings": youtube_settings,
            "thumbnail_target_size": target_size,
        }
    )


@app.post("/videos/{job_id}/youtube")
def save_video_youtube_metadata(job_id: str, payload: YouTubeMetadataRequest):
    """Salva os metadados do YouTube para um vídeo."""
    job = job_store.load_job(job_id)
    if not job:
        raise HTTPException(404, "Vídeo não encontrado.")
    
    if job["status"] != job_store.STATUS_DONE:
        raise HTTPException(400, "Vídeo ainda não foi renderizado.")
    
    try:
        # Valida visibilidade
        if payload.visibility not in ("public", "private", "unlisted"):
            raise ValueError("Visibilidade deve ser 'public', 'private' ou 'unlisted'.")
        
        # Salva as configurações do YouTube para o job
        job = job_store.update_job_youtube_settings(
            job_id=job_id,
            youtube_title=payload.title,
            youtube_description=payload.description,
            youtube_visibility=payload.visibility,
            youtube_playlist=payload.playlist,
            youtube_keywords=payload.keywords,
        )
        
        return {"ok": True, "message": "Metadados salvos com sucesso."}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Erro ao salvar metadados: {str(e)}")


@app.get("/auth/youtube")
def youtube_auth_start(job_id: str = ""):
    """Redireciona o usuário para a tela de consentimento do Google."""
    config = YouTubeConfig()
    if not config.is_configured():
        raise HTTPException(400, "YouTube não está configurado. Vá para Configurações > YouTube.")

    service = YouTubeService(config)
    try:
        auth_url = service.get_authorization_url(config.redirect_uri, state=job_id)
    except YouTubeError as e:
        raise HTTPException(400, str(e))
    return RedirectResponse(auth_url)


@app.get("/auth/youtube/callback")
def youtube_auth_callback(code: str = "", state: str = "", error: str = ""):
    """Recebe o retorno do Google, troca o código pelo token e volta para a página do vídeo."""
    target = f"/videos/{state}/youtube" if state else "/settings/youtube"

    if error:
        return RedirectResponse(f"{target}?youtube_auth=error")

    config = YouTubeConfig()
    service = YouTubeService(config)
    try:
        service.exchange_code(config.redirect_uri, code)
    except Exception:
        return RedirectResponse(f"{target}?youtube_auth=error")

    return RedirectResponse(f"{target}?youtube_auth=success")


def _run_youtube_upload(job_id: str, video_path: str, youtube_settings: dict, thumbnail_path: str | None) -> None:
    """Executa o upload em uma thread separada, publicando o progresso no job."""
    config = YouTubeConfig()
    service = YouTubeService(config)

    if not service.load_token():
        job_store.update_job_youtube_upload_status(
            job_id, status="error", error="A autenticação do YouTube expirou. Publique novamente para fazer login."
        )
        return

    try:
        keywords = [k.strip() for k in youtube_settings.get("keywords", "").split(",") if k.strip()]

        def on_progress(pct: int) -> None:
            job_store.update_job_youtube_upload_status(job_id, status="uploading", progress=pct)

        result = service.upload_video(
            video_path=video_path,
            title=youtube_settings["title"],
            description=youtube_settings["description"],
            visibility=youtube_settings["visibility"],
            keywords=keywords,
            playlist_id=youtube_settings.get("playlist_id") or None,
            thumbnail_path=thumbnail_path,
            progress_callback=on_progress,
        )

        info = service.get_video_info(result["id"]) or {}
        published_info = {
            "id": result["id"],
            "url": result["url"],
            "title": info.get("title", result["title"]),
            "thumbnail_url": info.get("thumbnail_url"),
            "view_count": info.get("view_count", "0"),
            "uploaded_at": result["uploaded_at"],
        }

        job_store.update_job_youtube_settings(job_id=job_id, youtube_video_id=result["id"])
        job_store.update_job_youtube_upload_status(
            job_id, status="done", progress=100, published_info=published_info
        )

        # O vídeo (e a thumbnail, se houver) já estão hospedados no YouTube —
        # remove as cópias locais em output/ para liberar espaço em disco.
        try:
            Path(video_path).unlink(missing_ok=True)
        except OSError:
            pass
        if thumbnail_path:
            try:
                Path(thumbnail_path).unlink(missing_ok=True)
            except OSError:
                pass
            job_store.update_job_youtube_settings(job_id=job_id, youtube_thumbnail_path="")
    except Exception as e:
        job_store.update_job_youtube_upload_status(job_id, status="error", error=str(e))


@app.post("/videos/{job_id}/youtube/upload")
def upload_to_youtube(job_id: str):
    """
    Inicia o upload do vídeo para o YouTube em segundo plano.

    O vídeo deve já ter sido renderizado (status = done) e ter
    os metadados configurados via POST /videos/{job_id}/youtube. O progresso
    pode ser acompanhado em GET /videos/{job_id}/youtube/upload/status.
    """
    job = job_store.load_job(job_id)
    if not job:
        raise HTTPException(404, "Vídeo não encontrado.")

    if job["status"] != job_store.STATUS_DONE:
        raise HTTPException(400, "Vídeo ainda não foi renderizado.")

    youtube_settings = job.get("youtube_settings")
    if not youtube_settings or not youtube_settings.get("title"):
        raise HTTPException(400, "Configure os metadados do vídeo antes de fazer upload.")

    current_upload = youtube_settings.get("upload") or {}
    if current_upload.get("status") == "uploading":
        return {"status": "uploading", "progress": current_upload.get("progress", 0)}

    # Verifica se a configuração do YouTube está completa (carrega do banco de dados)
    config = YouTubeConfig()
    if not config.is_configured():
        raise HTTPException(400, "YouTube não está configurado. Vá para Configurações > YouTube.")

    # Verifica se o vídeo renderizado existe
    video_path = job_store.output_video_path(job_id)
    if not video_path.exists():
        raise HTTPException(400, "Arquivo de vídeo não encontrado.")

    # Verifica se já existe um token de autenticação salvo; se não, o
    # front-end deve redirecionar o usuário para a URL de autenticação.
    service = YouTubeService(config)
    if not service.load_token():
        return {
            "status": "awaiting_auth",
            "message": "Autenticação do YouTube necessária.",
            "auth_url": f"/auth/youtube?job_id={job_id}",
        }

    thumb_path = job_store.youtube_thumbnail_path(job_id)
    thumbnail_path = str(thumb_path) if thumb_path.exists() else None

    job_store.update_job_youtube_upload_status(job_id, status="uploading", progress=0)

    thread = threading.Thread(
        target=_run_youtube_upload,
        args=(job_id, str(video_path), dict(youtube_settings), thumbnail_path),
        daemon=True,
    )
    thread.start()

    return {"status": "uploading", "progress": 0}


@app.get("/videos/{job_id}/youtube/upload/status")
def youtube_upload_status(job_id: str):
    """Retorna o progresso atual do upload, para o front-end fazer polling."""
    job = job_store.load_job(job_id)
    if not job:
        raise HTTPException(404, "Vídeo não encontrado.")

    upload = (job.get("youtube_settings") or {}).get("upload") or {"status": "idle", "progress": 0}
    return upload


@app.post("/settings/youtube/reset-token")
def reset_youtube_token():
    """Remove o token de autenticação salvo, forçando novo login no próximo upload."""
    YouTubeService.reset_token()
    return {"ok": True}


@app.get("/videos/{job_id}/youtube/thumbnail")
def get_youtube_thumbnail(job_id: str):
    """Retorna a thumbnail do YouTube se existir."""
    thumbnail_path = job_store.youtube_thumbnail_path(job_id)
    if not thumbnail_path.exists():
        raise HTTPException(404, "Thumbnail não encontrada.")
    return FileResponse(thumbnail_path, media_type="image/png")


def _truncate_for_thumbnail(text: str, max_len: int = 180) -> str:
    """Encurta a descrição para caber razoavelmente como texto inicial da
    thumbnail, cortando em uma palavra inteira."""
    text = (text or "").strip()
    if len(text) <= max_len:
        return text
    return text[:max_len].rsplit(" ", 1)[0].rstrip(",.;:") + "..."


# Espelham os valores fixos de templates/video/_shared/screen.html (mesmo
# template usado para renderizar a tela inicial do vídeo), para posicionar
# os textos iniciais da thumbnail no mesmo lugar em que apareceriam lá.
_SCREEN_TITLE_FONT_SIZE = 60
_SCREEN_SUBTITLE_FONT_SIZE = 36
_SCREEN_PADDING_LEFT = 120
_SCREEN_GAP = 30
_SCREEN_LINE_HEIGHT = 1.2


def _build_intro_style_texts(target_size: tuple[int, int], title: str, description: str) -> list[dict]:
    """
    Calcula título e descrição como itens de texto posicionados no mesmo
    lugar em que a tela inicial do vídeo os exibiria — para que entrem no
    editor como elementos de verdade (arrastáveis, editáveis, selecionáveis),
    em vez de texto fixo "queimado" numa imagem de fundo.
    """
    target_w, target_h = target_size
    title = (title or "").strip()
    description = (description or "").strip()

    title_h = _SCREEN_TITLE_FONT_SIZE * _SCREEN_LINE_HEIGHT
    subtitle_h = _SCREEN_SUBTITLE_FONT_SIZE * _SCREEN_LINE_HEIGHT if description else 0
    gap = _SCREEN_GAP if description else 0
    total_h = title_h + gap + subtitle_h

    start_y = (target_h - total_h) / 2
    x_ratio = _SCREEN_PADDING_LEFT / target_w

    texts = [{
        "text": title or "Título do vídeo",
        "x_ratio": x_ratio,
        "y_ratio": (start_y + title_h / 2) / target_h,
        "font_size_full": _SCREEN_TITLE_FONT_SIZE,
        "color": "#ffffff",
        "anchor": "w",
    }]

    if description:
        texts.append({
            "text": description,
            "x_ratio": x_ratio,
            "y_ratio": (start_y + title_h + gap + subtitle_h / 2) / target_h,
            "font_size_full": _SCREEN_SUBTITLE_FONT_SIZE,
            "color": "#e6e6e6",
            "anchor": "w",
        })

    return texts


@app.post("/videos/{job_id}/youtube/thumbnail/editor")
def open_youtube_thumbnail_editor(job_id: str):
    """
    Abre o editor de thumbnail nativo (Tkinter + Pillow) na máquina que roda
    o servidor. A requisição fica bloqueada até o usuário fechar a janela.

    Se ainda não existir uma thumbnail salva para este vídeo, o editor abre
    com uma imagem de fundo gerada automaticamente no mesmo padrão visual da
    tela inicial do vídeo (mesmo template, cores e grain) e com o título e a
    descrição configurados para o YouTube já posicionados como textos
    editáveis por cima — não como texto fixo desenhado na imagem.
    """
    job = job_store.load_job(job_id)
    if not job:
        raise HTTPException(404, "Vídeo não encontrado.")
    if job["status"] != job_store.STATUS_DONE:
        raise HTTPException(400, "Vídeo ainda não foi renderizado.")

    target_size = VIDEO_DIMENSIONS.get(job["video_format"], VIDEO_DIMENSIONS["landscape"])
    thumbnail_path = job_store.youtube_thumbnail_path(job_id)

    initial_image: Optional[str] = None
    initial_texts: Optional[list] = None
    base_image_path: Optional[Path] = None

    if thumbnail_path.exists():
        initial_image = str(thumbnail_path)
    else:
        youtube_settings = job.get("youtube_settings") or {}
        title = youtube_settings.get("title") or job.get("title", "")
        description = _truncate_for_thumbnail(youtube_settings.get("description", ""))

        base_image_path = OUTPUT_DIR / f"{job_id}_thumbnail_base.png"
        try:
            # Fundo apenas (sem título/subtítulo) — o texto é adicionado
            # depois como elementos editáveis do próprio editor.
            render_screen_image(
                template_name=job["template"],
                video_format=job["video_format"],
                title="",
                subtitle="",
                output_path=base_image_path,
            )
            initial_image = str(base_image_path)
        except Exception:
            # Se a geração da base falhar por qualquer motivo, o editor
            # simplesmente abre com um fundo em branco.
            base_image_path = None

        initial_texts = _build_intro_style_texts(target_size, title, description)

    try:
        saved = open_thumbnail_editor(
            target_size, thumbnail_path, initial_image_path=initial_image, initial_texts=initial_texts
        )
    except ThumbnailEditorBusyError as e:
        raise HTTPException(409, str(e))
    except Exception as e:
        raise HTTPException(500, f"Erro ao abrir o editor de thumbnail: {str(e)}")
    finally:
        if base_image_path and base_image_path.exists():
            base_image_path.unlink()

    if saved:
        job = job_store.load_job(job_id)
        job_store.update_job_youtube_settings(job_id=job_id, youtube_thumbnail_path=str(thumbnail_path))

    return {"ok": True, "saved": saved}


@app.delete("/videos/{job_id}/youtube/thumbnail")
def delete_youtube_thumbnail(job_id: str):
    """Remove a thumbnail salva para este vídeo."""
    job = job_store.load_job(job_id)
    if not job:
        raise HTTPException(404, "Vídeo não encontrado.")

    thumbnail_path = job_store.youtube_thumbnail_path(job_id)
    if thumbnail_path.exists():
        thumbnail_path.unlink()

    job_store.update_job_youtube_settings(job_id=job_id, youtube_thumbnail_path="")
    return {"ok": True}