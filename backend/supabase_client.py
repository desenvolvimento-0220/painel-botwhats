
import os
from pathlib import Path

from supabase import create_client, Client, ClientOptions


def carregar_configuracoes():
    """Carrega as configurações do arquivo .env."""
    arquivo = Path(__file__).resolve().parent.parent / ".env"

    if not arquivo.exists():
        raise RuntimeError("Arquivo .env não encontrado.")

    configuracoes = {}

    for linha in arquivo.read_text(encoding="utf-8-sig").splitlines():
        linha = linha.strip()

        if not linha or linha.startswith("#") or "=" not in linha:
            continue

        chave, valor = linha.split("=", 1)
        configuracoes[chave.strip()] = valor.strip().strip('"').strip("'")

    return configuracoes


def criar_cliente_supabase() -> Client:
    """Cria um cliente Supabase com chave publicável."""
    config = carregar_configuracoes()

    url = config.get("SUPABASE_URL")
    chave = config.get("SUPABASE_PUBLISHABLE_KEY")

    if not url or not chave:
        raise RuntimeError("Configuração Supabase incompleta.")

    # A renovação acontece sob o lock da sessão no gateway, nunca em um timer.
    return create_client(url, chave, options=ClientOptions(auto_refresh_token=False))
