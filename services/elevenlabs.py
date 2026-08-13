"""
Serviço para integração com a API da ElevenLabs (text-to-speech).

Responsável por:
- Gerenciar as configurações (API key, voz e modelo padrão)
- Listar as vozes disponíveis na conta configurada
- Gerar o áudio a partir de um texto (usado para transformar leituras em áudio)
"""

import requests

API_BASE_URL = "https://api.elevenlabs.io/v1"


class ElevenLabsError(Exception):
    pass


class ElevenLabsConfig:
    """Gerencia as configurações da ElevenLabs usando o banco de dados SQLite."""

    def __init__(self):
        from core.database import ElevenLabsConfigDB
        self.db = ElevenLabsConfigDB

        config_data = self.db.load()
        self.api_key = config_data.get("api_key", "")
        self.voice_id = config_data.get("voice_id", "")
        self.model_id = config_data.get("model_id") or self.db.DEFAULT_MODEL_ID

    def save(self) -> None:
        """Salva as configurações no banco de dados."""
        self.db.save(api_key=self.api_key, voice_id=self.voice_id, model_id=self.model_id)

    def is_configured(self) -> bool:
        """Verifica se a API Key e a voz padrão já foram configuradas."""
        return bool(self.api_key and self.voice_id)


def _extract_error(response: requests.Response) -> str:
    try:
        data = response.json()
        detail = data.get("detail")
        if isinstance(detail, dict):
            return detail.get("message") or str(detail)
        if detail:
            return str(detail)
    except ValueError:
        pass
    return f"Erro da API ElevenLabs (HTTP {response.status_code})."


class ElevenLabsService:
    """Serviço para comunicação com a API da ElevenLabs."""

    def __init__(self, config: ElevenLabsConfig):
        self.config = config

    def _headers(self) -> dict:
        return {"xi-api-key": self.config.api_key}

    def list_voices(self) -> list[dict]:
        """Retorna as vozes disponíveis na conta configurada."""
        if not self.config.api_key:
            raise ElevenLabsError("Configure a API Key da ElevenLabs primeiro.")

        try:
            response = requests.get(f"{API_BASE_URL}/voices", headers=self._headers(), timeout=30)
        except requests.RequestException as e:
            raise ElevenLabsError(f"Erro de conexão com a ElevenLabs: {e}")

        if not response.ok:
            raise ElevenLabsError(_extract_error(response))

        data = response.json()
        return [
            {"voice_id": v["voice_id"], "name": v.get("name", v["voice_id"])}
            for v in data.get("voices", [])
        ]

    def generate_speech(self, text: str) -> bytes:
        """Gera o áudio (mp3) a partir do texto, usando a voz e o modelo configurados."""
        if not self.config.is_configured():
            raise ElevenLabsError("ElevenLabs não está configurada. Vá em Configurações > ElevenLabs.")

        url = f"{API_BASE_URL}/text-to-speech/{self.config.voice_id}"
        payload = {
            "text": text,
            "model_id": self.config.model_id,
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        }
        try:
            response = requests.post(
                url,
                headers={**self._headers(), "Content-Type": "application/json"},
                json=payload,
                timeout=180,
            )
        except requests.RequestException as e:
            raise ElevenLabsError(f"Erro de conexão com a ElevenLabs: {e}")

        if not response.ok:
            raise ElevenLabsError(_extract_error(response))

        return response.content
