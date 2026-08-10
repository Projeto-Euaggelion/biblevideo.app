"""Transcrição do áudio via Gemini API, gerando legendas SRT (uma entrada
por versículo, com tempos precisos). Esta é a única fonte do texto exibido
no vídeo — não há checagem contra um texto bíblico de referência.
"""
from google import genai
from google.genai import types

from core.config import GEMINI_API_KEY, GEMINI_MODEL

PROMPT = """\
Você está transcrevendo um áudio no qual uma pessoa lê em voz alta um \
trecho da Bíblia em português.

Gere uma legenda no formato SRT padrão seguindo estas regras:
- Cada entrada do SRT corresponde a EXATAMENTE um versículo lido no áudio.
- O texto de cada entrada deve começar com o número do versículo entre \
colchetes, seguido do texto falado. Exemplo:
  1
  00:00:01,200 --> 00:00:05,800
  [1] No princípio, Deus criou os céus e a terra.
- Os tempos de início e fim devem refletir com a maior precisão possível \
quando cada versículo é falado no áudio (não deixe silêncio grande sobrando \
nas bordas).
- Transcreva exatamente o que é falado, incluindo eventuais variações em \
relação ao texto bíblico "padrão" — não corrija nem complete a partir de \
memória, apenas o que está no áudio.
- Numere os versículos em ordem crescente a partir do número falado ou \
indicado no áudio; se o locutor não falar o número, infira pela sequência.

Responda APENAS com o conteúdo SRT puro. Sem comentários, sem explicações, \
sem blocos de código markdown (nada de ```), sem texto antes ou depois.
"""


class TranscriptionError(Exception):
    pass


def transcribe_audio_to_srt(audio_path) -> str:
    if not GEMINI_API_KEY:
        raise TranscriptionError(
            "GEMINI_API_KEY não configurada. Defina a variável de ambiente "
            "ou crie um arquivo .env a partir de .env.example."
        )

    client = genai.Client(api_key=GEMINI_API_KEY)

    uploaded = client.files.upload(file=str(audio_path))

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[uploaded, PROMPT],
        config=types.GenerateContentConfig(temperature=0.1),
    )

    text = (response.text or "").strip()
    text = _strip_markdown_fences(text)

    if not text:
        raise TranscriptionError("O Gemini retornou uma resposta vazia.")

    return text


def _strip_markdown_fences(text: str) -> str:
    if text.startswith("```"):
        lines = text.splitlines()
        # remove primeira linha (```srt ou ```)
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()
