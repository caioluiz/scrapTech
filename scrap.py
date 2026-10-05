"""
ScrapTech - coletor de preços (versão Postgres).
Lê as URLs ativas da tabela `listings`, coleta o preço com Playwright
e grava cada coleta (inclusive falhas) em `price_history`.

Variável de ambiente necessária: DATABASE_URL
(Supabase > Project Settings > Database > Connection string > Session pooler)
"""
import os
import re
import sys
import json
import psycopg
from playwright.sync_api import sync_playwright

try:
    from dotenv import load_dotenv  # opcional, só para rodar localmente com .env
    load_dotenv()
except ImportError:
    pass

DATABASE_URL = os.environ["DATABASE_URL"]
SELETOR_PADRAO = 'number-flow-react'
TENTATIVAS = 2
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def limpar_preco(preco_texto: str) -> float:
    """'R$ 1.500,' -> 1500.0 | '1.899,90' -> 1899.9"""
    texto = re.sub(r"[^\d.,]", "", preco_texto)
    if not texto:
        return 0.0
    if texto.endswith((",", ".")):
        texto = texto[:-1]
    texto = texto.replace(".", "").replace(",", ".")
    try:
        return float(texto)
    except ValueError:
        return 0.0


def extrair_preco(page, dominio: str, seletor: str) -> float:
    page.wait_for_selector(seletor, timeout=8000)
    elemento = page.locator(seletor).first

    # KaBuM!: o valor exato vem em um atributo JSON do componente
    atributo = elemento.get_attribute("data")
    if atributo:
        return float(json.loads(atributo)["value"])

    preco = limpar_preco(elemento.inner_text())

    # Amazon: '.a-price-whole' traz só a parte inteira, os centavos ficam em outro elemento
    if "amazon" in dominio:
        centavos = page.locator(".a-price-fraction").first
        if centavos.count():
            digitos = re.sub(r"\D", "", centavos.inner_text())
            if digitos:
                preco += int(digitos) / 100

    if preco <= 0:
        raise ValueError("preço zerado")
    return preco


def carregar_listings(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            select l.id, p.name, l.url, s.domain, s.price_selector
            from listings l
            join products p on p.id = l.product_id
            join stores s   on s.id = l.store_id
            where l.active
            order by l.id
            """
        )
        return cur.fetchall()


def coletar(listings):
    resultados = []  # (listing_id, price, status)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT)
        # Economiza banda e tempo: não baixa imagens, mídia e fontes
        context.route(
            "**/*",
            lambda route: route.abort()
            if route.request.resource_type in ("image", "media", "font")
            else route.continue_(),
        )
        page = context.new_page()

        for listing_id, nome, url, dominio, seletor in listings:
            seletor = seletor or SELETOR_PADRAO
            print(f"Buscando: {nome} ({dominio})...")
            preco, status = None, "error"

            for tentativa in range(1, TENTATIVAS + 1):
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    preco = extrair_preco(page, dominio, seletor)
                    status = "ok"
                    break
                except Exception as e:
                    # Timeout no seletor = provável mudança no HTML ou produto sem estoque
                    status = "not_found" if "Timeout" in type(e).__name__ else "error"
                    print(f"  tentativa {tentativa}/{TENTATIVAS} falhou: {type(e).__name__}")

            print(f"  -> {'R$ %.2f' % preco if preco else status}")
            resultados.append((listing_id, preco, status))

        browser.close()
    return resultados


def salvar(conn, resultados):
    with conn.cursor() as cur:
        cur.executemany(
            "insert into price_history (listing_id, price, status) values (%s, %s, %s)",
            resultados,
        )
        cur.executemany(
            "update listings set last_checked_at = now() where id = %s",
            [(r[0],) for r in resultados],
        )
    conn.commit()


def main():
    # prepare_threshold=None evita problemas com o pooler do Supabase
    with psycopg.connect(DATABASE_URL, prepare_threshold=None) as conn:
        listings = carregar_listings(conn)
        if not listings:
            print("Nenhum listing ativo encontrado.")
            return

        resultados = coletar(listings)
        salvar(conn, resultados)

    ok = sum(1 for r in resultados if r[2] == "ok")
    print(f"Fim da coleta: {ok}/{len(resultados)} preços coletados.")

    # Faz o GitHub Actions marcar a execução como falha se nada foi coletado
    if ok == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
