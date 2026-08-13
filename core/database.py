"""
Gerenciador de banco de dados SQLite para configurações e dados da aplicação.
"""

import sqlite3
import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Dict, Any

# Diretório de dados
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_FILE = DATA_DIR / "app.db"


class Database:
    """Gerenciador de conexão e operações do SQLite."""
    
    def __init__(self, db_path: Path = DB_FILE):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """Inicializa o banco de dados e cria tabelas se necessário."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('PRAGMA foreign_keys = ON')
            cursor = conn.cursor()
            
            # Tabela de configurações gerais
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS app_settings (
                    id INTEGER PRIMARY KEY,
                    key TEXT UNIQUE NOT NULL,
                    value TEXT,
                    value_type TEXT DEFAULT 'string',
                    created_at TEXT,
                    updated_at TEXT
                )
            ''')
            
            # Tabela de configurações do YouTube
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS youtube_config (
                    id INTEGER PRIMARY KEY,
                    client_id TEXT,
                    client_secret TEXT,
                    api_key TEXT,
                    redirect_uri TEXT,
                    default_title TEXT,
                    default_description TEXT,
                    default_keywords TEXT,
                    default_visibility TEXT DEFAULT 'private',
                    created_at TEXT,
                    updated_at TEXT
                )
            ''')

            # Tabela de configurações da ElevenLabs (geração de áudio)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS elevenlabs_config (
                    id INTEGER PRIMARY KEY,
                    api_key TEXT,
                    voice_id TEXT,
                    model_id TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )
            ''')

            conn.commit()
    
    def get_connection(self) -> sqlite3.Connection:
        """Retorna uma conexão com o banco de dados."""
        conn = sqlite3.connect(self.db_path)
        conn.execute('PRAGMA foreign_keys = ON')
        conn.row_factory = sqlite3.Row
        return conn


# Instância global do banco de dados
db = Database()


class YouTubeConfigDB:
    """Gerencia as configurações do YouTube no banco de dados."""
    
    @staticmethod
    def load() -> Dict[str, Any]:
        """Carrega as configurações do YouTube do banco de dados."""
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM youtube_config ORDER BY id DESC LIMIT 1')
            row = cursor.fetchone()
            
            if row:
                return dict(row)
            else:
                # Retorna valores padrão se não houver configurações
                return {
                    "client_id": "",
                    "client_secret": "",
                    "api_key": "",
                    "redirect_uri": "http://127.0.0.1:8000/auth/youtube/callback",
                    "default_title": "",
                    "default_description": "",
                    "default_keywords": "",
                    "default_visibility": "private",
                }
    
    @staticmethod
    def save(
        client_id: str = "",
        client_secret: str = "",
        api_key: str = "",
        redirect_uri: str = "",
        default_title: str = "",
        default_description: str = "",
        default_keywords: str = "",
        default_visibility: str = "private",
    ) -> Dict[str, Any]:
        """
        Salva as configurações do YouTube no banco de dados.

        Os campos de credenciais (client_id, client_secret, api_key,
        redirect_uri) preservam o valor já salvo quando chegam em branco —
        o client_secret nunca é reenviado ao front-end por segurança, então
        formulários que só atualizam os padrões de upload (título, descrição
        etc.) não podem sobrescrevê-lo com um valor vazio.
        """
        now = datetime.now(timezone.utc).isoformat()
        existing = YouTubeConfigDB.load()

        client_id = client_id or existing.get("client_id", "")
        client_secret = client_secret or existing.get("client_secret", "")
        api_key = api_key or existing.get("api_key", "")
        redirect_uri = redirect_uri or existing.get("redirect_uri", "")

        with db.get_connection() as conn:
            cursor = conn.cursor()
            
            # Verifica se já existe uma configuração
            cursor.execute('SELECT COUNT(*) as count FROM youtube_config')
            exists = cursor.fetchone()['count'] > 0
            
            if exists:
                # Atualiza a configuração existente
                cursor.execute('''
                    UPDATE youtube_config SET
                        client_id = ?,
                        client_secret = ?,
                        api_key = ?,
                        redirect_uri = ?,
                        default_title = ?,
                        default_description = ?,
                        default_keywords = ?,
                        default_visibility = ?,
                        updated_at = ?
                    WHERE id = (SELECT MAX(id) FROM youtube_config)
                ''', (
                    client_id, client_secret, api_key, redirect_uri,
                    default_title, default_description, default_keywords,
                    default_visibility, now
                ))
            else:
                # Insere nova configuração
                cursor.execute('''
                    INSERT INTO youtube_config (
                        client_id, client_secret, api_key, redirect_uri,
                        default_title, default_description, default_keywords,
                        default_visibility, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    client_id, client_secret, api_key, redirect_uri,
                    default_title, default_description, default_keywords,
                    default_visibility, now, now
                ))
            
            conn.commit()
            
            # Retorna as configurações salvas
            cursor.execute('SELECT * FROM youtube_config ORDER BY id DESC LIMIT 1')
            row = cursor.fetchone()
            return dict(row) if row else {}
    
    @staticmethod
    def is_configured() -> bool:
        """Verifica se as configurações básicas estão configuradas."""
        config = YouTubeConfigDB.load()
        return bool(config.get('client_id') and config.get('client_secret'))
    
    @staticmethod
    def get_public() -> Dict[str, Any]:
        """Retorna configurações sem dados sensíveis."""
        config = YouTubeConfigDB.load()
        return {
            "client_id": config.get("client_id", ""),
            "api_key": config.get("api_key", ""),
            "redirect_uri": config.get("redirect_uri", ""),
            "default_title": config.get("default_title", ""),
            "default_description": config.get("default_description", ""),
            "default_keywords": config.get("default_keywords", ""),
            "default_visibility": config.get("default_visibility", "private"),
            "client_secret_set": bool(config.get("client_secret")),
            "is_configured": bool(config.get("client_id") and config.get("client_secret")),
        }


class ElevenLabsConfigDB:
    """Gerencia as configurações da ElevenLabs (geração de áudio) no banco de dados."""

    DEFAULT_MODEL_ID = "eleven_multilingual_v2"

    @staticmethod
    def load() -> Dict[str, Any]:
        """Carrega as configurações da ElevenLabs do banco de dados."""
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM elevenlabs_config ORDER BY id DESC LIMIT 1')
            row = cursor.fetchone()

            if row:
                return dict(row)
            return {
                "api_key": "",
                "voice_id": "",
                "model_id": ElevenLabsConfigDB.DEFAULT_MODEL_ID,
            }

    @staticmethod
    def save(api_key: str = "", voice_id: str = "", model_id: str = "") -> Dict[str, Any]:
        """
        Salva as configurações da ElevenLabs no banco de dados.

        A api_key preserva o valor já salvo quando chega em branco, assim
        como o client_secret do YouTube — para não exigir que o usuário a
        redigite toda vez que só quiser trocar a voz ou o modelo padrão.
        """
        now = datetime.now(timezone.utc).isoformat()
        existing = ElevenLabsConfigDB.load()
        api_key = api_key or existing.get("api_key", "")
        model_id = model_id or ElevenLabsConfigDB.DEFAULT_MODEL_ID

        with db.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute('SELECT COUNT(*) as count FROM elevenlabs_config')
            exists = cursor.fetchone()['count'] > 0

            if exists:
                cursor.execute('''
                    UPDATE elevenlabs_config SET
                        api_key = ?,
                        voice_id = ?,
                        model_id = ?,
                        updated_at = ?
                    WHERE id = (SELECT MAX(id) FROM elevenlabs_config)
                ''', (api_key, voice_id, model_id, now))
            else:
                cursor.execute('''
                    INSERT INTO elevenlabs_config (
                        api_key, voice_id, model_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                ''', (api_key, voice_id, model_id, now, now))

            conn.commit()

            cursor.execute('SELECT * FROM elevenlabs_config ORDER BY id DESC LIMIT 1')
            row = cursor.fetchone()
            return dict(row) if row else {}

    @staticmethod
    def is_configured() -> bool:
        """Verifica se a API Key e a voz padrão já foram configuradas."""
        config = ElevenLabsConfigDB.load()
        return bool(config.get('api_key') and config.get('voice_id'))

    @staticmethod
    def get_public() -> Dict[str, Any]:
        """Retorna configurações sem dados sensíveis."""
        config = ElevenLabsConfigDB.load()
        return {
            "voice_id": config.get("voice_id", ""),
            "model_id": config.get("model_id") or ElevenLabsConfigDB.DEFAULT_MODEL_ID,
            "api_key_set": bool(config.get("api_key")),
            "is_configured": bool(config.get("api_key") and config.get("voice_id")),
        }


class AppSettingsDB:
    """Gerencia configurações gerais da aplicação."""
    
    @staticmethod
    def get(key: str, default: Any = None) -> Any:
        """Obtém um valor de configuração."""
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT value, value_type FROM app_settings WHERE key = ?', (key,))
            row = cursor.fetchone()
            
            if row:
                value = row['value']
                value_type = row['value_type']
                
                # Converte o tipo apropriadamente
                if value_type == 'json':
                    return json.loads(value)
                elif value_type == 'int':
                    return int(value)
                elif value_type == 'float':
                    return float(value)
                elif value_type == 'bool':
                    return value.lower() in ('true', '1', 'yes')
                else:  # string
                    return value
            
            return default
    
    @staticmethod
    def set(key: str, value: Any) -> None:
        """Define um valor de configuração."""
        now = datetime.now(timezone.utc).isoformat()
        
        # Determina o tipo do valor
        if isinstance(value, bool):
            value_type = 'bool'
            value_str = str(value)
        elif isinstance(value, int):
            value_type = 'int'
            value_str = str(value)
        elif isinstance(value, float):
            value_type = 'float'
            value_str = str(value)
        elif isinstance(value, (dict, list)):
            value_type = 'json'
            value_str = json.dumps(value)
        else:
            value_type = 'string'
            value_str = str(value)
        
        with db.get_connection() as conn:
            cursor = conn.cursor()
            
            # Tenta fazer update, se não existir, faz insert
            cursor.execute('''
                INSERT INTO app_settings (key, value, value_type, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    value_type = excluded.value_type,
                    updated_at = excluded.updated_at
            ''', (key, value_str, value_type, now, now))
            
            conn.commit()
    
    @staticmethod
    def get_all() -> Dict[str, Any]:
        """Obtém todas as configurações."""
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT key, value, value_type FROM app_settings')
            rows = cursor.fetchall()
            
            result = {}
            for row in rows:
                key = row['key']
                value = row['value']
                value_type = row['value_type']
                
                if value_type == 'json':
                    result[key] = json.loads(value)
                elif value_type == 'int':
                    result[key] = int(value)
                elif value_type == 'float':
                    result[key] = float(value)
                elif value_type == 'bool':
                    result[key] = value.lower() in ('true', '1', 'yes')
                else:
                    result[key] = value
            
            return result
    
    @staticmethod
    def delete(key: str) -> bool:
        """Deleta uma configuração."""
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM app_settings WHERE key = ?', (key,))
            conn.commit()
            return cursor.rowcount > 0
