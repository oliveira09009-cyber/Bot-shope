
import requests
import json
import os
import time
import threading

# ==========================================
# CONFIGURAÇÕES
# ==========================================

TOKEN = "8262222708:AAGHH1nAtDGMKiNmvemrfnGoodag7ioRRQY"
CHAT_ID = "-1003905816059"

INTERVALO = 30
ARQUIVO_FILA = "fila.json"


# ==========================================
# FILA
# ==========================================

def carregar_fila():

    if not os.path.exists(ARQUIVO_FILA):
        return []

    try:
        with open(
            ARQUIVO_FILA,
            "r",
            encoding="utf-8"
        ) as arquivo:

            return json.load(arquivo)

    except Exception as erro:

        print("❌ Erro ao carregar fila:", erro)
        return []


def salvar_fila(fila):

    with open(
        ARQUIVO_FILA,
        "w",
        encoding="utf-8"
    ) as arquivo:

        json.dump(
            fila,
            arquivo,
            ensure_ascii=False,
            indent=4
        )


def adicionar_na_fila(nome, link, imagem=None):

    fila = carregar_fila()

    for produto in fila:

        if produto["link"] == link:

            return False, len(fila)

    produto = {
        "nome": nome,
        "link": link,
        "imagem": imagem
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

    dados = {
        "chat_id": chat_id,
        "text": texto
    }

    try:

        resposta = requests.post(
            url,
            data=dados,
            timeout=30
        )

        return resposta.json()

    except Exception as erro:

        print("❌ Erro Telegram:", erro)

        return {"ok": False}


# ==========================================
# PUBLICAR PRODUTO
# ==========================================

def publicar_proximo():

    fila = carregar_fila()

    if not fila:

        print("📭 Fila vazia.")

        return


    produto = fila[0]

    nome = produto["nome"]
    link = produto["link"]
    imagem = produto.get("imagem")


    legenda = f"""🔥 ACHADINHO DA SHOPEE

🛒 {nome}

💰 Confira o preço e as opções disponíveis!"""


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


    # ======================================
    # COM FOTO
    # ======================================

    if imagem:

        url = (
            f"https://api.telegram.org/"
            f"bot{TOKEN}/sendPhoto"
        )

        dados = {
            "chat_id": CHAT_ID,
            "photo": imagem,
            "caption": legenda,
            "reply_markup": json.dumps(teclado)
        }

    # ======================================
    # SEM FOTO
    # ======================================

    else:

        url = (
            f"https://api.telegram.org/"
            f"bot{TOKEN}/sendMessage"
        )

        dados = {
            "chat_id": CHAT_ID,
            "text": legenda,
            "reply_markup": json.dumps(teclado)
        }


    try:

        resposta = requests.post(
            url,
            data=dados,
            timeout=30
        )

        resultado = resposta.json()

    except Exception as erro:

        print("❌ Erro ao publicar:", erro)

        return


    # ======================================
    # SUCESSO
    # ======================================

    if resultado.get("ok"):

        fila.pop(0)

        salvar_fila(fila)

        print("================================")
        print("✅ PRODUTO PUBLICADO")
        print("🛒", nome)
        print("📦 Restam:", len(fila))
        print("================================")

    else:

        print("❌ Telegram recusou a publicação.")
        print(resultado)
        print("⚠️ Produto continua na fila.")


# ==========================================
# PUBLICADOR AUTOMÁTICO
# ==========================================

def publicador_automatico():

    print("🚀 Publicador automático iniciado.")
    print("⏰ Intervalo: 1 hora.")

    while True:

        try:

            publicar_proximo()

        except Exception as erro:

            print("❌ Erro:", erro)

        print("💤 Aguardando 1 hora...")

        time.sleep(INTERVALO)


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


    mensagem = "📦 PRODUTOS NA FILA\n\n"


    for numero, produto in enumerate(
        fila,
        start=1
    ):

        mensagem += (
            f"{numero}. 🛒 {produto['nome']}\n"
            f"🔗 {produto['link']}\n\n"
        )


    enviar_mensagem(
        chat_id,
        mensagem
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

    chat_id = mensagem["chat"]["id"]


    # ======================================
    # FOTO + LEGENDA
    # ======================================

    if "photo" in mensagem:

        fotos = mensagem["photo"]

        # Maior resolução disponível
        foto = fotos[-1]

        file_id = foto["file_id"]

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
                """❌ Formato incorreto.

Envie a FOTO com esta legenda:

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


        adicionado, posicao = adicionar_na_fila(
            nome,
            link,
            file_id
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

📦 Posição na fila: {posicao}

🖼️ Foto salva.
⏰ Publicação automática ativa."""
        )

        return


    # ======================================
    # TEXTO
    # ======================================

    texto = mensagem.get(
        "text",
        ""
    ).strip()


    # ======================================
    # START
    # ======================================

    if texto == "/start":

        enviar_mensagem(
            chat_id,
            """🤖 ACHADOS DA SHOPEE

📸 Para adicionar produto:

Envie uma FOTO com a legenda:

Nome do produto
Link da Shopee

Comandos:

/fila
Ver fila

/limparfila
Limpar fila"""
        )

        return


    # ======================================
    # FILA
    # ======================================

    if texto == "/fila":

        mostrar_fila(chat_id)

        return


    # ======================================
    # LIMPAR FILA
    # ======================================

    if texto == "/limparfila":

        limpar_fila(chat_id)

        return


    # ======================================
    # TEXTO SEM FOTO
    # ======================================

    linhas = [
        linha.strip()
        for linha in texto.split("\n")
        if linha.strip()
    ]


    if len(linhas) < 2:

        enviar_mensagem(
            chat_id,
            """❌ Formato incorreto.

Envie uma FOTO com:

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


    adicionado, posicao = adicionar_na_fila(
        nome,
        link
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

📦 Posição: {posicao}

⏰ Será publicado automaticamente."""
    )


# ==========================================
# RECEBER TELEGRAM
# ==========================================

def iniciar_bot():

    print("📥 Sistema de recebimento iniciado.")

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

                print("❌ Erro Telegram:", dados)

                time.sleep(5)

                continue


            for update in dados["result"]:

                ultimo_update = update[
                    "update_id"
                ]


                if "message" in update:

                    processar_mensagem(
                        update["message"]
                    )


        except Exception as erro:

            print("❌ Erro:", erro)

            time.sleep(5)


# ==========================================
# INICIAR
# ==========================================

thread_bot = threading.Thread(
    target=iniciar_bot,
    daemon=True
)

thread_publicador = threading.Thread(
    target=publicador_automatico,
    daemon=True
)


thread_bot.start()
thread_publicador.start()


print("")
print("======================================")
print("✅ SISTEMA COMPLETO ATIVO")
print("📥 Recebimento: ATIVO")
print("📦 Fila: ATIVA")
print("🖼️ Fotos: ATIVAS")
print("🔘 Botão de compra: ATIVO")
print("🚀 Publicação: ATIVA")
print("⏰ 1 PRODUTO POR HORA")
print("======================================")
