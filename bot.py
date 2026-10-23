import requests
import json
import os
import time
import threading
import re
from html import unescape
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

# ==========================================
# CONFIGURAÇÕES
# ==========================================

TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ.get("CHAT_ID", "-1003905816059")

REDIS_URL = os.environ["UPSTASH_REDIS_REST_URL"]
REDIS_TOKEN = os.environ["UPSTASH_REDIS_REST_TOKEN"]

ADMIN_ID = os.environ.get("ADMIN_ID")

# 5 produtos por hora
INTERVALO = 720


# ==========================================
# HEADERS SHOPEE
# ==========================================

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
    "Referer": "https://shopee.com.br/",
    "Connection": "keep-alive"
}


# ==========================================
# BANCO - UPSTASH REDIS
# ==========================================

def redis_cmd(*args):

    resposta = requests.post(
        REDIS_URL,
        headers={
            "Authorization": f"Bearer {REDIS_TOKEN}",
            "Content-Type": "application/json"
        },
        json=list(args),
        timeout=20
    )

    resposta.raise_for_status()

    dados = resposta.json()

    return dados.get("result")


def carregar_fila():

    try:

        dado = redis_cmd(
            "GET",
            "fila"
        )

        if not dado:
            return []

        fila = json.loads(dado)

        if isinstance(fila, list):
            return fila

        return []

    except Exception as erro:

        print(
            "❌ Erro carregando fila:",
            repr(erro)
        )

        return []


def salvar_fila(fila):

    redis_cmd(
        "SET",
        "fila",
        json.dumps(
            fila,
            ensure_ascii=False
        )
    )


def adicionar_na_fila(
    nome,
    link,
    imagem=None,
    preco=None
):

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


# ==========================================
# TELEGRAM
# ==========================================

def enviar_mensagem(chat_id, texto):

    url = (
        f"https://api.telegram.org/"
        f"bot{TOKEN}/sendMessage"
    )

    try:

        resposta = requests.post(
            url,
            data={
                "chat_id": chat_id,
                "text": texto
            },
            timeout=30
        )

        return resposta.json()

    except Exception as erro:

        print(
            "❌ Erro Telegram:",
            repr(erro)
        )

        return {
            "ok": False
        }


# ==========================================
# LIMPEZA DE TEXTO
# ==========================================

def limpar_html(texto):

    if not texto:
        return ""

    texto = unescape(
        str(texto)
    )

    texto = texto.replace(
        "\\/",
        "/"
    )

    texto = texto.replace(
        "\\u002F",
        "/"
    )

    texto = re.sub(
        r"<[^>]+>",
        " ",
        texto
    )

    texto = re.sub(
        r"\s+",
        " ",
        texto
    )

    return texto.strip()


def normalizar_url(url):

    if not url:
        return None

    try:

        url = unescape(
            str(url).strip()
        )

        url = url.replace(
            "\\/",
            "/"
        )

        url = url.replace(
            "\\u002F",
            "/"
        )

        if url.startswith("//"):
            url = "https:" + url

        if url.startswith("http://"):
            url = (
                "https://" +
                url[7:]
            )

        if not url.startswith("http"):
            return None

        return url

    except Exception:

        return None


# ==========================================
# META TAGS
# ==========================================

def extrair_meta(html, propriedade):

    if not html:
        return None

    padroes = [

        rf'<meta[^>]+property=["\']'
        rf'{re.escape(propriedade)}'
        rf'["\'][^>]+content=["\']'
        rf'([^"\']+)["\']',

        rf'<meta[^>]+content=["\']'
        rf'([^"\']+)["\'][^>]+property=["\']'
        rf'{re.escape(propriedade)}'
        rf'["\']',

        rf'<meta[^>]+name=["\']'
        rf'{re.escape(propriedade)}'
        rf'["\'][^>]+content=["\']'
        rf'([^"\']+)["\']',

        rf'<meta[^>]+content=["\']'
        rf'([^"\']+)["\'][^>]+name=["\']'
        rf'{re.escape(propriedade)}'
        rf'["\']'
    ]

    for padrao in padroes:

        resultado = re.search(
            padrao,
            html,
            re.IGNORECASE
        )

        if resultado:

            valor = limpar_html(
                resultado.group(1)
            )

            if valor:
                return valor

    return None


# ==========================================
# JSON-LD
# ==========================================

def extrair_jsonld(html):

    resultados = []

    if not html:
        return resultados

    blocos = re.findall(
        r'<script[^>]+type=["\']'
        r'application/ld\+json'
        r'["\'][^>]*>(.*?)</script>',
        html,
        re.IGNORECASE | re.DOTALL
    )

    for bloco in blocos:

        try:

            bloco = unescape(
                bloco.strip()
            )

            dados = json.loads(
                bloco
            )

            if isinstance(dados, list):

                resultados.extend(
                    dados
                )

            elif isinstance(dados, dict):

                resultados.append(
                    dados
                )

        except Exception:

            continue

    return resultados


def procurar_produto_jsonld(dados):

    nome = None
    imagem = None
    preco = None

    for item in dados:

        if not isinstance(
            item,
            dict
        ):
            continue

        tipo = item.get(
            "@type"
        )

        if isinstance(
            tipo,
            list
        ):

            eh_produto = (
                "Product" in tipo
            )

        else:

            eh_produto = (
                tipo == "Product"
            )

        if not eh_produto:
            continue

        nome = item.get(
            "name"
        )

        imagem = item.get(
            "image"
        )

        oferta = item.get(
            "offers"
        )

        if isinstance(
            oferta,
            list
        ) and oferta:

            oferta = oferta[0]

        if isinstance(
            oferta,
            dict
        ):

            preco = (
                oferta.get("price")
                or oferta.get("lowPrice")
                or oferta.get("highPrice")
            )

        break

    return (
        nome,
        imagem,
        preco
    )


# ==========================================
# NOME
# ==========================================

def nome_valido(nome):

    if not nome:
        return False

    nome = re.sub(
        r"\s+",
        " ",
        str(nome)
    ).strip()

    if len(nome) < 5:
        return False

    proibidos = [
        "shopee",
        "shopee brasil",
        "comprar na shopee",
        "shopee brasil |"
    ]

    if nome.lower() in proibidos:
        return False

    return True


def limpar_nome(nome):

    if not nome:
        return None

    nome = unescape(
        str(nome)
    )

    nome = limpar_html(
        nome
    )

    # Remove coisas adicionadas pela Shopee
    nome = re.sub(
        r"\s*\|\s*Shopee.*$",
        "",
        nome,
        flags=re.IGNORECASE
    )

    nome = re.sub(
        r"\s*-\s*Shopee.*$",
        "",
        nome,
        flags=re.IGNORECASE
    )

    nome = re.sub(
        r"\s+",
        " ",
        nome
    ).strip()

    if nome_valido(nome):
        return nome

    return None


# ==========================================
# PREÇO
# ==========================================

def converter_preco(valor):

    if valor is None:
        return None

    try:

        texto = str(valor).strip()

        texto = (
            texto
            .replace("R$", "")
            .replace("BRL", "")
            .replace("brl", "")
            .replace(" ", "")
        )

        if not texto:
            return None

        # Formato:
        # 1.234,56
        if "," in texto:

            texto = texto.replace(
                ".",
                ""
            )

            texto = texto.replace(
                ",",
                "."
            )

            numero = float(
                texto
            )

        else:

            numero = float(
                texto
            )

        if numero <= 0:
            return None

        if numero >= 10000000:
            return None

        return round(
            numero,
            2
        )

    except Exception:

        return None


def converter_preco_interno(valor):

    """
    Dados internos da Shopee podem aparecer
    em escalas diferentes.

    Esta função tenta as escalas mais comuns
    sem alterar preços normais.
    """

    if valor is None:
        return None

    try:

        texto = str(valor).strip()

        # Se já possui vírgula ou ponto decimal
        # e parece preço normal
        if "," in texto:

            return converter_preco(
                texto
            )

        # Decimal normal
        if "." in texto:

            numero = float(
                texto
            )

            if 0 < numero < 100000:
                return round(
                    numero,
                    2
                )

        numero = int(
            float(texto)
        )

        if numero <= 0:
            return None

        # Valores muito grandes podem estar
        # multiplicados por 100000.
        if numero >= 100000:

            candidato = numero / 100000

            if 0 < candidato < 100000:
                return round(
                    candidato,
                    2
                )

        # Segunda possibilidade:
        # centavos.
        if numero >= 1000:

            candidato = numero / 100

            if 0 < candidato < 100000:
                return round(
                    candidato,
                    2
                )

        if 0 < numero < 1000:

            return float(
                numero
            )

        return None

    except Exception:

        return None


def extrair_precos_texto(texto):

    candidatos = []

    if not texto:
        return candidatos

    padroes = [

        # R$ 1.234,56
        r'R\$\s*'
        r'([0-9]{1,3}'
        r'(?:\.[0-9]{3})*,[0-9]{2})',

        # R$ 123,45
        r'R\$\s*'
        r'([0-9]+,[0-9]{2})',

        # R$ 123.45
        r'R\$\s*'
        r'([0-9]+[.][0-9]{2})',

        # BRL
        r'BRL\s*'
        r'([0-9]+(?:[.,][0-9]{2})?)'
    ]

    for padrao in padroes:

        encontrados = re.findall(
            padrao,
            texto,
            re.IGNORECASE
        )

        for valor in encontrados:

            numero = converter_preco(
                valor
            )

            if numero:
                candidatos.append(
                    numero
                )

    return candidatos


# ==========================================
# IMAGEM
# ==========================================

def imagem_valida(url):

    url = normalizar_url(
        url
    )

    if not url:
        return False

    url_lower = url.lower()

    bloqueios = [
        "logo",
        "avatar",
        "favicon",
        "icon",
        "sprite",
        "qr-code"
    ]

    if any(
        palavra in url_lower
        for palavra in bloqueios
    ):
        return False

    return (
        "susercontent.com" in url_lower
        or "/file/" in url_lower
        or "shopee" in url_lower
    )


def extrair_imagem_html(html):

    if not html:
        return None

    # 1. OG IMAGE
    imagem = extrair_meta(
        html,
        "og:image"
    )

    imagem = normalizar_url(
        imagem
    )

    if imagem_valida(
        imagem
    ):

        print(
            "🖼️ Imagem encontrada no OG:"
        )

        return imagem

    # 2. CDN SUSERCONTENT
    padroes = [

        r'https?://[^"\']*'
        r'susercontent\.com/file/'
        r'[^"\'\s<>]+',

        r'https?:\\?/\\?/[^"\'\s<>]*'
        r'susercontent\.com/file/'
        r'[^"\'\s<>]+',

        r'https?://[^"\']*'
        r'shopee[^"\'\s<>]*/file/'
        r'[^"\'\s<>]+'
    ]

    encontradas = []

    for padrao in padroes:

        resultados = re.findall(
            padrao,
            html,
            re.IGNORECASE
        )

        for url in resultados:

            url = normalizar_url(
                url
            )

            if (
                imagem_valida(url)
                and url not in encontradas
            ):

                encontradas.append(
                    url
                )

    if encontradas:

        print(
            "🖼️ Imagens CDN encontradas:",
            len(encontradas)
        )

        return encontradas[0]

    return None


# ==========================================
# IMAGEM DO DOM
# ==========================================

def extrair_imagem_dom(pagina):

    candidatas = []

    try:

        imagens = pagina.locator(
            "img"
        )

        quantidade = imagens.count()

        print(
            "🖼️ Imagens no DOM:",
            quantidade
        )

        for i in range(
            min(quantidade, 250)
        ):

            try:

                elemento = imagens.nth(i)

                urls = []

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
                        urls.append(
                            valor
                        )

                srcset = elemento.get_attribute(
                    "srcset"
                )

                if srcset:

                    primeiro = (
                        srcset
                        .split(",")[0]
                        .strip()
                        .split(" ")[0]
                    )

                    urls.append(
                        primeiro
                    )

                for url in urls:

                    url = normalizar_url(
                        url
                    )

                    if imagem_valida(
                        url
                    ):

                        candidatas.append(
                            url
                        )

            except Exception:

                continue

        candidatas = list(
            dict.fromkeys(
                candidatas
            )
        )

        # Prioridade para CDN
        for url in candidatas:

            if (
                "susercontent.com/file/"
                in url.lower()
            ):

                print(
                    "🖼️ Imagem CDN encontrada:"
                )

                print(url)

                return url

        if candidatas:

            print(
                "🖼️ Imagem encontrada:"
            )

            print(
                candidatas[0]
            )

            return candidatas[0]

    except Exception as erro:

        print(
            "⚠️ Erro lendo imagens:",
            repr(erro)
        )

    return None


# ==========================================
# NOME NO HTML
# ==========================================

def extrair_nome_html(html):

    if not html:
        return None

    # OG TITLE
    nome = extrair_meta(
        html,
        "og:title"
    )

    nome = limpar_nome(
        nome
    )

    if nome:
        return nome

    # TITLE
    resultado = re.search(
        r"<title[^>]*>(.*?)</title>",
        html,
        re.IGNORECASE | re.DOTALL
    )

    if resultado:

        nome = limpar_nome(
            resultado.group(1)
        )

        if nome:
            return nome

    return None


# ==========================================
# NOME NO DOM
# ==========================================

def extrair_nome_dom(pagina):

    # H1
    try:

        h1s = pagina.locator(
            "h1"
        )

        quantidade = h1s.count()

        print(
            "🔤 H1 encontrados:",
            quantidade
        )

        for i in range(
            min(quantidade, 10)
        ):

            try:

                nome = h1s.nth(i).inner_text(
                    timeout=3000
                )

                nome = limpar_nome(
                    nome
                )

                if nome:
                    return nome

            except Exception:

                continue

    except Exception:

        pass

    # Seletores alternativos
    seletores = [
        '[class*="product-title"]',
        '[class*="ProductTitle"]',
        '[class*="productName"]',
        '[class*="ProductName"]'
    ]

    for seletor in seletores:

        try:

            elementos = pagina.locator(
                seletor
            )

            quantidade = elementos.count()

            for i in range(
                min(quantidade, 20)
            ):

                try:

                    nome = elementos.nth(i).inner_text(
                        timeout=2000
                    )

                    nome = limpar_nome(
                        nome
                    )

                    if nome:
                        return nome

                except Exception:

                    continue

        except Exception:

            continue

    return None


# ==========================================
# PREÇOS INTERNOS NO HTML
# ==========================================

def encontrar_precos_internos(html):

    candidatos = []

    if not html:
        return candidatos

    campos = [

        "price",
        "price_min",
        "price_max",
        "priceMin",
        "priceMax",

        "salePrice",
        "sale_price",

        "currentPrice",
        "current_price",

        "priceBeforeDiscount",
        "price_before_discount",

        "minPrice",
        "maxPrice"
    ]

    for campo in campos:

        padroes = [

            rf'"{re.escape(campo)}"\s*:\s*'
            rf'"([0-9]+(?:\.[0-9]+)?)"',

            rf'"{re.escape(campo)}"\s*:\s*'
            rf'([0-9]+(?:\.[0-9]+)?)'
        ]

        for padrao in padroes:

            encontrados = re.findall(
                padrao,
                html,
                re.IGNORECASE
            )

            for valor in encontrados:

                numero = converter_preco_interno(
                    valor
                )

                if numero:

                    candidatos.append(
                        numero
                    )

    return candidatos


# ==========================================
# LEITOR DE PREÇO DO DOM
# ==========================================

def encontrar_preco_dom(pagina):

    candidatos = []

    # ------------------------------------------
    # MÉTODO 1
    # Elementos que já possuem R$
    # ------------------------------------------

    try:

        elementos = pagina.locator(
            "text=/R\\$\\s*[0-9]/"
        )

        quantidade = elementos.count()

        print(
            "🔍 Elementos com R$: ",
            quantidade
        )

        for i in range(
            min(quantidade, 100)
        ):

            try:

                texto = elementos.nth(i).inner_text(
                    timeout=2000
                )

                encontrados = extrair_precos_texto(
                    texto
                )

                candidatos.extend(
                    encontrados
                )

            except Exception:

                continue

    except Exception as erro:

        print(
            "⚠️ Erro método preço 1:",
            repr(erro)
        )

    # ------------------------------------------
    # MÉTODO 2
    # Texto completo da página
    # ------------------------------------------

    try:

        texto = pagina.locator(
            "body"
        ).inner_text(
            timeout=5000
        )

        encontrados = extrair_precos_texto(
            texto
        )

        candidatos.extend(
            encontrados
        )

    except Exception as erro:

        print(
            "⚠️ Erro método preço 2:",
            repr(erro)
        )

    return candidatos


# ==========================================
# ESCOLHER PREÇO
# ==========================================

def escolher_preco(candidatos):

    if not candidatos:
        return None

    valores = []

    for valor in candidatos:

        try:

            valor = float(
                valor
            )

            if (
                0.01 <= valor <= 100000
            ):
                valores.append(
                    round(valor, 2)
                )

        except Exception:

            continue

    if not valores:
        return None

    valores = list(
        dict.fromkeys(
            valores
        )
    )

    valores.sort()

    print(
        "💰 Candidatos válidos:",
        valores[:30]
    )

    # Normalmente o menor valor
    # exibido é a oferta.
    return valores[0]


# ==========================================
# FORMATAR PREÇO
# ==========================================

def formatar_preco(valor):

    if not valor:
        return None

    try:

        numero = float(
            valor
        )

        return (
            f"R$ {numero:,.2f}"
            .replace(",", "X")
            .replace(".", ",")
            .replace("X", ".")
        )

    except Exception:

        return None


# ==========================================
# LEITOR SHOPEE
# ==========================================

def extrair_dados_shopee(link):

    print("")
    print("======================================")
    print("🔎 ANALISADOR SHOPEE V3")
    print("======================================")
    print("🔗 Link recebido:")
    print(link)
    print("======================================")

    nome = None
    preco = None
    imagem = None

    navegador = None

    html_http = ""
    url_final = link

    # ==========================================
    # RESOLVER LINK CURTO
    # ==========================================

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

        print(
            "🔗 URL final:",
            url_final
        )

        print(
            "📄 HTML HTTP:",
            len(html_http),
            "caracteres"
        )

    except Exception as erro:

        print(
            "⚠️ Falha ao resolver link:",
            repr(erro)
        )

    # ==========================================
    # PRIMEIRA CAMADA
    # HTML HTTP
    # ==========================================

    if html_http:

        # NOME
        nome = extrair_nome_html(
            html_http
        )

        if nome:

            print(
                "🛒 Nome encontrado no HTML:"
            )

            print(
                nome
            )

        # IMAGEM
        imagem = extrair_imagem_html(
            html_http
        )

        if imagem:

            print(
                "🖼️ Imagem encontrada no HTML:"
            )

            print(
                imagem
            )

        # PREÇO EM META TAG
        for propriedade in (
            "product:price:amount",
            "og:price:amount",
            "price"
        ):

            valor = extrair_meta(
                html_http,
                propriedade
            )

            if valor:

                convertido = converter_preco(
                    valor
                )

                if convertido:

                    preco = convertido

                    print(
                        "💰 Preço encontrado na meta:",
                        preco
                    )

                    break

        # JSON-LD
        try:

            jsonld = extrair_jsonld(
                html_http
            )

            (
                nome_json,
                imagem_json,
                preco_json
            ) = procurar_produto_jsonld(
                jsonld
            )

            if not nome:

                nome = limpar_nome(
                    nome_json
                )

                if nome:

                    print(
                        "🛒 Nome encontrado no JSON-LD:",
                        nome
                    )

            if not imagem and imagem_json:

                if isinstance(
                    imagem_json,
                    list
                ):

                    for img in imagem_json:

                        img = normalizar_url(
                            img
                        )

                        if imagem_valida(img):

                            imagem = img
                            break

                else:

                    img = normalizar_url(
                        imagem_json
                    )

                    if imagem_valida(img):
                        imagem = img

                if imagem:

                    print(
                        "🖼️ Imagem encontrada no JSON-LD:",
                        imagem
                    )

            if not preco and preco_json:

                convertido = converter_preco_interno(
                    preco_json
                )

                if convertido:

                    preco = convertido

                    print(
                        "💰 Preço encontrado no JSON-LD:",
                        preco
                    )

        except Exception as erro:

            print(
                "⚠️ Erro JSON-LD:",
                repr(erro)
            )

    # ==========================================
    # PLAYWRIGHT
    # ==========================================

    try:

        from playwright.sync_api import sync_playwright

    except Exception as erro:

        print(
            "❌ Playwright não está disponível:",
            repr(erro)
        )

        return (
            nome,
            formatar_preco(preco),
            imagem
        )

    try:

        with sync_playwright() as p:

            print(
                "🌐 Iniciando Chromium..."
            )

            navegador = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-software-rasterizer",
                    "--disable-blink-features=AutomationControlled"
                ]
            )

            contexto = navegador.new_context(

                viewport={
                    "width": 1366,
                    "height": 900
                },

                user_agent=HEADERS[
                    "User-Agent"
                ],

                locale="pt-BR",

                timezone_id="America/Sao_Paulo",

                extra_http_headers={
                    "Accept-Language":
                        "pt-BR,pt;q=0.9,en-US;q=0.8"
                }
            )

            pagina = contexto.new_page()

            pagina.set_default_timeout(
                12000
            )

            # ======================================
            # REDUZIR RECURSOS DESNECESSÁRIOS
            # ======================================

            try:

                pagina.route(
                    "**/*",
                    lambda rota: (
                        rota.abort()
                        if rota.request.resource_type
                        in ["font", "media"]
                        else rota.continue_()
                    )
                )

            except Exception:
                pass

            # ======================================
            # ESCONDER WEBDRIVER
            # ======================================

            try:

                pagina.add_init_script(
                    """
                    Object.defineProperty(
                        navigator,
                        'webdriver',
                        {
                            get: () => undefined
                        }
                    );

                    Object.defineProperty(
                        navigator,
                        'languages',
                        {
                            get: () => ['pt-BR', 'pt']
                        }
                    );

                    Object.defineProperty(
                        navigator,
                        'platform',
                        {
                            get: () => 'Win32'
                        }
                    );
                    """
                )

            except Exception:
                pass

            # ======================================
            # ABRIR
            # ======================================

            print(
                "📍 Abrindo:",
                url_final
            )

            try:

                pagina.goto(
                    url_final,
                    wait_until="domcontentloaded",
                    timeout=45000
                )

            except Exception as erro:

                print(
                    "⚠️ Goto:",
                    repr(erro)
                )

            # ======================================
            # ESPERAR SHOPEE
            # ======================================

            print(
                "⏳ Aguardando Shopee..."
            )

            pagina.wait_for_timeout(
                8000
            )

            # ======================================
            # SCROLL
            # ======================================

            try:

                pagina.evaluate(
                    """
                    window.scrollTo(
                        0,
                        900
                    );
                    """
                )

                pagina.wait_for_timeout(
                    2500
                )

                pagina.evaluate(
                    """
                    window.scrollTo(
                        0,
                        0
                    );
                    """
                )

                pagina.wait_for_timeout(
                    1500
                )

            except Exception:
                pass

            print(
                "📍 URL atual:",
                pagina.url
            )

            # ======================================
            # HTML RENDERIZADO
            # ======================================

            try:

                html = pagina.content()

            except Exception:

                html = ""

            print(
                "📄 HTML renderizado:",
                len(html),
                "caracteres"
            )

            # ======================================
            # TEXTO VISÍVEL
            # ======================================

            try:

                texto = pagina.locator(
                    "body"
                ).inner_text(
                    timeout=10000
                )

            except Exception:

                texto = ""

            print(
                "📝 Texto visível:",
                len(texto),
                "caracteres"
            )

            # ======================================
            # NOME
            # ======================================

            if not nome:

                nome = extrair_nome_html(
                    html
                )

                if nome:

                    print(
                        "🛒 Nome renderizado:",
                        nome
                    )

            if not nome:

                nome = extrair_nome_dom(
                    pagina
                )

                if nome:

                    print(
                        "🛒 Nome DOM:",
                        nome
                    )

            # ======================================
            # IMAGEM
            # ======================================

            if not imagem:

                imagem = extrair_imagem_html(
                    html
                )

                if imagem:

                    print(
                        "🖼️ Imagem renderizada:"
                    )

                    print(
                        imagem
                    )

            if not imagem:

                imagem = extrair_imagem_dom(
                    pagina
                )

            # ======================================
            # JSON-LD RENDERIZADO
            # ======================================

            try:

                jsonld = extrair_jsonld(
                    html
                )

                (
                    nome_json,
                    imagem_json,
                    preco_json
                ) = procurar_produto_jsonld(
                    jsonld
                )

                if not nome:

                    nome = limpar_nome(
                        nome_json
                    )

                if not imagem and imagem_json:

                    if isinstance(
                        imagem_json,
                        list
                    ):

                        for img in imagem_json:

                            img = normalizar_url(
                                img
                            )

                            if imagem_valida(img):

                                imagem = img
                                break

                    else:

                        img = normalizar_url(
                            imagem_json
                        )

                        if imagem_valida(img):
                            imagem = img

                if not preco and preco_json:

                    convertido = converter_preco_interno(
                        preco_json
                    )

                    if convertido:

                        preco = convertido

                        print(
                            "💰 Preço JSON-LD:",
                            preco
                        )

            except Exception as erro:

                print(
                    "⚠️ JSON-LD renderizado:",
                    repr(erro)
                )

            # ======================================
            # PREÇO VISÍVEL
            # ======================================

            if not preco:

                candidatos = encontrar_preco_dom(
                    pagina
                )

                preco = escolher_preco(
                    candidatos
                )

                if preco:

                    print(
                        "💰 PREÇO VISÍVEL ENCONTRADO:",
                        preco
                    )

            # ======================================
            # PREÇO INTERNO
            # ======================================

            if not preco:

                candidatos = encontrar_precos_internos(
                    html
                )

                preco = escolher_preco(
                    candidatos
                )

                if preco:

                    print(
                        "💰 PREÇO INTERNO ENCONTRADO:",
                        preco
                    )

            # ======================================
            # ÚLTIMA TENTATIVA:
            # TEXTO COMPLETO DO HTML
            # ======================================

            if not preco and html:

                candidatos = extrair_precos_texto(
                    limpar_html(html)
                )

                preco = escolher_preco(
                    candidatos
                )

                if preco:

                    print(
                        "💰 PREÇO ENCONTRADO NO HTML:",
                        preco
                    )

            # ======================================
            # FECHAR
            # ======================================

            try:
                contexto.close()
            except Exception:
                pass

            try:
                navegador.close()
            except Exception:
                pass

            navegador = None

    except Exception as erro:

        print(
            "❌ ERRO NO PLAYWRIGHT:",
            repr(erro)
        )

        try:

            if navegador:
                navegador.close()

        except Exception:
            pass

    # ==========================================
    # NORMALIZAR RESULTADO
    # ==========================================

    nome = limpar_nome(
        nome
    )

    imagem = normalizar_url(
        imagem
    )

    if imagem and not imagem_valida(
        imagem
    ):
        imagem = None

    preco_formatado = formatar_preco(
        preco
    )

    # ==========================================
    # RESULTADO
    # ==========================================

    print("")
    print("======================================")
    print("🔎 RESULTADO FINAL")
    print("======================================")
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


# ==========================================
# PUBLICAR PRODUTO
# ==========================================

def publicar_proximo():

    fila = carregar_fila()

    if not fila:

        print(
            "📭 Fila vazia."
        )

        return False

    produto = fila[0]

    nome = produto.get(
        "nome",
        "Produto"
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

    # ==========================================
    # PUBLICAR COM IMAGEM
    # ==========================================

    if imagem:

        url = (
            f"https://api.telegram.org/"
            f"bot{TOKEN}/sendPhoto"
        )

        try:

            print(
                "🖼️ Baixando imagem..."
            )

            resposta_imagem = requests.get(
                imagem,
                headers={
                    "User-Agent": HEADERS[
                        "User-Agent"
                    ],
                    "Referer":
                        "https://shopee.com.br/"
                },
                timeout=30
            )

            resposta_imagem.raise_for_status()

            arquivo_imagem = (
                resposta_imagem.content
            )

            print(
                "🖼️ Imagem baixada:",
                len(arquivo_imagem),
                "bytes"
            )

            dados = {
                "chat_id": CHAT_ID,
                "caption": legenda,
                "reply_markup": json.dumps(
                    teclado,
                    ensure_ascii=False
                )
            }

            arquivos = {
                "photo": (
                    "produto.jpg",
                    arquivo_imagem,
                    "image/jpeg"
                )
            }

            resposta = requests.post(
                url,
                data=dados,
                files=arquivos,
                timeout=60
            )

            resultado = resposta.json()

        except Exception as erro:

            print(
                "⚠️ Falha baixando/enviando:",
                repr(erro)
            )

            # ======================================
            # TENTATIVA PELA URL
            # ======================================

            try:

                print(
                    "🔄 Tentando enviar pela URL..."
                )

                resultado = requests.post(
                    url,
                    data={
                        "chat_id": CHAT_ID,
                        "photo": imagem,
                        "caption": legenda,
                        "reply_markup": json.dumps(
                            teclado,
                            ensure_ascii=False
                        )
                    },
                    timeout=60
                ).json()

            except Exception as erro2:

                print(
                    "❌ Falha total:",
                    repr(erro2)
                )

                resultado = {
                    "ok": False
                }

    # ==========================================
    # PUBLICAR SEM IMAGEM
    # ==========================================

    else:

        url = (
            f"https://api.telegram.org/"
            f"bot{TOKEN}/sendMessage"
        )

        try:

            resposta = requests.post(
                url,
                data={
                    "chat_id": CHAT_ID,
                    "text": legenda,
                    "reply_markup": json.dumps(
                        teclado,
                        ensure_ascii=False
                    )
                },
                timeout=30
            )

            resultado = resposta.json()

        except Exception as erro:

            print(
                "❌ Erro publicando:",
                repr(erro)
            )

            return False

    # ==========================================
    # RESULTADO TELEGRAM
    # ==========================================

    if resultado.get("ok"):

        fila.pop(0)

        salvar_fila(
            fila
        )

        print("================================")
        print("✅ PRODUTO PUBLICADO")
        print("🛒", nome)
        print("📦 Restam:", len(fila))
        print("================================")

        return True

    print(
        "❌ Telegram recusou:"
    )

    print(
        resultado
    )

    print(
        "⚠️ Produto continua na fila."
    )

    return False


# ==========================================
# PUBLICADOR AUTOMÁTICO
# ==========================================

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
                "❌ Erro no publicador:",
                repr(erro)
            )

        time.sleep(60)


# ==========================================
# MOSTRAR FILA
# ==========================================

def mostrar_fila(chat_id):

    fila = carregar_fila()

    if not fila:

        enviar_mensagem(
            chat_id,
            "📭 A fila está vazia."
        )

        return

    mensagem = (
        "📦 PRODUTOS NA FILA\n\n"
    )

    for numero, produto in enumerate(
        fila,
        start=1
    ):

        mensagem += (
            f"{numero}. 🛒 "
            f"{produto.get('nome', 'Produto')}\n"
            f"🔗 {produto.get('link', '')}\n\n"
        )

    enviar_mensagem(
        chat_id,
        mensagem[:4000]
    )


# ==========================================
# LIMPAR FILA
# ==========================================

def limpar_fila(chat_id):

    salvar_fila([])

    enviar_mensagem(
        chat_id,
        "🗑️ Fila limpa com sucesso!"
    )


# ==========================================
# PROCESSAR MENSAGEM
# ==========================================

def processar_mensagem(mensagem):

    chat_id = mensagem[
        "chat"
    ]["id"]

    # ==========================================
    # ADMIN
    # ==========================================

    if ADMIN_ID:

        usuario_id = str(
            mensagem.get(
                "from",
                {}
            ).get(
                "id"
            )
        )

        if usuario_id != str(
            ADMIN_ID
        ):

            return

    # ==========================================
    # FOTO + LEGENDA
    # ==========================================

    if "photo" in mensagem:

        legenda = mensagem.get(
            "caption",
            ""
        ).strip()

        linhas = [
            linha.strip()
            for linha in legenda.split(
                "\n"
            )
            if linha.strip()
        ]

        if len(linhas) < 2:

            enviar_mensagem(
                chat_id,
                """❌ Formato incorreto.

Envie a FOTO com esta legenda:

Nome do produto
Link da Shopee"""
            )

            return

        nome = linhas[0]
        link = linhas[1]

        if not link.startswith(
            "http"
        ):

            enviar_mensagem(
                chat_id,
                "❌ O link informado não é válido."
            )

            return

        # --------------------------------------
        # LER SHOPEE
        # --------------------------------------

        nome_shopee, preco, imagem_shopee = (
            extrair_dados_shopee(
                link
            )
        )

        if nome_shopee:
            nome = nome_shopee

        # A foto enviada pelo usuário
        # tem prioridade.
        file_id = mensagem[
            "photo"
        ][-1]["file_id"]

        imagem = file_id

        adicionado, posicao = (
            adicionar_na_fila(
                nome,
                link,
                imagem,
                preco
            )
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

🖼️ Foto salva ✅

📦 Posição: {posicao}

⏰ Publicação automática ativa."""
        )

        return

    # ==========================================
    # TEXTO
    # ==========================================

    texto = mensagem.get(
        "text",
        ""
    ).strip()

    # ==========================================
    # START
    # ==========================================

    if texto == "/start":

        enviar_mensagem(
            chat_id,
            """🤖 ACHADOS DA SHOPEE

📸 Para adicionar produto:

Envie uma FOTO com a legenda:

Nome do produto
Link da Shopee

OU envie somente:

🔗 Link da Shopee

O bot tentará encontrar automaticamente:

🛒 Nome
💰 Preço
🖼️ Imagem

Comandos:

/fila
Ver produtos na fila

/limparfila
Limpar toda a fila"""
        )

        return

    # ==========================================
    # FILA
    # ==========================================

    if texto == "/fila":

        mostrar_fila(
            chat_id
        )

        return

    # ==========================================
    # LIMPAR FILA
    # ==========================================

    if texto == "/limparfila":

        limpar_fila(
            chat_id
        )

        return

    # ==========================================
    # SOMENTE LINK
    # ==========================================

    if texto.startswith(
        "http"
    ):

        link = texto.split()[0]

        print(
            "🔎 Link recebido:",
            link
        )

        nome, preco, imagem = (
            extrair_dados_shopee(
                link
            )
        )

        if not nome:

            enviar_mensagem(
                chat_id,
                """⚠️ Não consegui identificar o nome do produto.

Envie uma FOTO com a legenda:

Nome do produto
Link da Shopee"""
            )

            return

        adicionado, posicao = (
            adicionar_na_fila(
                nome,
                link,
                imagem,
                preco
            )
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

    # ==========================================
    # NOME + LINK
    # ==========================================

    linhas = [
        linha.strip()
        for linha in texto.split(
            "\n"
        )
        if linha.strip()
    ]

    if len(linhas) < 2:

        enviar_mensagem(
            chat_id,
            """❌ Formato incorreto.

Você pode enviar:

1️⃣ Somente o link da Shopee

OU

2️⃣

Nome do produto
Link da Shopee"""
        )

        return

    nome = linhas[0]
    link = linhas[1]

    if not link.startswith(
        "http"
    ):

        enviar_mensagem(
            chat_id,
            "❌ Link inválido."
        )

        return

    nome_shopee, preco, imagem = (
        extrair_dados_shopee(
            link
        )
    )

    if nome_shopee:
        nome = nome_shopee

    adicionado, posicao = (
        adicionar_na_fila(
            nome,
            link,
            imagem,
            preco
        )
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


# ==========================================
# RECEBER TELEGRAM
# ==========================================

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

                parametros[
                    "offset"
                ] = ultimo_update + 1

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

                ultimo_update = (
                    update["update_id"]
                )

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
                                update[
                                    "message"
                                ]["chat"]["id"],
                                "⚠️ Erro temporário. Tente enviar novamente."
                            )

                        except Exception:
                            pass

        except Exception as erro:

            print(
                "❌ Erro recebendo Telegram:",
                repr(erro)
            )

            time.sleep(5)


# ==========================================
# SERVIDOR DO RENDER
# ==========================================

class Saude(
    BaseHTTPRequestHandler
):

    def do_GET(self):

        self.send_response(
            200
        )

        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8"
        )

        self.end_headers()

        self.wfile.write(
            b"Achados da Shopee - OK"
        )

    def do_HEAD(self):

        self.send_response(
            200
        )

        self.end_headers()

    def log_message(
        self,
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
        (
            "0.0.0.0",
            porta
        ),
        Saude
    )

    print(
        f"🌐 Servidor Render ativo na porta {porta}"
    )

    servidor.serve_forever()


# ==========================================
# INICIAR SISTEMA
# ==========================================

if __name__ == "__main__":

    # --------------------------------------
    # SERVIDOR
    # --------------------------------------

    threading.Thread(
        target=servidor_saude,
        daemon=True
    ).start()

    # --------------------------------------
    # PUBLICADOR
    # --------------------------------------

    threading.Thread(
        target=publicador_automatico,
        daemon=True
    ).start()

    # --------------------------------------
    # SISTEMA
    # --------------------------------------

    print(
        "======================================"
    )

    print(
        "✅ SISTEMA COMPLETO ATIVO"
    )

    print(
        "⏰ 5 PRODUTOS POR HORA"
    )

    print(
        "🔎 LEITOR SHOPEE V3 ATIVO"
    )

    print(
        "🌐 PLAYWRIGHT + CHROMIUM ATIVO"
    )

    print(
        "💰 LEITOR DE PREÇO MULTICAMADA"
    )

    print(
        "🖼️ LEITOR DE IMAGEM MULTICAMADA"
    )

    print(
        "======================================"
    )

    iniciar_bot()
