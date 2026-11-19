"""Registro exclusivo do processo ASGI, compartilhado apenas no servidor."""

# Sem gateway inicializado, o app bloqueia o acesso. Não há cache entre usuários.
sessoes = None
