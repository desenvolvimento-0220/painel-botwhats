
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

# Localiza o arquivo .env na raiz do projeto
arquivo_env = Path(__file__).resolve().parent.parent / ".env"

if not arquivo_env.exists():
    raise SystemExit("ERRO: arquivo .env não encontrado.")

config = {}

for linha in arquivo_env.read_text(encoding="utf-8-sig").splitlines():
    linha = linha.strip()

    if not linha or linha.startswith("#") or "=" not in linha:
        continue

    chave, valor = linha.split("=", 1)
    config[chave.strip()] = valor.strip().strip('"').strip("'")

url = config.get("SUPABASE_URL", "").rstrip("/")
chave = config.get("SUPABASE_PUBLISHABLE_KEY", "")

if not url.startswith("https://") or not chave.startswith("sb_publishable_"):
    raise SystemExit("ERRO: verifique as configurações do .env.")

print("Configurações encontradas.")
print("Verificando conexão com o Supabase...")

try:
    requisicao = Request(
        f"{url}/auth/v1/health",
        headers={"apikey": chave},
    )

    with urlopen(requisicao, timeout=15) as resposta:
        if resposta.status == 200:
            print("SUCESSO: serviço de autenticação acessível.")
        else:
            print("Resposta inesperada do serviço.")

except HTTPError as erro:
    print(f"Falha HTTP: {erro.code}")

except URLError:
    print("Falha de conexão. Verifique sua internet e a URL.")
