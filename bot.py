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

# 5 produtos por hora = 1 produto a cada 12 minutos
INTERVALO = 720

# Quantidade máxima de tentativas para um produto
MAX_TENTATIVAS = 3

# Chaves Redis
CHAVE_FILA = "fila"
CHAVE_ULTIMO_POST = "ultimo_post"
CHAVE_LOCK = "publicador_lock"


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
    "Referer": "https://shopee.com.br/"
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

def redis_cmd(*args, timeout=8):

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

        dado = redis_cmd(
            "GET",
            CHAVE_FILA
        )

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
        CHAVE_FILA,
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
    file_id=None,
    preco_antigo=None,
    desconto=None,
    avaliacao=None,
    vendas=None,
    loja=None
):

    fila = carregar_fila()

    for produto in fila:

        if produto.get("link") == link:

            return False, len(fila)

    produto = {
        "nome": nome,
        "link": link,
        "imagem": imagem,
        "file_id": file_id,
        "preco": preco,
        "preco_antigo": preco_antigo,
        "desconto": desconto,
        "avaliacao": avaliacao,
        "vendas": vendas,
        "loja": loja,
        "tentativas": 0,
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


def enviar_mensagem(
    chat_id,
    texto
):

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
# LIMPEZA DE TEXTO
# ============================================================

def limpar_texto(valor):

    if valor is None:
        return None

    valor = html_lib.unescape(
        str(valor)
    )

    valor = valor.replace(
        "\\/",
        "/"
    )

    valor = valor.replace(
        "\\u002F",
        "/"
    )

    valor = valor.replace(
        "\\u003A",
        ":"
    )

    valor = valor.replace(
        "\\u0026",
        "&"
    )

    valor = valor.replace(
        "\\u003F",
        "?"
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


# ============================================================
# NORMALIZAR URL
# ============================================================

def normalizar_url(url):

    if not url:
        return None

    url = limpar_texto(url)

    if not url:
        return None

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

def extrair_meta(
    html,
    nome
):

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

            texto = html_lib.unescape(
                bloco.strip()
            )

            dados = json.loads(texto)

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
    preco_antigo = None
    avaliacao = None
    vendas = None
    loja = None

    for item in dados:

        if not isinstance(item, dict):
            continue

        tipo = item.get("@type")

        if isinstance(tipo, list):

            tipos = [
                str(x).lower()
                for x in tipo
            ]

            eh_produto = (
                "product" in tipos
            )

        else:

            eh_produto = (
                str(tipo).lower()
                == "product"
            )

        if not eh_produto:
            continue

        nome = item.get("name")

        imagem = item.get("image")

        if isinstance(imagem, list):

            imagem = (
                imagem[0]
                if imagem
                else None
            )

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

        avaliacao_dados = item.get(
            "aggregateRating"
        )

        if isinstance(
            avaliacao_dados,
            dict
        ):

            avaliacao = (
                avaliacao_dados.get(
                    "ratingValue"
                )
            )

        marca = item.get("brand")

        if isinstance(marca, dict):

            loja = marca.get("name")

        elif marca:

            loja = marca

        break

    return {
        "nome": limpar_texto(nome),
        "imagem": normalizar_url(imagem),
        "preco": converter_preco(preco),
        "preco_antigo": converter_preco(
            preco_antigo
        ),
        "avaliacao": limpar_texto(
            avaliacao
        ),
        "vendas": limpar_texto(
            vendas
        ),
        "loja": limpar_texto(
            loja
        )
    }


# ============================================================
# PREÇO
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
            .replace(" ", "")
        )

        # 29,90
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

        # Valores internos comuns
        # Ex.: 2990 -> 29,90
        if (
            numero >= 1000
            and numero.is_integer()
        ):

            numero = numero / 100

        if numero <= 0:
            return None

        if numero > 500000:
            return None

        return round(
            numero,
            2
        )

    except Exception:

        return None


def preco_valido(valor):

    numero = converter_preco(
        valor
    )

    return (
        numero is not None
        and 0.01 <= numero <= 500000
    )


def precos_do_html(html):

    if not html:
        return []

    candidatos = []

    padroes = [

        # Meta
        r'<meta[^>]+property=["\']product:price:amount["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']product:price:amount["\']',

        # JSON
        r'"price"\s*:\s*"([^"]+)"',
        r'"price"\s*:\s*([0-9]+(?:\.[0-9]+)?)',

        r'"price_min"\s*:\s*"([^"]+)"',
        r'"price_min"\s*:\s*([0-9]+(?:\.[0-9]+)?)',

        r'"priceMin"\s*:\s*"([^"]+)"',
        r'"priceMin"\s*:\s*([0-9]+(?:\.[0-9]+)?)',

        r'"salePrice"\s*:\s*"([^"]+)"',
        r'"salePrice"\s*:\s*([0-9]+(?:\.[0-9]+)?)',

        r'"currentPrice"\s*:\s*"([^"]+)"',
        r'"currentPrice"\s*:\s*([0-9]+(?:\.[0-9]+)?)',

        r'"current_price"\s*:\s*"([^"]+)"',
        r'"current_price"\s*:\s*([0-9]+(?:\.[0-9]+)?)',

        r'"final_price"\s*:\s*"([^"]+)"',
        r'"final_price"\s*:\s*([0-9]+(?:\.[0-9]+)?)',

        r'"sale_price"\s*:\s*"([^"]+)"',
        r'"sale_price"\s*:\s*([0-9]+(?:\.[0-9]+)?)',

        r'"display_price"\s*:\s*"([^"]+)"',
        r'"display_price"\s*:\s*([0-9]+(?:\.[0-9]+)?)',

        # Preço visível em HTML
        r'R\$\s*([0-9]{1,6}(?:[.,][0-9]{2})?)'
    ]

    for padrao in padroes:

        encontrados = re.findall(
            padrao,
            html,
            re.IGNORECASE
        )

        for valor in encontrados:

            numero = converter_preco(
                valor
            )

            if preco_valido(numero):

                candidatos.append(
                    numero
                )

    return candidatos


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

        url = normalizar_url(url)

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

            candidatos.append(url)

    # ========================================================
    # OG IMAGE
    # ========================================================

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

    # ========================================================
    # PRODUCT IMAGE
    # ========================================================

    adicionar(
        extrair_meta(
            html,
            "twitter:image"
        )
    )

    # ========================================================
    # SUSERCONTENT
    # ========================================================

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

        r'"image_url_list"\s*:\s*\[\s*"([^"]+)"',

        r'"imageList"\s*:\s*\[\s*"([^"]+)"'
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

            if len(candidatos) >= 30:
                break

        if len(candidatos) >= 30:
            break

    # ========================================================
    # UNIFICAR
    # ========================================================

    unicos = []
    vistos = set()

    for url in candidatos:

        chave = url.lower()

        if chave in vistos:
            continue

        vistos.add(chave)

        unicos.append(url)

    return unicos


def encontrar_imagem_html(html):

    imagens = encontrar_imagens_html(
        html
    )

    # Primeiro susercontent/file
    for url in imagens:

        if (
            "susercontent.com/file/"
            in url.lower()
        ):

            return url

    # Depois qualquer susercontent
    for url in imagens:

        if (
            "susercontent.com"
            in url.lower()
        ):

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

        if (
            len(nome) >= 5
            and nome.lower()
            not in (
                "shopee",
                "shopee brasil"
            )
        ):

            return nome

    # PRODUCT NAME
    nome = extrair_meta(
        html,
        "product:name"
    )

    if nome:

        if len(nome) >= 5:

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

    # Alguns padrões JSON
    padroes = [

        r'"productName"\s*:\s*"([^"]+)"',

        r'"product_name"\s*:\s*"([^"]+)"',

        r'"name"\s*:\s*"([^"]{5,200})"'
    ]

    for padrao in padroes:

        encontrados = re.findall(
            padrao,
            html,
            re.IGNORECASE
        )

        for encontrado in encontrados:

            candidato = limpar_texto(
                encontrado
            )

            if not candidato:
                continue

            baixo = candidato.lower()

            if baixo in (
                "shopee",
                "produto",
                "product"
            ):
                continue

            if "shopee" in baixo:
                continue

            if len(candidato) >= 5:

                return candidato

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
# EXTRAIR DESCONTO
# ============================================================

def encontrar_desconto(html):

    if not html:
        return None

    padroes = [

        r'"discount"\s*:\s*"?([0-9]{1,3})',
        r'"discount_percent"\s*:\s*"?([0-9]{1,3})',
        r'"discountPercentage"\s*:\s*"?([0-9]{1,3})',
        r'"discountRate"\s*:\s*"?([0-9]{1,3})'
    ]

    for padrao in padroes:

        encontrado = re.search(
            padrao,
            html,
            re.IGNORECASE
        )

        if encontrado:

            try:

                numero = int(
                    encontrado.group(1)
                )

                if 1 <= numero <= 100:

                    return f"{numero}% OFF"

            except Exception:

                pass

    return None


# ============================================================
# EXTRAIR AVALIAÇÃO
# ============================================================

def encontrar_avaliacao(html):

    if not html:
        return None

    padroes = [

        r'"rating"\s*:\s*"?(4(?:\.[0-9]+)?|5(?:\.0+)?)',
        r'"ratingValue"\s*:\s*"?(4(?:\.[0-9]+)?|5(?:\.0+)?)',
        r'"rating_value"\s*:\s*"?(4(?:\.[0-9]+)?|5(?:\.0+)?)'
    ]

    for padrao in padroes:

        encontrado = re.search(
            padrao,
            html,
            re.IGNORECASE
        )

        if encontrado:

            return encontrado.group(1)

    return None


# ============================================================
# EXTRAIR VENDAS
# ============================================================

def encontrar_vendas(html):

    if not html:
        return None

    padroes = [

        r'"sold"\s*:\s*"?([0-9]+)',
        r'"soldCount"\s*:\s*"?([0-9]+)',
        r'"historicalSold"\s*:\s*"?([0-9]+)',
        r'"historical_sold"\s*:\s*"?([0-9]+)'
    ]

    for padrao in padroes:

        encontrado = re.search(
            padrao,
            html,
            re.IGNORECASE
        )

        if encontrado:

            return (
                encontrado.group(1)
                + " vendidos"
            )

    return None


# ============================================================
# EXTRAIR LOJA
# ============================================================

def encontrar_loja(html):

    if not html:
        return None

    padroes = [

        r'"shopName"\s*:\s*"([^"]+)"',
        r'"shop_name"\s*:\s*"([^"]+)"',
        r'"shopname"\s*:\s*"([^"]+)"'
    ]

    for padrao in padroes:

        encontrado = re.search(
            padrao,
            html,
            re.IGNORECASE
        )

        if encontrado:

            loja = limpar_texto(
                encontrado.group(1)
            )

            if loja:

                return loja

    return None


# ============================================================
# LEITOR SHOPEE
# ============================================================

def extrair_dados_shopee(link):

    print("")
    print("🔎 LENDO SHOPEE...")
    print("🔗", link)

    dados_finais = {
        "nome": None,
        "preco": None,
        "imagem": None,
        "preco_antigo": None,
        "desconto": None,
        "avaliacao": None,
        "vendas": None,
        "loja": None
    }

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
            "🔗 URL FINAL:",
            resposta.url
        )

        if resposta.status_code != 200:

            print(
                "❌ Shopee não retornou HTTP 200."
            )

            return dados_finais

        html = resposta.text

        print(
            "📄 HTML:",
            len(html),
            "caracteres"
        )

        # ====================================================
        # NOME
        # ====================================================

        dados_finais["nome"] = (
            encontrar_nome_html(html)
        )

        # ====================================================
        # IMAGEM
        # ====================================================

        dados_finais["imagem"] = (
            encontrar_imagem_html(html)
        )

        # ====================================================
        # JSON-LD
        # ====================================================

        try:

            jsonld = extrair_jsonld(
                html
            )

            produto_json = (
                procurar_jsonld_produto(
                    jsonld
                )
            )

            if (
                not dados_finais["nome"]
                and produto_json["nome"]
            ):

                dados_finais["nome"] = (
                    produto_json["nome"]
                )

            if (
                not dados_finais["imagem"]
                and produto_json["imagem"]
            ):

                dados_finais["imagem"] = (
                    produto_json["imagem"]
                )

            if (
                not dados_finais["preco"]
                and produto_json["preco"]
            ):

                dados_finais["preco"] = (
                    produto_json["preco"]
                )

            if (
                not dados_finais["avaliacao"]
                and produto_json["avaliacao"]
            ):

                dados_finais["avaliacao"] = (
                    produto_json["avaliacao"]
                )

            if (
                not dados_finais["loja"]
                and produto_json["loja"]
            ):

                dados_finais["loja"] = (
                    produto_json["loja"]
                )

        except Exception as erro:

            print(
                "⚠️ Erro JSON-LD:",
                repr(erro)
            )

        # ====================================================
        # PREÇO
        # ====================================================

        if not dados_finais["preco"]:

            precos = precos_do_html(
                html
            )

            if precos:

                # Remove duplicados
                precos = sorted(
                    set(precos)
                )

                # Escolhe o menor preço encontrado
                dados_finais["preco"] = (
                    precos[0]
                )

        # ====================================================
        # OUTRAS INFORMAÇÕES
        # ====================================================

        dados_finais["desconto"] = (
            encontrar_desconto(html)
        )

        dados_finais["avaliacao"] = (
            dados_finais["avaliacao"]
            or encontrar_avaliacao(html)
        )

        dados_finais["vendas"] = (
            encontrar_vendas(html)
        )

        dados_finais["loja"] = (
            dados_finais["loja"]
            or encontrar_loja(html)
        )

        # ====================================================
        # LIMPEZA
        # ====================================================

        if dados_finais["nome"]:

            nome = limpar_texto(
                dados_finais["nome"]
            )

            nome = re.sub(
                r'\s*[-|]\s*Shopee.*$',
                '',
                nome,
                flags=re.IGNORECASE
            )

            dados_finais["nome"] = (
                nome.strip()
            )

        if dados_finais["preco"]:

            dados_finais["preco"] = (
                formatar_preco(
                    dados_finais["preco"]
                )
            )

        if dados_finais["imagem"]:

            dados_finais["imagem"] = (
                normalizar_url(
                    dados_finais["imagem"]
                )
            )

        print("")
        print("📊 RESULTADO DA LEITURA")
        print(
            "🛒 Nome:",
            dados_finais["nome"]
            or "NÃO ENCONTRADO"
        )
        print(
            "💰 Preço:",
            dados_finais["preco"]
            or "NÃO ENCONTRADO"
        )
        print(
            "🖼️ Imagem:",
            dados_finais["imagem"]
            or "NÃO ENCONTRADA"
        )
        print(
            "🏷️ Desconto:",
            dados_finais["desconto"]
            or "NÃO ENCONTRADO"
        )
        print(
            "⭐ Avaliação:",
            dados_finais["avaliacao"]
            or "NÃO ENCONTRADA"
        )
        print(
            "📦 Vendas:",
            dados_finais["vendas"]
            or "NÃO ENCONTRADAS"
        )
        print(
            "🏪 Loja:",
            dados_finais["loja"]
            or "NÃO ENCONTRADA"
        )

        return dados_finais

    except Exception as erro:

        print(
            "❌ ERRO LEITOR SHOPEE:",
            repr(erro)
        )

        return dados_finais


# ============================================================
# BAIXAR IMAGEM
# ============================================================

def baixar_imagem(url):

    if not url:
        return None, None, None

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

            return None, None, None

        content_type = (
            resposta.headers
            .get(
                "Content-Type",
                ""
            )
            .lower()
            .split(";")[0]
        )

        print(
            "📦 Bytes:",
            len(conteudo)
        )

        print(
            "📄 Content-Type:",
            content_type
        )

        # ====================================================
        # DETECTAR FORMATO PELOS BYTES
        # ====================================================

        if conteudo.startswith(
            b"\xff\xd8\xff"
        ):

            return (
                conteudo,
                "produto.jpg",
                "image/jpeg"
            )

        if conteudo.startswith(
            b"\x89PNG"
        ):

            return (
                conteudo,
                "produto.png",
                "image/png"
            )

        if conteudo.startswith(
            b"GIF8"
        ):

            return (
                conteudo,
                "produto.gif",
                "image/gif"
            )

        if conteudo.startswith(
            b"RIFF"
        ) and b"WEBP" in conteudo[:20]:

            print(
                "⚠️ Imagem WEBP detectada."
            )

            return (
                conteudo,
                "produto.webp",
                "image/webp"
            )

        if content_type.startswith(
            "image/"
        ):

            extensao = "jpg"

            if "png" in content_type:
                extensao = "png"

            elif "gif" in content_type:
                extensao = "gif"

            elif "webp" in content_type:
                extensao = "webp"

            return (
                conteudo,
                f"produto.{extensao}",
                content_type
            )

        print(
            "❌ O servidor não retornou imagem."
        )

        return None, None, None

    except Exception as erro:

        print(
            "❌ Erro baixando imagem:",
            repr(erro)
        )

        return None, None, None


# ============================================================
# PUBLICAR FOTO
# ============================================================

def enviar_foto_produto(
    imagem,
    legenda,
    teclado
):

    # ========================================================
    # 1. FILE_ID DO TELEGRAM
    # ========================================================

    if (
        imagem
        and not imagem.startswith(
            ("http://", "https://")
        )
    ):

        print(
            "📸 Usando FILE_ID do Telegram..."
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
                "✅ FOTO PUBLICADA PELO FILE_ID!"
            )

            return resultado

        print(
            "⚠️ FILE_ID falhou."
        )

    # ========================================================
    # 2. URL DIRETA
    # ========================================================

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
            timeout=25
        )

        if resultado.get("ok"):

            print(
                "✅ FOTO PUBLICADA PELA URL!"
            )

            return resultado

        print(
            "⚠️ URL direta falhou."
        )

    # ========================================================
    # 3. BAIXAR E ENVIAR
    # ========================================================

    if (
        imagem
        and imagem.startswith(
            ("http://", "https://")
        )
    ):

        conteudo, nome_arquivo, mime = (
            baixar_imagem(imagem)
        )

        if conteudo:

            print(
                "📤 Enviando arquivo para Telegram..."
            )

            arquivos = {
                "photo": (
                    nome_arquivo,
                    conteudo,
                    mime
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
                timeout=30
            )

            if resultado.get("ok"):

                print(
                    "✅ FOTO PUBLICADA PELO ARQUIVO!"
                )

                return resultado

            print(
                "❌ Telegram rejeitou o arquivo."
            )

    return {
        "ok": False,
        "error": "Não foi possível publicar a imagem."
    }


# ============================================================
# GERAR LEGENDA
# ============================================================

def gerar_legenda(produto):

    nome = (
        produto.get("nome")
        or ""
    ).strip()

    preco = (
        produto.get("preco")
        or ""
    ).strip()

    preco_antigo = (
        produto.get("preco_antigo")
        or ""
    ).strip()

    desconto = (
        produto.get("desconto")
        or ""
    ).strip()

    avaliacao = (
        produto.get("avaliacao")
        or ""
    ).strip()

    vendas = (
        produto.get("vendas")
        or ""
    ).strip()

    loja = (
        produto.get("loja")
        or ""
    ).strip()

    partes = []

    # ========================================================
    # NOME
    # ========================================================

    if nome:

        partes.append(
            f"🔥 ACHADINHO DA SHOPEE 🔥\n\n"
            f"🛍️ {nome}"
        )

    else:

        partes.append(
            "🔥 ACHADINHO DA SHOPEE 🔥"
        )

    # ========================================================
    # PREÇO
    # ========================================================

    if preco:

        partes.append(
            f"💰 PREÇO: {preco}"
        )

    if preco_antigo:

        partes.append(
            f"🏷️ De: {preco_antigo}"
        )

    if desconto:

        partes.append(
            f"🔥 {desconto}"
        )

    # ========================================================
    # AVALIAÇÃO
    # ========================================================

    if avaliacao:

        partes.append(
            f"⭐ Avaliação: {avaliacao}/5"
        )

    # ========================================================
    # VENDAS
    # ========================================================

    if vendas:

        partes.append(
            f"📦 {vendas}"
        )

    # ========================================================
    # LOJA
    # ========================================================

    if loja:

        partes.append(
            f"🏪 Loja: {loja}"
        )

    # ========================================================
    # FINAL
    # ========================================================

    partes.append(
        "⚡ Aproveite enquanto estiver disponível!"
    )

    partes.append(
        "👇 Clique abaixo para conferir:"
    )

    return "\n\n".join(
        partes
    )


# ============================================================
# PUBLICAR PRÓXIMO
# ============================================================

def publicar_proximo():

    fila = carregar_fila()

    if not fila:

        print(
            "📭 Fila vazia."
        )

        return False

    produto = fila[0]

    nome = (
        produto.get("nome")
        or ""
    ).strip()

    link = (
        produto.get("link")
        or ""
    ).strip()

    imagem = produto.get(
        "file_id"
    ) or produto.get(
        "imagem"
    )

    # ========================================================
    # NÃO PUBLICAR PRODUTO VAZIO
    # ========================================================

    if not link:

        print(
            "❌ Produto sem link. Removendo da fila."
        )

        fila.pop(0)

        salvar_fila(fila)

        return False

    # ========================================================
    # PROTEÇÃO CONTRA DADOS VAZIOS
    # ========================================================

    if (
        not nome
        or nome.lower()
        in (
            "produto shopee",
            "produto"
        )
    ):

        print(
            "❌ Produto sem nome real."
        )

        print(
            "➡️ Produto não será publicado."
        )

        produto["tentativas"] = (
            int(
                produto.get(
                    "tentativas",
                    0
                )
            ) + 1
        )

        if (
            produto["tentativas"]
            >= MAX_TENTATIVAS
        ):

            print(
                "🗑️ Produto removido após "
                "muitas tentativas."
            )

            fila.pop(0)

        salvar_fila(fila)

        return False

    # ========================================================
    # LEGENDA
    # ========================================================

    legenda = gerar_legenda(
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
        nome
    )

    print(
        "💰",
        produto.get(
            "preco"
        )
        or "sem preço"
    )

    print(
        "🖼️",
        (
            "FILE_ID"
            if produto.get("file_id")
            else produto.get("imagem")
            or "SEM IMAGEM"
        )
    )

    # ========================================================
    # FOTO
    # ========================================================

    if imagem:

        resultado = enviar_foto_produto(
            imagem,
            legenda,
            teclado
        )

        if resultado.get("ok"):

            fila.pop(0)

            salvar_fila(fila)

            print(
                "✅ PRODUTO PUBLICADO COM FOTO!"
            )

            print(
                "📦 Restam:",
                len(fila)
            )

            return True

        print(
            "⚠️ Foto falhou."
        )

    else:

        print(
            "⚠️ Produto está sem imagem."
        )

    # ========================================================
    # TEXTO SOMENTE SE NÃO HOUVER IMAGEM
    # ========================================================

    # Não vamos publicar automaticamente
    # um anúncio sem foto se a intenção é
    # manter o canal com produtos completos.

    print(
        "⛔ NÃO PUBLICADO: produto sem imagem válida."
    )

    return False


# ============================================================
# CONTROLE DE INTERVALO
# ============================================================

def pode_publicar():

    try:

        ultimo = float(
            redis_cmd(
                "GET",
                CHAVE_ULTIMO_POST
            ) or 0
        )

        agora = time.time()

        if ultimo <= 0:

            return True

        restante = (
            INTERVALO
            - (agora - ultimo)
        )

        if restante > 0:

            minutos = int(
                restante // 60
            )

            segundos = int(
                restante % 60
            )

            print(
                f"⏳ Próxima publicação em "
                f"{minutos}m {segundos}s"
            )

            return False

        return True

    except Exception as erro:

        print(
            "❌ Erro verificando intervalo:",
            repr(erro)
        )

        return False


# ============================================================
# PUBLICADOR AUTOMÁTICO
# ============================================================

def publicador_automatico():

    print(
        "🚀 Publicador automático iniciado."
    )

    print(
        "⏰ Intervalo configurado:",
        INTERVALO,
        "segundos"
    )

    while True:

        try:

            # =================================================
            # LOCK DISTRIBUÍDO
            # =================================================

            lock = redis_cmd(
                "SET",
                CHAVE_LOCK,
                str(time.time()),
                "NX",
                "EX",
                "60"
            )

            if lock != "OK":

                print(
                    "🔒 Outra instância está "
                    "controlando o publicador."
                )

                time.sleep(30)

                continue

            try:

                # =============================================
                # VERIFICAR INTERVALO
                # =============================================

                if pode_publicar():

                    sucesso = publicar_proximo()

                    if sucesso:

                        agora = time.time()

                        redis_cmd(
                            "SET",
                            CHAVE_ULTIMO_POST,
                            str(agora)
                        )

                        print(
                            "⏰ CONTADOR DE 12 MINUTOS "
                            "INICIADO."
                        )

            finally:

                try:

                    redis_cmd(
                        "DEL",
                        CHAVE_LOCK
                    )

                except Exception:
                    pass

        except Exception as erro:

            print(
                "❌ Erro publicador:",
                repr(erro)
            )

        # Verifica a cada 30 segundos,
        # mas só publica quando completar 720 segundos.
        time.sleep(30)


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
            f"{produto.get('nome') or 'SEM NOME'}\n"
            f"💰 "
            f"{produto.get('preco') or 'sem preço'}\n"
            f"🖼️ "
            f"{'SIM' if produto.get('file_id') or produto.get('imagem') else 'NÃO'}\n"
            f"⭐ "
            f"{produto.get('avaliacao') or 'não encontrada'}\n"
            f"📦 "
            f"{produto.get('vendas') or 'não encontradas'}\n"
            f"🏪 "
            f"{produto.get('loja') or 'não encontrada'}\n"
            f"🔗 "
            f"{produto.get('link', '')}\n\n"
        )

    enviar_mensagem(
        chat_id,
        mensagem[:4000]
    )


# ============================================================
# STATUS
# ============================================================

def mostrar_status(chat_id):

    fila = carregar_fila()

    try:

        ultimo = float(
            redis_cmd(
                "GET",
                CHAVE_ULTIMO_POST
            ) or 0
        )

    except Exception:

        ultimo = 0

    agora = time.time()

    if ultimo <= 0:

        proxima = (
            "pronta para publicar"
        )

    else:

        restante = (
            INTERVALO
            - (agora - ultimo)
        )

        if restante <= 0:

            proxima = (
                "pronta para publicar"
            )

        else:

            minutos = int(
                restante // 60
            )

            segundos = int(
                restante % 60
            )

            proxima = (
                f"{minutos}m {segundos}s"
            )

    com_imagem = sum(
        1
        for produto in fila
        if (
            produto.get("file_id")
            or produto.get("imagem")
        )
    )

    mensagem = f"""🤖 STATUS DO BOT

🟢 Sistema online

📦 Produtos na fila: {len(fila)}

🖼️ Com imagem: {com_imagem}

⏰ Intervalo: 12 minutos

➡️ Próxima publicação:
{proxima}

📊 Limite:
5 produtos por hora

🔒 Controle anti-duplicação:
ATIVO
"""

    enviar_mensagem(
        chat_id,
        mensagem
    )


# ============================================================
# PULAR PRODUTO
# ============================================================

def pular_produto(chat_id):

    fila = carregar_fila()

    if not fila:

        enviar_mensagem(
            chat_id,
            "📭 A fila está vazia."
        )

        return

    produto = fila.pop(0)

    salvar_fila(fila)

    enviar_mensagem(
        chat_id,
        "⏭️ Produto removido da fila:\n\n"
        + (
            produto.get("nome")
            or "Sem nome"
        )
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

    nome = (
        nome_informado
        if nome_informado
        else None
    )

    imagem = (
        imagem_telegram
        if imagem_telegram
        else None
    )

    # ========================================================
    # LEITURA SHOPEE
    # ========================================================

    dados = extrair_dados_shopee(
        link
    )

    # ========================================================
    # NOME
    # ========================================================

    if dados.get("nome"):

        nome = dados["nome"]

    # ========================================================
    # IMAGEM
    # ========================================================

    if not imagem:

        imagem = dados.get(
            "imagem"
        )

    # ========================================================
    # PREÇO
    # ========================================================

    preco = dados.get(
        "preco"
    )

    # ========================================================
    # OUTRAS INFORMAÇÕES
    # ========================================================

    preco_antigo = dados.get(
        "preco_antigo"
    )

    desconto = dados.get(
        "desconto"
    )

    avaliacao = dados.get(
        "avaliacao"
    )

    vendas = dados.get(
        "vendas"
    )

    loja = dados.get(
        "loja"
    )

    # ========================================================
    # LOG
    # ========================================================

    print("")
    print(
        "📦 DADOS FINAIS DO PRODUTO"
    )

    print(
        "🛒",
        nome or "NÃO ENCONTRADO"
    )

    print(
        "💰",
        preco or "NÃO ENCONTRADO"
    )

    print(
        "🖼️",
        "FILE_ID DO TELEGRAM"
        if imagem_telegram
        else imagem or "NÃO ENCONTRADA"
    )

    # ========================================================
    # NÃO ACEITAR PRODUTO SEM NOME
    # ========================================================

    if not nome:

        enviar_mensagem(
            chat_id,
            """⚠️ NÃO CONSEGUI LER O PRODUTO

A Shopee não entregou o nome do produto nessa página.

O produto NÃO foi colocado na fila.

💡 Se quiser garantir a imagem e o nome, envie:

📸 FOTO DO PRODUTO

Nome do produto
Link da Shopee"""
        )

        return

    # ========================================================
    # ADICIONAR FILA
    # ========================================================

    adicionado, posicao = adicionar_na_fila(
        nome=nome,
        link=link,
        imagem=imagem,
        preco=preco,
        file_id=(
            imagem_telegram
            if imagem_telegram
            else None
        ),
        preco_antigo=preco_antigo,
        desconto=desconto,
        avaliacao=avaliacao,
        vendas=vendas,
        loja=loja
    )

    if not adicionado:

        enviar_mensagem(
            chat_id,
            "⚠️ Esse produto já está na fila."
        )

        return

    # ========================================================
    # RESPOSTA
    # ========================================================

    detalhes = []

    if preco:
        detalhes.append(
            f"💰 Preço: {preco}"
        )

    if desconto:
        detalhes.append(
            f"🔥 Desconto: {desconto}"
        )

    if avaliacao:
        detalhes.append(
            f"⭐ Avaliação: {avaliacao}/5"
        )

    if vendas:
        detalhes.append(
            f"📦 {vendas}"
        )

    if loja:
        detalhes.append(
            f"🏪 Loja: {loja}"
        )

    if not detalhes:

        detalhes.append(
            "ℹ️ Nenhuma informação adicional "
            "foi encontrada."
        )

    enviar_mensagem(
        chat_id,
        f"""✅ PRODUTO ADICIONADO!

🛍️ {nome}

{chr(10).join(detalhes)}

🖼️ Imagem:
{'GARANTIDA PELO TELEGRAM ✅' if imagem_telegram else ('encontrada ✅' if imagem else 'não encontrada ⚠️')}

📦 Posição na fila: {posicao}

⏰ Publicação automática a cada 12 minutos."""
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

        remetente = (
            mensagem
            .get("from", {})
            .get("id")
        )

        if str(remetente) != str(ADMIN_ID):

            return

    # ========================================================
    # FOTO + LEGENDA
    # ========================================================

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

Envie:

📸 FOTO

Nome do produto
Link da Shopee"""
            )

            return

        nome = linhas[0]

        link = linhas[1]

        if not link.startswith(
            ("http://", "https://")
        ):

            enviar_mensagem(
                chat_id,
                "❌ O link informado não é válido."
            )

            return

        # ====================================================
        # FILE ID
        # ====================================================

        file_id = (
            mensagem["photo"][-1]["file_id"]
        )

        print(
            "📸 FILE_ID RECEBIDO:",
            file_id
        )

        # ====================================================
        # RESPOSTA
        # ====================================================

        enviar_mensagem(
            chat_id,
            "⏳ Link recebido! Estou preparando o produto..."
        )

        # ====================================================
        # THREAD
        # ====================================================

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

    # ========================================================
    # TEXTO
    # ========================================================

    texto = (
        mensagem
        .get("text", "")
        .strip()
    )

    # ========================================================
    # START
    # ========================================================

    if texto == "/start":

        enviar_mensagem(
            chat_id,
            """🤖 ACHADOS DA SHOPEE

📸 ADICIONAR PRODUTO

Mande:

FOTO + legenda

Nome do produto
Link da Shopee

OU:

🔗 somente o link

O bot tenta encontrar:

🛒 Nome
💰 Preço
🏷️ Desconto
⭐ Avaliação
📦 Vendas
🏪 Loja
🖼️ Imagem

COMANDOS:

/fila
📦 Ver fila

/status
📊 Ver status

/pular
⏭️ Pular produto

/limparfila
🗑️ Limpar fila"""
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
    # STATUS
    # ========================================================

    if texto == "/status":

        mostrar_status(
            chat_id
        )

        return

    # ========================================================
    # PULAR
    # ========================================================

    if texto == "/pular":

        pular_produto(
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
        ("http://", "https://")
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

    if not link.startswith(
        ("http://", "https://")
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

                if (
                    dados.get(
                        "error_code"
                    ) == 401
                ):

                    print("")
                    print(
                        "🚨 TOKEN TELEGRAM INVÁLIDO!"
                    )
                    print(
                        "Verifique TELEGRAM_TOKEN "
                        "no Render."
                    )
                    print("")

                    time.sleep(10)

                elif (
                    dados.get(
                        "error_code"
                    ) == 409
                ):

                    print("")
                    print(
                        "🚨 CONFLITO 409!"
                    )
                    print(
                        "Existe outra instância "
                        "usando getUpdates."
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

    # ========================================================
    # TESTAR TOKEN
    # ========================================================

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

    # ========================================================
    # SERVIDOR
    # ========================================================

    threading.Thread(
        target=servidor_saude,
        daemon=True
    ).start()

    # ========================================================
    # PUBLICADOR
    # ========================================================

    threading.Thread(
        target=publicador_automatico,
        daemon=True
    ).start()

    print("")
    print(
        "======================================"
    )
    print(
        "✅ ACHADOS DA SHOPEE ONLINE"
    )
    print(
        "🖼️ SISTEMA DE IMAGEM ATIVO"
    )
    print(
        "📸 FILE_ID TELEGRAM ATIVO"
    )
    print(
        "📊 LEITOR DE INFORMAÇÕES ATIVO"
    )
    print(
        "🔒 CONTROLE ANTI-DUPLICAÇÃO ATIVO"
    )
    print(
        "⏰ 5 PRODUTOS POR HORA"
    )
    print(
        "⏱️ 1 PRODUTO A CADA 12 MINUTOS"
    )
    print(
        "======================================"
    )
    print("")

    iniciar_bot()
