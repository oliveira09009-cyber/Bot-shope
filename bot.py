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
# 720 segundos = 12 minutos
# ============================================================

INTERVALO = 720


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


# ============================================================
# FILA
# ============================================================

def carregar_fila():

    try:

        dado = redis_cmd("GET", "fila")

        if not dado:
            return []

        fila = json.loads(dado)

        if isinstance(fila, list):
            return fila

    except Exception as erro:

        print("❌ Erro carregando fila:", repr(erro))

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
# FOTO PENDENTE
#
# Guarda a foto quando o usuário manda primeiro a imagem
# e somente depois manda o link.
# ============================================================

def chave_foto_pendente(chat_id):

    return f"foto_pendente:{chat_id}"


def salvar_foto_pendente(
    chat_id,
    file_id,
    nome=None
):

    dados = {
        "file_id": file_id,
        "nome": nome
    }

    redis_cmd(
        "SET",
        chave_foto_pendente(chat_id),
        json.dumps(
            dados,
            ensure_ascii=False
        )
    )


def carregar_foto_pendente(chat_id):

    try:

        dado = redis_cmd(
            "GET",
            chave_foto_pendente(chat_id)
        )

        if not dado:
            return None

        return json.loads(dado)

    except Exception as erro:

        print(
            "❌ Erro lendo foto pendente:",
            repr(erro)
        )

        return None


def apagar_foto_pendente(chat_id):

    try:

        redis_cmd(
            "DEL",
            chave_foto_pendente(chat_id)
        )

    except Exception as erro:

        print(
            "⚠️ Erro apagando foto pendente:",
            repr(erro)
        )


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

        # Valores internos comuns da Shopee
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


# ============================================================
# PREÇOS DO HTML
# ============================================================

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

        r'"original_image"\s*:\s*"([^"]+)"'
    ]

    for padrao in padroes:

        encontrados = re.findall(
            padrao,
            html,
            re.IGNORECASE
        )

        for encontrado in encontrados:

            adicionar(encontrado)

            if len(candidatos) >= 30:
                break

        if len(candidatos) >= 30:
            break

    # ========================================================
    # REMOVER DUPLICADAS
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

    imagens = encontrar_imagens_html(html)

    for url in imagens:

        if "susercontent.com/file/" in url.lower():

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
# FORMATAÇÃO DE PREÇO
# ============================================================

def formatar_preco(valor):

    numero = converter_preco(valor)

    if numero is None:
        return None

    return (
        f"R$ {numero:,.2f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )


# ============================================================
# CALCULAR DESCONTO
# ============================================================

def calcular_desconto(
    preco_antigo,
    preco_atual
):

    antigo = converter_preco(
        preco_antigo
    )

    atual = converter_preco(
        preco_atual
    )

    if not antigo or not atual:
        return None

    if antigo <= atual:
        return None

    desconto = (
        (antigo - atual)
        / antigo
        * 100
    )

    return round(
        desconto
    )


# ============================================================
# LEITOR SHOPEE
# ============================================================

def extrair_dados_shopee(link):

    print("")
    print("🔎 Lendo Shopee...")
    print("🔗", link)

    nome = None
    preco = None
    imagem = None
    preco_antigo = None

    try:

        sessao = nova_sessao()

        resposta = sessao.get(
            link,
            timeout=20,
            allow_redirects=True
        )

        print(
            "🌐 HTTP:",
            resposta.status_code
        )

        if resposta.status_code != 200:

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

        # ====================================================
        # NOME
        # ====================================================

        nome = encontrar_nome_html(
            html
        )

        # ====================================================
        # IMAGEM SHOPEE
        # ====================================================

        imagem = encontrar_imagem_html(
            html
        )

        # ====================================================
        # JSON-LD
        # ====================================================

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

            if not nome and nome_json:

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

        # ====================================================
        # PREÇOS
        # ====================================================

        precos = precos_do_html(
            html
        )

        if precos:

            precos = sorted(
                set(precos)
            )

            # Menor valor encontrado
            preco = preco or precos[0]

            # Se houver outro valor maior,
            # tenta tratar como preço anterior.
            if len(precos) >= 2:

                maiores = [
                    x for x in precos
                    if x > preco
                ]

                if maiores:

                    preco_antigo = max(
                        maiores
                    )

        # ====================================================
        # LIMPEZA DO NOME
        # ====================================================

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

        # ====================================================
        # FORMATAÇÃO
        # ====================================================

        preco_formatado = (
            formatar_preco(preco)
            if preco
            else None
        )

        preco_antigo_formatado = (
            formatar_preco(preco_antigo)
            if preco_antigo
            else None
        )

        desconto = calcular_desconto(
            preco_antigo,
            preco
        )

        if imagem:

            imagem = normalizar_url(
                imagem
            )

        # ====================================================
        # LOG
        # ====================================================

        print(
            "🛒 Nome:",
            nome or "não encontrado"
        )

        print(
            "💰 Preço:",
            preco_formatado or "não encontrado"
        )

        print(
            "🏷️ Preço anterior:",
            preco_antigo_formatado
            or "não encontrado"
        )

        print(
            "🔥 Desconto:",
            f"{desconto}%"
            if desconto is not None
            else "não encontrado"
        )

        print(
            "🖼️ Imagem Shopee:",
            imagem or "não encontrada"
        )

        return (
            nome,
            preco_formatado,
            imagem,
            preco_antigo_formatado,
            desconto
        )

    except Exception as erro:

        print(
            "❌ Erro leitor Shopee:",
            repr(erro)
        )

        return (
            nome,
            preco,
            imagem,
            preco_antigo,
            None
        )


# ============================================================
# BAIXAR IMAGEM
# ============================================================

def baixar_imagem(url):

    if not url:
        return None

    try:

        print(
            "⬇️ Baixando imagem Shopee..."
        )

        sessao = nova_sessao()

        resposta = sessao.get(
            url,
            timeout=20,
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
            content_type.startswith("image/")
            or any(
                conteudo.startswith(x)
                for x in assinaturas
            )
        )

        if not parece_imagem:

            print(
                "❌ Servidor não retornou imagem."
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

    # ========================================================
    # 1. FILE_ID DO TELEGRAM
    # ========================================================

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
            "📸 Usando FOTO ORIGINAL DO TELEGRAM..."
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
                "✅ FOTO DO TELEGRAM PUBLICADA!"
            )

            return resultado

        print(
            "⚠️ FILE_ID não funcionou."
        )

    # ========================================================
    # 2. URL DA SHOPEE
    # ========================================================

    if imagem:

        print(
            "🌐 Tentando imagem da Shopee..."
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
                "✅ FOTO SHOPEE PUBLICADA!"
            )

            return resultado

        print(
            "⚠️ URL direta falhou."
        )

    # ========================================================
    # 3. BAIXAR + ENVIAR
    # ========================================================

    if imagem:

        conteudo = baixar_imagem(
            imagem
        )

        if conteudo:

            try:

                print(
                    "📤 Enviando bytes da imagem..."
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
                    timeout=30
                )

                if resultado.get("ok"):

                    print(
                        "✅ FOTO ENVIADA PELOS BYTES!"
                    )

                    return resultado

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
# MONTAR LEGENDA
# ============================================================

def montar_legenda(produto):

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

    # ========================================================
    # PREÇO
    # ========================================================

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
            "💰 OFERTA ESPECIAL"
        )

    # ========================================================
    # DESCONTO
    # ========================================================

    if desconto is not None:

        linhas.append(
            f"🔥 {desconto}% OFF"
        )

    linhas.extend([
        "",
        "⚡ Aproveite enquanto estiver disponível!",
        "",
        "👇 Clique abaixo para conferir:"
    ])

    return "\n".join(
        linhas
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
        or "Produto Shopee"
    )

    link = produto.get(
        "link"
    )

    imagem = produto.get(
        "imagem"
    )

    legenda = montar_legenda(
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
        "📤 PUBLICANDO:"
    )

    print(
        "🛒",
        nome
    )

    print(
        "🖼️",
        imagem or "sem imagem"
    )

    print(
        "💰",
        produto.get("preco")
        or "sem preço"
    )

    print(
        "🏷️",
        produto.get("preco_antigo")
        or "sem preço anterior"
    )

    print(
        "🔥",
        produto.get("desconto")
        if produto.get("desconto") is not None
        else "sem desconto"
    )

    # ========================================================
    # TENTAR FOTO
    # ========================================================

    resultado = enviar_foto_produto(
        imagem,
        legenda,
        teclado
    )

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
        "⚠️ Foto falhou."
    )

    print(
        "➡️ Tentando publicar texto..."
    )

    texto_resultado = enviar_texto_produto(
        legenda,
        teclado
    )

    if texto_resultado.get("ok"):

        print(
            "⚠️ TEXTO PUBLICADO."
        )

        print(
            "📦 Produto permanece na fila."
        )

        return False

    print(
        "❌ Publicação falhou."
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

            if (
                agora - ultimo
                >= INTERVALO
            ):

                sucesso = publicar_proximo()

                # ====================================================
                # IMPORTANTE
                #
                # Só atualiza o relógio se houve FOTO publicada.
                # ====================================================

                if sucesso:

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

        imagem = produto.get(
            "imagem"
        )

        if imagem:

            tipo_imagem = (
                "TELEGRAM"
                if not imagem.startswith(
                    (
                        "http://",
                        "https://"
                    )
                )
                else "SHOPEE"
            )

        else:

            tipo_imagem = "NÃO"

        mensagem += (
            f"{numero}. 🛒 "
            f"{produto.get('nome', 'Produto')}\n"
            f"💰 "
            f"{produto.get('preco') or 'não encontrado'}\n"
            f"🏷️ "
            f"{produto.get('preco_antigo') or 'não encontrado'}\n"
            f"🔥 "
            f"{str(produto.get('desconto')) + '% OFF' if produto.get('desconto') is not None else 'não encontrado'}\n"
            f"🖼️ {tipo_imagem}\n"
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
# STATUS
# ============================================================

def mostrar_status(chat_id):

    fila = carregar_fila()

    ultimo = redis_cmd(
        "GET",
        "ultimo_post"
    )

    try:

        ultimo = float(
            ultimo or 0
        )

    except Exception:

        ultimo = 0

    if ultimo:

        proximo = max(
            0,
            int(
                INTERVALO
                - (
                    time.time()
                    - ultimo
                )
            )
        )

        minutos = proximo // 60
        segundos = proximo % 60

        tempo = (
            f"{minutos} min "
            f"{segundos} seg"
        )

    else:

        tempo = "pronto para publicar"

    mensagem = f"""🤖 STATUS DO BOT

🟢 Bot online

📦 Produtos na fila: {len(fila)}

⏰ Intervalo: 12 minutos

⏳ Próxima publicação:
{tempo}

📸 Fotos:
O bot prioriza a foto enviada pelo Telegram.

💰 Leitura:
Nome, preço e desconto são buscados quando disponíveis."""

    enviar_mensagem(
        chat_id,
        mensagem
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

    print(
        "🔗",
        link
    )

    nome = nome_informado

    imagem = imagem_telegram

    preco = None

    preco_antigo = None

    desconto = None

    # ========================================================
    # LEITURA SHOPEE
    # ========================================================

    (
        nome_shopee,
        preco_shopee,
        imagem_shopee,
        preco_antigo_shopee,
        desconto_shopee
    ) = extrair_dados_shopee(
        link
    )

    # ========================================================
    # NOME
    # ========================================================

    if nome_shopee:

        nome = nome_shopee

    # ========================================================
    # FOTO
    #
    # FOTO TELEGRAM TEM PRIORIDADE ABSOLUTA.
    # ========================================================

    if imagem_telegram:

        imagem = imagem_telegram

        print(
            "📸 FOTO TELEGRAM PRIORIZADA."
        )

    elif imagem_shopee:

        imagem = imagem_shopee

        print(
            "🖼️ Usando imagem encontrada na Shopee."
        )

    else:

        print(
            "⚠️ Nenhuma imagem encontrada."
        )

    # ========================================================
    # PREÇOS
    # ========================================================

    preco = preco_shopee

    preco_antigo = preco_antigo_shopee

    desconto = desconto_shopee

    # ========================================================
    # ADICIONAR FILA
    # ========================================================

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

    # ========================================================
    # RESPOSTA
    # ========================================================

    preco_msg = (
        preco
        if preco
        else "não encontrado"
    )

    antigo_msg = (
        preco_antigo
        if preco_antigo
        else "não encontrado"
    )

    desconto_msg = (
        f"{desconto}% OFF"
        if desconto is not None
        else "não encontrado"
    )

    imagem_msg = (
        "Telegram ✅"
        if imagem_telegram
        else (
            "Shopee ✅"
            if imagem_shopee
            else "não encontrada ⚠️"
        )
    )

    enviar_mensagem(
        chat_id,
        f"""✅ PRODUTO ADICIONADO!

🛍️ {nome or 'Produto Shopee'}

💰 Por: {preco_msg}

🏷️ De: {antigo_msg}

🔥 Desconto: {desconto_msg}

🖼️ Imagem: {imagem_msg}

📦 Posição: {posicao}

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
    # FOTO
    # ========================================================

    if "photo" in mensagem:

        file_id = (
            mensagem["photo"][-1]["file_id"]
        )

        legenda = (
            mensagem
            .get("caption", "")
            .strip()
        )

        print("")
        print(
            "📸 FOTO RECEBIDA"
        )

        print(
            "🆔 FILE_ID:",
            file_id
        )

        # ====================================================
        # PROCURAR LINK NA LEGENDA
        # ====================================================

        links = re.findall(
            r'https?://\S+',
            legenda
        )

        if links:

            link = links[0].rstrip(
                ".,;)"
            )

            linhas = [
                linha.strip()
                for linha in legenda.split("\n")
                if linha.strip()
            ]

            nome = None

            if linhas:

                if linhas[0] != link:

                    nome = linhas[0]

            enviar_mensagem(
                chat_id,
                "⏳ Foto + link recebidos! Estou preparando o produto..."
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

        # ====================================================
        # SEM LINK
        #
        # GUARDA FOTO PARA O PRÓXIMO LINK
        # ====================================================

        nome = None

        linhas = [
            linha.strip()
            for linha in legenda.split("\n")
            if linha.strip()
        ]

        if linhas:

            nome = linhas[0]

        salvar_foto_pendente(
            chat_id,
            file_id,
            nome
        )

        enviar_mensagem(
            chat_id,
            """📸 FOTO RECEBIDA E GUARDADA!

Agora envie o 🔗 LINK DA SHOPEE.

Exemplo:

https://shopee.com.br/...

O bot vai juntar a foto com o link automaticamente."""
        )

        return

    # ========================================================
    # TEXTO
    # ========================================================

    texto = (
        mensagem
        .get("text", "")
        .strip()
    )

    if not texto:
        return

    # ========================================================
    # START
    # ========================================================

    if texto == "/start":

        enviar_mensagem(
            chat_id,
            """🤖 ACHADOS DA SHOPEE

📸 COMO ADICIONAR PRODUTO

Você pode enviar:

1️⃣ FOTO

Depois:

2️⃣ LINK DA SHOPEE

OU enviar tudo junto:

📸 FOTO + legenda

Nome do produto
Link da Shopee

OU simplesmente:

🔗 LINK DA SHOPEE

O sistema tenta encontrar:

🛍️ Nome
💰 Preço
🏷️ Preço anterior
🔥 Desconto
🖼️ Imagem

COMANDOS:

/fila
📦 Ver produtos

/status
📊 Ver status

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
    # LIMPAR
    # ========================================================

    if texto == "/limparfila":

        limpar_fila(
            chat_id
        )

        return

    # ========================================================
    # PROCURAR LINK
    # ========================================================

    links = re.findall(
        r'https?://\S+',
        texto
    )

    if links:

        link = links[0].rstrip(
            ".,;)"
        )

        # ====================================================
        # FOTO PENDENTE
        # ====================================================

        foto_pendente = (
            carregar_foto_pendente(
                chat_id
            )
        )

        if foto_pendente:

            file_id = (
                foto_pendente.get(
                    "file_id"
                )
            )

            nome_pendente = (
                foto_pendente.get(
                    "nome"
                )
            )

            # Apaga imediatamente para não reutilizar
            apagar_foto_pendente(
                chat_id
            )

            enviar_mensagem(
                chat_id,
                "⏳ Foto + link associados! Estou preparando o produto..."
            )

            threading.Thread(
                target=processar_link_background,
                args=(
                    chat_id,
                    link,
                    nome_pendente,
                    file_id
                ),
                daemon=True
            ).start()

            return

        # ====================================================
        # LINK NORMAL
        # ====================================================

        linhas = [
            linha.strip()
            for linha in texto.split("\n")
            if linha.strip()
        ]

        nome = None

        if len(linhas) >= 2:

            if linhas[0] != link:

                nome = linhas[0]

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

        return

    # ========================================================
    # NOME + LINK NÃO ENCONTRADO
    # ========================================================

    linhas = [
        linha.strip()
        for linha in texto.split("\n")
        if linha.strip()
    ]

    if len(linhas) < 2:

        enviar_mensagem(
            chat_id,
            """❌ NÃO ENCONTREI UM LINK.

Envie:

🔗 Link da Shopee

OU

📸 Foto
depois
🔗 Link da Shopee"""
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
        "⚡ MODO ROBUSTO ATIVO"
    )

    print(
        "📸 FOTO TELEGRAM PRIORITÁRIA"
    )

    print(
        "🔗 FOTO + LINK SEPARADOS"
    )

    print(
        "💰 PREÇO / DESCONTO"
    )

    print(
        "🧵 PROCESSAMENTO EM SEGUNDO PLANO"
    )

    print(
        "⏰ 1 PRODUTO A CADA 12 MINUTOS"
    )

    print(
        "======================================"
    )

    print("")

    iniciar_bot()
