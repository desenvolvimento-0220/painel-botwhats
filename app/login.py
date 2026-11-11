
import streamlit as st

from backend.supabase_client import criar_cliente_supabase


st.set_page_config(
    page_title="Login — BotWhats",
    page_icon="🔐",
    layout="centered",
)

st.title("🤖 BotWhats")
st.subheader("Acesso administrativo")

with st.form("form_login"):
    email = st.text_input("E-mail")
    senha = st.text_input("Senha", type="password")
    entrar = st.form_submit_button(
        "Entrar",
        use_container_width=True,
    )

if entrar:
    if not email.strip() or not senha:
        st.error("Informe seu e-mail e sua senha.")
        st.stop()

    try:
        cliente = criar_cliente_supabase()

        resposta = cliente.auth.sign_in_with_password({
            "email": email.strip(),
            "password": senha,
        })

        if not resposta.user or not resposta.session:
            st.error("Não foi possível autenticar.")
            st.stop()

        resultado = cliente.rpc("sou_dono").execute()

        if resultado.data is not True:
            cliente.auth.sign_out()
            st.error("Acesso não autorizado.")
            st.stop()

        # Nesta etapa, apenas confirmamos o login.
        # Ainda não liberamos o painel administrativo.
        st.success("Login do Dono realizado com sucesso!")

        cliente.auth.sign_out()

    except Exception:
        st.error("Não foi possível concluir o login.")
