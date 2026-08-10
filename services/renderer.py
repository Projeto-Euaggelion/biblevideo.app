"""Renderiza o template HTML/CSS escolhido em uma sequência de frames PNG,
um para cada 'estado' de destaque (troca de versículo em destaque).

Como o conteúdo só muda em instantes discretos (mudança de versículo), não
renderizamos a 30/60fps: geramos uma imagem estática por estado e deixamos o
ffmpeg controlar a duração de cada imagem via concat demuxer. Isso é muito
mais rápido e não perde precisão nenhuma, já que não há animação contínua.
"""
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from playwright.sync_api import sync_playwright

from core.config import VIDEO_TEMPLATES_DIR, VIDEO_DIMENSIONS


def build_states(segments: list[dict]) -> list[dict]:
    """Converte a lista de versículos com tempos em estados de tela.

    Um estado = (versículo em destaque, instante de início, duração).
    O destaque de um versículo permanece na tela até o início do próximo
    (estilo teleprompter), não apenas durante sua própria fala.
    """
    segments = sorted(segments, key=lambda s: s["start"])
    states = []

    if segments and segments[0]["start"] > 0.05:
        states.append(
            {"verse": None, "start": 0.0, "duration": segments[0]["start"]}
        )

    for i, seg in enumerate(segments):
        start = seg["start"]
        if i + 1 < len(segments):
            end = segments[i + 1]["start"]
        else:
            end = seg["end"]
        duration = max(end - start, 0.05)
        states.append({"verse": seg["verse"], "start": start, "duration": duration})

    return states


def render_frames(
    job_id: str,
    frames_dir: Path,
    template_name: str,
    chapter_title: str,
    segments: list[dict],
    video_format: str,
) -> list[dict]:
    """Renderiza um PNG por estado e retorna a lista de estados com o
    caminho do arquivo de frame correspondente (na ordem de exibição)."""

    template_dir = VIDEO_TEMPLATES_DIR / template_name
    if not template_dir.exists():
        raise FileNotFoundError(f"Template de vídeo não encontrado: {template_name}")

    css_content = (template_dir / "style.css").read_text(encoding="utf-8")

    env = Environment(loader=FileSystemLoader(str(template_dir)))
    tpl = env.get_template("template.html")

    width, height = VIDEO_DIMENSIONS[video_format]

    verses_ordered = sorted(segments, key=lambda s: s["verse"])
    states = build_states(segments)

    for f in frames_dir.glob("*.png"):
        f.unlink()

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": height})

        for i, state in enumerate(states):
            html = tpl.render(
                css_content=css_content,
                chapter_title=chapter_title,
                current_verse=state["verse"],
                verses=verses_ordered,
                video_format=video_format,
            )
            page.set_content(html, wait_until="load")
            # centraliza o versículo em destaque na tela (efeito teleprômpter)
            page.evaluate(
                "document.querySelector('.verse.active')"
                "?.scrollIntoView({block: 'center', inline: 'nearest'})"
            )
            frame_path = frames_dir / f"{i:05d}.png"
            page.screenshot(path=str(frame_path))
            state["frame"] = frame_path

        browser.close()

    return states


def write_concat_file(states: list[dict], concat_path: Path) -> None:
    """Gera o arquivo de lista para o demuxer 'concat' do ffmpeg, com a
    duração exata de cada frame."""
    lines = []
    for state in states:
        # ffmpeg concat exige path relativo ao arquivo de lista OU absoluto;
        # usamos absoluto para simplicidade.
        lines.append(f"file '{state['frame'].resolve().as_posix()}'")
        lines.append(f"duration {state['duration']:.3f}")

    # O demuxer concat ignora a duration do último item, então repetimos o
    # último frame para garantir que ele apareça pelo tempo certo.
    if states:
        lines.append(f"file '{states[-1]['frame'].resolve().as_posix()}'")

    concat_path.write_text("\n".join(lines), encoding="utf-8")
