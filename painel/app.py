
import os
from urllib.parse import urlsplit
from flask import Flask, request, redirect, session, render_template_string
import bot

app = Flask(__name__)

app.secret_key = os.environ.get("SECRET_KEY", "chave-temporaria")

SENHA = os.environ.get("PANEL_PASSWORD", "")


def validar_link_shopee(link):
    if not link:
        return None

    link = link.strip()

    if not link.startswith(("http://", "https://")):
        link = "https://" + link

    try:
        partes = urlsplit(link)
        dominio = (partes.hostname or "").lower()

        dominios_validos = (
            "shopee.com.br",
            "www.shopee.com.br",
            "s.shopee.com.br",
            "shopee.com",
            "www.shopee.com",
            "shp.ee",
            "www.shp.ee"
        )

        if dominio not in dominios_validos:
            return None

        if not partes.path or partes.path == "/":
            return None

        return link

    except Exception:
        return None


HTML = """
<!DOCTYPE html>
<html lang="pt-br">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Painel Achados Shopee</title>

<style>
body {
    font-family: Arial;
    background: #f5f5f5;
    padding: 20px;
}

.card {
    background: white;
    padding: 22px;
    border-radius: 15px;
    max-width: 500px;
    margin: 30px auto;
}

input, button {
    width: 100%;
    padding: 14px;
    margin: 10px 0;
    box-sizing: border-box;
    border-radius: 8px;
    font-size: 16px;
}

button {
    background: #ee4d2d;
    color: white;
    border: none;
    font-weight: bold;
}

img {
    width: 100%;
    border-radius: 10px;
}

.erro {
    color: red;
}

.sucesso {
    color: green;
}
</style>
</head>

<body>
<div class="card">

<h2>🛍️ Achados da Shopee</h2>

<p>Consulte seus produtos e adicione à fila automática.</p>

<form method="post" action="/consultar">

<input
name="link"
placeholder="Cole seu link de afiliado Shopee"
required>

<button>CONSULTAR PRODUTO</button>

</form>

{% if erro %}
<p class="erro">{{ erro }}</p>
{% endif %}

{% if produto %}

<hr>

<h3>{{ produto.nome }}</h3>

{% if produto.imagem %}
<img src="{{ produto.imagem }}">
{% endif %}

<p>💰 {{ produto.preco }}</p>

<p>🔗 {{ produto.link }}</p>

<form method="post" action="/adicionar">

<input
type="hidden"
name="link"
value="{{ produto.link }}">

<button>ADICIONAR À FILA</button>

</form>

{% endif %}

{% if mensagem %}
<h3 class="sucesso">{{ mensagem }}</h3>
{% endif %}

<a href="/sair">Sair</a>

</div>
</body>
</html>
"""


@app.route("/", methods=["GET", "POST"])
def inicio():

    if not SENHA:
        return "Configure PANEL_PASSWORD no Render.", 503

    if not session.get("autorizado"):

        if request.method == "POST":

            if request.form.get("senha") == SENHA:
                session["autorizado"] = True
                return redirect("/")

            return "Senha incorreta."

        return """
        <form method="post" style="padding:30px">
        <h2>Painel Achados Shopee</h2>
        <input type="password" name="senha"
        placeholder="Senha de acesso" required>
        <button>Entrar</button>
        </form>
        """

    return render_template_string(HTML)


@app.route("/consultar", methods=["POST"])
def consultar():

    if not session.get("autorizado"):
        return redirect("/")

    link_original = request.form.get("link", "").strip()

    link = validar_link_shopee(link_original)

    if not link:
        return render_template_string(
            HTML,
            erro="Link Shopee inválido. Confira o endereço."
        )

    # Tenta utilizar a normalização do bot.
    # Se ela rejeitar um link curto válido, preserva o original.
    try:
        link_normalizado = bot.normalizar_link(link)

        if link_normalizado:
            link = link_normalizado

    except Exception:
        pass

    try:
        dados = bot.consultar_shopee(link)

        if not isinstance(dados, dict):
            dados = {}

    except Exception as erro:
        return render_template_string(
            HTML,
            erro="Erro ao consultar produto: " + str(erro)
        )

    preco = dados.get("preco")

    if preco:
        try:
            preco_texto = bot.texto_preco(preco)
        except Exception:
            preco_texto = str(preco)
    else:
        preco_texto = "Preço não encontrado"

    produto = {
        "nome": dados.get("nome") or "Produto Shopee",
        "imagem": dados.get("imagem"),
        "preco": preco_texto,
        "link": link
    }

    session["produto"] = produto

    return render_template_string(
        HTML,
        produto=produto
    )


@app.route("/adicionar", methods=["POST"])
def adicionar():

    if not session.get("autorizado"):
        return redirect("/")

    produto = session.get("produto")

    if not produto or produto["link"] != request.form.get("link"):
        return render_template_string(
            HTML,
            erro="Consulte novamente o produto."
        )

    try:
        dados = bot.consultar_shopee(produto["link"])

        if not isinstance(dados, dict):
            dados = {}

        nome = dados.get("nome") or produto["nome"]

        imagem = dados.get("imagem") or produto["imagem"]

        preco = dados.get("preco")

        resolvido = dados.get("link_resolvido")

        ok, posicao = bot.adicionar_na_fila(
            nome,
            produto["link"],
            imagem,
            preco,
            resolvido
        )

        mensagem = (
            f"✅ Produto adicionado! Posição: {posicao}"
            if ok
            else "⚠️ Produto já está na fila."
        )

        session.pop("produto", None)

        return render_template_string(
            HTML,
            mensagem=mensagem
        )

    except Exception as erro:

        return render_template_string(
            HTML,
            erro="Erro ao adicionar produto: " + str(erro)
        )


@app.route("/sair")
def sair():

    session.clear()

    return redirect("/")


@app.route("/health")
def health():

    return "OK", 200
