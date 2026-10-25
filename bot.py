
import requests
import json
import os
import time
import threading
import re
import html
from urllib.parse import urljoin, urlsplit, urlunsplit
from http.server import BaseHTTPRequestHandler, HTTPServer


# ============================================================
# CONFIGURAÇÕES
# ============================================================

TOKEN = os.environ["TELEGRAM_TOKEN"]

CHAT_ID = os.environ.get(
    "CHAT_ID",
    "-1003905816059"
)

REDIS_URL = os.environ["UPSTASH_REDIS_REST_URL"]

REDIS_TOKEN = os.environ["UPSTASH_REDIS_REST_TOKEN"]

ADMIN_ID = os.environ.get("ADMIN_ID")

# ============================================================
# 5 PRODUTOS POR HORA
# 720 segundos = 12 minutos
# ============================================================

INTERVALO = 720


# ============================================================
# HEADERS
# ============================================================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 13; SM-G991B) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/131.0.0.0 Mobile Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,"
        "*/*;q=0.8"
    ),
    "Connection": "keep-alive",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache"
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

    dado = redis_cmd("GET", "fila")

    if not dado:
        return []

    try:

        return json.loads(dado)

    except Exception as erro:

        print("❌ Erro ao ler fila:", erro)

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

    fila.append(
        {
            "nome": nome,
            "link": link,
            "imagem": imagem,
            "preco": preco,
            "link_resolvido": None
        }
    )

    salvar_fila(fila)

    return True, len(fila)


# ============================================================
# TELEGRAM
# ============================================================

def telegram_post(
    metodo,
    data=None,
    files=None
):

    url = (
        f"https://api.telegram.org/"
        f"bot{TOKEN}/{metodo}"
    )

    try:

        resposta = requests.post(
            url,
            data=data,
            files=files,
            timeout=60
        )

        try:

            return resposta.json()

        except Exception:

            return {
                "ok": False,
                "description": resposta.text
            }

    except Exception as erro:

        print(
            f"❌ Erro Telegram {metodo}:",
            erro
        )

        return {
            "ok": False,
            "description": str(erro)
        }


def enviar_mensagem(
    chat_id,
    texto
):

    return telegram_post(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": texto
        }
    )


# ============================================================
# VALIDAR BYTES DA IMAGEM
# ============================================================

def imagem_valida_bytes(conteudo):

    if not conteudo:
        return False

    if len(conteudo) < 1000:
        return False

    # JPEG
    if conteudo[:3] == b"\xff\xd8\xff":
        return True

    # PNG
    if conteudo[:8] == b"\x89PNG\r\n\x1a\n":
        return True

    # GIF
    if conteudo[:6] in (
        b"GIF87a",
        b"GIF89a"
    ):
        return True

    # WEBP
    if (
        conteudo[:4] == b"RIFF"
        and conteudo[8:12] == b"WEBP"
    ):
        return True

    # BMP
    if conteudo[:2] == b"BM":
        return True

    return False


# ============================================================
# BAIXAR IMAGEM
# ============================================================

def baixar_imagem(
    url,
    referer=None
):

    if not url:
        return None

    try:

        headers = dict(HEADERS)

        headers["Accept"] = (
            "image/avif,image/webp,"
            "image/apng,image/jpeg,"
            "image/png,*/*;q=0.8"
        )

        if referer:
            headers["Referer"] = referer

        resposta = requests.get(
            url,
            headers=headers,
            timeout=30,
            allow_redirects=True
        )

        print(
            "🖼️ Download imagem:",
            resposta.status_code,
            resposta.headers.get(
                "Content-Type",
                ""
            ),
            len(resposta.content)
        )

        if resposta.status_code != 200:

            return None

        if not imagem_valida_bytes(
            resposta.content
        ):

            print(
                "⚠️ Os bytes recebidos "
                "não parecem ser uma imagem."
            )

            return None

        # Limite seguro para sendPhoto
        if len(resposta.content) > 10 * 1024 * 1024:

            print(
                "⚠️ Imagem maior que 10 MB."
            )

            return None

        print(
            "✅ Imagem baixada e validada:",
            len(resposta.content),
            "bytes"
        )

        return resposta.content

    except Exception as erro:

        print(
            "⚠️ Erro ao baixar imagem:",
            erro
        )

        return None


# ============================================================
# NORMALIZAR LINK
# ============================================================

def normalizar_link(link):

    if not link:
        return None

    try:

        link = html.unescape(
            str(link)
        ).strip()

        link = re.sub(
            r"[\u200b-\u200f"
            r"\u202a-\u202e"
            r"\ufeff]",
            "",
            link
        )

        if link.startswith(
            "www.shopee.com.br"
        ):

            link = "https://" + link

        elif link.startswith(
            "s.shopee.com.br"
        ):

            link = "https://" + link

        elif link.startswith(
            "shopee.com.br"
        ):

            link = "https://" + link

        if not re.match(
            r"^https?://",
            link,
            re.IGNORECASE
        ):

            return None

        link = link.rstrip(
            ".,;:!?)]}>\"'"
        )

        partes = urlsplit(link)

        if not partes.netloc:
            return None

        dominio = partes.netloc.lower()

        if dominio.startswith("www."):
            dominio_sem_www = dominio[4:]
        else:
            dominio_sem_www = dominio

        dominios_validos = [
            "shopee.com.br",
            "s.shopee.com.br",
            "shopee.com",
            "s.shopee.com"
        ]

        if dominio_sem_www not in dominios_validos:

            print(
                "⚠️ Domínio não reconhecido:",
                dominio
            )

            return None

        return urlunsplit(
            (
                "https",
                dominio,
                partes.path,
                partes.query,
                ""
            )
        )

    except Exception as erro:

        print(
            "❌ Erro ao normalizar link:",
            erro
        )

        return None


# ============================================================
# ENCONTRAR LINK
# ============================================================

def encontrar_link(texto):

    if not texto:
        return None

    encontrados = re.findall(
        r'https?://[^\s<>"\']+',
        texto,
        re.IGNORECASE
    )

    if not encontrados:
        return None

    for encontrado in encontrados:

        link = normalizar_link(
            encontrado
        )

        if link:

            print(
                "🔗 Link normalizado:",
                link
            )

            return link

    return None


# ============================================================
# RESOLVER LINK
# ============================================================

def resolver_link(link):

    link_original = normalizar_link(
        link
    )

    if not link_original:
        return None

    try:

        print(
            "🔗 Link original:",
            link_original
        )

        resposta = requests.get(
            link_original,
            headers=HEADERS,
            allow_redirects=True,
            timeout=25
        )

        print(
            "🔗 URL final recebida:",
            resposta.url
        )

        final = normalizar_link(
            resposta.url
        )

        if final:

            print(
                "🔗 Link resolvido:",
                final
            )

            return final

        print(
            "⚠️ Não foi possível "
            "normalizar destino."
        )

        return link_original

    except Exception as erro:

        print(
            "⚠️ Erro ao resolver link:",
            erro
        )

        return link_original


# ============================================================
# BAIXAR PÁGINA
# ============================================================

def baixar_pagina(link):

    try:

        resposta = requests.get(
            link,
            headers=HEADERS,
            allow_redirects=True,
            timeout=35
        )

        print(
            "🌐 Status Shopee:",
            resposta.status_code
        )

        print(
            "📄 HTML recebido:",
            len(resposta.text),
            "caracteres"
        )

        print(
            "🌐 URL final:",
            resposta.url
        )

        if resposta.status_code != 200:

            print(
                "⚠️ Shopee retornou:",
                resposta.status_code
            )

            return None, resposta.url

        return (
            resposta.text,
            resposta.url
        )

    except Exception as erro:

        print(
            "❌ Erro ao baixar Shopee:",
            erro
        )

        return None, link


# ============================================================
# LIMPAR TEXTO
# ============================================================

def limpar_texto(texto):

    if not texto:
        return ""

    texto = html.unescape(
        texto
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


# ============================================================
# CONVERTER PREÇO
# ============================================================

def converter_preco(valor):

    if valor is None:
        return None

    valor = str(valor).strip()

    valor = valor.replace(
        "R$",
        ""
    )

    valor = valor.replace(
        " ",
        ""
    )

    if "," in valor:

        valor = valor.replace(
            ".",
            ""
        )

        valor = valor.replace(
            ",",
            "."
        )

    try:

        numero = float(valor)

    except Exception:

        return None

    if numero <= 0:
        return None

    # Valores em centavos
    if numero >= 100000:

        numero = numero / 100

    if numero >= 100000:

        return None

    return numero


def formatar_preco(numero):

    if numero is None:
        return None

    valor = (
        f"{numero:,.2f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )

    return f"R$ {valor}"


# ============================================================
# EXTRAIR PREÇOS
# ============================================================

def extrair_precos(texto):

    if not texto:
        return []

    texto = html.unescape(
        texto
    )

    precos = []

    padroes = [

        r"R\$\s*"
        r"([0-9]{1,3}"
        r"(?:\.[0-9]{3})*,[0-9]{2})",

        r"R\$\s*"
        r"([0-9]+"
        r"(?:\.[0-9]{2})?)",
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

            if numero is not None:

                precos.append(
                    numero
                )

    campos = [
        "price",
        "salePrice",
        "currentPrice",
        "priceMin",
        "priceMax",
        "discountPrice",
        "finalPrice"
    ]

    for campo in campos:

        padroes_json = [

            rf'"{campo}"\s*:\s*"([^"]+)"',

            rf'"{campo}"\s*:\s*'
            rf'([0-9]+(?:\.[0-9]+)?)',
        ]

        for padrao in padroes_json:

            encontrados = re.findall(
                padrao,
                texto,
                re.IGNORECASE
            )

            for valor in encontrados:

                numero = converter_preco(
                    valor
                )

                if numero is not None:

                    precos.append(
                        numero
                    )

    return sorted(
        set(
            round(p, 2)
            for p in precos
        )
    )


# ============================================================
# EXTRAIR FAIXA DE PREÇO
# ============================================================

def extrair_faixa_preco(texto):

    if not texto:
        return None

    texto = html.unescape(
        texto
    )

    padroes = [

        r"R\$\s*"
        r"([0-9]{1,3}"
        r"(?:\.[0-9]{3})*,[0-9]{2})"
        r"\s*[-–—]\s*"
        r"R\$\s*"
        r"([0-9]{1,3}"
        r"(?:\.[0-9]{3})*,[0-9]{2})",

        r"R\$\s*"
        r"([0-9]+"
        r"(?:[.,][0-9]{2})?)"
        r"\s*[-–—]\s*"
        r"R\$\s*"
        r"([0-9]+"
        r"(?:[.,][0-9]{2})?)",
    ]

    for padrao in padroes:

        encontrados = re.findall(
            padrao,
            texto,
            re.IGNORECASE
        )

        for primeiro, segundo in encontrados:

            p1 = converter_preco(
                primeiro
            )

            p2 = converter_preco(
                segundo
            )

            if p1 and p2:

                return {
                    "menor": min(p1, p2),
                    "maior": max(p1, p2)
                }

    return None


# ============================================================
# EXTRAIR NOME
# ============================================================

def extrair_nome(
    html_text,
    url
):

    if not html_text:
        return None

    candidatos = []

    padroes = [

        r'<meta[^>]+property=["\']'
        r'og:title["\'][^>]+content=["\']'
        r'([^"\']+)',

        r'<meta[^>]+content=["\']'
        r'([^"\']+)["\'][^>]+property=["\']'
        r'og:title["\']',

        r'<meta[^>]+name=["\']'
        r'twitter:title["\'][^>]+content=["\']'
        r'([^"\']+)',

        r'<meta[^>]+content=["\']'
        r'([^"\']+)["\'][^>]+name=["\']'
        r'twitter:title["\']',
    ]

    for padrao in padroes:

        encontrados = re.findall(
            padrao,
            html_text,
            re.IGNORECASE
        )

        for nome in encontrados:

            nome = limpar_texto(
                nome
            )

            if len(nome) >= 10:

                candidatos.append(
                    nome
                )

    encontrado = re.search(
        r"<title[^>]*>(.*?)</title>",
        html_text,
        re.IGNORECASE |
        re.DOTALL
    )

    if encontrado:

        nome = limpar_texto(
            encontrado.group(1)
        )

        if len(nome) >= 10:

            candidatos.append(
                nome
            )

    blocos = re.findall(
        r'<script[^>]+type=["\']'
        r'application/ld\+json["\'][^>]*>'
        r'(.*?)</script>',
        html_text,
        re.IGNORECASE |
        re.DOTALL
    )

    for bloco in blocos:

        try:

            dados = json.loads(
                html.unescape(bloco)
            )

            objetos = []

            if isinstance(
                dados,
                list
            ):

                objetos = dados

            elif isinstance(
                dados,
                dict
            ):

                objetos = [dados]

            for objeto in objetos:

                if not isinstance(
                    objeto,
                    dict
                ):
                    continue

                nome = objeto.get(
                    "name"
                )

                if nome:

                    nome = limpar_texto(
                        nome
                    )

                    if len(nome) >= 10:

                        candidatos.append(
                            nome
                        )

        except Exception:

            pass

    nomes_invalidos = [

        "shopee brasil",
        "ofertas incríveis",
        "ofertas incriveis",
        "melhores preços do mercado",
        "melhores precos do mercado",
        "compre na shopee",
        "shopee",
    ]

    candidatos_filtrados = []

    for nome in candidatos:

        nome = re.sub(
            r"\s*[|\-–—]\s*"
            r"shopee(\s+brasil)?\s*$",
            "",
            nome,
            flags=re.IGNORECASE
        ).strip()

        nome_lower = nome.lower()

        if any(
            invalido in nome_lower
            for invalido in nomes_invalidos
        ):

            continue

        if len(nome.split()) < 3:

            continue

        candidatos_filtrados.append(
            nome
        )

    if candidatos_filtrados:

        melhor = max(
            candidatos_filtrados,
            key=len
        )

        print(
            "🛍️ Nome encontrado:",
            melhor
        )

        return melhor[:250]

    print(
        "⚠️ Nome do produto não encontrado."
    )

    return None


# ============================================================
# EXTRAIR IMAGENS
# ============================================================

def extrair_imagens(
    html_text,
    url_base
):

    if not html_text:
        return []

    imagens = []

    # --------------------------------------------------------
    # OG IMAGE
    # --------------------------------------------------------

    padroes = [

        r'<meta[^>]+property=["\']'
        r'og:image["\'][^>]+content=["\']'
        r'([^"\']+)',

        r'<meta[^>]+content=["\']'
        r'([^"\']+)["\'][^>]+property=["\']'
        r'og:image["\']',

        r'"og:image"\s*:\s*"([^"]+)"',

        r'<meta[^>]+name=["\']'
        r'twitter:image["\'][^>]+content=["\']'
        r'([^"\']+)',
    ]

    for padrao in padroes:

        encontrados = re.findall(
            padrao,
            html_text,
            re.IGNORECASE
        )

        for imagem in encontrados:

            imagem = html.unescape(
                imagem
            )

            imagem = imagem.replace(
                "\\/",
                "/"
            )

            imagem = imagem.replace(
                "\\u002F",
                "/"
            )

            if imagem.startswith("//"):

                imagem = "https:" + imagem

            elif imagem.startswith("/"):

                imagem = urljoin(
                    url_base,
                    imagem
                )

            if imagem.startswith("http"):

                imagens.append(
                    imagem
                )

    # --------------------------------------------------------
    # JSON-LD
    # --------------------------------------------------------

    blocos = re.findall(
        r'<script[^>]+type=["\']'
        r'application/ld\+json["\'][^>]*>'
        r'(.*?)</script>',
        html_text,
        re.IGNORECASE |
        re.DOTALL
    )

    for bloco in blocos:

        try:

            dados = json.loads(
                html.unescape(bloco)
            )

            objetos = (
                dados
                if isinstance(
                    dados,
                    list
                )
                else [dados]
            )

            for objeto in objetos:

                if not isinstance(
                    objeto,
                    dict
                ):
                    continue

                imagem = objeto.get(
                    "image"
                )

                if isinstance(
                    imagem,
                    str
                ):

                    imagens.append(
                        imagem
                    )

                elif isinstance(
                    imagem,
                    list
                ):

                    for item in imagem:

                        if isinstance(
                            item,
                            str
                        ):

                            imagens.append(
                                item
                            )

        except Exception:

            pass

    # --------------------------------------------------------
    # SUSERCONTENT
    # --------------------------------------------------------

    encontrados = re.findall(
        r'https?://[^"\'<>\s]+'
        r'susercontent\.com[^"\'<>\s]*',
        html_text,
        re.IGNORECASE
    )

    for imagem in encontrados:

        imagem = html.unescape(
            imagem
        )

        imagem = imagem.replace(
            "\\/",
            "/"
        )

        imagem = imagem.replace(
            "\\u002F",
            "/"
        )

        imagem = imagem.rstrip(
            "\\\"'<>),"
        )

        if imagem.startswith("http"):

            imagens.append(
                imagem
            )

    # --------------------------------------------------------
    # NORMALIZAR E FILTRAR
    # --------------------------------------------------------

    ignorar = [

        "logo",
        "avatar",
        "favicon",
        "placeholder",
        "icon",
        "sprite",
        "qr-code",
        "qrcode"
    ]

    finais = []

    for imagem in imagens:

        imagem = imagem.strip()

        if not imagem.startswith(
            "http"
        ):
            continue

        baixa = imagem.lower()

        if any(
            palavra in baixa
            for palavra in ignorar
        ):
            continue

        # Evita URLs obviamente pequenas/de interface
        if len(imagem) < 30:
            continue

        if imagem not in finais:

            finais.append(
                imagem
            )

    print(
        "🖼️ Candidatas encontradas:",
        len(finais)
    )

    for numero, imagem in enumerate(
        finais[:10],
        start=1
    ):

        print(
            f"   {numero}. {imagem[:150]}"
        )

    return finais


def extrair_imagem(
    html_text,
    url_base
):

    imagens = extrair_imagens(
        html_text,
        url_base
    )

    if imagens:

        return imagens[0]

    print(
        "⚠️ Imagem não encontrada."
    )

    return None


# ============================================================
# CONSULTAR SHOPEE
# ============================================================

def consultar_shopee(link):

    resultado = {

        "nome": None,

        "preco": None,

        "imagem": None,

        "imagens": [],

        "link": link,

        "link_resolvido": link
    }

    link_original = normalizar_link(
        link
    )

    if not link_original:

        print(
            "❌ Link Shopee inválido."
        )

        return resultado

    resultado["link"] = (
        link_original
    )

    # --------------------------------------------------------
    # RESOLVER LINK
    # --------------------------------------------------------

    link_final = resolver_link(
        link_original
    )

    if link_final:

        resultado[
            "link_resolvido"
        ] = link_final

    # --------------------------------------------------------
    # BAIXAR PÁGINA
    # --------------------------------------------------------

    html_text, url_real = (
        baixar_pagina(
            link_final
        )
    )

    if not html_text:

        return resultado

    # --------------------------------------------------------
    # NOME
    # --------------------------------------------------------

    resultado["nome"] = (
        extrair_nome(
            html_text,
            url_real
        )
    )

    # --------------------------------------------------------
    # IMAGENS
    # --------------------------------------------------------

    imagens = extrair_imagens(
        html_text,
        url_real
    )

    resultado[
        "imagens"
    ] = imagens

    if imagens:

        resultado[
            "imagem"
        ] = imagens[0]

    # --------------------------------------------------------
    # PREÇO
    # --------------------------------------------------------

    faixa = extrair_faixa_preco(
        html_text
    )

    if faixa:

        resultado["preco"] = {

            "tipo": "faixa",

            "menor": faixa["menor"],

            "maior": faixa["maior"]
        }

        print(
            "💰 Faixa encontrada:",
            formatar_preco(
                faixa["menor"]
            ),
            "-",
            formatar_preco(
                faixa["maior"]
            )
        )

    else:

        precos = extrair_precos(
            html_text
        )

        if precos:

            menor = min(precos)

            resultado["preco"] = {

                "tipo": "unico",

                "menor": menor,

                "maior": menor
            }

            print(
                "💰 Preço encontrado:",
                formatar_preco(menor)
            )

        else:

            print(
                "💰 Preço: None"
            )

    print(
        "🛍️ Nome:",
        resultado["nome"]
    )

    print(
        "🖼️ Imagens:",
        len(resultado["imagens"])
    )

    print(
        "🔗 Link original:",
        resultado["link"]
    )

    print(
        "🔗 Link resolvido:",
        resultado["link_resolvido"]
    )

    print(
        "--------------------------------"
    )

    return resultado


# ============================================================
# PREÇO
# ============================================================

def texto_preco(preco):

    if not preco:

        return "💰 OFERTA ESPECIAL"

    if preco.get("tipo") == "faixa":

        menor = formatar_preco(
            preco["menor"]
        )

        return (
            f"💰 A PARTIR DE {menor}"
        )

    menor = formatar_preco(
        preco["menor"]
    )

    return f"💰 {menor}"


# ============================================================
# PREÇO DA MENSAGEM
# ============================================================

def extrair_preco_mensagem(
    texto
):

    if not texto:
        return None

    texto = html.unescape(
        texto
    )

    faixa = extrair_faixa_preco(
        texto
    )

    if faixa:

        return {

            "tipo": "faixa",

            "menor": faixa["menor"],

            "maior": faixa["maior"]
        }

    numero = (
        r"([0-9]{1,3}"
        r"(?:\.[0-9]{3})+"
        r"(?:,[0-9]{1,2})?"
        r"|[0-9]+"
        r"(?:[.,][0-9]{1,2})?)"
    )

    for padrao in (

        rf"por\s*R\$\s*{numero}",

        rf"R\$\s*{numero}"
    ):

        encontrado = re.search(
            padrao,
            texto,
            re.IGNORECASE
        )

        if not encontrado:
            continue

        valor = encontrado.group(1)

        if re.fullmatch(
            r"\d{1,3}(?:\.\d{3})+",
            valor
        ):

            valor = valor.replace(
                ".",
                ""
            )

        preco = converter_preco(
            valor
        )

        if preco:

            return {

                "tipo": "unico",

                "menor": preco,

                "maior": preco
            }

    return None


# ============================================================
# ENVIAR FOTO POR FILE_ID
# ============================================================

def enviar_foto_file_id(
    chat_id,
    file_id,
    legenda,
    teclado
):

    print(
        "🖼️ Enviando foto usando "
        "FILE_ID do Telegram..."
    )

    payload = {

        "chat_id": chat_id,

        "photo": file_id,

        "caption": legenda,

        "reply_markup": json.dumps(
            teclado,
            ensure_ascii=False
        )
    }

    resposta = telegram_post(
        "sendPhoto",
        payload
    )

    print(
        "📨 Resposta sendPhoto:",
        resposta
    )

    return resposta


# ============================================================
# ENVIAR FOTO POR BYTES
# ============================================================

def enviar_foto_bytes(
    chat_id,
    conteudo,
    legenda,
    teclado
):

    if not conteudo:

        return {
            "ok": False,
            "description": (
                "Imagem vazia"
            )
        }

    payload = {

        "chat_id": chat_id,

        "caption": legenda,

        "reply_markup": json.dumps(
            teclado,
            ensure_ascii=False
        )
    }

    arquivos = {

        "photo": (
            "produto.jpg",
            conteudo,
            "image/jpeg"
        )
    }

    print(
        "🖼️ Enviando arquivo de imagem "
        "para o Telegram..."
    )

    resposta = telegram_post(
        "sendPhoto",
        payload,
        arquivos
    )

    print(
        "📨 Resposta sendPhoto:",
        resposta
    )

    return resposta


# ============================================================
# ENVIAR FOTO POR URL
# ============================================================

def enviar_foto_url(
    chat_id,
    url_imagem,
    legenda,
    teclado
):

    if not url_imagem:

        return {
            "ok": False,
            "description": (
                "URL da imagem vazia"
            )
        }

    payload = {

        "chat_id": chat_id,

        "photo": url_imagem,

        "caption": legenda,

        "reply_markup": json.dumps(
            teclado,
            ensure_ascii=False
        )
    }

    print(
        "🌐 Tentando enviar URL da "
        "imagem diretamente ao Telegram..."
    )

    resposta = telegram_post(
        "sendPhoto",
        payload
    )

    print(
        "📨 Resposta URL sendPhoto:",
        resposta
    )

    return resposta


# ============================================================
# PUBLICAR
# ============================================================

def publicar_proximo():

    fila = carregar_fila()

    if not fila:

        print(
            "📭 Fila vazia."
        )

        return False

    produto = fila[0]

    nome_original = (
        produto.get("nome")
        or "Achadinho da Shopee"
    )

    link = normalizar_link(
        produto.get("link")
    )

    imagem = produto.get(
        "imagem"
    )

    # ========================================================
    # LINK
    # ========================================================

    if not link:

        print(
            "❌ Produto possui link inválido."
        )

        print(
            "⚠️ Produto continua na fila."
        )

        return False

    # ========================================================
    # CONSULTAR SHOPEE
    # ========================================================

    dados = consultar_shopee(
        link
    )

    nome = (
        dados.get("nome")
        or nome_original
    )

    preco = (
        produto.get("preco")
        or dados.get("preco")
    )

    imagens_shopee = (
        dados.get("imagens")
        or []
    )

    # ========================================================
    # LINK DO BOTÃO
    # ========================================================

    link_botao = normalizar_link(
        link
    )

    if not link_botao:

        print(
            "❌ Não foi possível "
            "preparar o link."
        )

        return False

    print(
        "🔘 LINK DO BOTÃO:",
        link_botao
    )

    # ========================================================
    # LEGENDA
    # ========================================================

    preco_texto = texto_preco(
        preco
    )

    legenda = f"""🔥 ACHADINHO DA SHOPEE 🔥

🛍️ {nome}

{preco_texto}
⚡ Aproveite enquanto estiver disponível!

👇 Clique abaixo para conferir:

🛒 COMPRAR AGORA"""

    teclado = {

        "inline_keyboard": [

            [

                {
                    "text":
                        "🛒 COMPRAR AGORA",

                    "url":
                        link_botao
                }
            ]
        ]
    }

    # ========================================================
    # CAMINHO 1
    # FOTO ENVIADA PELO USUÁRIO
    # ========================================================

    if imagem:

        print(
            "================================"
        )

        print(
            "📸 FOTO DO USUÁRIO ENCONTRADA"
        )

        print(
            "🔐 Usando FILE_ID diretamente."
        )

        resposta = enviar_foto_file_id(
            CHAT_ID,
            imagem,
            legenda,
            teclado
        )

        if resposta.get("ok"):

            fila.pop(0)

            salvar_fila(fila)

            print(
                "================================"
            )

            print(
                "✅ PRODUTO PUBLICADO"
            )

            print(
                "🖼️ IMAGEM DO USUÁRIO"
            )

            print(
                "🛍️",
                nome
            )

            print(
                "💰",
                texto_preco(preco)
            )

            print(
                "📦 Restam:",
                len(fila)
            )

            print(
                "================================"
            )

            return True

        # IMPORTANTE:
        # Se o file_id falhou, não substituímos por uma
        # imagem aleatória da Shopee. Primeiro tentamos
        # a imagem da Shopee como reserva.

        print(
            "⚠️ Telegram recusou o FILE_ID."
        )

        print(
            resposta
        )

    # ========================================================
    # CAMINHO 2
    # IMAGEM DA SHOPEE
    # ========================================================

    if imagens_shopee:

        print(
            "================================"
        )

        print(
            "🌐 TENTANDO IMAGEM DA SHOPEE"
        )

        print(
            "📦 Candidatas:",
            len(imagens_shopee)
        )

        for numero, url_imagem in enumerate(
            imagens_shopee[:5],
            start=1
        ):

            print(
                f"🖼️ Tentativa {numero}:",
                url_imagem[:150]
            )

            # ------------------------------------------------
            # BAIXAR
            # ------------------------------------------------

            conteudo = baixar_imagem(
                url_imagem,
                referer=dados.get(
                    "link_resolvido"
                )
            )

            if conteudo:

                resposta = enviar_foto_bytes(
                    CHAT_ID,
                    conteudo,
                    legenda,
                    teclado
                )

                if resposta.get("ok"):

                    fila.pop(0)

                    salvar_fila(
                        fila
                    )

                    print(
                        "================================"
                    )

                    print(
                        "✅ PRODUTO PUBLICADO"
                    )

                    print(
                        "🖼️ IMAGEM DA SHOPEE"
                    )

                    print(
                        "🛍️",
                        nome
                    )

                    print(
                        "💰",
                        texto_preco(preco)
                    )

                    print(
                        "📦 Restam:",
                        len(fila)
                    )

                    print(
                        "================================"
                    )

                    return True

                print(
                    "⚠️ Telegram recusou "
                    "essa imagem."
                )

            # ------------------------------------------------
            # TENTATIVA DIRETA
            # ------------------------------------------------

            resposta = enviar_foto_url(
                CHAT_ID,
                url_imagem,
                legenda,
                teclado
            )

            if resposta.get("ok"):

                fila.pop(0)

                salvar_fila(
                    fila
                )

                print(
                    "================================"
                )

                print(
                    "✅ PRODUTO PUBLICADO"
                )

                print(
                    "🖼️ IMAGEM ENVIADA POR URL"
                )

                print(
                    "🛍️",
                    nome
                )

                print(
                    "💰",
                    texto_preco(preco)
                )

                print(
                    "📦 Restam:",
                    len(fila)
                )

                print(
                    "================================"
                )

                return True

            print(
                "⚠️ URL também recusada."
            )

        print(
            "❌ Todas as imagens candidatas "
            "falharam."
        )

    else:

        print(
            "⚠️ Nenhuma imagem encontrada "
            "na página da Shopee."
        )

    # ========================================================
    # CAMINHO 3
    # SEM IMAGEM
    # ========================================================

    # Se o usuário forneceu uma foto, não publicamos
    # texto sem imagem. O produto permanece na fila.
    #
    # Isso evita que o bot diga que publicou corretamente
    # quando justamente a imagem falhou.

    if imagem:

        print(
            "❌ A foto fornecida pelo usuário "
            "não pôde ser publicada."
        )

        print(
            "⚠️ Produto continua na fila."
        )

        return False

    # Se foi enviado somente o link e nenhuma imagem
    # foi encontrada, ainda podemos publicar como texto.

    print(
        "📝 Nenhuma imagem disponível."
    )

    print(
        "📝 Publicando como texto."
    )

    resposta = telegram_post(
        "sendMessage",
        {
            "chat_id": CHAT_ID,

            "text": legenda,

            "reply_markup": json.dumps(
                teclado,
                ensure_ascii=False
            )
        }
    )

    if resposta.get("ok"):

        fila.pop(0)

        salvar_fila(fila)

        print(
            "================================"
        )

        print(
            "✅ PRODUTO PUBLICADO "
            "COMO TEXTO"
        )

        print(
            "🛍️",
            nome
        )

        print(
            "💰",
            texto_preco(preco)
        )

        print(
            "📦 Restam:",
            len(fila)
        )

        print(
            "================================"
        )

        return True

    print(
        "❌ Telegram recusou publicação."
    )

    print(
        resposta
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
                "❌ Erro no publicador:",
                erro
            )

        time.sleep(60)


# ============================================================
# MOSTRAR FILA
# ============================================================

def mostrar_fila(
    chat_id
):

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

def limpar_fila(
    chat_id
):

    salvar_fila([])

    enviar_mensagem(
        chat_id,
        "🗑️ Fila limpa com sucesso!"
    )


# ============================================================
# NOME DA MENSAGEM
# ============================================================

def encontrar_nome_mensagem(
    texto
):

    if not texto:

        return (
            "Achadinho da Shopee"
        )

    linhas = [

        linha.strip()

        for linha in texto.splitlines()

        if linha.strip()
    ]

    for linha in linhas:

        if re.search(
            r'https?://',
            linha,
            re.IGNORECASE
        ):

            continue

        linha_lower = linha.lower()

        if (
            "compre na shopee"
            in linha_lower
        ):

            continue

        if (
            "shopee brasil"
            in linha_lower
        ):

            continue

        if (
            "procurando a"
            in linha_lower
        ):

            continue

        if len(linha) >= 10:

            return linha[:250]

    return (
        "Achadinho da Shopee"
    )


# ============================================================
# PROCESSAR MENSAGEM
# ============================================================

def processar_mensagem(
    mensagem
):

    chat_id = mensagem[
        "chat"
    ]["id"]

    # ========================================================
    # ADMIN
    # ========================================================

    if ADMIN_ID:

        usuario_id = str(
            mensagem.get(
                "from",
                {}
            ).get(
                "id",
                ""
            )
        )

        if usuario_id != str(
            ADMIN_ID
        ):

            return

    # ========================================================
    # FOTO + TEXTO
    # ========================================================

    if "photo" in mensagem:

        file_id = mensagem[
            "photo"
        ][-1]["file_id"]

        legenda = mensagem.get(
            "caption",
            ""
        ).strip()

        link = encontrar_link(
            legenda
        )

        if not link:

            enviar_mensagem(
                chat_id,
                """❌ Não encontrei um link válido da Shopee.

Envie a foto com o link da Shopee
na legenda."""
            )

            return

        nome = encontrar_nome_mensagem(
            legenda
        )

        preco_msg = (
            extrair_preco_mensagem(
                legenda
            )
        )

        adicionado, posicao = (
            adicionar_na_fila(
                nome,
                link,
                file_id,
                preco_msg
            )
        )

        if preco_msg:

            linha_preco = (
                texto_preco(
                    preco_msg
                )
                + " (da legenda)"
            )

        else:

            linha_preco = (
                "💰 Preço será "
                "consultado na Shopee."
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

📦 Posição na fila: {posicao}

🔗 Link encontrado.
{linha_preco}
🖼️ Sua foto será usada na publicação.
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

    # ========================================================
    # START
    # ========================================================

    if texto == "/start":

        enviar_mensagem(
            chat_id,
            """🤖 ACHADOS DA SHOPEE

Envie o anúncio ou link da Shopee.

O bot procura automaticamente:

🛍️ Nome
💰 Preço
🖼️ Imagem
🔗 Link

⭐ MELHOR OPÇÃO:

Envie a FOTO do produto com o link
na legenda.

Assim o bot usa a própria foto
que você enviou, sem depender da
imagem da página da Shopee.

Se quiser o preço exato, escreva
na legenda:

R$ 49,90

Comandos:

/fila
Ver fila

/limparfila
Limpar fila"""
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
    # LIMPAR FILA
    # ========================================================

    if texto == "/limparfila":

        limpar_fila(
            chat_id
        )

        return

    # ========================================================
    # LINK
    # ========================================================

    link = encontrar_link(
        texto
    )

    if not link:

        enviar_mensagem(
            chat_id,
            """❌ Não encontrei um link válido da Shopee.

Cole a mensagem completa do produto
ou envie somente o link da Shopee."""
        )

        return

    # ========================================================
    # NOME
    # ========================================================

    nome = encontrar_nome_mensagem(
        texto
    )

    # ========================================================
    # PREÇO
    # ========================================================

    preco_msg = (
        extrair_preco_mensagem(
            texto
        )
    )

    # ========================================================
    # ADICIONAR
    # ========================================================

    adicionado, posicao = (
        adicionar_na_fila(
            nome,
            link,
            None,
            preco_msg
        )
    )

    if preco_msg:

        linha_preco = (
            texto_preco(
                preco_msg
            )
            + " (da mensagem)"
        )

    else:

        linha_preco = (
            "💰 Preço será "
            "consultado na Shopee."
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

📦 Posição na fila: {posicao}

🔗 Link encontrado.
{linha_preco}
🖼️ Imagem será consultada na Shopee.
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

            for update in dados[
                "result"
            ]:

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
                            "❌ Erro ao processar:",
                            erro
                        )

                        try:

                            enviar_mensagem(
                                update[
                                    "message"
                                ]["chat"]["id"],

                                "⚠️ Erro temporário. "
                                "Tente enviar novamente."
                            )

                        except Exception:

                            pass

        except Exception as erro:

            print(
                "❌ Erro:",
                erro
            )

            time.sleep(5)


# ============================================================
# SERVIDOR DE SAÚDE
# ============================================================

class Saude(
    BaseHTTPRequestHandler
):

    def do_GET(self):

        self.send_response(
            200
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

    servidor.serve_forever()


# ============================================================
# INICIAR
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
        "💰 LEITOR DE PREÇOS ATIVO"
    )

    print(
        "🖼️ LEITOR DE IMAGENS ATIVO"
    )

    print(
        "📸 FILE_ID DO TELEGRAM ATIVO"
    )

    print(
        "🔗 LEITOR DE LINKS ATIVO"
    )

    print(
        "======================================"
    )

    iniciar_bot()
