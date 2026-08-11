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
    voice_volume: float = 1.0
) -> None:
    
    cmd = [
        'ffmpeg', '-y',
        '-f', 'concat',
        '-safe', '0',
        '-i', str(concat_path),
        '-i', str(voice_audio_path)
    ]

    if soundtrack_path and soundtrack_path.exists():
        # Trilha sonora é o terceiro input [2:a]
        cmd.extend(['-i', str(soundtrack_path)])
        
        # Filtro de equalização e mixagem
        filter_str = (
            f"[1:a]volume={voice_volume}[a1];"
            f"[2:a]volume={bg_volume}[a2];"
            f"[a1][a2]amix=inputs=2:duration=first:dropout_transition=2[a]"
        )
        cmd.extend(['-filter_complex', filter_str])
        cmd.extend(['-map', '0:v', '-map', '[a]'])
    else:
        # Apenas equaliza a voz se não houver trilha
        cmd.extend(['-filter_complex', f"[1:a]volume={voice_volume}[a]"])
        cmd.extend(['-map', '0:v', '-map', '[a]'])

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