"""Renderiza o template HTML/CSS escolhido em uma sequência de frames PNG,
com suporte a animações de transição.

Gera frames contínuos (ex: 30fps) durante os momentos de transição de versículos
e um único frame longo para o restante do tempo de fala, otimizando
drasticamente o tempo de renderização no ffmpeg via concat demuxer.
"""
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from playwright.sync_api import sync_playwright

from core.config import VIDEO_TEMPLATES_DIR, VIDEO_DIMENSIONS


def build_animated_states(segments: list[dict], transition_duration: float = 0.4, fps: int = 30) -> list[dict]:
    """Converte a lista de versículos com tempos em estados de tela,
    mesclando animação (múltiplos frames curtos) e repouso estático (um frame longo).
    """
    segments = sorted(segments, key=lambda s: s["start"])
    states = []
    frame_duration = 1.0 / fps

    # Adiciona estado inicial vazio se o áudio não começar imediatamente[cite: 1]
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
            
        # 2. Fase Estática (Segura a tela no estado final da animação sem gerar frames extras)
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

    # Ordena de forma cronológica para a lista do template
    verses_ordered = sorted(segments, key=lambda s: s["start"])
    states = build_animated_states(segments)

    for f in frames_dir.glob("*.png"):
        f.unlink()

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": height})

        # Renderizamos e injetamos o HTML no Playwright apenas UMA vez para extrema performance
        html = tpl.render(
            css_content=css_content,
            chapter_title=chapter_title,
            verses=verses_ordered,
            video_format=video_format,
        )
        page.set_content(html, wait_until="load")

        # Avalia frame a frame injetando o progresso da animação via JavaScript
        for i, state in enumerate(states):
            # Chama a função nativa do template antigo por fallback ou a nova 'updateFrame' 
            # do estilo Spotify
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


def write_concat_file(states: list[dict], concat_path: Path) -> None:
    """Gera o arquivo de lista para o demuxer 'concat' do ffmpeg, com a
    duração exata de cada frame.[cite: 1]"""
    lines = []
    for state in states:
        # ffmpeg concat exige path relativo ao arquivo de lista OU absoluto;[cite: 1]
        # usamos absoluto para simplicidade.[cite: 1]
        lines.append(f"file '{state['frame'].resolve().as_posix()}'")
        lines.append(f"duration {state['duration']:.3f}")

    # O demuxer concat ignora a duration do último item, então repetimos o[cite: 1]
    # último frame para garantir que ele apareça pelo tempo certo.[cite: 1]
    if states:
        lines.append(f"file '{states[-1]['frame'].resolve().as_posix()}'")

    concat_path.write_text("\n".join(lines), encoding="utf-8")