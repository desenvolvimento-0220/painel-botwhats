import streamlit as st

# Configuração da página (O White Label começa aqui)
st.set_page_config(page_title="Painel BotWhats", page_icon="🤖", layout="wide")

# Barra lateral para navegação
st.sidebar.title("🤖 Meu BotWhats")
st.sidebar.markdown("---")
menu = st.sidebar.radio(
    "Navegação",
    ["🏠 Início (QR Code)", "⚙️ Regras de Conversa", "🚀 Disparo em Massa", "🎨 Personalização"]
)

# Tela 1: Início / Conexão
if menu == "🏠 Início (QR Code)":
    st.title("Conexão do WhatsApp")
    st.write("Bem-vindo ao painel central do seu Bot.")
    
    col1, col2 = st.columns(2)
    with col1:
        st.info("Status da Conexão: 🔴 Desconectado")
        if st.button("Gerar QR Code para Conectar"):
            st.warning("Aviso: O motor do bot (Evolution API) ainda não está conectado. O QR Code aparecerá aqui quando estivermos na Oracle Cloud.")
    with col2:
        st.write("Área reservada para exibir a imagem do QR Code.")

# Tela 2: Regras de Conversa
elif menu == "⚙️ Regras de Conversa":
    st.title("Regras de Resposta")
    st.write("Configure aqui o que o bot deve responder para palavras-chave específicas antes de enviar para a IA.")
    
    with st.form("nova_regra"):
        palavra = st.text_input("Se o cliente digitar:")
        resposta = st.text_area("O bot deve responder:")
        salvar = st.form_submit_button("Salvar Regra")
        
        if salvar:
            st.success(f"Regra salva! (Simulação: '{palavra}' -> '{resposta}')")

# Tela 3: Disparo em Massa
elif menu == "🚀 Disparo em Massa":
    st.title("Envio de Mensagens em Massa")
    st.write("Faça o upload da sua lista de contatos para iniciar uma campanha.")
    
    arquivo_contatos = st.file_uploader("Subir arquivo (CSV ou Excel)", type=["csv", "xlsx"])
    mensagem_campanha = st.text_area("Digite a mensagem da campanha:")
    
    if st.button("Iniciar Disparo"):
        st.info("Função de disparo será ativada após a conexão com o servidor.")

# Tela 4: Personalização
elif menu == "🎨 Personalização":
    st.title("Configurações Visuais (White Label)")
    st.write("Mude as cores e a marca do seu painel.")
    
    st.color_picker("Cor principal do painel", "#00c300")
    st.text_input("Nome da sua Empresa", "Minha Empresa Bot")
    st.file_uploader("Upload da Logomarca (PNG/JPG)")
    st.button("Salvar Alterações Visuais")
