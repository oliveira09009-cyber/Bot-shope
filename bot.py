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
# PUBLICAÇÃO
# ============================================================

# 5 produtos por hora = 1 produto a cada 12 minutos
INTERVALO = 12 * 60

# Quantidade máxima de tentativas para um produto sem imagem
MAX_TENTATIVAS = 3

# Tempo do loop interno
CICLO = 30


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
    preco=None
):

    fila = carregar_fila()

    for produto in fila:

        if produto.get("link") == link:

            return False, len(fila)

    produto = {
        "nome": nome or None,
        "link": link,
        "imagem": imagem,
        "preco": preco,
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
                or ofertas.get("highPrice")
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
        preco
    )


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
        if numero >= 1000000:

            numero /= 100000

        elif numero >= 10000:

            numero /= 100

        elif (
            numero >= 1000
            and numero.is_integer()
        ):

            numero /= 100

        if numero <= 0 or numero > 500000:
            return None

        return round(
            numero,
            2
        )

    except Exception:

        return None


def preco_valido(valor):

    numero = converter_preco(valor)

    return (
        numero is not None
        and 0.01 <= numero <= 500000
    )


def precos_do_html(html):

    if not html:
        return []

    candidatos = []

    padroes = [

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
        r'"display_price"\s*:\s*([0-9]+(?:\.[0-9]+)?)'
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

    # --------------------------------------------------------
    # OG IMAGE
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

    # --------------------------------------------------------
    # IMAGENS SUSERCONTENT
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

        r'"original_image"\s*:\s*"([^"]+)"'
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

    return candidatos


def encontrar_imagem_html(html):

    imagens = encontrar_imagens_html(
        html
    )

    for url in imagens:

        if (
            "susercontent.com/file/"
            in url.lower()
        ):

            return url

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
# FORMATAÇÕES
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


def nome_valido(nome):

    if not nome:
        return False

    texto = limpar_texto(
        nome
    )

    if not texto:
        return False

    if len(texto) < 5:
        return False

    if texto.lower() in (
        "produto",
        "produto shopee",
        "shopee",
        "oferta especial"
    ):
        return False

    return True


# ============================================================
# LEITOR SHOPEE
# ============================================================

def extrair_dados_shopee(link):

    print("")
    print("🔎 LENDO SHOPEE")
    print("🔗", link)

    nome = None
    preco = None
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
            "🔗 URL FINAL:",
            resposta.url
        )

        if resposta.status_code != 200:

            print(
                "❌ Shopee não respondeu HTTP 200"
            )

            return None, None, None

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
                preco_json
            ) = procurar_jsonld_produto(
                dados
            )

            if (
                not nome
                and nome_valido(nome_json)
            ):

                nome = nome_json

            if not imagem and imagem_json:

                imagem = imagem_json

            if preco_json:

                numero = converter_preco(
                    preco_json
                )

                if preco_valido(numero):

                    preco = numero

        except Exception as erro:

            print(
                "⚠️ Erro JSON-LD:",
                repr(erro)
            )

        # ----------------------------------------------------
        # PREÇO
        # ----------------------------------------------------

        if preco is None:

            precos = precos_do_html(
                html
            )

            if precos:

                # Menor preço encontrado
                preco = min(
                    precos
                )

        # ----------------------------------------------------
        # LIMPEZA NOME
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
        # PREÇO
        # ----------------------------------------------------

        if preco:

            preco = formatar_preco(
                preco
            )

        # ----------------------------------------------------
        # IMAGEM
        # ----------------------------------------------------

        if imagem:

            imagem = normalizar_url(
                imagem
            )

        print("")
        print("📋 RESULTADO DA LEITURA")
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

        return (
            nome,
            preco,
            imagem
        )

    except Exception as erro:

        print(
            "❌ Erro leitor Shopee:",
            repr(erro)
        )

        return (
            nome,
            preco,
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
            "⬇️ BAIXANDO IMAGEM..."
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
                "❌ URL não retornou uma imagem."
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
# PUBLICAR FOTO
# ============================================================

def enviar_foto_produto(
    imagem,
    legenda,
    teclado
):

    if not imagem:

        print(
            "❌ Não existe imagem para enviar."
        )

        return {
            "ok": False,
            "error": "Imagem inexistente."
        }

    # ========================================================
    # 1. FILE_ID TELEGRAM
    # ========================================================

    if not imagem.startswith(
        ("http://", "https://")
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
            timeout=25
        )

        if resultado.get("ok"):

            print(
                "✅ FOTO ENVIADA PELO FILE_ID"
            )

            return resultado

        print(
            "⚠️ FILE_ID falhou."
        )

    # ========================================================
    # 2. URL DIRETA
    # ========================================================

    if imagem.startswith(
        ("http://", "https://")
    ):

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
                "✅ FOTO ENVIADA PELA URL"
            )

            return resultado

        print(
            "⚠️ URL direta falhou."
        )

    # ========================================================
    # 3. DOWNLOAD + BYTES
    # ========================================================

    if imagem.startswith(
        ("http://", "https://")
    ):

        conteudo = baixar_imagem(
            imagem
        )

        if conteudo:

            print(
                "📤 Enviando bytes ao Telegram..."
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
                    "✅ FOTO ENVIADA PELOS BYTES"
                )

                return resultado

            print(
                "❌ Telegram rejeitou os bytes."
            )

    return {
        "ok": False,
        "error": "Todas as tentativas de envio da foto falharam."
    }


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

    nome = produto.get(
        "nome"
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

    tentativas = int(
        produto.get(
            "tentativas",
            0
        )
        or 0
    )

    print("")
    print(
        "======================================"
    )
    print(
        "📤 PUBLICANDO PRODUTO"
    )
    print(
        "🛒",
        nome or "SEM NOME"
    )
    print(
        "💰",
        preco or "SEM PREÇO"
    )
    print(
        "🖼️",
        imagem or "SEM IMAGEM"
    )
    print(
        "🔄 Tentativa:",
        tentativas + 1
    )

    # ========================================================
    # NÃO PUBLICAR PRODUTO VAZIO
    # ========================================================

    if not nome_valido(nome):

        print(
            "❌ Produto sem nome válido."
        )

        produto["tentativas"] = (
            tentativas + 1
        )

        fila[0] = produto

        # Depois de 3 tentativas, remove
        if produto["tentativas"] >= MAX_TENTATIVAS:

            fila.pop(0)

            salvar_fila(
                fila
            )

            print(
                "🗑️ Produto removido após "
                f"{MAX_TENTATIVAS} tentativas."
            )

        else:

            salvar_fila(
                fila
            )

        return False

    # ========================================================
    # NÃO PUBLICAR SEM LINK
    # ========================================================

    if not link or not link.startswith(
        ("http://", "https://")
    ):

        print(
            "❌ Produto sem link válido."
        )

        fila.pop(0)

        salvar_fila(
            fila
        )

        return False

    # ========================================================
    # LEGENDA
    # ========================================================

    if preco:

        linha_preco = (
            f"💰 {preco}"
        )

    else:

        linha_preco = (
            "💰 Confira o preço na Shopee"
        )

    legenda = f"""🔥 ACHADINHO DA SHOPEE 🔥

🛍️ {nome}

{linha_preco}

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

    resultado = enviar_foto_produto(
        imagem,
        legenda,
        teclado
    )

    # ========================================================
    # SUCESSO
    # ========================================================

    if resultado.get("ok"):

        fila.pop(0)

        salvar_fila(
            fila
        )

        print(
            "✅ PRODUTO PUBLICADO COM FOTO!"
        )

        print(
            "📦 Restam:",
            len(fila)
        )

        return True

    # ========================================================
    # FOTO FALHOU
    # ========================================================

    print(
        "❌ FOTO NÃO FOI PUBLICADA."
    )

    produto["tentativas"] = (
        tentativas + 1
    )

    fila[0] = produto

    # --------------------------------------------------------
    # IMPORTANTE:
    # NÃO PUBLICAR TEXTO AUTOMATICAMENTE
    # --------------------------------------------------------

    if produto["tentativas"] >= MAX_TENTATIVAS:

        fila.pop(0)

        salvar_fila(
            fila
        )

        print(
            "🗑️ Produto removido após "
            f"{MAX_TENTATIVAS} falhas."
        )

    else:

        salvar_fila(
            fila
        )

        print(
            "⏸️ Produto continuará na fila."
        )

    return False


# ============================================================
# CONTROLE DO INTERVALO
# ============================================================

def obter_ultimo_post():

    try:

        valor = redis_cmd(
            "GET",
            "ultimo_post"
        )

        if not valor:
            return 0

        return float(
            valor
        )

    except Exception as erro:

        print(
            "⚠️ Erro lendo ultimo_post:",
            repr(erro)
        )

        return 0


def registrar_ultima_tentativa():

    redis_cmd(
        "SET",
        "ultimo_post",
        str(
            time.time()
        )
    )


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

            ultimo = obter_ultimo_post()

            agora = time.time()

            tempo_passado = (
                agora - ultimo
            )

            if tempo_passado >= INTERVALO:

                fila = carregar_fila()

                if fila:

                    # ------------------------------------------------
                    # REGISTRA A TENTATIVA ANTES DE PUBLICAR
                    # ------------------------------------------------
                    #
                    # Isso impede que um erro de foto faça o bot
                    # disparar novamente em poucos segundos.
                    #
                    registrar_ultima_tentativa()

                    publicar_proximo()

                else:

                    print(
                        "📭 Sem produtos para publicar."
                    )

                    # Não precisa marcar horário quando
                    # não há produto.

            else:

                restante = int(
                    INTERVALO
                    - tempo_passado
                )

                print(
                    f"⏳ Próxima publicação em "
                    f"{restante // 60}m "
                    f"{restante % 60}s"
                )

        except Exception as erro:

            print(
                "❌ Erro publicador:",
                repr(erro)
            )

            # Mesmo em erro, impede loop agressivo
            registrar_ultima_tentativa()

        time.sleep(
            CICLO
        )


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
            f"{produto.get('preco') or 'não encontrado'}\n"
            f"🖼️ "
            f"{'SIM' if produto.get('imagem') else 'NÃO'}\n"
            f"🔄 Tentativas: "
            f"{produto.get('tentativas', 0)}\n"
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

    # ========================================================
    # FOTO DO TELEGRAM
    # ========================================================

    if imagem_telegram:

        print(
            "🖼️ FOTO DO TELEGRAM RECEBIDA."
        )

        print(
            "📌 File ID:",
            imagem_telegram
        )

    # ========================================================
    # LEITURA SHOPEE
    # ========================================================

    (
        nome_shopee,
        preco,
        imagem_shopee
    ) = extrair_dados_shopee(
        link
    )

    # ========================================================
    # NOME
    # ========================================================

    if nome_shopee:

        nome = nome_shopee

    # ========================================================
    # IMAGEM
    #
    # FOTO ENVIADA PELO USUÁRIO TEM PRIORIDADE
    # ========================================================

    if not imagem:

        imagem = imagem_shopee

    # ========================================================
    # VALIDAÇÃO
    # ========================================================

    if not nome_valido(nome):

        print(
            "⚠️ Nome não encontrado."
        )

        # Se foi enviada foto, ainda podemos usar
        # o nome informado pelo usuário.
        if not nome_informado:

            enviar_mensagem(
                chat_id,
                """⚠️ Não consegui identificar o nome do produto na Shopee.

Envie novamente no formato:

Nome do produto
Link da Shopee

Ou envie uma FOTO com a legenda:

Nome do produto
Link da Shopee"""
            )

            return

    # ========================================================
    # ADICIONAR NA FILA
    # ========================================================

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

🛍️ {nome}

💰 Preço: {preco or 'não encontrado'}

🖼️ Imagem: {'SIM ✅' if imagem else 'NÃO ⚠️'}

📦 Posição na fila: {posicao}

⏰ Publicação: a cada 12 minutos."""
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

FOTO

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
        # FILE_ID
        # ====================================================

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

Você pode enviar:

1️⃣ FOTO + legenda

Nome do produto
Link da Shopee

OU

2️⃣ Somente o link

🔎 O bot tenta encontrar:

🛍️ Nome
💰 Preço
🖼️ Imagem

COMANDOS:

/fila
📦 Ver produtos na fila

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
                "https://api.telegram.org/"
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
                        "🚨 TOKEN TELEGRAM INVÁLIDO!"
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

    # ========================================================
    # TOKEN
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
        "⚡ MODO ROBUSTO ATIVO"
    )
    print(
        "🖼️ FOTO TELEGRAM + SHOPEE"
    )
    print(
        "🛡️ PROTEÇÃO CONTRA POSTAGEM VAZIA"
    )
    print(
        "⏰ 5 PRODUTOS POR HORA"
    )
    print(
        "⏱️ 1 PRODUTO A CADA 12 MINUTOS"
    )
    print(
        "🧵 PROCESSAMENTO EM SEGUNDO PLANO"
    )
    print(
        "======================================"
    )
    print("")

    iniciar_bot()
