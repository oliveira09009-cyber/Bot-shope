import requests
import json
import os
import time
import threading
import re
from html import unescape
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urljoin


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
    "Referer": "https://shopee.com.br/",
    "Connection": "keep-alive",
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

    return resposta.json().get("result")


def carregar_fila():

    dado = redis_cmd("GET", "fila")

    if not dado:
        return []

    try:
        return json.loads(dado)
    except Exception:
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


# ============================================================
# TELEGRAM
# ============================================================

def enviar_mensagem(chat_id, texto):

    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

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

        print("❌ Erro Telegram:", repr(erro))

        return {
            "ok": False
        }


# ============================================================
# LIMPEZA
# ============================================================

def limpar_html(texto):

    if not texto:
        return ""

    texto = unescape(str(texto))

    texto = (
        texto
        .replace("\\/", "/")
        .replace("\\u002F", "/")
        .replace("\\u002f", "/")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )

    texto = re.sub(
        r"<[^>]+>",
        "",
        texto
    )

    texto = re.sub(
        r"\s+",
        " ",
        texto
    )

    return texto.strip()


# ============================================================
# NORMALIZAR URL
# ============================================================

def normalizar_imagem(url):

    if not url:
        return None

    try:

        url = unescape(str(url))

        url = (
            url
            .replace("\\/", "/")
            .replace("\\u002F", "/")
            .replace("\\u002f", "/")
            .replace("\\u003A", ":")
            .replace("\\u003a", ":")
        )

        url = url.strip().strip('"').strip("'")

        if url.startswith("//"):
            url = "https:" + url

        if url.startswith("http://"):
            url = "https://" + url[7:]

        if not url.startswith("http"):
            return None

        url_lower = url.lower()

        # Ignorar ícones/logos
        if any(
            palavra in url_lower
            for palavra in [
                "favicon",
                "avatar",
                "logo",
                "sprite",
                "icon"
            ]
        ):
            return None

        return url

    except Exception:

        return None


# ============================================================
# META TAG
# ============================================================

def extrair_meta(html, propriedade):

    if not html:
        return None

    padroes = [

        rf'<meta[^>]+property=["\']'
        rf'{re.escape(propriedade)}'
        rf'["\'][^>]+content=["\']([^"\']+)["\']',

        rf'<meta[^>]+content=["\']([^"\']+)["\']'
        rf'[^>]+property=["\']'
        rf'{re.escape(propriedade)}'
        rf'["\']',

        rf'<meta[^>]+name=["\']'
        rf'{re.escape(propriedade)}'
        rf'["\'][^>]+content=["\']([^"\']+)["\']',

        rf'<meta[^>]+content=["\']([^"\']+)["\']'
        rf'[^>]+name=["\']'
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

            return limpar_html(
                resultado.group(1)
            )

    return None


# ============================================================
# JSON-LD
# ============================================================

def extrair_jsonld(html):

    resultados = []

    if not html:
        return resultados

    blocos = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>'
        r'(.*?)'
        r'</script>',

        html,

        re.IGNORECASE |
        re.DOTALL
    )

    for bloco in blocos:

        try:

            dados = json.loads(
                unescape(
                    bloco.strip()
                )
            )

            if isinstance(dados, list):

                resultados.extend(
                    dados
                )

            else:

                resultados.append(
                    dados
                )

        except Exception:

            continue

    return resultados


# ============================================================
# ENCONTRAR PRODUTO NO JSON-LD
# ============================================================

def procurar_produto_jsonld(dados):

    nome = None
    imagem = None
    preco = None

    for item in dados:

        if not isinstance(item, dict):
            continue

        tipo = item.get("@type")

        if isinstance(tipo, list):

            eh_produto = (
                "Product" in tipo
            )

        else:

            eh_produto = (
                tipo == "Product"
            )

        if not eh_produto:
            continue

        nome = item.get("name")

        imagem = item.get("image")

        ofertas = item.get("offers")

        if isinstance(ofertas, list):

            if ofertas:
                ofertas = ofertas[0]

        if isinstance(ofertas, dict):

            preco = (
                ofertas.get("price")
                or ofertas.get("lowPrice")
                or ofertas.get("highPrice")
            )

        break

    return nome, imagem, preco


# ============================================================
# CONVERTER PREÇO
# ============================================================

def converter_preco(valor):

    if valor is None:
        return None

    try:

        texto = str(valor).strip()

        texto = (
            texto
            .replace("R$", "")
            .replace("BRL", "")
            .replace(" ", "")
        )

        # Formato brasileiro
        if "," in texto:

            texto = (
                texto
                .replace(".", "")
                .replace(",", ".")
            )

            numero = float(texto)

        else:

            numero = float(texto)

        if numero <= 0:
            return None

        if numero >= 1000000:
            return None

        # Valores Shopee frequentemente vêm
        # em centavos.
        #
        # Ex:
        # 3596 -> R$35,96
        # 1299 -> R$12,99

        if numero >= 1000:

            numero = numero / 100

        if numero <= 0 or numero >= 100000:
            return None

        return round(
            numero,
            2
        )

    except Exception:

        return None


# ============================================================
# PREÇO BRASILEIRO
# ============================================================

def procurar_precos_texto(texto):

    resultados = []

    if not texto:
        return resultados

    padroes = [

        r'R\$\s*'
        r'([0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2})',

        r'R\$\s*'
        r'([0-9]+,[0-9]{2})',

        r'R\$\s*'
        r'([0-9]{1,3}(?:\.[0-9]{3})*\.[0-9]{2})',

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

                resultados.append(
                    numero
                )

    return resultados


# ============================================================
# BUSCAR PREÇO NO TEXTO
# ============================================================

def encontrar_preco_texto(texto):

    candidatos = procurar_precos_texto(
        texto
    )

    if not candidatos:
        return None

    candidatos = [
        x for x in candidatos
        if 0.50 <= x <= 100000
    ]

    if not candidatos:
        return None

    candidatos.sort()

    preco = candidatos[0]

    print(
        "💰 PREÇO ENCONTRADO NO TEXTO:",
        preco
    )

    return preco


# ============================================================
# EXTRAIR NÚMERO DE PREÇO DO JSON
# ============================================================

def extrair_numero_preco(valor):

    if value_is_empty(valor):
        return None

    if isinstance(valor, (int, float)):

        return converter_preco(
            valor
        )

    texto = str(valor).strip()

    # Se veio "R$ 35,96"
    encontrados = procurar_precos_texto(
        texto
    )

    if encontrados:
        return min(encontrados)

    # Número puro
    return converter_preco(
        texto
    )


def value_is_empty(valor):

    return (
        valor is None
        or valor == ""
        or valor == []
        or valor == {}
    )


# ============================================================
# BUSCA RECURSIVA NO JSON
# ============================================================

def procurar_dados_recursivos(obj):

    precos = []
    imagens = []
    nomes = []

    chaves_preco = {
        "price",
        "price_min",
        "price_max",
        "priceMin",
        "priceMax",
        "sale_price",
        "salePrice",
        "current_price",
        "currentPrice",
        "min_price",
        "max_price",
        "discount_price",
        "discountPrice",
        "final_price",
        "finalPrice",
        "amount",
        "value"
    }

    chaves_imagem = {
        "image",
        "images",
        "image_url",
        "imageUrl",
        "image_url_list",
        "imageList",
        "cover",
        "cover_image",
        "coverImage"
    }

    chaves_nome = {
        "name",
        "item_name",
        "itemName",
        "product_name",
        "productName",
        "title"
    }

    def percorrer(valor, caminho=""):

        if isinstance(valor, dict):

            # Verificar contexto de produto
            contexto_produto = any(
                str(k).lower() in {
                    "itemid",
                    "item_id",
                    "shopid",
                    "shop_id",
                    "productid",
                    "product_id"
                }
                for k in valor.keys()
            )

            for chave, conteudo in valor.items():

                chave_str = str(chave)

                chave_lower = chave_str.lower()

                # --------------------------------------------
                # PREÇO
                # --------------------------------------------

                if (
                    chave_str in chaves_preco
                    or chave_lower in {
                        "price",
                        "price_min",
                        "price_max",
                        "sale_price",
                        "saleprice",
                        "current_price",
                        "currentprice",
                        "discount_price",
                        "discountprice",
                        "final_price",
                        "finalprice"
                    }
                ):

                    numero = extrair_numero_preco(
                        conteudo
                    )

                    if numero:

                        # Dar prioridade a preços
                        # em contexto de produto
                        peso = 10 if contexto_produto else 1

                        precos.append(
                            (
                                peso,
                                numero
                            )
                        )

                # --------------------------------------------
                # IMAGEM
                # --------------------------------------------

                if (
                    chave_str in chaves_imagem
                    or chave_lower in {
                        "image",
                        "image_url",
                        "imageurl",
                        "image_url_list",
                        "cover_image",
                        "coverimage"
                    }
                ):

                    if isinstance(
                        conteudo,
                        str
                    ):

                        img = normalizar_imagem(
                            conteudo
                        )

                        if img:
                            imagens.append(img)

                    elif isinstance(
                        conteudo,
                        list
                    ):

                        for item in conteudo:

                            if isinstance(
                                item,
                                str
                            ):

                                img = normalizar_imagem(
                                    item
                                )

                                if img:
                                    imagens.append(img)

                # --------------------------------------------
                # NOME
                # --------------------------------------------

                if (
                    chave_str in chaves_nome
                    or chave_lower in {
                        "name",
                        "item_name",
                        "itemname",
                        "product_name",
                        "productname"
                    }
                ):

                    if isinstance(
                        conteudo,
                        str
                    ):

                        texto_nome = limpar_html(
                            conteudo
                        )

                        if (
                            len(texto_nome) >= 5
                            and "shopee" not in texto_nome.lower()
                        ):

                            nomes.append(
                                texto_nome
                            )

                percorrer(
                    conteudo,
                    caminho + "." + chave_str
                )

        elif isinstance(valor, list):

            for item in valor:

                percorrer(
                    item,
                    caminho
                )

    percorrer(obj)

    return (
        precos,
        imagens,
        nomes
    )


# ============================================================
# ESCOLHER MELHOR IMAGEM
# ============================================================

def escolher_imagem(imagens):

    if not imagens:
        return None

    # Remover duplicadas
    unicas = []

    for img in imagens:

        if img not in unicas:
            unicas.append(img)

    # Dar prioridade à CDN da Shopee
    for img in unicas:

        baixo = img.lower()

        if (
            "susercontent.com" in baixo
            or "shopee" in baixo
        ):

            return img

    return unicas[0]


# ============================================================
# EXTRAIR DADOS DOS JSONS CAPTURADOS
# ============================================================

def analisar_json_respostas(
    respostas_json
):

    todos_precos = []
    todas_imagens = []
    todos_nomes = []

    for dados in respostas_json:

        try:

            precos, imagens, nomes = (
                procurar_dados_recursivos(
                    dados
                )
            )

            todos_precos.extend(
                precos
            )

            todas_imagens.extend(
                imagens
            )

            todos_nomes.extend(
                nomes
            )

        except Exception as erro:

            print(
                "⚠️ Erro analisando JSON:",
                repr(erro)
            )

    # --------------------------------------------------------
    # PREÇO
    # --------------------------------------------------------

    preco = None

    if todos_precos:

        # Priorizar valores em contexto
        # de produto
        todos_precos.sort(
            key=lambda x: (
                -x[0],
                x[1]
            )
        )

        preco = todos_precos[0][1]

    # --------------------------------------------------------
    # IMAGEM
    # --------------------------------------------------------

    imagem = escolher_imagem(
        todas_imagens
    )

    # --------------------------------------------------------
    # NOME
    # --------------------------------------------------------

    nome = None

    if todos_nomes:

        # Remover duplicados
        nomes_unicos = []

        for n in todos_nomes:

            if n not in nomes_unicos:
                nomes_unicos.append(n)

        # Melhor nome costuma ser um
        # dos maiores textos
        nomes_unicos.sort(
            key=len,
            reverse=True
        )

        for n in nomes_unicos:

            if (
                len(n) >= 8
                and len(n) <= 300
            ):

                nome = n
                break

    return (
        nome,
        preco,
        imagem
    )


# ============================================================
# LEITOR SHOPEE
# ============================================================

def extrair_dados_shopee(link):

    print("")
    print("=" * 60)
    print("🔎 ANALISANDO LINK SHOPEE")
    print(link)
    print("=" * 60)

    nome = None
    preco = None
    imagem = None

    respostas_json = []

    navegador = None

    # ========================================================
    # 1. REQUEST NORMAL
    # ========================================================

    html_http = ""
    url_final = link

    try:

        print("🔗 Resolvendo link curto...")

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
            "⚠️ Falha HTTP:",
            repr(erro)
        )

    # ========================================================
    # 2. META TAGS
    # ========================================================

    if html_http:

        nome_meta = extrair_meta(
            html_http,
            "og:title"
        )

        if nome_meta:

            nome_meta = re.sub(
                r"\s+",
                " ",
                nome_meta
            ).strip()

            if (
                len(nome_meta) >= 5
                and nome_meta.lower() not in {
                    "shopee",
                    "shopee brasil"
                }
            ):

                nome = nome_meta

                print(
                    "🛒 Nome META:",
                    nome
                )

        imagem_meta = extrair_meta(
            html_http,
            "og:image"
        )

        imagem_meta = normalizar_imagem(
            imagem_meta
        )

        if imagem_meta:

            imagem = imagem_meta

            print(
                "🖼️ Imagem META encontrada"
            )

        # Preço pelas metas
        for propriedade in [
            "product:price:amount",
            "og:price:amount",
            "product:price",
            "price"
        ]:

            valor = extrair_meta(
                html_http,
                propriedade
            )

            if valor:

                convertido = (
                    converter_preco(
                        valor
                    )
                )

                if convertido:

                    preco = convertido

                    print(
                        "💰 Preço META:",
                        preco
                    )

                    break

    # ========================================================
    # 3. PLAYWRIGHT
    # ========================================================

    try:

        from playwright.sync_api import (
            sync_playwright
        )

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
                    "--disable-blink-features=AutomationControlled"
                ]
            )

            pagina = navegador.new_page(
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

            pagina.set_default_timeout(
                10000
            )

            # =================================================
            # CAPTURAR RESPOSTAS JSON DA SHOPEE
            # =================================================

            def capturar_resposta(
                resposta
            ):

                try:

                    tipo = (
                        resposta.headers
                        .get("content-type", "")
                        .lower()
                    )

                    url = resposta.url.lower()

                    if (
                        "json" not in tipo
                        and not any(
                            x in url
                            for x in [
                                "/api/",
                                "item",
                                "product",
                                "pdp",
                                "search"
                            ]
                        )
                    ):

                        return

                    if (
                        "shopee" not in url
                        and "susercontent" not in url
                    ):

                        return

                    try:

                        dados = resposta.json()

                    except Exception:

                        return

                    if dados:

                        respostas_json.append(
                            dados
                        )

                        print(
                            "📡 JSON Shopee capturado:",
                            len(respostas_json)
                        )

                except Exception:
                    pass

            pagina.on(
                "response",
                capturar_resposta
            )

            # =================================================
            # ANTI-AUTOMAÇÃO BÁSICO
            # =================================================

            try:

                pagina.add_init_script("""
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
                            get: () => ['pt-BR', 'pt', 'en-US']
                        }
                    );

                    Object.defineProperty(
                        navigator,
                        'platform',
                        {
                            get: () => 'Win32'
                        }
                    );
                """)

            except Exception:
                pass

            # =================================================
            # ABRIR LINK ORIGINAL
            # =================================================

            print(
                "📍 Abrindo Shopee..."
            )

            try:

                pagina.goto(
                    link,
                    wait_until="domcontentloaded",
                    timeout=50000
                )

            except Exception as erro:

                print(
                    "⚠️ goto:",
                    repr(erro)
                )

            # Esperar carregamento
            print(
                "⏳ Aguardando dados..."
            )

            pagina.wait_for_timeout(
                8000
            )

            # =================================================
            # ROLAGEM
            # =================================================

            try:

                pagina.evaluate("""
                    window.scrollTo(
                        0,
                        Math.min(
                            document.body.scrollHeight,
                            1800
                        )
                    );
                """)

                pagina.wait_for_timeout(
                    3000
                )

            except Exception:
                pass

            print(
                "📍 Página atual:",
                pagina.url
            )

            # =================================================
            # HTML
            # =================================================

            try:

                html = pagina.content()

            except Exception:

                html = ""

            print(
                "📄 HTML renderizado:",
                len(html),
                "caracteres"
            )

            # =================================================
            # TEXTO VISÍVEL
            # =================================================

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

            # =================================================
            # NOME NO HTML
            # =================================================

            if not nome:

                nome_og = extrair_meta(
                    html,
                    "og:title"
                )

                if nome_og:

                    nome_og = re.sub(
                        r"\s+",
                        " ",
                        nome_og
                    ).strip()

                    if (
                        len(nome_og) >= 5
                        and "shopee brasil"
                        not in nome_og.lower()
                        and nome_og.lower()
                        != "shopee"
                    ):

                        nome = nome_og

                        print(
                            "🛒 Nome HTML:",
                            nome
                        )

            # =================================================
            # NOME H1
            # =================================================

            if not nome:

                try:

                    h1s = pagina.locator(
                        "h1"
                    )

                    quantidade = h1s.count()

                    print(
                        "🔤 H1:",
                        quantidade
                    )

                    for i in range(
                        min(quantidade, 10)
                    ):

                        try:

                            texto_h1 = (
                                h1s
                                .nth(i)
                                .inner_text(
                                    timeout=2000
                                )
                                .strip()
                            )

                            texto_h1 = re.sub(
                                r"\s+",
                                " ",
                                texto_h1
                            )

                            if (
                                len(texto_h1) >= 5
                                and "shopee"
                                not in texto_h1.lower()
                            ):

                                nome = texto_h1

                                print(
                                    "🛒 Nome H1:",
                                    nome
                                )

                                break

                        except Exception:
                            continue

                except Exception:
                    pass

            # =================================================
            # JSON EMBUTIDO NO HTML
            # =================================================

            try:

                # Procurar blocos JSON
                scripts = pagina.locator(
                    "script"
                )

                quantidade = scripts.count()

                print(
                    "📜 Scripts:",
                    quantidade
                )

                for i in range(
                    min(quantidade, 300)
                ):

                    try:

                        conteudo = (
                            scripts
                            .nth(i)
                            .inner_text(
                                timeout=500
                            )
                        )

                        if not conteudo:
                            continue

                        # Só analisar scripts
                        # que parecem conter produto
                        conteudo_lower = (
                            conteudo.lower()
                        )

                        if not any(
                            palavra in conteudo_lower
                            for palavra in [
                                "price",
                                "itemid",
                                "shopid",
                                "image"
                            ]
                        ):

                            continue

                        try:

                            dados = json.loads(
                                conteudo
                            )

                            respostas_json.append(
                                dados
                            )

                        except Exception:

                            # Alguns scripts têm
                            # JSON misturado com texto.
                            # Procuramos preços diretamente.
                            encontrados = (
                                procurar_precos_texto(
                                    conteudo
                                )
                            )

                            for valor in encontrados:

                                preco = (
                                    preco
                                    or valor
                                )

                    except Exception:
                        continue

            except Exception as erro:

                print(
                    "⚠️ Scripts:",
                    repr(erro)
                )

            # =================================================
            # ANALISAR JSON CAPTURADO
            # =================================================

            nome_json, preco_json, imagem_json = (
                analisar_json_respostas(
                    respostas_json
                )
            )

            if not nome and nome_json:

                nome = nome_json

                print(
                    "🛒 Nome JSON:",
                    nome
                )

            if not preco and preco_json:

                preco = preco_json

                print(
                    "💰 PREÇO JSON:",
                    preco
                )

            if not imagem and imagem_json:

                imagem = imagem_json

                print(
                    "🖼️ IMAGEM JSON:",
                    imagem
                )

            # =================================================
            # PREÇO NO TEXTO VISÍVEL
            # =================================================

            if not preco:

                preco_texto = (
                    encontrar_preco_texto(
                        texto
                    )
                )

                if preco_texto:

                    preco = preco_texto

            # =================================================
            # PREÇO DIRETO NO HTML
            # =================================================

            if not preco and html:

                candidatos = []

                padroes = [

                    # preço em JSON
                    r'"price"\s*:\s*"([^"]+)"',

                    r'"price"\s*:\s*([0-9]+(?:\.[0-9]+)?)',

                    r'"price_min"\s*:\s*"([^"]+)"',

                    r'"price_min"\s*:\s*([0-9]+(?:\.[0-9]+)?)',

                    r'"price_max"\s*:\s*"([^"]+)"',

                    r'"salePrice"\s*:\s*"([^"]+)"',

                    r'"sale_price"\s*:\s*"([^"]+)"',

                    r'"currentPrice"\s*:\s*"([^"]+)"',

                    r'"current_price"\s*:\s*"([^"]+)"',

                    r'"finalPrice"\s*:\s*"([^"]+)"',

                    r'"final_price"\s*:\s*"([^"]+)"',

                    r'"discountPrice"\s*:\s*"([^"]+)"',

                    r'"discount_price"\s*:\s*"([^"]+)"'
                ]

                for padrao in padroes:

                    encontrados = re.findall(
                        padrao,
                        html,
                        re.IGNORECASE
                    )

                    for valor in encontrados:

                        convertido = (
                            converter_preco(
                                valor
                            )
                        )

                        if convertido:

                            candidatos.append(
                                convertido
                            )

                if candidatos:

                    # Preços razoáveis
                    candidatos = [
                        x for x in candidatos
                        if 0.50 <= x <= 100000
                    ]

                    if candidatos:

                        candidatos.sort()

                        preco = candidatos[0]

                        print(
                            "💰 PREÇO HTML:",
                            preco
                        )

            # =================================================
            # IMAGEM META NOVAMENTE
            # =================================================

            if not imagem:

                imagem = normalizar_imagem(
                    extrair_meta(
                        html,
                        "og:image"
                    )
                )

                if imagem:

                    print(
                        "🖼️ IMAGEM OG:",
                        imagem
                    )

            # =================================================
            # IMAGENS DO HTML
            # =================================================

            if not imagem and html:

                padroes_imagem = [

                    r'https?://[^"\']*susercontent\.com/file/[^"\'\s]+',

                    r'https?:\\?/\\?/[^"\'\s]*susercontent\.com/file/[^"\'\s]+',

                    r'https?://[^"\']*shopee[^"\'\s]*/file/[^"\'\s]+'
                ]

                candidatas = []

                for padrao in padroes_imagem:

                    encontradas = re.findall(
                        padrao,
                        html,
                        re.IGNORECASE
                    )

                    for img in encontradas:

                        img = normalizar_imagem(
                            img
                        )

                        if (
                            img
                            and img not in candidatas
                        ):

                            candidatas.append(
                                img
                            )

                if candidatas:

                    imagem = (
                        candidatas[0]
                    )

                    print(
                        "🖼️ IMAGEM CDN:",
                        imagem
                    )

            # =================================================
            # IMAGENS DO DOM
            # =================================================

            if not imagem:

                try:

                    imagens = pagina.locator(
                        "img"
                    )

                    quantidade = imagens.count()

                    print(
                        "🖼️ IMG DOM:",
                        quantidade
                    )

                    candidatas = []

                    for i in range(
                        min(quantidade, 150)
                    ):

                        try:

                            elemento = (
                                imagens.nth(i)
                            )

                            src = None

                            for atributo in [
                                "src",
                                "data-src",
                                "data-lazy-src",
                                "data-original",
                                "data-url"
                            ]:

                                valor = (
                                    elemento
                                    .get_attribute(
                                        atributo
                                    )
                                )

                                if valor:

                                    src = valor

                                    break

                            if not src:

                                srcset = (
                                    elemento
                                    .get_attribute(
                                        "srcset"
                                    )
                                )

                                if srcset:

                                    src = (
                                        srcset
                                        .split(",")[0]
                                        .strip()
                                        .split(" ")[0]
                                    )

                            src = normalizar_imagem(
                                src
                            )

                            if not src:
                                continue

                            candidatas.append(
                                src
                            )

                        except Exception:
                            continue

                    # Primeiro CDN da Shopee
                    for src in candidatas:

                        if (
                            "susercontent.com/file/"
                            in src.lower()
                        ):

                            imagem = src

                            break

                    if not imagem:

                        for src in candidatas:

                            if (
                                "shopee"
                                in src.lower()
                            ):

                                imagem = src

                                break

                    if not imagem and candidatas:

                        imagem = candidatas[0]

                    if imagem:

                        print(
                            "🖼️ IMAGEM DOM:",
                            imagem
                        )

                except Exception as erro:

                    print(
                        "⚠️ Erro imagens:",
                        repr(erro)
                    )

            # =================================================
            # ÚLTIMA TENTATIVA DE PREÇO NO DOM
            # =================================================

            if not preco:

                try:

                    elementos = pagina.locator(
                        "span,div,p,strong,b"
                    )

                    quantidade = elementos.count()

                    candidatos = []

                    for i in range(
                        min(quantidade, 2000)
                    ):

                        try:

                            texto_elemento = (
                                elementos
                                .nth(i)
                                .inner_text(
                                    timeout=300
                                )
                                .strip()
                            )

                            if "R$" not in texto_elemento:
                                continue

                            encontrados = (
                                procurar_precos_texto(
                                    texto_elemento
                                )
                            )

                            candidatos.extend(
                                encontrados
                            )

                        except Exception:
                            continue

                    if candidatos:

                        candidatos = [
                            x for x in candidatos
                            if 0.50 <= x <= 100000
                        ]

                        if candidatos:

                            candidatos.sort()

                            preco = (
                                candidatos[0]
                            )

                            print(
                                "💰 PREÇO DOM:",
                                preco
                            )

                except Exception as erro:

                    print(
                        "⚠️ Erro preço DOM:",
                        repr(erro)
                    )

            # =================================================
            # FECHAR
            # =================================================

            try:

                navegador.close()

            except Exception:
                pass

            navegador = None

    except Exception as erro:

        print(
            "❌ ERRO PLAYWRIGHT:",
            repr(erro)
        )

        try:

            if navegador:
                navegador.close()

        except Exception:
            pass

    # ========================================================
    # FORMATAÇÃO FINAL
    # ========================================================

    if nome:

        nome = limpar_html(
            nome
        )

        # Remover lixo comum
        nome = re.sub(
            r"\s+",
            " ",
            nome
        ).strip()

    if preco:

        try:

            preco = (
                f"R$ {float(preco):,.2f}"
                .replace(",", "X")
                .replace(".", ",")
                .replace("X", ".")
            )

        except Exception:

            pass

    imagem = normalizar_imagem(
        imagem
    )

    # ========================================================
    # RESULTADO
    # ========================================================

    print("")
    print("=" * 60)
    print("🔎 RESULTADO FINAL SHOPEE")
    print(
        "🛒 Nome:",
        nome or "NÃO ENCONTRADO"
    )
    print(
        "💰 Preço:",
        preco or "NÃO ENCONTRADO"
    )
    print(
        "🖼️ Imagem:",
        imagem or "NÃO ENCONTRADA"
    )
    print("=" * 60)
    print("")

    return (
        nome,
        preco,
        imagem
    )


# ============================================================
# PUBLICAR PRODUTO
# ============================================================

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

    # ========================================================
    # FOTO
    # ========================================================

    if imagem:

        # Foto enviada pelo usuário
        # pode ser file_id do Telegram.
        if (
            isinstance(imagem, str)
            and imagem.startswith(
                "AgAC"
            )
        ):

            url = (
                f"https://api.telegram.org/"
                f"bot{TOKEN}/sendPhoto"
            )

            try:

                resposta = requests.post(
                    url,
                    data={
                        "chat_id": CHAT_ID,
                        "photo": imagem,
                        "caption": legenda,
                        "reply_markup": json.dumps(
                            teclado
                        )
                    },
                    timeout=60
                )

                resultado = resposta.json()

            except Exception as erro:

                print(
                    "❌ Erro foto Telegram:",
                    repr(erro)
                )

                resultado = {
                    "ok": False
                }

        else:

            # =================================================
            # BAIXAR IMAGEM DA SHOPEE
            # =================================================

            try:

                print(
                    "🖼️ Baixando imagem..."
                )

                resposta_imagem = requests.get(
                    imagem,
                    headers={
                        **HEADERS,
                        "Referer":
                            "https://shopee.com.br/"
                    },
                    timeout=30
                )

                resposta_imagem.raise_for_status()

                arquivo = (
                    resposta_imagem.content
                )

                print(
                    "🖼️ Imagem:",
                    len(arquivo),
                    "bytes"
                )

                url = (
                    f"https://api.telegram.org/"
                    f"bot{TOKEN}/sendPhoto"
                )

                resposta = requests.post(
                    url,
                    data={
                        "chat_id": CHAT_ID,
                        "caption": legenda,
                        "reply_markup": json.dumps(
                            teclado
                        )
                    },
                    files={
                        "photo": (
                            "produto.jpg",
                            arquivo,
                            "image/jpeg"
                        )
                    },
                    timeout=60
                )

                resultado = resposta.json()

            except Exception as erro:

                print(
                    "⚠️ Falha baixando imagem:",
                    repr(erro)
                )

                # Tentar mandar URL diretamente
                try:

                    resposta = requests.post(
                        url,
                        data={
                            "chat_id": CHAT_ID,
                            "photo": imagem,
                            "caption": legenda,
                            "reply_markup": json.dumps(
                                teclado
                            )
                        },
                        timeout=60
                    )

                    resultado = resposta.json()

                except Exception as erro2:

                    print(
                        "❌ Falha total:",
                        repr(erro2)
                    )

                    resultado = {
                        "ok": False
                    }

    # ========================================================
    # SEM IMAGEM
    # ========================================================

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
                        teclado
                    )
                },
                timeout=30
            )

            resultado = resposta.json()

        except Exception as erro:

            print(
                "❌ Erro publicação:",
                repr(erro)
            )

            return False

    # ========================================================
    # RESULTADO
    # ========================================================

    if resultado.get("ok"):

        fila.pop(0)

        salvar_fila(
            fila
        )

        print("")
        print("=" * 40)
        print("✅ PRODUTO PUBLICADO")
        print("🛒", nome)
        print(
            "📦 Restam:",
            len(fila)
        )
        print("=" * 40)

        return True

    print(
        "❌ Telegram recusou:",
        resultado
    )

    print(
        "⚠️ Produto continua na fila."
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

            if (
                time.time() - ultimo
                >= INTERVALO
            ):

                if publicar_proximo():

                    redis_cmd(
                        "SET",
                        "ultimo_post",
                        str(time.time())
                    )

        except Exception as erro:

            print(
                "❌ Erro publicador:",
                repr(erro)
            )

        time.sleep(60)


# ============================================================
# MOSTRAR FILA
# ============================================================

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
            f"💰 "
            f"{produto.get('preco') or 'não encontrado'}\n\n"
        )

    enviar_mensagem(
        chat_id,
        mensagem[:4000]
    )


# ============================================================
# LIMPAR FILA
# ============================================================

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

    # ========================================================
    # ADMIN
    # ========================================================

    if ADMIN_ID:

        usuario = str(
            mensagem.get(
                "from",
                {}
            ).get(
                "id"
            )
        )

        if usuario != str(ADMIN_ID):

            return

    # ========================================================
    # FOTO + LEGENDA
    # ========================================================

    if "photo" in mensagem:

        legenda = (
            mensagem.get(
                "caption",
                ""
            ).strip()
        )

        linhas = [
            l.strip()
            for l in legenda.split("\n")
            if l.strip()
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

        print(
            "📸 Foto + link recebidos"
        )

        nome_shopee, preco, imagem_shopee = (
            extrair_dados_shopee(
                link
            )
        )

        if nome_shopee:

            nome = nome_shopee

        # ====================================================
        # FOTO ENVIADA PELO USUÁRIO
        # SEMPRE TEM PRIORIDADE
        # ====================================================

        file_id = (
            mensagem["photo"][-1]
            ["file_id"]
        )

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

🖼️ Foto: salva ✅

📦 Posição: {posicao}

⏰ Publicação automática ativa."""
        )

        return

    # ========================================================
    # TEXTO
    # ========================================================

    texto = (
        mensagem.get(
            "text",
            ""
        ).strip()
    )

    # ========================================================
    # START
    # ========================================================

    if texto == "/start":

        enviar_mensagem(
            chat_id,
            """🤖 ACHADOS DA SHOPEE

📸 ADICIONAR PRODUTO

Envie uma FOTO com:

Nome do produto
Link da Shopee

OU envie somente o link.

🔎 O sistema tentará encontrar automaticamente:

🛒 Nome
💰 Preço
🖼️ Imagem

📦 Comandos:

/fila
Ver produtos aguardando publicação.

/limparfila
Limpar a fila."""
        )

        return

    # ========================================================
    # FILA
    # ========================================================

    if texto == "/fila":

        mostrar_fila(
            chat_id
        )

        return

    # ========================================================
    # LIMPAR
    # ========================================================

    if texto == "/limparfila":

        limpar_fila(
            chat_id
        )

        return

    # ========================================================
    # SOMENTE LINK
    # ========================================================

    if texto.startswith(
        "http"
    ):

        link = (
            texto.split()[0]
        )

        print(
            "🔎 LINK RECEBIDO:",
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
                """⚠️ A Shopee não entregou o nome do produto.

Envie uma FOTO do produto com a legenda:

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

    # ========================================================
    # NOME + LINK
    # ========================================================

    linhas = [
        l.strip()
        for l in texto.split("\n")
        if l.strip()
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


# ============================================================
# TELEGRAM GET UPDATES
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
                                update["message"]
                                ["chat"]["id"],

                                "⚠️ Erro temporário. "
                                "Tente enviar novamente."
                            )

                        except Exception:
                            pass

        except Exception as erro:

            print(
                "❌ Erro recebimento:",
                repr(erro)
            )

            time.sleep(5)


# ============================================================
# SERVIDOR RENDER
# ============================================================

class Saude(
    BaseHTTPRequestHandler
):

    def do_GET(self):

        self.send_response(
            200
        )

        self.send_header(
            "Content-Type",
            "text/plain"
        )

        self.end_headers()

        self.wfile.write(
            b"ok"
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
        "🌐 Servidor Render ativo na porta",
        porta
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
    print("=" * 60)
    print("✅ SISTEMA COMPLETO ATIVO")
    print("⏰ 5 PRODUTOS POR HORA")
    print("🔎 LEITOR SHOPEE V4 ATIVO")
    print("🌐 PLAYWRIGHT + CHROMIUM ATIVO")
    print("💰 LEITOR DE PREÇO MULTICAMADA")
    print("🖼️ LEITOR DE IMAGEM MULTICAMADA")
    print("📡 CAPTURA DE JSON DA SHOPEE ATIVA")
    print("🚫 SEM API ID")
    print("=" * 60)
    print("")

    iniciar_bot()
