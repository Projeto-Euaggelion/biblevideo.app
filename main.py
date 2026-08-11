import shutil
from pathlib import Path
import sys
import asyncio

from fastapi import FastAPI, Request, UploadFile, Form, File, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from core import jobs as job_store
from core.config import UPLOADS_DIR, VIDEO_TEMPLATES_DIR
from core.srt_utils import srt_text_to_segments, segments_to_srt_text
from services.transcription import transcribe_audio_to_srt, TranscriptionError
from services.renderer import render_frames, write_concat_file
from services.video_export import export_video, VideoExportError

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

app = FastAPI(title="Bible Video Generator")

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates/app")


def available_video_templates() -> list[str]:
    return sorted(
        d.name for d in VIDEO_TEMPLATES_DIR.iterdir()
        if d.is_dir() and (d / "template.html").exists()
    )


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "video_templates": available_video_templates(),
            "jobs": job_store.list_jobs(),
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
    return templates.TemplateResponse("job.html", {"request": request, "job": job})


@app.get("/jobs/{job_id}/status")
def job_status(job_id: str):
    job = job_store.load_job(job_id)
    if not job:
        raise HTTPException(404, "Job não encontrado.")
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


@app.get("/jobs/{job_id}/segments")
def get_segments(job_id: str):
    path = job_store.srt_path(job_id)
    if not path.exists():
        raise HTTPException(404, "Legenda ainda não gerada para este job.")
    return {"segments": srt_text_to_segments(path.read_text(encoding="utf-8"))}


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


@app.get("/jobs/{job_id}/download")
def download(job_id: str):
    path = job_store.output_video_path(job_id)
    if not path.exists():
        raise HTTPException(404, "Vídeo ainda não renderizado.")
    return FileResponse(path, media_type="video/mp4", filename=f"{job_id}.mp4")
