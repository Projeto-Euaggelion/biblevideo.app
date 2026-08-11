import subprocess
from pathlib import Path

class VideoExportError(Exception):
    pass

def export_video(
    concat_path: Path,
    voice_audio_path: Path,
    output_path: Path,
    soundtrack_path: Path = None,
    bg_volume: float = 0.1,
    voice_volume: float = 1.0,
    video_fade_filter: str = None,
    voice_delay_seconds: float = 0.0,
) -> None:

    cmd = [
        'ffmpeg', '-y',
        '-f', 'concat',
        '-safe', '0',
        '-i', str(concat_path),
        '-i', str(voice_audio_path)
    ]

    filter_stmts = []

    if video_fade_filter:
        filter_stmts.append(f"[0:v]{video_fade_filter}[v]")
        video_map = '[v]'
    else:
        video_map = '0:v'

    # adelay na voz: atrasa a narração para começar só quando a tela do
    # capítulo aparece (depois da tela inicial). A trilha sonora, por outro
    # lado, toca desde o início do vídeo (inclusive durante a tela inicial).
    # apad no final da cadeia de áudio: garante silêncio durante a tela de
    # encerramento, que estende o vídeo além da duração da narração.
    # O corte no tamanho certo é feito pelo -shortest, limitado pelo vídeo.
    voice_delay_ms = max(int(voice_delay_seconds * 1000), 0)
    voice_chain = f"[1:a]adelay={voice_delay_ms}:all=1,volume={voice_volume}" if voice_delay_ms else f"[1:a]volume={voice_volume}"

    if soundtrack_path and soundtrack_path.exists():
        # Trilha sonora é o terceiro input [2:a]
        cmd.extend(['-i', str(soundtrack_path)])

        # Filtro de equalização e mixagem
        filter_stmts.append(
            f"{voice_chain}[a1];"
            f"[2:a]volume={bg_volume}[a2];"
            f"[a1][a2]amix=inputs=2:duration=first:dropout_transition=2,apad[a]"
        )
    else:
        # Apenas equaliza a voz se não houver trilha
        filter_stmts.append(f"{voice_chain},apad[a]")

    cmd.extend(['-filter_complex', ";".join(filter_stmts)])
    cmd.extend(['-map', video_map, '-map', '[a]'])

    # Configurações de encode (h264 para vídeo, aac para áudio)
    cmd.extend([
        '-c:v', 'libx264',
        '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', 
        '-b:a', '192k',
        '-shortest', 
        str(output_path)
    ])

    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        raise VideoExportError(f"Erro no FFmpeg: {e.stderr}")