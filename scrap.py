import os
import json
from playwright.sync_api import sync_playwright
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime

# 1. Configurar o acesso ao Google Sheets usando Variável de Ambiente
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

# Pega o texto do cofre do GitHub e transforma em um dicionário
credenciais_texto = os.environ.get("GOOGLE_CREDENTIALS")
if not credenciais_texto:
    raise ValueError("Credenciais não encontradas. Verifique os Secrets do GitHub.")

creds_dict = json.loads(credenciais_texto)
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
client = gspread.authorize(creds)

# Abre a planilha inteira
planilha = client.open("Monitor_Precos_PC")
# 2. Conectar nas abas específicas
aba_links = planilha.worksheet("Links")      # Aba de onde vamos LER
aba_historico = planilha.worksheet("Historico") # Aba onde vamos ESCREVER

# O Python vai ler a planilha e criar a lista automaticamente!
lista_de_pecas = aba_links.get_all_records()

def limpar_preco(preco_texto):
    try:
        # Remove o 'R$' e espaços em branco
        texto_limpo = preco_texto.replace("R$", "").strip()
        # Remove o ponto dos milhares (ex: 1.500,00 -> 1500,00)
        texto_limpo = texto_limpo.replace(".", "")
        # Troca a vírgula dos centavos por ponto (ex: 1500,00 -> 1500.00)
        texto_limpo = texto_limpo.replace(",", ".")
        
        # Converte para número decimal (float)
        return float(texto_limpo)
    except ValueError:
        # Se der erro (ex: site mostrar "Indisponível"), retorna 0 ou vazio
        return 0.0

def checar_multiplos_precos(pecas):
    if not pecas:
        print("Nenhum link encontrado na aba 'Links'.")
        return
    
    # Lista para guardar todos os resultados e enviar para a planilha de uma vez só
    dados_para_salvar = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        # OTIMIZAÇÃO 1: Bloqueia o download de imagens, vídeos e fontes (economiza muita banda e tempo)
        context.route("**/*", lambda route: route.abort() if route.request.resource_type in ["image", "media", "font"] else route.continue_())

        page = context.new_page()
        
        for peca in pecas:
            nome = peca.get('Nome')
            url = peca.get('URL')
            
            # Se a linha estiver em branco na planilha, o script pula
            if not url or not nome:
                continue
                
            print(f"Buscando preço de: {nome}...")
            
            try:
                page.goto(url, wait_until="domcontentloaded")
                try:
                    # OTIMIZAÇÃO 2: Espera Inteligente. 
                    # Aguarda até 5 segundos pelo preço. Mas se o preço carregar em 0.2s, ele avança imediatamente!
                    page.wait_for_selector('h4:has-text("R$")', timeout=5000)
                    
                    elemento_preco = page.locator('h4:has-text("R$")').first
                    preco_texto = elemento_preco.inner_text().strip()
                    preco_numero = limpar_preco(preco_texto)
                    
                    data_hora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
                    
                    # Guarda o dado na lista em vez de enviar pro Google na mesma hora
                    dados_para_salvar.append([data_hora, nome, preco_numero])
                    print(f"✅ R$ {preco_numero}")
                    
                except Exception:
                    print(f"❌ Preço não encontrado (Tempo limite excedido).")
                    
            except Exception as e:
                print(f"⚠️ Erro ao carregar a página: {e}")
                
        browser.close()
        
        # OTIMIZAÇÃO 3: Envia tudo para o Google Sheets de uma única vez no final
        if dados_para_salvar:
            print("Salvando todos os dados na planilha...")
            aba_historico.append_rows(dados_para_salvar)
            print("✅ Planilha atualizada com sucesso!")
        
        print("Fim da coleta!")

if __name__ == "__main__":
    checar_multiplos_precos(lista_de_pecas)