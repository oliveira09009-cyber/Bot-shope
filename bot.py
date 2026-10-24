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

# ============================================================
# 5 PRODUTOS POR HORA
# 60 minutos / 5 = 12 minutos
# ============================================================

INTERVALO = 12 * 60

# ============================================================
# TEMPO DA TRAVA DE PUBLICAÇÃO
# Impede duas instâncias do Render publicarem juntas
# ============================================================

LOCK_TTL = 90


# ============================================================
# HEADERS SHOPEE
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
        "*/*;q=0.8"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8",
    "Referer": "https://shopee.com.br/",
    "Cache-Control": "no-cache",
}


# ============================================================
# SESSÃO HTTP
# ============================================================

def nova_sessao():

    sessao = requests.Session()
    sessao.headers.update(HEADERS)

    return sessao


# ============================================================
# REDIS
# ============================================================

def redis_cmd(*args, timeout=10):

    resposta = requests.post(
        REDIS_URL,
        headers={
            "Authorization": f"Bearer {REDIS_TOKEN}",
            "Content-Type": "application/json"
        },
        json=list(args),
        timeout=timeout
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
    preco=None,
    preco_antigo=None,
    desconto=None
):

    fila = carregar_fila()

    for produto in fila:

        if produto.get("link") == link:

            return False, len(fila)

    produto = {
        "nome": nome or "Produto Shopee",
        "link": link,
        "imagem": imagem,
        "preco": preco,
        "preco_antigo": preco_antigo,
        "desconto": desconto,
        "criado_em": int(time.time())
    }

    fila.append(produto)

    salvar_fila(fila)

    return True, len(fila)


# ============================================================
# TELEGRAM
# ============================================================

def telegram_post(
    endpoint,
    data=None,
    files=None,
    timeout=20
):

    try:

        resposta = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/{endpoint}",
            data=data,
            files=files,
            timeout=timeout
        )

        try:

            resultado = resposta.json()

        except Exception:

            resultado = {
                "ok": False,
                "error": resposta.text
            }

        if not resultado.get("ok"):

            print(
                f"❌ Telegram {endpoint}:",
                resultado
            )

        return resultado

    except Exception as erro:

        print(
            "❌ Erro conexão Telegram:",
            repr(erro)
        )

        return {
            "ok": False,
            "error": str(erro)
        }


def enviar_mensagem(chat_id, texto):

    return telegram_post(
        "sendMessage",
        data={
            "chat_id": chat_id,
            "text": texto
        },
        timeout=15
    )


# ============================================================
# TESTAR TOKEN
# ============================================================

def testar_token():

    resultado = telegram_post(
        "getMe",
        timeout=10
    )

    if resultado.get("ok"):

        bot = resultado.get(
            "result",
            {}
        )

        print("")
        print("🤖 TOKEN TELEGRAM OK")
        print(
            "Bot:",
            bot.get("first_name")
        )
        print(
            "Username:",
            "@" + str(
                bot.get("username", "")
            )
        )
        print("")

        return True

    print("")
    print("❌ TOKEN TELEGRAM INVÁLIDO")
    print(
        "Resposta:",
        resultado
    )
    print("")

    return False


# ============================================================
# LIMPEZA
# ============================================================

def limpar_texto(valor):

    if valor is None:
        return None

    valor = html_lib.unescape(
        str(valor)
    )

    # Decodifica escapes comuns
    substituicoes = {
        "\\/": "/",
        "\\u002F": "/",
        "\\u003A": ":",
        "\\u0026": "&",
        "\\u003F": "?",
        "\\u003D": "=",
        "\\u0025": "%",
    }

    for antigo, novo in substituicoes.items():

        valor = valor.replace(
            antigo,
            novo
        )

    valor = re.sub(
        r"<[^>]+>",
        " ",
        valor
    )

    valor = re.sub(
        r"\s+",
        " ",
        valor
    )

    return valor.strip()


def normalizar_url(url):

    if not url:
        return None

    url = limpar_texto(url)

    if not url:
        return None

    url = url.strip(
        "\"' ,;"
    )

    if url.startswith("//"):

        url = "https:" + url

    elif url.startswith("/"):

        url = urljoin(
            "https://shopee.com.br/",
            url
        )

    if not url.startswith(
        ("http://", "https://")
    ):
        return None

    return url


# ============================================================
# META TAG
# ============================================================

def extrair_meta(html, nome):

    if not html:
        return None

    padroes = [

        rf'<meta[^>]+property=["\']'
        rf'{re.escape(nome)}'
        rf'["\'][^>]+content=["\']'
        rf'([^"\']+)["\']',

        rf'<meta[^>]+content=["\']'
        rf'([^"\']+)["\'][^>]+property=["\']'
        rf'{re.escape(nome)}'
        rf'["\']',

        rf'<meta[^>]+name=["\']'
        rf'{re.escape(nome)}'
        rf'["\'][^>]+content=["\']'
        rf'([^"\']+)["\']',

        rf'<meta[^>]+content=["\']'
        rf'([^"\']+)["\'][^>]+name=["\']'
        rf'{re.escape(nome)}'
        rf'["\']'
    ]

    for padrao in padroes:

        encontrado = re.search(
            padrao,
            html,
            re.IGNORECASE
        )

        if encontrado:

            return limpar_texto(
                encontrado.group(1)
            )

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
                html_lib.unescape(
                    bloco.strip()
                )
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


def procurar_jsonld_produto(dados):

    nome = None
    imagem = None
    preco = None
    preco_antigo = None

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

            eh_produto = (
                str(tipo).lower() == "product"
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
            )

        break

    if isinstance(imagem, list):

        imagem = (
            imagem[0]
            if imagem
            else None
        )

    return (
        limpar_texto(nome),
        normalizar_url(imagem),
        converter_preco(preco),
        converter_preco(preco_antigo)
    )


# ============================================================
# CONVERTER PREÇO
# ============================================================

def converter_preco(valor):

    if valor is None:
        return None

    try:

        texto = str(valor).strip()

        if not texto:
            return None

        texto = (
            texto
            .replace("R$", "")
            .replace("BRL", "")
            .replace("brl", "")
            .replace(" ", "")
        )

        # R$ 78,56
        if "," in texto:

            texto = texto.replace(
                ".",
                ""
            )

            texto = texto.replace(
                ",",
                "."
            )

            numero = float(texto)

        else:

            numero = float(texto)

        if numero <= 0:
            return None

        # Valores internos comuns da Shopee
        if numero >= 1000000:

            numero /= 100000

        elif numero >= 10000:

            numero /= 100

        elif numero >= 1000 and numero.is_integer():

            numero /= 100

        if numero <= 0 or numero > 500000:

            return None

        return round(
            numero,
            2
        )

    except Exception:

        return None


# ============================================================
# FORMATAR PREÇO
# ============================================================

def formatar_preco(valor):

    numero = converter_preco(
        valor
    )

    if numero is None:
        return None

    return (
        f"R$ {numero:,.2f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )


# ============================================================
# ENCONTRAR PREÇOS
# ============================================================

def extrair_precos_html(html):

    if not html:
        return None, None

    atuais = []
    antigos = []

    # --------------------------------------------------------
    # PREÇO ATUAL
    # --------------------------------------------------------

    padroes_atual = [

        r'"price"\s*:\s*"([^"]+)"',
        r'"price"\s*:\s*([0-9]+(?:\.[0-9]+)?)',

        r'"price_min"\s*:\s*"([^"]+)"',
        r'"price_min"\s*:\s*([0-9]+(?:\.[0-9]+)?)',

        r'"priceMin"\s*:\s*"([^"]+)"',
        r'"priceMin"\s*:\s*([0-9]+(?:\.[0-9]+)?)',

        r'"salePrice"\s*:\s*"([^"]+)"',
        r'"salePrice"\s*:\s*([0-9]+(?:\.[0-9]+)?)',

        r'"sale_price"\s*:\s*"([^"]+)"',
        r'"sale_price"\s*:\s*([0-9]+(?:\.[0-9]+)?)',

        r'"currentPrice"\s*:\s*"([^"]+)"',
        r'"currentPrice"\s*:\s*([0-9]+(?:\.[0-9]+)?)',

        r'"current_price"\s*:\s*"([^"]+)"',
        r'"current_price"\s*:\s*([0-9]+(?:\.[0-9]+)?)',

        r'"final_price"\s*:\s*"([^"]+)"',
        r'"final_price"\s*:\s*([0-9]+(?:\.[0-9]+)?)',

        r'"display_price"\s*:\s*"([^"]+)"',
        r'"display_price"\s*:\s*([0-9]+(?:\.[0-9]+)?)'
    ]

    for padrao in padroes_atual:

        encontrados = re.findall(
            padrao,
            html,
            re.IGNORECASE
        )

        for valor in encontrados:

            numero = converter_preco(
                valor
            )

            if (
                numero is not None
                and 0.01 <= numero <= 500000
            ):

                atuais.append(
                    numero
                )

    # --------------------------------------------------------
    # PREÇO ANTIGO
    # --------------------------------------------------------

    padroes_antigo = [

        r'"price_before_discount"\s*:\s*"([^"]+)"',
        r'"price_before_discount"\s*:\s*([0-9]+(?:\.[0-9]+)?)',

        r'"priceBeforeDiscount"\s*:\s*"([^"]+)"',
        r'"priceBeforeDiscount"\s*:\s*([0-9]+(?:\.[0-9]+)?)',

        r'"originalPrice"\s*:\s*"([^"]+)"',
        r'"originalPrice"\s*:\s*([0-9]+(?:\.[0-9]+)?)',

        r'"original_price"\s*:\s*"([^"]+)"',
        r'"original_price"\s*:\s*([0-9]+(?:\.[0-9]+)?)',

        r'"marketPrice"\s*:\s*"([^"]+)"',
        r'"marketPrice"\s*:\s*([0-9]+(?:\.[0-9]+)?)',

        r'"market_price"\s*:\s*"([^"]+)"',
        r'"market_price"\s*:\s*([0-9]+(?:\.[0-9]+)?)',

        r'"listPrice"\s*:\s*"([^"]+)"',
        r'"listPrice"\s*:\s*([0-9]+(?:\.[0-9]+)?)',

        r'"list_price"\s*:\s*"([^"]+)"',
        r'"list_price"\s*:\s*([0-9]+(?:\.[0-9]+)?)'
    ]

    for padrao in padroes_antigo:

        encontrados = re.findall(
            padrao,
            html,
            re.IGNORECASE
        )

        for valor in encontrados:

            numero = converter_preco(
                valor
            )

            if (
                numero is not None
                and 0.01 <= numero <= 500000
            ):

                antigos.append(
                    numero
                )

    atuais = sorted(
        set(atuais)
    )

    antigos = sorted(
        set(antigos)
    )

    preco_atual = (
        atuais[0]
        if atuais
        else None
    )

    preco_antigo = None

    if preco_atual:

        maiores = [
            x
            for x in antigos
            if x > preco_atual
        ]

        if maiores:

            # Pega o preço anterior mais próximo
            preco_antigo = min(
                maiores,
                key=lambda x: x - preco_atual
            )

    return (
        preco_atual,
        preco_antigo
    )


# ============================================================
# DESCONTO
# ============================================================

def calcular_desconto(
    preco,
    preco_antigo
):

    preco = converter_preco(
        preco
    )

    preco_antigo = converter_preco(
        preco_antigo
    )

    if not preco or not preco_antigo:
        return None

    if preco_antigo <= preco:
        return None

    desconto = (
        (preco_antigo - preco)
        / preco_antigo
    ) * 100

    desconto = round(
        desconto
    )

    if desconto <= 0 or desconto >= 100:
        return None

    return desconto


# ============================================================
# IMAGENS
# ============================================================

def encontrar_imagens_html(html):

    if not html:
        return []

    candidatos = []

    def adicionar(url):

        if not url:
            return

        url = normalizar_url(
            url
        )

        if not url:
            return

        baixo = url.lower()

        ignorar = (
            "logo",
            "favicon",
            "avatar",
            "icon",
            "sprite",
            "placeholder",
            "loading",
            "default_image"
        )

        if any(
            palavra in baixo
            for palavra in ignorar
        ):
            return

        if url not in candidatos:

            candidatos.append(
                url
            )

    # --------------------------------------------------------
    # META
    # --------------------------------------------------------

    adicionar(
        extrair_meta(
            html,
            "og:image"
        )
    )

    adicionar(
        extrair_meta(
            html,
            "og:image:secure_url"
        )
    )

    adicionar(
        extrair_meta(
            html,
            "twitter:image"
        )
    )

    adicionar(
        extrair_meta(
            html,
            "twitter:image:src"
        )
    )

    # --------------------------------------------------------
    # PADRÕES SHOPEE
    # --------------------------------------------------------

    padroes = [

        r'https?://[^"\'<>\s]+susercontent\.com/file/[^"\'<>\s]+',

        r'https?:\\?/\\?/[^"\'<>\s]+susercontent\.com/file/[^"\'<>\s]+',

        r'https?://[^"\'<>\s]*img\.susercontent\.com/[^"\'<>\s]+',

        r'https?:\\?/\\?/[^"\'<>\s]*img\.susercontent\.com/[^"\'<>\s]+',

        r'"image"\s*:\s*"([^"]+)"',

        r'"imageUrl"\s*:\s*"([^"]+)"',

        r'"image_url"\s*:\s*"([^"]+)"',

        r'"coverImage"\s*:\s*"([^"]+)"',

        r'"cover_image"\s*:\s*"([^"]+)"',

        r'"mainImage"\s*:\s*"([^"]+)"',

        r'"main_image"\s*:\s*"([^"]+)"',

        r'"originalImage"\s*:\s*"([^"]+)"',

        r'"original_image"\s*:\s*"([^"]+)"',

        r'"imageFullUrl"\s*:\s*"([^"]+)"',

        r'"image_full_url"\s*:\s*"([^"]+)"'
    ]

    for padrao in padroes:

        encontrados = re.findall(
            padrao,
            html,
            re.IGNORECASE
        )

        for encontrado in encontrados:

            adicionar(
                encontrado
            )

            if len(candidatos) >= 50:
                break

        if len(candidatos) >= 50:
            break

    # --------------------------------------------------------
    # REMOVER DUPLICADAS
    # --------------------------------------------------------

    unicos = []
    vistos = set()

    for url in candidatos:

        chave = url.lower()

        if chave in vistos:
            continue

        vistos.add(chave)

        unicos.append(
            url
        )

    return unicos


def encontrar_imagem_html(html):

    imagens = encontrar_imagens_html(
        html
    )

    # Primeiro: imagem oficial da Shopee
    for url in imagens:

        if "susercontent.com/file/" in url.lower():

            return url

    # Segundo: qualquer CDN da Shopee
    for url in imagens:

        if "susercontent.com" in url.lower():

            return url

    return (
        imagens[0]
        if imagens
        else None
    )


# ============================================================
# NOME
# ============================================================

def encontrar_nome_html(html):

    if not html:
        return None

    # OG TITLE
    nome = extrair_meta(
        html,
        "og:title"
    )

    if nome:

        nome = limpar_texto(
            nome
        )

        if (
            len(nome) >= 5
            and nome.lower()
            not in (
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

        nome = limpar_texto(
            titulo.group(1)
        )

        if (
            nome
            and len(nome) >= 5
            and "shopee" not in nome.lower()
        ):

            return nome

    return None


# ============================================================
# LEITOR SHOPEE
# ============================================================

def extrair_dados_shopee(link):

    print("")
    print(
        "🔎 ======================================"
    )
    print(
        "🔎 LENDO SHOPEE"
    )
    print(
        "🔗",
        link
    )
    print(
        "🔎 ======================================"
    )

    nome = None
    preco = None
    preco_antigo = None
    desconto = None
    imagem = None

    try:

        sessao = nova_sessao()

        resposta = sessao.get(
            link,
            timeout=25,
            allow_redirects=True
        )

        print(
            "🌐 HTTP:",
            resposta.status_code
        )

        print(
            "🔗 URL final:",
            resposta.url
        )

        if resposta.status_code != 200:

            print(
                "❌ Shopee não retornou HTTP 200"
            )

            return (
                None,
                None,
                None,
                None,
                None
            )

        html = resposta.text

        print(
            "📄 HTML:",
            len(html),
            "caracteres"
        )

        # ----------------------------------------------------
        # NOME
        # ----------------------------------------------------

        nome = encontrar_nome_html(
            html
        )

        # ----------------------------------------------------
        # IMAGEM
        # ----------------------------------------------------

        imagem = encontrar_imagem_html(
            html
        )

        # ----------------------------------------------------
        # JSON-LD
        # ----------------------------------------------------

        try:

            dados = extrair_jsonld(
                html
            )

            (
                nome_json,
                imagem_json,
                preco_json,
                antigo_json
            ) = procurar_jsonld_produto(
                dados
            )

            if not nome and nome_json:

                nome = nome_json

            if not imagem and imagem_json:

                imagem = imagem_json

            if preco_json:

                preco = preco_json

            if antigo_json:

                preco_antigo = antigo_json

        except Exception as erro:

            print(
                "⚠️ Erro JSON-LD:",
                repr(erro)
            )

        # ----------------------------------------------------
        # PREÇOS DO HTML
        # ----------------------------------------------------

        preco_html, antigo_html = (
            extrair_precos_html(
                html
            )
        )

        if preco is None:

            preco = preco_html

        if preco_antigo is None:

            preco_antigo = antigo_html

        # ----------------------------------------------------
        # DESCONTO
        # ----------------------------------------------------

        desconto = calcular_desconto(
            preco,
            preco_antigo
        )

        # ----------------------------------------------------
        # LIMPEZA DO NOME
        # ----------------------------------------------------

        if nome:

            nome = limpar_texto(
                nome
            )

            nome = re.sub(
                r'\s*[-|]\s*Shopee.*$',
                '',
                nome,
                flags=re.IGNORECASE
            ).strip()

        # ----------------------------------------------------
        # NORMALIZAR IMAGEM
        # ----------------------------------------------------

        if imagem:

            imagem = normalizar_url(
                imagem
            )

        # ----------------------------------------------------
        # RESULTADO
        # ----------------------------------------------------

        print("")
        print(
            "🛒 Nome:",
            nome or "NÃO ENCONTRADO"
        )

        print(
            "💰 Preço:",
            formatar_preco(preco)
            or "NÃO ENCONTRADO"
        )

        print(
            "🏷️ Preço antigo:",
            formatar_preco(preco_antigo)
            or "NÃO ENCONTRADO"
        )

        print(
            "🔥 Desconto:",
            f"{desconto}%"
            if desconto
            else "NÃO ENCONTRADO"
        )

        print(
            "🖼️ Imagem:",
            imagem
            or "NÃO ENCONTRADA"
        )

        print("")

        return (
            nome,
            formatar_preco(preco),
            formatar_preco(preco_antigo),
            desconto,
            imagem
        )

    except Exception as erro:

        print(
            "❌ Erro leitor Shopee:",
            repr(erro)
        )

        return (
            nome,
            formatar_preco(preco),
            formatar_preco(preco_antigo),
            desconto,
            imagem
        )


# ============================================================
# BAIXAR IMAGEM
# ============================================================

def baixar_imagem(url):

    if not url:
        return None

    try:

        print(
            "⬇️ Baixando imagem..."
        )

        sessao = nova_sessao()

        resposta = sessao.get(
            url,
            timeout=25,
            allow_redirects=True
        )

        print(
            "🌐 Imagem HTTP:",
            resposta.status_code
        )

        resposta.raise_for_status()

        conteudo = resposta.content

        if not conteudo:

            print(
                "❌ Imagem vazia."
            )

            return None

        content_type = (
            resposta.headers
            .get(
                "Content-Type",
                ""
            )
            .lower()
        )

        print(
            "📦 Bytes:",
            len(conteudo)
        )

        print(
            "📄 Content-Type:",
            content_type
        )

        assinaturas = (
            b"\xff\xd8\xff",
            b"\x89PNG",
            b"RIFF",
            b"GIF8"
        )

        parece_imagem = (
            content_type.startswith(
                "image/"
            )
            or any(
                conteudo.startswith(x)
                for x in assinaturas
            )
        )

        if not parece_imagem:

            print(
                "❌ O servidor não retornou uma imagem."
            )

            print(
                "📄 Início da resposta:",
                conteudo[:100]
            )

            return None

        return conteudo

    except Exception as erro:

        print(
            "❌ Erro baixando imagem:",
            repr(erro)
        )

        return None


# ============================================================
# LEGENDA
# ============================================================

def criar_legenda(produto):

    nome = (
        produto.get("nome")
        or "Produto Shopee"
    )

    preco = produto.get(
        "preco"
    )

    preco_antigo = produto.get(
        "preco_antigo"
    )

    desconto = produto.get(
        "desconto"
    )

    linhas = [
        "🔥 ACHADINHO DA SHOPEE 🔥",
        "",
        f"🛍️ {nome}",
        ""
    ]

    # --------------------------------------------------------
    # PREÇOS
    # --------------------------------------------------------

    if preco_antigo and preco:

        linhas.append(
            f"🏷️ De: {preco_antigo}"
        )

        linhas.append(
            f"💰 Por: {preco}"
        )

    elif preco:

        linhas.append(
            f"💰 Por: {preco}"
        )

    else:

        linhas.append(
            "💰 Confira o preço na Shopee"
        )

    # --------------------------------------------------------
    # DESCONTO
    # --------------------------------------------------------

    if desconto:

        linhas.append(
            f"🔥 {desconto}% OFF"
        )

    linhas.extend([
        "",
        "✅ Vale a pena conferir!",
        "",
        "👇 Confira aqui:"
    ])

    return "\n".join(
        linhas
    )


# ============================================================
# PUBLICAR FOTO
# ============================================================

def enviar_foto_produto(
    imagem,
    legenda,
    teclado
):

    # --------------------------------------------------------
    # 1. FILE_ID TELEGRAM
    # --------------------------------------------------------

    if (
        imagem
        and not imagem.startswith(
            (
                "http://",
                "https://"
            )
        )
    ):

        print(
            "📸 Tentando FILE_ID do Telegram..."
        )

        resultado = telegram_post(
            "sendPhoto",
            data={
                "chat_id": CHAT_ID,
                "photo": imagem,
                "caption": legenda,
                "reply_markup": json.dumps(
                    teclado,
                    ensure_ascii=False
                )
            },
            timeout=20
        )

        if resultado.get("ok"):

            print(
                "✅ FOTO ENVIADA PELO FILE_ID!"
            )

            return resultado

        print(
            "⚠️ FILE_ID falhou."
        )

    # --------------------------------------------------------
    # 2. URL DIRETA
    # --------------------------------------------------------

    if imagem:

        print(
            "🌐 Tentando URL direta..."
        )

        resultado = telegram_post(
            "sendPhoto",
            data={
                "chat_id": CHAT_ID,
                "photo": imagem,
                "caption": legenda,
                "reply_markup": json.dumps(
                    teclado,
                    ensure_ascii=False
                )
            },
            timeout=30
        )

        if resultado.get("ok"):

            print(
                "✅ FOTO ENVIADA PELA URL!"
            )

            return resultado

        print(
            "⚠️ URL direta falhou."
        )

    # --------------------------------------------------------
    # 3. DOWNLOAD + BYTES
    # --------------------------------------------------------

    if imagem:

        conteudo = baixar_imagem(
            imagem
        )

        if conteudo:

            try:

                print(
                    "📤 Enviando bytes para Telegram..."
                )

                arquivos = {
                    "photo": (
                        "produto.jpg",
                        conteudo,
                        "image/jpeg"
                    )
                }

                resultado = telegram_post(
                    "sendPhoto",
                    data={
                        "chat_id": CHAT_ID,
                        "caption": legenda,
                        "reply_markup": json.dumps(
                            teclado,
                            ensure_ascii=False
                        )
                    },
                    files=arquivos,
                    timeout=35
                )

                if resultado.get("ok"):

                    print(
                        "✅ FOTO ENVIADA PELOS BYTES!"
                    )

                    return resultado

                print(
                    "❌ Telegram rejeitou os bytes."
                )

            except Exception as erro:

                print(
                    "❌ Erro enviando bytes:",
                    repr(erro)
                )

    return {
        "ok": False,
        "error": "Não foi possível enviar a imagem."
    }


# ============================================================
# PUBLICAR TEXTO
# ============================================================

def enviar_texto_produto(
    legenda,
    teclado
):

    return telegram_post(
        "sendMessage",
        data={
            "chat_id": CHAT_ID,
            "text": legenda,
            "reply_markup": json.dumps(
                teclado,
                ensure_ascii=False
            )
        },
        timeout=20
    )


# ============================================================
# TRAVA REDIS
# ============================================================

def adquirir_trava():

    try:

        valor = (
            f"{os.getpid()}-{time.time()}"
        )

        resultado = redis_cmd(
            "SET",
            "publicador_lock",
            valor,
            "NX",
            "EX",
            LOCK_TTL
        )

        return resultado == "OK"

    except Exception as erro:

        print(
            "⚠️ Erro na trava Redis:",
            repr(erro)
        )

        return False


def liberar_trava():

    try:

        redis_cmd(
            "DEL",
            "publicador_lock"
        )

    except Exception as erro:

        print(
            "⚠️ Erro liberando trava:",
            repr(erro)
        )


# ============================================================
# PUBLICAR PRÓXIMO
# ============================================================

def publicar_proximo():

    if not adquirir_trava():

        print(
            "🔒 Outro processo está publicando."
        )

        return False

    try:

        fila = carregar_fila()

        if not fila:

            print(
                "📭 Fila vazia."
            )

            return False

        produto = fila[0]

        link = produto.get(
            "link"
        )

        if not link:

            print(
                "❌ Produto sem link. Removendo."
            )

            fila.pop(0)

            salvar_fila(fila)

            return False

        legenda = criar_legenda(
            produto
        )

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

        print("")
        print(
            "======================================"
        )
        print(
            "📤 PUBLICANDO PRODUTO"
        )
        print(
            "🛒",
            produto.get(
                "nome",
                "Produto"
            )
        )
        print(
            "💰",
            produto.get(
                "preco"
            )
            or "sem preço"
        )
        print(
            "🏷️",
            produto.get(
                "preco_antigo"
            )
            or "sem preço antigo"
        )
        print(
            "🔥",
            produto.get(
                "desconto"
            )
            or "sem desconto"
        )
        print(
            "🖼️",
            produto.get(
                "imagem"
            )
            or "SEM IMAGEM"
        )
        print(
            "======================================"
        )

        # ----------------------------------------------------
        # TENTA FOTO
        # ----------------------------------------------------

        resultado_foto = enviar_foto_produto(
            produto.get("imagem"),
            legenda,
            teclado
        )

        if resultado_foto.get("ok"):

            fila.pop(0)

            salvar_fila(
                fila
            )

            agora = time.time()

            redis_cmd(
                "SET",
                "ultimo_post",
                str(agora)
            )

            print(
                "✅ PRODUTO PUBLICADO COM FOTO!"
            )

            print(
                "📦 Restam:",
                len(fila)
            )

            return True

        # ----------------------------------------------------
        # FOTO FALHOU
        # PUBLICA TEXTO
        # ----------------------------------------------------

        print(
            "⚠️ FOTO FALHOU."
        )

        print(
            "➡️ Publicando texto como fallback..."
        )

        resultado_texto = enviar_texto_produto(
            legenda,
            teclado
        )

        if resultado_texto.get("ok"):

            # IMPORTANTE:
            # agora o produto é removido mesmo quando
            # a foto falha, evitando repetição infinita.

            fila.pop(0)

            salvar_fila(
                fila
            )

            agora = time.time()

            redis_cmd(
                "SET",
                "ultimo_post",
                str(agora)
            )

            print(
                "⚠️ TEXTO PUBLICADO."
            )

            print(
                "📦 Produto removido da fila."
            )

            print(
                "⏰ Próxima publicação em 12 minutos."
            )

            return True

        print(
            "❌ FOTO E TEXTO FALHARAM."
        )

        return False

    except Exception as erro:

        print(
            "❌ Erro publicando produto:",
            repr(erro)
        )

        return False

    finally:

        liberar_trava()


# ============================================================
# PUBLICADOR AUTOMÁTICO
# ============================================================

def publicador_automatico():

    print(
        "🚀 Publicador automático iniciado."
    )

    print(
        "⏰ Intervalo:",
        INTERVALO,
        "segundos"
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

            restante = (
                INTERVALO
                - (agora - ultimo)
            )

            if restante > 0:

                print(
                    f"⏳ Próxima publicação em "
                    f"{int(restante)} segundos."
                )

            else:

                publicar_proximo()

        except Exception as erro:

            print(
                "❌ Erro publicador:",
                repr(erro)
            )

        # Não precisa verificar a cada 30 segundos.
        # 10 segundos é suficiente para manter o horário
        # próximo do planejado.

        time.sleep(10)


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
            f"{produto.get('preco') or 'não encontrado'}\n"
            f"🏷️ "
            f"{produto.get('preco_antigo') or 'não encontrado'}\n"
            f"🔥 "
            f"{produto.get('desconto') or 'não encontrado'}"
            f"{'%' if produto.get('desconto') else ''}\n"
            f"🖼️ "
            f"{'SIM' if produto.get('imagem') else 'NÃO'}\n"
            f"🔗 "
            f"{produto.get('link', '')}\n\n"
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
# PROCESSAMENTO EM SEGUNDO PLANO
# ============================================================

def processar_link_background(
    chat_id,
    link,
    nome_informado=None,
    imagem_telegram=None
):

    print("")
    print(
        "🧵 PROCESSAMENTO EM SEGUNDO PLANO"
    )

    nome = nome_informado
    imagem = imagem_telegram
    preco = None
    preco_antigo = None
    desconto = None

    # --------------------------------------------------------
    # FOTO ENVIADA PELO USUÁRIO
    # --------------------------------------------------------

    if imagem_telegram:

        print(
            "🖼️ FOTO DO TELEGRAM RECEBIDA."
        )

    # --------------------------------------------------------
    # SHOPEE
    # --------------------------------------------------------

    (
        nome_shopee,
        preco_shopee,
        antigo_shopee,
        desconto_shopee,
        imagem_shopee
    ) = extrair_dados_shopee(
        link
    )

    if nome_shopee:

        nome = nome_shopee

    if preco_shopee:

        preco = preco_shopee

    if antigo_shopee:

        preco_antigo = antigo_shopee

    if desconto_shopee:

        desconto = desconto_shopee

    # --------------------------------------------------------
    # IMAGEM
    # --------------------------------------------------------

    # Foto enviada pelo usuário tem prioridade
    if not imagem:

        imagem = imagem_shopee

    # --------------------------------------------------------
    # RECALCULAR DESCONTO
    # --------------------------------------------------------

    if not desconto:

        desconto = calcular_desconto(
            preco,
            preco_antigo
        )

    # --------------------------------------------------------
    # SALVAR
    # --------------------------------------------------------

    adicionado, posicao = adicionar_na_fila(
        nome or "Produto Shopee",
        link,
        imagem,
        preco,
        preco_antigo,
        desconto
    )

    if not adicionado:

        enviar_mensagem(
            chat_id,
            "⚠️ Esse produto já está na fila."
        )

        return

    mensagem = f"""✅ PRODUTO ADICIONADO!

🛍️ {nome or 'Produto Shopee'}

💰 Preço: {preco or 'não encontrado'}

🏷️ De: {preco_antigo or 'não encontrado'}

🔥 Desconto: {str(desconto) + '%' if desconto else 'não encontrado'}

🖼️ Imagem: {'encontrada ✅' if imagem else 'não encontrada ⚠️'}

📦 Posição: {posicao}

⏰ Publicação automática a cada 12 minutos."""

    enviar_mensagem(
        chat_id,
        mensagem
    )


# ============================================================
# PROCESSAR MENSAGEM
# ============================================================

def processar_mensagem(mensagem):

    chat_id = mensagem["chat"]["id"]

    # --------------------------------------------------------
    # ADMIN
    # --------------------------------------------------------

    if ADMIN_ID:

        remetente = (
            mensagem
            .get("from", {})
            .get("id")
        )

        if str(remetente) != str(ADMIN_ID):

            return

    # --------------------------------------------------------
    # FOTO + LEGENDA
    # --------------------------------------------------------

    if "photo" in mensagem:

        legenda = (
            mensagem
            .get("caption", "")
            .strip()
        )

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

        if not link.startswith(
            "http"
        ):

            enviar_mensagem(
                chat_id,
                "❌ O link informado não é válido."
            )

            return

        # ----------------------------------------------------
        # FILE ID
        # ----------------------------------------------------

        file_id = (
            mensagem["photo"][-1]["file_id"]
        )

        print(
            "📸 FILE_ID RECEBIDO:",
            file_id
        )

        enviar_mensagem(
            chat_id,
            "⏳ Link recebido! Estou preparando o produto..."
        )

        threading.Thread(
            target=processar_link_background,
            args=(
                chat_id,
                link,
                nome,
                file_id
            ),
            daemon=True
        ).start()

        return

    # --------------------------------------------------------
    # TEXTO
    # --------------------------------------------------------

    texto = (
        mensagem
        .get("text", "")
        .strip()
    )

    # --------------------------------------------------------
    # START
    # --------------------------------------------------------

    if texto == "/start":

        enviar_mensagem(
            chat_id,
            """🤖 ACHADOS DA SHOPEE

📸 ADICIONAR PRODUTO

Envie:

📷 FOTO + legenda

Nome do produto
Link da Shopee

OU simplesmente:

🔗 Link da Shopee

O sistema tenta encontrar:

🛍️ Nome
💰 Preço
🏷️ Preço anterior
🔥 Desconto
🖼️ Imagem

COMANDOS:

/fila
📦 Ver fila

/limparfila
🗑️ Limpar fila"""
        )

        return

    # --------------------------------------------------------
    # FILA
    # --------------------------------------------------------

    if texto == "/fila":

        mostrar_fila(
            chat_id
        )

        return

    # --------------------------------------------------------
    # LIMPAR
    # --------------------------------------------------------

    if texto == "/limparfila":

        limpar_fila(
            chat_id
        )

        return

    # --------------------------------------------------------
    # SOMENTE LINK
    # --------------------------------------------------------

    if texto.startswith(
        "http"
    ):

        link = texto.split()[0]

        print(
            "🔗 Link recebido:",
            link
        )

        enviar_mensagem(
            chat_id,
            "⏳ Link recebido! Estou preparando o produto..."
        )

        threading.Thread(
            target=processar_link_background,
            args=(
                chat_id,
                link,
                None,
                None
            ),
            daemon=True
        ).start()

        return

    # --------------------------------------------------------
    # NOME + LINK
    # --------------------------------------------------------

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

    if not link.startswith(
        "http"
    ):

        enviar_mensagem(
            chat_id,
            "❌ Link inválido."
        )

        return

    enviar_mensagem(
        chat_id,
        "⏳ Link recebido! Estou preparando o produto..."
    )

    threading.Thread(
        target=processar_link_background,
        args=(
            chat_id,
            link,
            nome,
            None
        ),
        daemon=True
    ).start()


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
                "timeout": 25
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

                if dados.get(
                    "error_code"
                ) == 401:

                    print("")
                    print(
                        "🚨 TELEGRAM TOKEN INVÁLIDO!"
                    )
                    print(
                        "Verifique TELEGRAM_TOKEN no Render."
                    )
                    print("")

                    time.sleep(10)

                else:

                    time.sleep(3)

                continue

            for update in dados.get(
                "result",
                []
            ):

                ultimo_update = (
                    update["update_id"]
                )

                mensagem = update.get(
                    "message"
                )

                if not mensagem:
                    continue

                try:

                    processar_mensagem(
                        mensagem
                    )

                except Exception as erro:

                    print(
                        "❌ Erro processando:",
                        repr(erro)
                    )

                    try:

                        enviar_mensagem(
                            mensagem["chat"]["id"],
                            "⚠️ Erro temporário. Tente novamente."
                        )

                    except Exception:
                        pass

        except Exception as erro:

            print(
                "❌ Erro recebendo Telegram:",
                repr(erro)
            )

            time.sleep(3)


# ============================================================
# SERVIDOR RENDER
# ============================================================

class Saude(
    BaseHTTPRequestHandler
):

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
        f"🌐 Render ativo na porta {porta}"
    )

    servidor.serve_forever()


# ============================================================
# INICIAR
# ============================================================

if __name__ == "__main__":

    # --------------------------------------------------------
    # TESTAR TOKEN
    # --------------------------------------------------------

    if not testar_token():

        print("")
        print(
            "🛑 BOT NÃO SERÁ INICIADO."
        )
        print(
            "Corrija TELEGRAM_TOKEN no Render."
        )
        print("")

        while True:

            time.sleep(60)

    # --------------------------------------------------------
    # SERVIDOR
    # --------------------------------------------------------

    threading.Thread(
        target=servidor_saude,
        daemon=True
    ).start()

    # --------------------------------------------------------
    # PUBLICADOR
    # --------------------------------------------------------

    threading.Thread(
        target=publicador_automatico,
        daemon=True
    ).start()

    # --------------------------------------------------------
    # INÍCIO
    # --------------------------------------------------------

    print("")
    print(
        "======================================"
    )
    print(
        "✅ ACHADOS DA SHOPEE ONLINE"
    )
    print(
        "🖼️ IMAGEM + DADOS DO PRODUTO"
    )
    print(
        "💰 PREÇO ATUAL"
    )
    print(
        "🏷️ PREÇO ANTERIOR"
    )
    print(
        "🔥 DESCONTO"
    )
    print(
        "🛒 BOTÃO COMPRAR AGORA"
    )
    print(
        "⏰ 5 PRODUTOS POR HORA"
    )
    print(
        "🔒 TRAVA CONTRA DUPLA PUBLICAÇÃO"
    )
    print(
        "======================================"
    )
    print("")

    iniciar_bot()
