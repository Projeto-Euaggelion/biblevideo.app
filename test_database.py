#!/usr/bin/env python
# Script de teste do database

from core.database import YouTubeConfigDB, AppSettingsDB, DATA_DIR

print("✓ Database inicializado")
print(f"✓ Diretório de dados: {DATA_DIR}")

config = YouTubeConfigDB.load()
print("✓ YouTube Config carregada")
print(f"  Client ID: {config.get('client_id', '(nao configurado)')}")
print(f"  Is Configured: {bool(config.get('client_id'))}")

print("\n✓ Todas as verificações passaram!")
