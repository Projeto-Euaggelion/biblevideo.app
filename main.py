import shutil
from pathlib import Path
import sys
import asyncio
import os
import json
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
from core.config import UPLOADS_DIR, VIDEO_TEMPLATES_DIR, OUTPUT_DIR
from core.srt_utils import srt_text_to_segments, segments_to_srt_text
from services.transcription import transcribe_audio_to_srt, TranscriptionError
from services.renderer import render_frames, render_edge_screens, write_concat_file, EDGE_SCREEN_FADE_SECONDS
from services.video_export import export_video, VideoExportError

app = FastAPI(title="Bible Video Generator")

class RenderRequest(BaseModel):
    soundtrack: str = ""
    bg_volume: float = 0.1
    voice_volume: float = 1.0

# Configuração dos arquivos estáticos e diretórios de templates da aplicação
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/output", StaticFiles(directory=str(OUTPUT_DIR)), name="output")

templates = Jinja2Templates(directory="templates")

class SaveTemplateRequest(BaseModel):
    name: str          # Nome da pasta do template (ex: "spotify", "manuscrito")
    html_content: str  # Conteúdo do template.html
    css_content: str   # Conteúdo do style.css

# Dados fixos padrão para testes no Editor de Templates
MOCK_CHAPTER_TITLE = "Salmos 23"
MOCK_VERSES = [
    {"verse": 1, "text": "O Senhor é o meu pastor; nada me faltará."},
    {"verse": 2, "text": "Deita-me faz em verdes pastos, guia-me suavemente a águas tranquilas."},
    {"verse": 3, "text": "Refrigera a minha alma; guia-me pelas veredas da justiça, por amor do seu nome."},
    {"verse": 4, "text": "Ainda que eu andasse pelo vale da sombra da morte, não temeria mal algum, porque tu estás comigo; a tua vara e o teu cajado me consolam."},
    {"verse": 5, "text": "Preparas uma mesa perante mim na presença dos meus inimigos, unhas a minha cabeça com óleo, o meu cálice transborda."},
    {"verse": 6, "text": "Certamente que a bondade e a misericórdia me seguirão todos os dias da minha vida; e habitarei na casa do Senhor por longos dias."}
]

def available_video_templates() -> list[str]:
    """Retorna a lista de pastas de templates disponíveis em templates/video/."""
    if not VIDEO_TEMPLATES_DIR.exists():
        return []
    return [
        d.name for d in VIDEO_TEMPLATES_DIR.iterdir()
        if d.is_dir() and not d.name.startswith(".") and not d.name.startswith("_")
    ]

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Página inicial do sistema."""
    # Verifique no seu arquivo core/jobs.py o nome exato da função 
    # que lista os projetos (ex: list_jobs(), get_all_jobs(), etc).
    # Substitua abaixo caso seja diferente.
    jobs_list = job_store.list_jobs() 
    
    return templates.TemplateResponse(
        "app/index.html", 
        {
            "request": request, 
            "jobs": jobs_list  # <-- Enviando a lista para o HTML
        }
    )

@app.get("/jobs/new", response_class=HTMLResponse)
def new_job_page(request: Request):
    return templates.TemplateResponse(
        "app/new_job.html",
        {
            "request": request,
            "video_templates": available_video_templates(),
        },
    )

@app.post("/jobs")
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

    return RedirectResponse(url=f"/jobs/{job['id']}", status_code=303)


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_page(request: Request, job_id: str):
    job = job_store.load_job(job_id)
    if not job:
        raise HTTPException(404, "Job não encontrado.")
    if job["status"] in (job_store.STATUS_SOUNDTRACK, job_store.STATUS_RENDERING):
        return RedirectResponse(url=f"/jobs/{job_id}/soundtrack", status_code=303)
    return templates.TemplateResponse("app/job.html", {"request": request, "job": job})


@app.get("/jobs/{job_id}/soundtrack", response_class=HTMLResponse)
def soundtrack_page(request: Request, job_id: str):
    job = job_store.load_job(job_id)
    if not job:
        raise HTTPException(404, "Job não encontrado.")
    if job["status"] not in (job_store.STATUS_SOUNDTRACK, job_store.STATUS_RENDERING):
        return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)
    return templates.TemplateResponse(
        "app/soundtrack.html",
        {"request": request, "job": job, "soundtracks": list_soundtracks()},
    )


@app.post("/jobs/{job_id}/advance-to-soundtrack")
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


@app.post("/jobs/{job_id}/back-to-review")
def back_to_review(job_id: str):
    job = job_store.load_job(job_id)
    if not job:
        raise HTTPException(404, "Job não encontrado.")
    job = job_store.set_status(job_id, job_store.STATUS_REVIEW)
    return job

@app.get("/jobs/{job_id}/status")
def job_status(job_id: str):
    job = job_store.load_job(job_id)
    if not job:
        raise HTTPException(404, "Job não encontrado.")
    return job

@app.get("/jobs/{job_id}/audio")
def preview_audio(job_id: str):
    """Serve o áudio original enviado, para o player da tela de revisão."""
    job = job_store.load_job(job_id)
    if not job:
        raise HTTPException(404, "Job não encontrado.")
    path = job_store.audio_path(job_id)
    if not path.exists():
        raise HTTPException(404, "Áudio não encontrado.")
    return FileResponse(path, media_type="audio/mpeg")

@app.post("/jobs/{job_id}/audio")
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

@app.post("/jobs/{job_id}/transcribe")
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

@app.post("/jobs/{job_id}/segments")
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

@app.post("/jobs/{job_id}/render")
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
        video_fade_filter = ",".join(fade_parts) if fade_parts else None

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

@app.get("/jobs/{job_id}/video")
def preview_video(job_id: str):
    """Serve o vídeo para o player de preview (streaming, sem forçar download)."""
    path = job_store.output_video_path(job_id)
    if not path.exists():
        raise HTTPException(404, "Vídeo ainda não renderizado.")
    return FileResponse(path, media_type="video/mp4")

@app.get("/jobs/{job_id}/download")
def download(job_id: str):
    path = job_store.output_video_path(job_id)
    if not path.exists():
        raise HTTPException(404, "Vídeo ainda não renderizado.")
    return FileResponse(path, media_type="video/mp4", filename=f"{job_id}.mp4")

@app.get("/template/new", response_class=HTMLResponse)
@app.get("/editor", response_class=HTMLResponse)
async def editor_view(request: Request):
    """Renderiza a interface gráfica do Editor de Templates."""
    templates_list = available_video_templates()
    return templates.TemplateResponse(
        "app/editor.html", 
        {
            "request": request, 
            "templates_list": templates_list
        }
    )

@app.delete("/jobs/{job_id}")
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

@app.post("/jobs/{job_id}/reedit")
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

    # Volta o status para a fase de revisão
    job = job_store.set_status(job_id, job_store.STATUS_REVIEW)
    return job

@app.get("/jobs/{job_id}/segments")
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

@app.post("/api/preview")
async def preview_template(request: Request):
    """Renderiza a prévia do HTML/CSS em tempo real com os dados fixos de teste."""
    data = await request.json()
    
    try:
        template = Template(data.get("html", ""))
        html_rendered = template.render(
            css_content=data.get("css", ""),
            verses=MOCK_VERSES,
            chapter_title=MOCK_CHAPTER_TITLE,
            video_format=data.get("video_format", "vertical")
        )
        return HTMLResponse(content=html_rendered)
    except Exception as e:
        return HTMLResponse(
            content=f"<div style='color:#ff5555; background:#121212; padding:20px; font-family:monospace;'>"
                    f"<h3>⚠️ Erro de Compilação Jinja2/HTML:</h3><p>{str(e)}</p></div>"
        )

@app.post("/template/save")
@app.post("/api/template/save")
async def save_template(data: SaveTemplateRequest):
    """Salva/sobrescreve o template.html e style.css na pasta do template correspondente."""
    folder_name = data.name.lower().strip().replace(" ", "_")
    if not folder_name:
        raise HTTPException(status_code=400, detail="Nome de template inválido")

    template_dir = VIDEO_TEMPLATES_DIR / folder_name
    template_dir.mkdir(parents=True, exist_ok=True)

    # Escreve os arquivos no disco para uso imediato pelo renderer
    (template_dir / "style.css").write_text(data.css_content, encoding="utf-8")
    (template_dir / "template.html").write_text(data.html_content, encoding="utf-8")

    return {"status": "success", "template_name": folder_name}

def list_soundtracks() -> list[str]:
    soundtracks_dir = "static/soundtracks"
    if not os.path.exists(soundtracks_dir):
        return []
    return [f for f in os.listdir(soundtracks_dir) if f.endswith(('.mp3', '.wav'))]


@app.get("/api/soundtracks")
def get_soundtracks():
    return {"soundtracks": list_soundtracks()}