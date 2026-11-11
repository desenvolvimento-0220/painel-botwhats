# Painel BotWhats

Painel web feito com Streamlit. Algumas funções, como conexão ao WhatsApp, envio
de mensagens e salvamento de configurações, ainda são demonstrações e precisam
ser integradas a um servidor para funcionar de verdade.

## Executar no Windows

1. Instale o Python e abra o PowerShell nesta pasta.
2. Crie um ambiente virtual (use `python` se o comando `py` não estiver disponível):

   ```powershell
   py -m venv .venv
   ```

3. Instale as dependências e inicie o painel usando o Python do ambiente:

   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   .\.venv\Scripts\python.exe -m streamlit run app.py
   ```

O painel ficará disponível em `http://localhost:8501`.

Se nem `py` nem `python` forem reconhecidos, instale o Python pelo site oficial
e marque a opção para adicioná-lo ao `PATH` durante a instalação.
