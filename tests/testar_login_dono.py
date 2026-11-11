
from getpass import getpass
from backend.supabase_client import criar_cliente_supabase

print("=== BotWhats — Teste de Login do Dono ===")

email = input("E-mail do Dono: ").strip()
senha = getpass("Senha: ")

cliente = criar_cliente_supabase()

try:
    # Autenticação no Supabase
    resposta = cliente.auth.sign_in_with_password({
        "email": email,
        "password": senha,
    })

    if not resposta.user or not resposta.session:
        raise RuntimeError("Sessão não foi criada.")

    print("Login autenticado com sucesso!")

    # Verificação do papel administrativo
    resultado = cliente.rpc("sou_dono").execute()

    if resultado.data is True:
        print("SUCESSO: usuário autorizado como Dono.")
    else:
        print("ACESSO NEGADO: usuário não é Dono.")

except Exception as erro:
    print(f"Falha na autenticação ou autorização: {type(erro).__name__}")

finally:
    cliente.auth.sign_out()
    print("Teste encerrado.")
