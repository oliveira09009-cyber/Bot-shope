import os
from flask import Flask, request, redirect, session, render_template_string
import bot

app = Flask(__name__)
app.secret_key = os.environ["SECRET_KEY"]

SENHA = os.environ.get("PANEL_PASSWORD", "")

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
</style>
</head>
<body>
<div class="card">
<h2>🛍️ Achados da Shopee</h2>
<p>Consulte seus produtos e adicione à fila automática.</p>

<form method="post" action="/consultar">
<input name="link" placeholder="Cole seu link de afiliado Shopee" required>
<button>CONSULTAR PRODUTO</button>
</form>

{% if erro %}
<p>{{ erro }}</p>
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
<input type="hidden" name="link" value="{{ produto.link }}">
<button>ADICIONAR À FILA</button>
</form>
{% endif %}

{% if mensagem %}
<h3>{{ mensagem }}</h3>
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

    link = bot.normalizar_link(request.form.get("link", ""))

    if not link:
        return render_template_string(HTML, erro="Link Shopee inválido.")

    dados = bot.consultar_shopee(link)

    preco = dados.get("preco")
    if preco:
        preco_texto = bot.texto_preco(preco)
    else:
        preco_texto = "Preço não encontrado"

    produto = {
        "nome": dados.get("nome") or "Produto Shopee",
        "imagem": dados.get("imagem"),
        "preco": preco_texto,
        "link": link
    }

    session["produto"] = produto
    return render_template_string(HTML, produto=produto)


@app.route("/adicionar", methods=["POST"])
def adicionar():
    if not session.get("autorizado"):
        return redirect("/")

    produto = session.get("produto")

    if not produto or produto["link"] != request.form.get("link"):
        return render_template_string(
            HTML, erro="Consulte novamente o produto."
        )

    dados = bot.consultar_shopee(produto["link"])

    nome = dados.get("nome") or produto["nome"]
    imagem = dados.get("imagem")
    preco = dados.get("preco")
    resolvido = dados.get("link_resolvido")

    ok, posicao = bot.adicionar_na_fila(
        nome, produto["link"], imagem, preco, resolvido
    )

    mensagem = (
        f"✅ Produto adicionado! Posição: {posicao}"
        if ok else "⚠️ Produto já está na fila."
    )

    session.pop("produto", None)
    return render_template_string(HTML, mensagem=mensagem)


@app.route("/sair")
def sair():
    session.clear()
    return redirect("/")


@app.route("/health")
def health():
    return "OK", 200
