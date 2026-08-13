"""
Serviço para integração com YouTube Data API v3.

Responsável por:
- Autenticação e gerenciamento de tokens OAuth
- Upload de vídeos
- Gerenciamento de playlists
- Configuração de metadados de vídeos
"""

import json
import os
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone
import pickle

# Será importado quando google-auth-oauthlib estiver instalado
# from google.auth.transport.requests import Request
# from google.oauth2.credentials import Credentials
# from google_auth_oauthlib.flow import InstalledAppFlow
# from googleapiclient.discovery import build
# from googleapiclient.http import MediaFileUpload


class YouTubeConfig:
    """Gerencia as configurações do YouTube usando banco de dados SQLite."""
    
    def __init__(self):
        # Importa o módulo de database
        from core.database import YouTubeConfigDB
        self.db = YouTubeConfigDB
        
        # Carrega as configurações do banco
        config_data = self.db.load()
        
        self.api_key = config_data.get("api_key", "")
        self.client_id = config_data.get("client_id", "")
        self.client_secret = config_data.get("client_secret", "")
        self.redirect_uri = config_data.get("redirect_uri", "http://127.0.0.1:8000/auth/youtube/callback")
        
        # Configurações padrão para novo upload
        self.default_title = config_data.get("default_title", "")
        self.default_description = config_data.get("default_description", "")
        self.default_keywords = config_data.get("default_keywords", "")
        self.default_visibility = config_data.get("default_visibility", "private")
    
    def save(self) -> None:
        """Salva as configurações no banco de dados."""
        self.db.save(
            client_id=self.client_id,
            client_secret=self.client_secret,
            api_key=self.api_key,
            redirect_uri=self.redirect_uri,
            default_title=self.default_title,
            default_description=self.default_description,
            default_keywords=self.default_keywords,
            default_visibility=self.default_visibility,
        )
    
    @classmethod
    def load_from_db(cls) -> "YouTubeConfig":
        """Carrega as configurações do banco de dados."""
        config = cls()
        return config
    
    def is_configured(self) -> bool:
        """Verifica se as configurações básicas de API estão configuradas."""
        return bool(self.client_id and self.client_secret)
    
    def to_dict(self) -> dict:
        """Retorna as configurações como dicionário (sem secrets)."""
        return {
            "api_key_set": bool(self.api_key),
            "client_id_set": bool(self.client_id),
            "client_secret_set": bool(self.client_secret),
            "redirect_uri": self.redirect_uri,
            "default_title": self.default_title,
            "default_description": self.default_description,
            "default_keywords": self.default_keywords,
            "default_visibility": self.default_visibility,
        }


class YouTubeService:
    """Serviço para comunicação com YouTube API."""
    
    SCOPES = [
        'https://www.googleapis.com/auth/youtube.upload',
        'https://www.googleapis.com/auth/youtube',
        'https://www.googleapis.com/auth/youtube.force-ssl',
    ]
    
    TOKEN_FILE = "youtube_token.pickle"
    
    def __init__(self, config: YouTubeConfig):
        self.config = config
        self.youtube = None
        self.credentials = None
    
    def _build_flow(self, redirect_uri: str):
        """Monta o objeto Flow do OAuth2 a partir das credenciais salvas no banco."""
        from google_auth_oauthlib.flow import Flow

        if not self.config.client_id or not self.config.client_secret:
            raise YouTubeError("Client ID e Client Secret do YouTube não configurados.")

        client_config = {
            "web": {
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [redirect_uri],
            }
        }
        return Flow.from_client_config(client_config, scopes=self.SCOPES, redirect_uri=redirect_uri)

    def get_authorization_url(self, redirect_uri: str, state: str = "") -> str:
        """Gera a URL de consentimento do Google para iniciar o login do usuário."""
        flow = self._build_flow(redirect_uri)
        auth_url, _ = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
            state=state,
        )
        return auth_url

    def exchange_code(self, redirect_uri: str, code: str) -> None:
        """Troca o código de autorização recebido no callback por um token de acesso."""
        from googleapiclient.discovery import build

        flow = self._build_flow(redirect_uri)
        flow.fetch_token(code=code)
        self.credentials = flow.credentials

        with open(self.TOKEN_FILE, "wb") as f:
            pickle.dump(self.credentials, f)

        self.youtube = build("youtube", "v3", credentials=self.credentials)

    def load_token(self) -> bool:
        """
        Carrega um token já salvo, sem exigir interação do usuário.

        Returns:
            True se já existe um token válido (ou renovável) e o serviço está pronto para uso.
        """
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build

        if not Path(self.TOKEN_FILE).exists():
            return False

        with open(self.TOKEN_FILE, "rb") as f:
            self.credentials = pickle.load(f)

        if self.credentials and self.credentials.expired and self.credentials.refresh_token:
            try:
                self.credentials.refresh(Request())
                with open(self.TOKEN_FILE, "wb") as f:
                    pickle.dump(self.credentials, f)
            except Exception:
                return False

        if not self.credentials or not self.credentials.valid:
            return False

        self.youtube = build("youtube", "v3", credentials=self.credentials)
        return True

    @classmethod
    def reset_token(cls) -> bool:
        """Remove o token salvo localmente, forçando um novo login no próximo upload."""
        path = Path(cls.TOKEN_FILE)
        if path.exists():
            path.unlink()
            return True
        return False
    
    def upload_video(
        self,
        video_path: str,
        title: str,
        description: str,
        visibility: str = "private",
        keywords: list[str] = None,
        playlist_id: str = None,
        thumbnail_path: str = None,
        progress_callback=None,
    ) -> Optional[dict]:
        """
        Faz upload de um vídeo para o YouTube.

        Args:
            video_path: Caminho para o arquivo de vídeo
            title: Título do vídeo
            description: Descrição do vídeo
            visibility: Visibilidade ('public', 'private', 'unlisted')
            keywords: Lista de keywords
            playlist_id: ID da playlist (opcional)
            thumbnail_path: Caminho para arquivo de thumbnail (opcional)
            progress_callback: Função chamada com o progresso (0-100) a cada chunk enviado

        Returns:
            Dict com informações do vídeo enviado ou None se falhar
        """
        if not self.youtube:
            raise RuntimeError(
                "Serviço não autenticado. Execute load_token() ou exchange_code() primeiro."
            )
        
        if not Path(video_path).exists():
            raise FileNotFoundError(f"Arquivo de vídeo não encontrado: {video_path}")
        
        try:
            from googleapiclient.http import MediaFileUpload
        except ImportError:
            raise ImportError("google-api-python-client é necessário.")
        
        # Prepara metadados do vídeo
        body = {
            "snippet": {
                "title": title,
                "description": description,
                "tags": keywords or [],
                "categoryId": "27"  # 27 = Educação
            },
            "status": {
                "privacyStatus": visibility,
                "selfDeclaredMadeForKids": False,
            }
        }
        
        # Upload do vídeo em chunks de 5MB, para permitir reportar o progresso
        # incrementalmente (chunksize=-1 enviaria tudo de uma vez só).
        media = MediaFileUpload(video_path, chunksize=5 * 1024 * 1024, resumable=True)
        request = self.youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media
        )
        
        # Executa upload com retry, reportando o progresso a cada chunk
        response = None
        while response is None:
            try:
                status, response = request.next_chunk()
                if progress_callback and status:
                    progress_callback(int(status.progress() * 100))
            except Exception as e:
                raise RuntimeError(f"Erro durante upload: {str(e)}")

        if progress_callback:
            progress_callback(100)

        video_id = response.get("id")
        
        # Upload de thumbnail se fornecido
        if thumbnail_path and Path(thumbnail_path).exists():
            self._upload_thumbnail(video_id, thumbnail_path)
        
        # Adiciona à playlist se fornecida
        if playlist_id:
            self._add_to_playlist(video_id, playlist_id)
        
        return {
            "id": video_id,
            "title": title,
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "uploaded_at": datetime.now(timezone.utc).isoformat()
        }
    
    def _upload_thumbnail(self, video_id: str, thumbnail_path: str) -> bool:
        """Faz upload de uma thumbnail para um vídeo."""
        if not self.youtube:
            return False
        
        try:
            from googleapiclient.http import MediaFileUpload
            
            media = MediaFileUpload(
                thumbnail_path,
                mimetype="image/png",
                resumable=False
            )
            
            self.youtube.thumbnails().set(
                videoId=video_id,
                media_body=media
            ).execute()
            
            return True
        except Exception as e:
            print(f"Aviso: Não foi possível fazer upload da thumbnail: {str(e)}")
            return False
    
    def _add_to_playlist(self, video_id: str, playlist_id: str) -> bool:
        """Adiciona um vídeo a uma playlist."""
        if not self.youtube:
            return False
        
        try:
            self.youtube.playlistItems().insert(
                part="snippet",
                body={
                    "snippet": {
                        "playlistId": playlist_id,
                        "resourceId": {
                            "kind": "youtube#video",
                            "videoId": video_id
                        }
                    }
                }
            ).execute()
            
            return True
        except Exception as e:
            print(f"Aviso: Não foi possível adicionar vídeo à playlist: {str(e)}")
            return False
    
    def get_playlists(self) -> list[dict]:
        """Retorna lista de playlists do usuário."""
        if not self.youtube:
            return []
        
        try:
            playlists = []
            request = self.youtube.playlists().list(
                part="snippet",
                mine=True,
                maxResults=25
            )
            
            while request:
                response = request.execute()
                for item in response.get("items", []):
                    playlists.append({
                        "id": item["id"],
                        "title": item["snippet"]["title"],
                        "description": item["snippet"].get("description", ""),
                    })
                
                request = self.youtube.playlists().list_next(request, response)
            
            return playlists
        except Exception as e:
            print(f"Erro ao buscar playlists: {str(e)}")
            return []
    
    def get_video_info(self, video_id: str) -> Optional[dict]:
        """Obtém informações de um vídeo."""
        if not self.youtube:
            return None
        
        try:
            response = self.youtube.videos().list(
                part="snippet,status,statistics",
                id=video_id
            ).execute()
            
            if response.get("items"):
                item = response["items"][0]
                return {
                    "id": video_id,
                    "title": item["snippet"]["title"],
                    "description": item["snippet"]["description"],
                    "thumbnail_url": item["snippet"].get("thumbnails", {}).get("default", {}).get("url"),
                    "view_count": item.get("statistics", {}).get("viewCount", "0"),
                    "like_count": item.get("statistics", {}).get("likeCount", "0"),
                }
            
            return None
        except Exception as e:
            print(f"Erro ao buscar informações do vídeo: {str(e)}")
            return None


class YouTubeError(Exception):
    """Exceção para erros relacionados ao YouTube."""
    pass
