import requests
import json
import os
import time
import threading
import re
import html as html_lib
from urllib.parse import urljoin
from http.server import BaseHTTPRequestHandler, HTTPServer

# ============================================================
# CONFIGURAÇÕES
# ============================================================

TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ.get("CHAT_ID", "-1003905816059")

REDIS_URL = os.environ["UPSTASH_REDIS_REST_URL"]
REDIS_TOKEN = os.environ["UPSTASH_REDIS_REST_TOKEN"]

ADMIN_ID = os.environ.get("ADMIN_ID")

# 5 produtos por hora
INTERVALO = 720


# ============================================================
# HEADERS
# ============================================================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,image/avif,image/webp,"
        "image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Referer": "https://shopee.com.br/",
}


# ============================================================
# REDIS
# ============================================================

def redis_cmd(*args):
    resposta = requests.post(
        REDIS_URL,
        headers={
            "Authorization": f"Bearer {REDIS_TOKEN}",
            "Content-Type": "application/json"
        },
        json=list(args),
        timeout=15
    )

    resposta.raise_for_status()

    dados = resposta.json()

    return dados.get("result")


def carregar_fila():
    try:
        dado = redis_cmd("GET", "fila")

        if not dado:
            return []

        fila = json.loads(dado)

        if isinstance(fila, list):
            return fila

        return []

    except Exception as erro:
        print("❌ Erro carregando fila:", repr(erro))
        return []


def salvar_fila(fila):
    redis_cmd(
        "SET",
        "fila",
        json.dumps(fila, ensure_ascii=False)
    )


def adicionar_na_fila(nome, link, imagem=None, preco=None):
    fila = carregar_fila()

    for produto in fila:
        if produto.get("link") == link:
            return False, len(fila)

    produto = {
        "nome": nome,
        "link": link,
        "imagem": imagem,
        "preco": preco
    }

    fila.append(produto)

    salvar_fila(fila)

    return True, len(fila)


# ============================================================
# TELEGRAM
# ============================================================

def enviar_mensagem(chat_id, texto):
    try:
        resposta = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            data={
                "chat_id": chat_id,
                "text": texto
            },
            timeout=30
        )

        return resposta.json()

    except Exception as erro:
        print("❌ Erro Telegram:", repr(erro))
        return {"ok": False}


# ============================================================
# LIMPEZA
# ============================================================

def limpar_texto(valor):
    if valor is None:
        return None

    valor = html_lib.unescape(str(valor))

    valor = valor.replace("\\/", "/")
    valor = valor.replace("\\u002F", "/")
    valor = valor.replace("\\u003A", ":")
    valor = valor.replace("\\u0026", "&")

    valor = re.sub(r"<[^>]+>", " ", valor)

    valor = re.sub(r"\s+", " ", valor)

    return valor.strip()


def normalizar_url(url):
    if not url:
        return None

    url = limpar_texto(url)

    if url.startswith("//"):
        url = "https:" + url

    if url.startswith("/"):
        url = urljoin("https://shopee.com.br/", url)

    if not url.startswith("http"):
        return None

    return url


# ============================================================
# META TAGS
# ============================================================

def extrair_meta(html, nome):
    padroes = [
        rf'<meta[^>]+property=["\']{re.escape(nome)}["\'][^>]+content=["\']([^"\']+)["\']',

        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']{re.escape(nome)}["\']',

        rf'<meta[^>]+name=["\']{re.escape(nome)}["\'][^>]+content=["\']([^"\']+)["\']',

        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']{re.escape(nome)}["\']'
    ]

    for padrao in padroes:
        encontrado = re.search(
            padrao,
            html,
            re.IGNORECASE | re.DOTALL
        )

        if encontrado:
            return limpar_texto(encontrado.group(1))

    return None


# ============================================================
# JSON-LD
# ============================================================

def extrair_jsonld(html):
    resultados = []

    blocos = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>'
        r'(.*?)'
        r'</script>',
        html,
        re.IGNORECASE | re.DOTALL
    )

    for bloco in blocos:

        try:
            dados = json.loads(
                html_lib.unescape(bloco.strip())
            )

            if isinstance(dados, list):
                resultados.extend(dados)

            elif isinstance(dados, dict):
                resultados.append(dados)

        except Exception:
            continue

    return resultados


def procurar_jsonld_produto(dados):
    nome = None
    imagem = None
    preco = None

    for item in dados:

        if not isinstance(item, dict):
            continue

        tipo = item.get("@type")

        if isinstance(tipo, list):
            eh_produto = any(
                str(x).lower() == "product"
                for x in tipo
            )
        else:
            eh_produto = str(tipo).lower() == "product"

        if not eh_produto:
            continue

        nome = item.get("name")

        imagem = item.get("image")

        ofertas = item.get("offers")

        if isinstance(ofertas, list) and ofertas:
            ofertas = ofertas[0]

        if isinstance(ofertas, dict):
            preco = (
                ofertas.get("price")
                or ofertas.get("lowPrice")
                or ofertas.get("highPrice")
            )

        break

    return (
        limpar_texto(nome),
        normalizar_url(imagem[0] if isinstance(imagem, list) and imagem else imagem),
        preco
    )


# ============================================================
# CONVERSÃO DE PREÇO
# ============================================================

def converter_preco(valor):
    if valor is None:
        return None

    try:

        texto = str(valor).strip()

        texto = texto.replace("R$", "")
        texto = texto.replace("BRL", "")
        texto = texto.replace(" ", "")

        # 1.234,56
        if "," in texto:

            texto = texto.replace(".", "")
            texto = texto.replace(",", ".")

            numero = float(texto)

        else:

            numero = float(texto)

        if numero <= 0:
            return None

        if numero >= 1000000:
            return None

        # Valores inteiros muito altos podem estar em centavos.
        if numero >= 1000:
            numero = numero / 100

        if numero <= 0 or numero >= 100000:
            return None

        return round(numero, 2)

    except Exception:
        return None


# ============================================================
# VALIDAR PREÇO
# ============================================================

def preco_valido(valor):
    numero = converter_preco(valor)

    if numero is None:
        return False

    # Evita números absurdos encontrados no JSON.
    if numero < 0.01 or numero > 500000:
        return False

    return True


# ============================================================
# EXTRAIR PREÇOS DO TEXTO
# ============================================================

def precos_do_texto(texto):

    if not texto:
        return []

    candidatos = []

    padroes = [

        # R$ 29,90
        r'R\$\s*([0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2})',

        # R$ 29.90
        r'R\$\s*([0-9]+(?:\.[0-9]{2}))',

        # R$ 29
        r'R\$\s*([0-9]+)',

        # BRL 29,90
        r'BRL\s*([0-9]+(?:[.,][0-9]{1,2})?)',

        # preço: 29,90
        r'(?:preço|price|valor)\s*[:=]?\s*'
        r'(?:R\$|BRL)?\s*'
        r'([0-9]{1,6}(?:[.,][0-9]{1,2})?)'
    ]

    for padrao in padroes:

        encontrados = re.findall(
            padrao,
            texto,
            re.IGNORECASE
        )

        for valor in encontrados:

            numero = converter_preco(valor)

            if preco_valido(numero):
                candidatos.append(numero)

    return candidatos


# ============================================================
# EXTRAIR PREÇOS DO HTML / JSON
# ============================================================

def precos_do_html(html):

    if not html:
        return []

    candidatos = []

    # Primeiro procurar explicitamente valores acompanhados
    # por nomes de campos de preço.
    padroes = [

        r'"price"\s*:\s*"([^"]+)"',
        r'"price"\s*:\s*([0-9]+(?:\.[0-9]+)?)',

        r'"salePrice"\s*:\s*"([^"]+)"',
        r'"salePrice"\s*:\s*([0-9]+(?:\.[0-9]+)?)',

        r'"currentPrice"\s*:\s*"([^"]+)"',
        r'"currentPrice"\s*:\s*([0-9]+(?:\.[0-9]+)?)',

        r'"current_price"\s*:\s*"([^"]+)"',
        r'"current_price"\s*:\s*([0-9]+(?:\.[0-9]+)?)',

        r'"minPrice"\s*:\s*"([^"]+)"',
        r'"minPrice"\s*:\s*([0-9]+(?:\.[0-9]+)?)',

        r'"min_price"\s*:\s*"([^"]+)"',
        r'"min_price"\s*:\s*([0-9]+(?:\.[0-9]+)?)',

        r'"priceMin"\s*:\s*"([^"]+)"',
        r'"priceMin"\s*:\s*([0-9]+(?:\.[0-9]+)?)',

        r'"price_min"\s*:\s*"([^"]+)"',
        r'"price_min"\s*:\s*([0-9]+(?:\.[0-9]+)?)',

        r'"priceMax"\s*:\s*"([^"]+)"',
        r'"priceMax"\s*:\s*([0-9]+(?:\.[0-9]+)?)',

        r'"price_max"\s*:\s*"([^"]+)"',
        r'"price_max"\s*:\s*([0-9]+(?:\.[0-9]+)?)',

        r'"price_before_discount"\s*:\s*"([^"]+)"',
        r'"price_before_discount"\s*:\s*([0-9]+(?:\.[0-9]+)?)',

        r'"priceBeforeDiscount"\s*:\s*"([^"]+)"',
        r'"priceBeforeDiscount"\s*:\s*([0-9]+(?:\.[0-9]+)?)',

        r'"discountPrice"\s*:\s*"([^"]+)"',
        r'"discountPrice"\s*:\s*([0-9]+(?:\.[0-9]+)?)'
    ]

    for padrao in padroes:

        encontrados = re.findall(
            padrao,
            html,
            re.IGNORECASE
        )

        for valor in encontrados:

            numero = converter_preco(valor)

            if preco_valido(numero):
                candidatos.append(numero)

    return candidatos


# ============================================================
# PROCURAR PREÇO PERTO DE PALAVRAS-CHAVE
# ============================================================

def preco_por_contexto(html):

    if not html:
        return None

    texto = limpar_texto(html)

    # Pega trechos onde aparecem palavras relacionadas a preço.
    padrao_contexto = (
        r'.{0,250}'
        r'(?:price|preço|salePrice|currentPrice|priceMin|price_min)'
        r'.{0,250}'
    )

    trechos = re.findall(
        padrao_contexto,
        texto,
        re.IGNORECASE | re.DOTALL
    )

    candidatos = []

    for trecho in trechos[:100]:

        encontrados = precos_do_texto(trecho)

        candidatos.extend(encontrados)

    if candidatos:

        candidatos = sorted(set(candidatos))

        return candidatos[0]

    return None


# ============================================================
# EXTRAIR IMAGEM DO HTML
# ============================================================

def encontrar_imagem_html(html):

    if not html:
        return None

    # 1. OG IMAGE
    imagem = extrair_meta(html, "og:image")

    imagem = normalizar_url(imagem)

    if imagem:
        return imagem

    # 2. TWITTER IMAGE
    imagem = extrair_meta(html, "twitter:image")

    imagem = normalizar_url(imagem)

    if imagem:
        return imagem

    # 3. CDN SHOPEE
    padroes = [

        r'https?:\\?/\\?/[^"\']*susercontent\.com/file/[^"\'\s]+',

        r'https?://[^"\']*susercontent\.com/file/[^"\'\s]+',

        r'https?://[^"\']*shopee[^"\'\s]*/file/[^"\'\s]+'
    ]

    for padrao in padroes:

        encontrados = re.findall(
            padrao,
            html,
            re.IGNORECASE
        )

        for url in encontrados:

            url = normalizar_url(url)

            if url:
                return url

    return None


# ============================================================
# EXTRAIR NOME DO HTML
# ============================================================

def encontrar_nome_html(html):

    if not html:
        return None

    # OG TITLE
    nome = extrair_meta(html, "og:title")

    if nome:
        nome = limpar_texto(nome)

        if (
            len(nome) >= 5
            and nome.lower() not in (
                "shopee",
                "shopee brasil"
            )
        ):
            return nome

    # TITLE
    titulo = re.search(
        r'<title[^>]*>(.*?)</title>',
        html,
        re.IGNORECASE | re.DOTALL
    )

    if titulo:

        nome = limpar_texto(titulo.group(1))

        if (
            nome
            and len(nome) >= 5
            and "shopee" not in nome.lower()
        ):
            return nome

    return None


# ============================================================
# PLAYWRIGHT OPCIONAL
# ============================================================

def tentar_playwright(url):

    nome = None
    preco = None
    imagem = None

    try:

        from playwright.sync_api import sync_playwright

    except Exception as erro:

        print("⚠️ Playwright não disponível:", repr(erro))

        return nome, preco, imagem

    try:

        print("🌐 Tentando navegador Playwright...")

        with sync_playwright() as p:

            navegador = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu"
                ]
            )

            pagina = navegador.new_page(
                viewport={
                    "width": 1366,
                    "height": 900
                },
                user_agent=HEADERS["User-Agent"],
                locale="pt-BR",
                timezone_id="America/Sao_Paulo"
            )

            pagina.set_default_timeout(10000)

            try:

                pagina.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=40000
                )

            except Exception as erro:

                print(
                    "⚠️ Navegação Playwright:",
                    repr(erro)
                )

            pagina.wait_for_timeout(5000)

            # ------------------------------------------------
            # HTML
            # ------------------------------------------------

            try:
                html = pagina.content()
            except Exception:
                html = ""

            print(
                "📄 HTML PLAYWRIGHT:",
                len(html),
                "caracteres"
            )

            if html:

                nome = encontrar_nome_html(html)

                imagem = encontrar_imagem_html(html)

                precos = precos_do_html(html)

                if precos:
                    preco = sorted(set(precos))[0]

            # ------------------------------------------------
            # TEXTO VISÍVEL
            # ------------------------------------------------

            try:

                texto = pagina.locator(
                    "body"
                ).inner_text(timeout=10000)

                precos_texto = precos_do_texto(texto)

                if precos_texto:

                    # Preferir preços razoáveis
                    precos_texto = sorted(
                        set(precos_texto)
                    )

                    if preco is None:
                        preco = precos_texto[0]

                # H1
                if not nome:

                    h1 = pagina.locator("h1")

                    quantidade = h1.count()

                    for i in range(
                        min(quantidade, 10)
                    ):

                        try:

                            valor = h1.nth(i).inner_text(
                                timeout=2000
                            )

                            valor = limpar_texto(valor)

                            if (
                                valor
                                and len(valor) >= 5
                                and "shopee" not in valor.lower()
                            ):

                                nome = valor
                                break

                        except Exception:
                            continue

            except Exception as erro:

                print(
                    "⚠️ Erro texto Playwright:",
                    repr(erro)
                )

            # ------------------------------------------------
            # IMAGENS DOM
            # ------------------------------------------------

            if not imagem:

                try:

                    imagens = pagina.locator("img")

                    quantidade = imagens.count()

                    for i in range(
                        min(quantidade, 100)
                    ):

                        try:

                            elemento = imagens.nth(i)

                            src = None

                            for atributo in (
                                "src",
                                "data-src",
                                "data-lazy-src",
                                "data-original",
                                "data-url"
                            ):

                                valor = elemento.get_attribute(
                                    atributo
                                )

                                if valor:
                                    src = valor
                                    break

                            if not src:
                                continue

                            src = normalizar_url(src)

                            if not src:
                                continue

                            baixo = src.lower()

                            if any(
                                palavra in baixo
                                for palavra in (
                                    "logo",
                                    "avatar",
                                    "icon",
                                    "favicon"
                                )
                            ):
                                continue

                            if (
                                "susercontent.com/file/"
                                in baixo
                            ):

                                imagem = src
                                break

                            if not imagem:
                                imagem = src

                        except Exception:
                            continue

                except Exception as erro:

                    print(
                        "⚠️ Erro imagens DOM:",
                        repr(erro)
                    )

            navegador.close()

    except Exception as erro:

        # IMPORTANTE:
        # Playwright NÃO pode derrubar o leitor inteiro.
        print(
            "⚠️ PLAYWRIGHT INDISPONÍVEL:",
            repr(erro)
        )

    return nome, preco, imagem


# ============================================================
# LEITOR PRINCIPAL SHOPEE
# ============================================================

def extrair_dados_shopee(link):

    print("")
    print("======================================")
    print("🔎 LEITOR SHOPEE V4")
    print("🔗 LINK RECEBIDO:")
    print(link)
    print("======================================")

    nome = None
    preco = None
    imagem = None

    # ========================================================
    # 1. RESOLVER LINK
    # ========================================================

    url_final = link
    html_http = ""

    try:

        sessao = requests.Session()

        resposta = sessao.get(
            link,
            headers=HEADERS,
            timeout=30,
            allow_redirects=True
        )

        url_final = resposta.url

        html_http = resposta.text

        print("🔗 URL FINAL:")
        print(url_final)

        print(
            "📄 HTML HTTP:",
            len(html_http),
            "caracteres"
        )

    except Exception as erro:

        print(
            "⚠️ Erro resolvendo link:",
            repr(erro)
        )

    # ========================================================
    # 2. HTML HTTP
    # ========================================================

    if html_http:

        print("🔍 Analisando HTML HTTP...")

        # NOME
        nome = encontrar_nome_html(html_http)

        if nome:
            print("🛒 NOME HTTP:", nome)

        # IMAGEM
        imagem = encontrar_imagem_html(html_http)

        if imagem:
            print("🖼️ IMAGEM HTTP:", imagem)

        # JSON-LD
        try:

            dados_jsonld = extrair_jsonld(
                html_http
            )

            nome_json, imagem_json, preco_json = (
                procurar_jsonld_produto(
                    dados_jsonld
                )
            )

            if not nome and nome_json:
                nome = nome_json
                print(
                    "🛒 NOME JSON-LD:",
                    nome
                )

            if not imagem and imagem_json:
                imagem = imagem_json
                print(
                    "🖼️ IMAGEM JSON-LD:",
                    imagem
                )

            if not preco and preco_json:

                convertido = converter_preco(
                    preco_json
                )

                if preco_valido(convertido):

                    preco = convertido

                    print(
                        "💰 PREÇO JSON-LD:",
                        preco
                    )

        except Exception as erro:

            print(
                "⚠️ Erro JSON-LD:",
                repr(erro)
            )

        # PREÇO NO TEXTO/HTML
        if not preco:

            precos = precos_do_html(
                html_http
            )

            if precos:

                precos = sorted(
                    set(precos)
                )

                preco = precos[0]

                print(
                    "💰 PREÇO HTML:",
                    preco
                )

        # PREÇO POR CONTEXTO
        if not preco:

            preco_contexto = preco_por_contexto(
                html_http
            )

            if preco_contexto:

                preco = preco_contexto

                print(
                    "💰 PREÇO CONTEXTO:",
                    preco
                )

    # ========================================================
    # 3. PLAYWRIGHT
    # ========================================================

    # Só tenta se ainda faltar alguma coisa.
    if (
        not nome
        or not preco
        or not imagem
    ):

        (
            nome_pw,
            preco_pw,
            imagem_pw
        ) = tentar_playwright(url_final)

        if not nome and nome_pw:
            nome = nome_pw

        if not preco and preco_pw:
            preco = preco_pw

        if not imagem and imagem_pw:
            imagem = imagem_pw

    # ========================================================
    # 4. ÚLTIMA TENTATIVA: HTML HTTP NOVAMENTE
    # ========================================================

    if html_http:

        if not nome:
            nome = encontrar_nome_html(
                html_http
            )

        if not imagem:
            imagem = encontrar_imagem_html(
                html_http
            )

        if not preco:

            precos = precos_do_html(
                html_http
            )

            if precos:
                preco = sorted(
                    set(precos)
                )[0]

    # ========================================================
    # NORMALIZAÇÃO
    # ========================================================

    if nome:

        nome = limpar_texto(nome)

        # Remove lixo comum
        nome = re.sub(
            r'\s*[-|]\s*Shopee.*$',
            '',
            nome,
            flags=re.IGNORECASE
        ).strip()

    if preco:

        preco = converter_preco(preco)

    if imagem:

        imagem = normalizar_url(
            imagem
        )

    # ========================================================
    # FORMATAÇÃO
    # ========================================================

    preco_formatado = None

    if preco:

        preco_formatado = (
            f"R$ {preco:,.2f}"
            .replace(",", "X")
            .replace(".", ",")
            .replace("X", ".")
        )

    # ========================================================
    # RESULTADO
    # ========================================================

    print("")
    print("======================================")
    print("🔎 RESULTADO FINAL SHOPEE")
    print(
        "🛒 Nome:",
        nome or "NÃO ENCONTRADO"
    )
    print(
        "💰 Preço:",
        preco_formatado or "NÃO ENCONTRADO"
    )
    print(
        "🖼️ Imagem:",
        imagem or "NÃO ENCONTRADA"
    )
    print("======================================")
    print("")

    return (
        nome,
        preco_formatado,
        imagem
    )


# ============================================================
# PUBLICAR
# ============================================================

def publicar_proximo():

    fila = carregar_fila()

    if not fila:

        print("📭 Fila vazia.")

        return False

    produto = fila[0]

    nome = produto.get(
        "nome",
        "Produto Shopee"
    )

    link = produto.get(
        "link"
    )

    imagem = produto.get(
        "imagem"
    )

    preco = produto.get(
        "preco"
    )

    oferta = (
        f"💰 {preco}"
        if preco
        else
        "💰 OFERTA ESPECIAL"
    )

    legenda = f"""🔥 ACHADINHO DA SHOPEE 🔥

🛍️ {nome}

{oferta}

⚡ Aproveite enquanto estiver disponível!

👇 Clique abaixo para conferir:"""

    teclado = {
        "inline_keyboard": [
            [
                {
                    "text": "🛒 COMPRAR AGORA",
                    "url": link
                }
            ]
        ]
    }

    resultado = {
        "ok": False
    }

    # ========================================================
    # IMAGEM
    # ========================================================

    if imagem:

        try:

            # Se for file_id do Telegram,
            # envia diretamente.
            if not imagem.startswith("http"):

                resposta = requests.post(
                    f"https://api.telegram.org/bot{TOKEN}/sendPhoto",
                    data={
                        "chat_id": CHAT_ID,
                        "photo": imagem,
                        "caption": legenda,
                        "reply_markup": json.dumps(
                            teclado
                        )
                    },
                    timeout=30
                )

                resultado = resposta.json()

            else:

                print(
                    "🖼️ Baixando imagem..."
                )

                resposta_imagem = requests.get(
                    imagem,
                    headers=HEADERS,
                    timeout=30
                )

                resposta_imagem.raise_for_status()

                arquivos = {
                    "photo": (
                        "produto.jpg",
                        resposta_imagem.content,
                        "image/jpeg"
                    )
                }

                dados = {
                    "chat_id": CHAT_ID,
                    "caption": legenda,
                    "reply_markup": json.dumps(
                        teclado
                    )
                }

                resposta = requests.post(
                    f"https://api.telegram.org/bot{TOKEN}/sendPhoto",
                    data=dados,
                    files=arquivos,
                    timeout=60
                )

                resultado = resposta.json()

        except Exception as erro:

            print(
                "⚠️ Erro enviando imagem:",
                repr(erro)
            )

    # ========================================================
    # SEM IMAGEM / FALLBACK
    # ========================================================

    if not resultado.get("ok"):

        try:

            resposta = requests.post(
                f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                data={
                    "chat_id": CHAT_ID,
                    "text": legenda,
                    "reply_markup": json.dumps(
                        teclado
                    )
                },
                timeout=30
            )

            resultado = resposta.json()

        except Exception as erro:

            print(
                "❌ Erro fallback Telegram:",
                repr(erro)
            )

            return False

    # ========================================================
    # CONFIRMAÇÃO
    # ========================================================

    if resultado.get("ok"):

        fila.pop(0)

        salvar_fila(fila)

        print("")
        print("================================")
        print("✅ PRODUTO PUBLICADO")
        print("🛒", nome)
        print("📦 Restam:", len(fila))
        print("================================")

        return True

    print(
        "❌ Telegram recusou:",
        resultado
    )

    return False


# ============================================================
# PUBLICADOR AUTOMÁTICO
# ============================================================

def publicador_automatico():

    print(
        "🚀 Publicador automático iniciado."
    )

    while True:

        try:

            ultimo = float(
                redis_cmd(
                    "GET",
                    "ultimo_post"
                ) or 0
            )

            agora = time.time()

            if agora - ultimo >= INTERVALO:

                if publicar_proximo():

                    redis_cmd(
                        "SET",
                        "ultimo_post",
                        str(agora)
                    )

        except Exception as erro:

            print(
                "❌ Erro publicador:",
                repr(erro)
            )

        time.sleep(60)


# ============================================================
# FILA
# ============================================================

def mostrar_fila(chat_id):

    fila = carregar_fila()

    if not fila:

        enviar_mensagem(
            chat_id,
            "📭 A fila está vazia."
        )

        return

    mensagem = "📦 PRODUTOS NA FILA\n\n"

    for numero, produto in enumerate(
        fila,
        start=1
    ):

        mensagem += (
            f"{numero}. 🛒 "
            f"{produto.get('nome', 'Produto')}\n"
            f"💰 "
            f"{produto.get('preco') or 'não encontrado'}\n"
            f"🔗 "
            f"{produto.get('link', '')}\n\n"
        )

    enviar_mensagem(
        chat_id,
        mensagem[:4000]
    )


def limpar_fila(chat_id):

    salvar_fila([])

    enviar_mensagem(
        chat_id,
        "🗑️ Fila limpa com sucesso!"
    )


# ============================================================
# PROCESSAR MENSAGEM
# ============================================================

def processar_mensagem(mensagem):

    chat_id = mensagem["chat"]["id"]

    # ADMIN
    if ADMIN_ID:

        remetente = mensagem.get(
            "from",
            {}
        ).get("id")

        if str(remetente) != str(ADMIN_ID):

            return

    # ========================================================
    # FOTO + LEGENDA
    # ========================================================

    if "photo" in mensagem:

        legenda = mensagem.get(
            "caption",
            ""
        ).strip()

        linhas = [
            linha.strip()
            for linha in legenda.split("\n")
            if linha.strip()
        ]

        if len(linhas) < 2:

            enviar_mensagem(
                chat_id,
                """❌ FORMATO INCORRETO

Envie a FOTO com:

Nome do produto
Link da Shopee"""
            )

            return

        nome = linhas[0]
        link = linhas[1]

        if not link.startswith("http"):

            enviar_mensagem(
                chat_id,
                "❌ O link informado não é válido."
            )

            return

        # LEITOR
        nome_shopee, preco, imagem_shopee = (
            extrair_dados_shopee(link)
        )

        if nome_shopee:
            nome = nome_shopee

        # FOTO DO TELEGRAM TEM PRIORIDADE
        file_id = mensagem["photo"][-1]["file_id"]

        imagem = file_id

        adicionado, posicao = adicionar_na_fila(
            nome,
            link,
            imagem,
            preco
        )

        if not adicionado:

            enviar_mensagem(
                chat_id,
                "⚠️ Esse produto já está na fila."
            )

            return

        enviar_mensagem(
            chat_id,
            f"""✅ PRODUTO ADICIONADO!

🛒 {nome}

💰 Preço: {preco or 'não encontrado'}

📦 Posição: {posicao}

🖼️ Foto recebida e salva.

⏰ Publicação automática ativa."""
        )

        return

    # ========================================================
    # TEXTO
    # ========================================================

    texto = mensagem.get(
        "text",
        ""
    ).strip()

    # START
    if texto == "/start":

        enviar_mensagem(
            chat_id,
            """🤖 ACHADOS DA SHOPEE

📸 ADICIONAR PRODUTO

Envie:

FOTO + legenda

Nome do produto
Link da Shopee

OU simplesmente:

🔗 Link da Shopee

O sistema tenta encontrar automaticamente:

🛒 Nome
💰 Preço
🖼️ Imagem

COMANDOS:

/fila
📦 Ver fila

/limparfila
🗑️ Limpar fila"""
        )

        return

    # FILA
    if texto == "/fila":

        mostrar_fila(chat_id)

        return

    # LIMPAR
    if texto == "/limparfila":

        limpar_fila(chat_id)

        return

    # ========================================================
    # SOMENTE LINK
    # ========================================================

    if texto.startswith("http"):

        link = texto.split()[0]

        print(
            "🔎 Link recebido:",
            link
        )

        nome, preco, imagem = (
            extrair_dados_shopee(link)
        )

        if not nome:

            enviar_mensagem(
                chat_id,
                """⚠️ Não consegui identificar o produto.

Envie uma FOTO com a legenda:

Nome do produto
Link da Shopee"""
            )

            return

        adicionado, posicao = adicionar_na_fila(
            nome,
            link,
            imagem,
            preco
        )

        if not adicionado:

            enviar_mensagem(
                chat_id,
                "⚠️ Esse produto já está na fila."
            )

            return

        enviar_mensagem(
            chat_id,
            f"""✅ PRODUTO ADICIONADO!

🛒 {nome}

💰 Preço: {preco or 'não encontrado'}

🖼️ Imagem: {'encontrada ✅' if imagem else 'não encontrada ⚠️'}

📦 Posição: {posicao}

⏰ Será publicado automaticamente."""
        )

        return

    # ========================================================
    # NOME + LINK
    # ========================================================

    linhas = [
        linha.strip()
        for linha in texto.split("\n")
        if linha.strip()
    ]

    if len(linhas) < 2:

        enviar_mensagem(
            chat_id,
            """❌ FORMATO INCORRETO

Você pode enviar:

1️⃣ Somente o link

OU

2️⃣

Nome do produto
Link da Shopee"""
        )

        return

    nome = linhas[0]
    link = linhas[1]

    if not link.startswith("http"):

        enviar_mensagem(
            chat_id,
            "❌ Link inválido."
        )

        return

    nome_shopee, preco, imagem = (
        extrair_dados_shopee(link)
    )

    if nome_shopee:
        nome = nome_shopee

    adicionado, posicao = adicionar_na_fila(
        nome,
        link,
        imagem,
        preco
    )

    if not adicionado:

        enviar_mensagem(
            chat_id,
            "⚠️ Esse produto já está na fila."
        )

        return

    enviar_mensagem(
        chat_id,
        f"""✅ PRODUTO ADICIONADO!

🛒 {nome}

💰 Preço: {preco or 'não encontrado'}

🖼️ Imagem: {'encontrada ✅' if imagem else 'não encontrada ⚠️'}

📦 Posição: {posicao}

⏰ Será publicado automaticamente."""
    )


# ============================================================
# RECEBER TELEGRAM
# ============================================================

def iniciar_bot():

    print(
        "📥 Sistema de recebimento iniciado."
    )

    ultimo_update = None

    while True:

        try:

            url = (
                f"https://api.telegram.org/"
                f"bot{TOKEN}/getUpdates"
            )

            parametros = {
                "timeout": 30
            }

            if ultimo_update is not None:

                parametros["offset"] = (
                    ultimo_update + 1
                )

            resposta = requests.get(
                url,
                params=parametros,
                timeout=35
            )

            dados = resposta.json()

            if not dados.get("ok"):

                print(
                    "❌ Erro Telegram:",
                    dados
                )

                time.sleep(5)

                continue

            for update in dados.get(
                "result",
                []
            ):

                ultimo_update = update[
                    "update_id"
                ]

                if "message" in update:

                    try:

                        processar_mensagem(
                            update["message"]
                        )

                    except Exception as erro:

                        print(
                            "❌ Erro processando:",
                            repr(erro)
                        )

                        try:

                            enviar_mensagem(
                                update["message"]["chat"]["id"],
                                "⚠️ Erro temporário. Tente novamente."
                            )

                        except Exception:
                            pass

        except Exception as erro:

            print(
                "❌ Erro recebendo Telegram:",
                repr(erro)
            )

            time.sleep(5)


# ============================================================
# SERVIDOR RENDER
# ============================================================

class Saude(BaseHTTPRequestHandler):

    def do_GET(self):

        self.send_response(200)

        self.send_header(
            "Content-Type",
            "text/plain"
        )

        self.end_headers()

        self.wfile.write(
            b"ACHADOS DA SHOPEE ONLINE"
        )

    def do_HEAD(self):

        self.send_response(200)

        self.end_headers()

    def log_message(
        self,
        formato,
        *args
    ):
        pass


def servidor_saude():

    porta = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    servidor = HTTPServer(
        ("0.0.0.0", porta),
        Saude
    )

    print(
        f"🌐 Servidor Render ativo na porta {porta}"
    )

    servidor.serve_forever()


# ============================================================
# INICIAR SISTEMA
# ============================================================

if __name__ == "__main__":

    threading.Thread(
        target=servidor_saude,
        daemon=True
    ).start()

    threading.Thread(
        target=publicador_automatico,
        daemon=True
    ).start()

    print("")
    print("======================================")
    print("✅ SISTEMA COMPLETO ATIVO")
    print("⏰ 5 PRODUTOS POR HORA")
    print("🔎 LEITOR SHOPEE V4 ATIVO")
    print("💰 LEITOR DE PREÇO MULTICAMADA")
    print("🖼️ LEITOR DE IMAGEM MULTICAMADA")
    print("🌐 PLAYWRIGHT OPCIONAL")
    print("======================================")
    print("")

    iniciar_bot()
