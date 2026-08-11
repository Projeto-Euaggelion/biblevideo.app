import subprocess
from pathlib import Path


class VideoExportError(Exception):
    pass


def export_video(concat_list_path: Path, audio_path: Path, output_path: Path) -> None:
    cmd = [
        "ffmpeg",
        "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_list_path),
        "-i", str(audio_path),
        "-r", "30",
        "-pix_fmt", "yuv420p",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "20",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise VideoExportError(
            f"ffmpeg falhou (código {result.returncode}):\n{result.stderr[-4000:]}"
        )
