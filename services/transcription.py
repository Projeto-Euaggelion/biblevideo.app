"""Transcrição do áudio via Faster Whisper, gerando legendas SRT.
O modelo roda localmente, garantindo timestamps precisos sem depender de APIs externas.
"""
import math
from faster_whisper import WhisperModel

class TranscriptionError(Exception):
    pass

def format_timestamp(seconds: float) -> str:
    """Converte segundos em float para o formato SRT (HH:MM:SS,mmm)"""
    hours = math.floor(seconds / 3600)
    seconds %= 3600
    minutes = math.floor(seconds / 60)
    seconds %= 60
    milliseconds = round((seconds - math.floor(seconds)) * 1000)
    seconds = math.floor(seconds)
    
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"

def transcribe_audio_to_srt(audio_path) -> str:
    try:
        # Carrega o modelo "small". 
        # Na primeira execução, ele fará o download do modelo (aprox. 480MB).
        # compute_type="int8" otimiza o uso de memória na CPU.
        model = WhisperModel("small", device="cpu", compute_type="int8")
        
        # Inicia a transcrição forçando o idioma português
        segments, info = model.transcribe(str(audio_path), language="pt", beam_size=5)
        
        srt_content = []
        
        for i, segment in enumerate(segments, start=1):
            start_time = format_timestamp(segment.start)
            end_time = format_timestamp(segment.end)
            text = segment.text.strip()
            
            # Adiciona o número simulando o versículo (opcional, baseado na sua regra anterior)
            # Como o Whisper corta por frases/pausas, cada bloco será numerado sequencialmente.
            formatted_text = f"[{i}] {text}"
            
            # Bloco padrão do formato SRT
            srt_content.append(f"{i}")
            srt_content.append(f"{start_time} --> {end_time}")
            srt_content.append(formatted_text)
            srt_content.append("") # Linha em branco separadora
            
        final_srt = "\n".join(srt_content).strip()
        
        if not final_srt:
            raise TranscriptionError("O Whisper não conseguiu identificar nenhum áudio no arquivo.")
            
        return final_srt

    except Exception as e:
        raise TranscriptionError(f"Erro ao transcrever com o Whisper: {str(e)}")