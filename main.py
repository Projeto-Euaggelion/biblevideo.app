import shutil
from pathlib import Path
import sys
import asyncio
import os
import json
from fastapi import Request
from pydantic import BaseModel
from jinja2 import Template

from fastapi import FastAPI, Request, UploadFile, Form, File, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from core import jobs as job_store
from core.config import UPLOADS_DIR, VIDEO_TEMPLATES_DIR, OUTPUT_DIR
from core.srt_utils import srt_text_to_segments, segments_to_srt_text
from services.transcription import transcribe_audio_to_srt, TranscriptionError
from services.renderer import render_frames, write_concat_file
from services.video_export import export_video, VideoExportError

app = FastAPI(title="Bible Video Generator")

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
        if d.is_dir() and not d.name.startswith(".")
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
    return templates.TemplateResponse("app/job.html", {"request": request, "job": job})

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
def render(job_id: str):
    job = job_store.load_job(job_id)
    if not job:
        raise HTTPException(404, "Job não encontrado.")

    srt_path = job_store.srt_path(job_id)
    if not srt_path.exists():
        raise HTTPException(400, "Este job ainda não tem legendas revisadas.")

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

        concat_path = job_store.concat_list_path(job_id)
        write_concat_file(states, concat_path)

        output_path = job_store.output_video_path(job_id)
        export_video(concat_path, job_store.audio_path(job_id), output_path)

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