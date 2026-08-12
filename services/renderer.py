import sys
import asyncio
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from playwright.sync_api import sync_playwright

from core.config import VIDEO_TEMPLATES_DIR, VIDEO_DIMENSIONS

SHARED_TEMPLATES_DIR = VIDEO_TEMPLATES_DIR / "_shared"
EDGE_SCREEN_HOLD_SECONDS = 3.0
EDGE_SCREEN_FADE_SECONDS = 1.0


def build_animated_states(segments: list[dict], transition_duration: float = 0.4, fps: int = 30) -> list[dict]:
    """Converte a lista de versículos com tempos em estados de tela,
    mesclando animação (múltiplos frames curtos) e repouso estático (um frame longo).
    """
    segments = sorted(segments, key=lambda s: s["start"])
    states = []
    frame_duration = 1.0 / fps

    if segments and segments[0]["start"] > 0.05:
        states.append({
            "verse_index": -1,
            "progress": 1.0,
            "duration": segments[0]["start"],
            "verse": None
        })

    for i, seg in enumerate(segments):
        start = seg["start"]
        if i + 1 < len(segments):
            end = segments[i + 1]["start"]
        else:
            end = seg["end"]
        
        duration = max(end - start, 0.05)
        
        # 1. Fase de Transição Animada
        trans_time = min(transition_duration, duration)
        num_trans_frames = int(trans_time * fps)
        
        for f in range(num_trans_frames):
            progress = (f + 1) / num_trans_frames
            states.append({
                "verse_index": i,
                "progress": progress,
                "duration": frame_duration,
                "verse": seg["verse"]
            })
            
        # 2. Fase Estática 
        hold_duration = duration - (num_trans_frames * frame_duration)
        if hold_duration > 0.01:
            states.append({
                "verse_index": i,
                "progress": 1.0,
                "duration": hold_duration,
                "verse": seg["verse"]
            })

    return states

def render_frames(
    job_id: str,
    frames_dir: Path,
    template_name: str,
    chapter_title: str,
    segments: list[dict],
    video_format: str,
) -> list[dict]:
    """Renderiza a sequência híbrida de frames PNG (transições animadas + repousos estáticos)."""

    template_dir = VIDEO_TEMPLATES_DIR / template_name
    if not template_dir.exists():
        raise FileNotFoundError(f"Template de vídeo não encontrado: {template_name}")

    css_content = (template_dir / "style.css").read_text(encoding="utf-8")

    env = Environment(loader=FileSystemLoader(str(template_dir)))
    tpl = env.get_template("template.html")

    width, height = VIDEO_DIMENSIONS[video_format]

    verses_ordered = sorted(segments, key=lambda s: s["start"])
    states = build_animated_states(segments)

    for f in frames_dir.glob("*.png"):
        f.unlink()

    # CORREÇÃO DEFINITIVA PARA WINDOWS:
    # Como o FastAPI roda funções 'def' em threads de trabalho separadas,
    # nós forçamos a política correta exclusivamente nesta thread antes do Playwright.
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": height})

        html = tpl.render(
            css_content=css_content,
            chapter_title=chapter_title,
            verses=verses_ordered,
            video_format=video_format,
        )
        
        page.set_content(html, wait_until="load")

        for i, state in enumerate(states):
            page.evaluate(
                f"if (typeof updateFrame === 'function') {{"
                f"    updateFrame({state['verse_index']}, {state['progress']});"
                f"}} else {{"
                f"    document.querySelector('.verse.active')?.scrollIntoView({{block: 'center', inline: 'nearest'}});"
                f"}}"
            )
            
            frame_path = frames_dir / f"{i:05d}.png"
            page.screenshot(path=str(frame_path))
            state["frame"] = frame_path

        browser.close()

    return states


def render_edge_screens(
    frames_dir: Path,
    template_name: str,
    video_format: str,
    chapter_title: str,
    intro_subtitle: str,
    outro_text: str,
) -> tuple[list[dict], list[dict]]:
    """Renderiza as telas estáticas de abertura (título + subtítulo) e encerramento
    (texto livre), reaproveitando o visual (cores, fonte, grain) do template escolhido."""

    template_dir = VIDEO_TEMPLATES_DIR / template_name
    css_content = (template_dir / "style.css").read_text(encoding="utf-8")

    env = Environment(loader=FileSystemLoader(str(SHARED_TEMPLATES_DIR)))
    tpl = env.get_template("screen.html")

    width, height = VIDEO_DIMENSIONS[video_format]

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    intro_states: list[dict] = []
    outro_states: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": height})

        intro_html = tpl.render(
            css_content=css_content,
            title=chapter_title,
            subtitle=intro_subtitle,
            video_format=video_format,
        )
        page.set_content(intro_html, wait_until="load")
        intro_frame = frames_dir / "intro.png"
        page.screenshot(path=str(intro_frame))
        intro_states.append({
            "verse_index": None,
            "progress": 1.0,
            "duration": EDGE_SCREEN_HOLD_SECONDS,
            "verse": None,
            "frame": intro_frame,
        })

        outro_html = tpl.render(
            css_content=css_content,
            title="",
            subtitle=outro_text,
            video_format=video_format,
        )
        page.set_content(outro_html, wait_until="load")
        outro_frame = frames_dir / "outro.png"
        page.screenshot(path=str(outro_frame))
        outro_states.append({
            "verse_index": None,
            "progress": 1.0,
            "duration": EDGE_SCREEN_HOLD_SECONDS,
            "verse": None,
            "frame": outro_frame,
        })

        browser.close()

    return intro_states, outro_states


def write_concat_file(states: list[dict], concat_path: Path) -> None:
    """Gera o arquivo de lista para o demuxer 'concat' do ffmpeg, com a duração exata de cada frame."""
    lines = []
    for state in states:
        lines.append(f"file '{state['frame'].resolve().as_posix()}'")
        lines.append(f"duration {state['duration']:.3f}")

    if states:
        lines.append(f"file '{states[-1]['frame'].resolve().as_posix()}'")

    concat_path.write_text("\n".join(lines), encoding="utf-8")