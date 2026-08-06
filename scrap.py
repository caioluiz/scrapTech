import os
import json
import re
import gspread
from playwright.sync_api import sync_playwright
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
from urllib.parse import urlparse

scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

# O Python tenta pegar a senha do cofre do GitHub primeiro
credenciais_texto = os.environ.get("GOOGLE_CREDENTIALS")

if credenciais_texto:
    # Cenário 1: Está rodando na nuvem (GitHub Actions)
    print(" Rodando na nuvem: Usando credenciais do GitHub Secrets...")
    creds_dict = json.loads(credenciais_texto)
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
else:
    # Cenário 2: Está rodando no seu computador (VSCode)
    print(" Rodando localmente: Usando arquivo credenciais.json...")
    # Ele volta a ler o arquivo físico que está na sua pasta
    creds = ServiceAccountCredentials.from_json_keyfile_name('credenciais.json', scope)

# Conecta no Google
client = gspread.authorize(creds)
planilha = client.open("Monitor_Precos_PC")
aba_links = planilha.worksheet("Links")
aba_historico = planilha.worksheet("Historico")

# Mapeamento de domínios para seus respectivos seletores de preço
SELETORES_POR_LOJA = {
    'kabum.com.br': 'h4:has-text("R$")',
    'terabyteshop.com.br': '#valVista', # ID comum de preço à vista na Terabyte
    'amazon.com.br': '.a-price-whole',  # Classe do valor principal na Amazon
    #'mercadolivre.com.br': '.andes-money-amount__fraction', # porradeira, segurança muito forte, não usando por enquanto
    #'pichau.com.br': 'div:has-text("à vista") + div' # por enquanto não vou usar(ter que mudar função limpar_preco)
}

# Se a URL não for de nenhuma loja acima, ele tenta esse seletor padrão
SELETOR_PADRAO = 'h4:has-text("R$")'

# O Python vai ler a planilha e criar a lista automaticamente!
lista_de_pecas = aba_links.get_all_records()

def limpar_preco(preco_texto):
    try:
        # 1. O Regex arranca TUDO que não for número, ponto ou vírgula.
        # Se a Amazon mandar "R$ 1.500 \n , ", isso vira "1.500,"
        texto_limpo = re.sub(r'[^\d.,]', '', preco_texto)
        
        # Se a string ficar vazia, retorna 0
        if not texto_limpo:
            return 0.0
            
        # 2. Se sobrar uma vírgula ou ponto solto no final (O bug da Amazon)
        # Ele arranca esse último caractere. "1.500," vira "1.500"
        if texto_limpo.endswith(',') or texto_limpo.endswith('.'):
            texto_limpo = texto_limpo[:-1]
        
        # 3. Faz a limpeza matemática padrão (Brasil para EUA)
        texto_limpo = texto_limpo.replace(".", "") # Tira ponto de milhar
        texto_limpo = texto_limpo.replace(",", ".") # Troca vírgula por ponto decimal
        
        # Converte para Float matemático
        return float(texto_limpo)
        
    except Exception as e:
        print(f"Erro ao limpar o preço '{preco_texto}': {e}")
        return 0.0
    
def descobrir_seletor(url):
    # Transforma "https://www.amazon.com.br/produto-x" em "www.amazon.com.br"
    dominio = urlparse(url).netloc 
    
    for loja, seletor in SELETORES_POR_LOJA.items():
        if loja in dominio:
            return seletor
            
    return SELETOR_PADRAO

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
            
            if not url or not nome:
                continue
                
            print(f"Buscando: {nome}...")
            
            # NOVO: Descobre qual seletor usar baseado na URL
            seletor_atual = descobrir_seletor(url)
            print(f"Usando seletor: {seletor_atual}")
            
            try:
                page.goto(url, wait_until="domcontentloaded")
                
                try:
                    page.wait_for_selector(seletor_atual, timeout=5000)
                    elemento_preco = page.locator(seletor_atual).first
                    
                    preco_texto = elemento_preco.inner_text().strip()
                    preco_numero = limpar_preco(preco_texto)
                    
                    data_hora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
                    dados_para_salvar.append([data_hora, nome, preco_numero])
                    print(f"✅ R$ {preco_numero}")
                    
                except Exception:
                    print(f"❌ Preço não encontrado. Capturando a tela...")
                    
                    # 1. Limpa o nome da peça para usar como nome de arquivo no Windows
                    # Remove caracteres especiais que o Windows não aceita em nomes de arquivos
                   # nome_seguro = "".join(c for c in nome if c.isalnum() or c in " ").replace(" ", "_")
                   # nome_arquivo = f"erro_{nome_seguro}.png"
                    
                    # 2. Tira a foto da página inteira e salva na mesma pasta do script
                  #  page.screenshot(path=nome_arquivo, full_page=True)
                    
                  #  print(f"📸 Imagem salva: {nome_arquivo}. Abra para investigar!")
                    
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