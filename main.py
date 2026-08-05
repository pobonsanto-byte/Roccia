import os
import json
import requests
import secrets
import hashlib
import base64
import datetime
import uuid
import re
from functools import wraps
from flask import Flask, request, redirect, session, render_template_string, url_for, jsonify
from discord import Webhook, AsyncWebhookAdapter
import discord
from discord.ext import commands
import asyncio
import threading
import time
import random

# =========================================================
# CONFIGURAÇÕES
# =========================================================

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))

# Discord OAuth2
DISCORD_CLIENT_ID = os.environ.get('DISCORD_CLIENT_ID')
DISCORD_CLIENT_SECRET = os.environ.get('DISCORD_CLIENT_SECRET')
DISCORD_REDIRECT_URI = os.environ.get('DISCORD_REDIRECT_URI', 'http://localhost:5000/callback')
DISCORD_BOT_TOKEN = os.environ.get('DISCORD_BOT_TOKEN')
GUILD_ID = int(os.environ.get('GUILD_ID', 0))  # ID do servidor Discord

# GitHub (para salvamento)
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN')
GITHUB_REPO = os.environ.get('GITHUB_REPO')
GITHUB_FILE_PATH = os.environ.get('GITHUB_FILE_PATH', 'data.json')

# Arquivo JSON local (para fallback)
DATA_FILE = 'data.json'

# =========================================================
# ESTRUTURA DE DADOS
# =========================================================

DEFAULT_DATA = {
    "clientes": {},          # discord_id -> {uid, nick, pontos, total_ganhos, total_usados, ultima_compra, ultimo_resgate, historico: [], solicitacoes: [], cupons: []}
    "servicos": {},          # id_servico -> {nome, categoria, descricao, valor, pontos, ativo, imagem}
    "solicitacoes": {},      # id_solicitacao -> {cliente_discord, uid, servico_id, observacoes, data, status: 'pendente'|'aceita'|'recusada', motivo, admin_responsavel}
    "fila": [],              # lista de itens da fila (estrutura existente)
    "fidelidade": {
        "pontos_por_real": 1,
        "recompensas": [
            {"id": "r1", "nome": "1 Dia de Quests Diárias Grátis", "pontos": 60, "tipo": "quests"},
            {"id": "r2", "nome": "Desafio Rápido", "pontos": 100, "tipo": "servico"},
            {"id": "r3", "nome": "Portinha", "pontos": 100, "tipo": "servico"},
            {"id": "r4", "nome": "Hologramas de Huanglong", "pontos": 100, "tipo": "servico"},
            {"id": "r5", "nome": "Cupom de R$5", "pontos": 100, "tipo": "cupom", "valor": 5},
            {"id": "r6", "nome": "Análise de Conta", "pontos": 200, "tipo": "servico"},
            {"id": "r7", "nome": "Companion Quest", "pontos": 200, "tipo": "servico"},
            {"id": "r8", "nome": "Cupom de R$10", "pontos": 200, "tipo": "cupom", "valor": 10},
            {"id": "r9", "nome": "Build Completa", "pontos": 400, "tipo": "servico"},
            {"id": "r10", "nome": "Cupom de R$20", "pontos": 400, "tipo": "cupom", "valor": 20}
        ],
        "cupons": {},        # codigo -> {usuario_discord, uid, data_resgate, tipo, valor, validade, status: 'ativo'|'usado'|'expirado'}
        "validade_pontos_dias": 90,
        "validade_cupons_dias": 30
    },
    "config": {
        "pontos_por_real": 1,
        "admin_ids": []      # lista de IDs Discord dos administradores
    }
}

# =========================================================
# FUNÇÕES DE CARREGAMENTO/SALVAMENTO
# =========================================================

def load_data():
    """Carrega os dados do JSON (GitHub ou local)."""
    # Tenta carregar do GitHub primeiro (se configurado)
    if GITHUB_TOKEN and GITHUB_REPO:
        try:
            url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE_PATH}"
            headers = {
                "Authorization": f"token {GITHUB_TOKEN}",
                "Accept": "application/vnd.github.v3+json"
            }
            resp = requests.get(url, headers=headers)
            if resp.status_code == 200:
                content = resp.json()['content']
                decoded = base64.b64decode(content).decode('utf-8')
                data = json.loads(decoded)
                # Mesclar com default para garantir chaves
                for key, value in DEFAULT_DATA.items():
                    if key not in data:
                        data[key] = value
                return data
        except Exception as e:
            print(f"Erro ao carregar do GitHub: {e}")
    # Fallback para arquivo local
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                for key, value in DEFAULT_DATA.items():
                    if key not in data:
                        data[key] = value
                return data
        except Exception as e:
            print(f"Erro ao carregar local: {e}")
    return DEFAULT_DATA.copy()

def save_data(data):
    """Salva os dados no JSON (GitHub ou local)."""
    # Salva localmente
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"Erro ao salvar local: {e}")
    # Envia para GitHub
    if GITHUB_TOKEN and GITHUB_REPO:
        try:
            url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE_PATH}"
            headers = {
                "Authorization": f"token {GITHUB_TOKEN}",
                "Accept": "application/vnd.github.v3+json"
            }
            # Obter SHA atual
            resp = requests.get(url, headers=headers)
            sha = None
            if resp.status_code == 200:
                sha = resp.json()['sha']
            content = base64.b64encode(json.dumps(data, indent=4, ensure_ascii=False).encode('utf-8')).decode('utf-8')
            payload = {
                "message": "Atualização automática via bot",
                "content": content,
                "branch": "main"
            }
            if sha:
                payload["sha"] = sha
            put_resp = requests.put(url, headers=headers, json=payload)
            if put_resp.status_code not in [200, 201]:
                print(f"Erro ao salvar no GitHub: {put_resp.text}")
        except Exception as e:
            print(f"Erro ao salvar no GitHub: {e}")

# =========================================================
# FUNÇÕES AUXILIARES
# =========================================================

def is_admin(user_id):
    """Verifica se o usuário é administrador."""
    data = load_data()
    return str(user_id) in data.get('config', {}).get('admin_ids', [])

def is_member_of_guild(user_id):
    """Verifica se o usuário é membro do servidor Discord configurado."""
    if not DISCORD_BOT_TOKEN or not GUILD_ID:
        return False
    # Usar a API do Discord para verificar membro (necessita bot token)
    try:
        # Opção 1: usar requests com token bot
        url = f"https://discord.com/api/v10/guilds/{GUILD_ID}/members/{user_id}"
        headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}"}
        resp = requests.get(url, headers=headers)
        return resp.status_code == 200
    except Exception as e:
        print(f"Erro ao verificar membro: {e}")
        return False

def get_discord_user(access_token):
    """Obtém informações do usuário Discord via OAuth2."""
    url = "https://discord.com/api/v10/users/@me"
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = requests.get(url, headers=headers)
    if resp.status_code == 200:
        return resp.json()
    return None

def get_user_data(discord_id):
    """Retorna os dados do cliente a partir do ID."""
    data = load_data()
    return data['clientes'].get(str(discord_id))

def save_user_data(discord_id, user_data):
    """Salva os dados do cliente."""
    data = load_data()
    data['clientes'][str(discord_id)] = user_data
    save_data(data)

def generate_coupon_code():
    """Gera um código de cupom aleatório."""
    return f"ZANKON-{secrets.token_hex(4).upper()}"

def add_to_fila(item):
    """Adiciona um item à fila (função existente)."""
    data = load_data()
    data['fila'].append(item)
    save_data(data)

def remove_from_fila(index):
    """Remove um item da fila (função existente)."""
    data = load_data()
    if 0 <= index < len(data['fila']):
        removed = data['fila'].pop(index)
        save_data(data)
        return removed
    return None

def get_next_id(collection):
    """Gera um ID incremental para coleções."""
    data = load_data()
    ids = [int(k) for k in data.get(collection, {}).keys() if k.isdigit()]
    return str(max(ids) + 1) if ids else "1"

def format_datetime(dt):
    """Formata datetime para string."""
    return dt.strftime("%d/%m/%Y %H:%M")

# =========================================================
# DECORATORS
# =========================================================

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        if not is_admin(session['user_id']):
            return "Acesso negado: você não é administrador.", 403
        return f(*args, **kwargs)
    return decorated

def member_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        if not is_member_of_guild(session['user_id']):
            return "Acesso negado: você não é membro do servidor.", 403
        return f(*args, **kwargs)
    return decorated

# =========================================================
# ROTAS DE AUTENTICAÇÃO
# =========================================================

@app.route('/')
def index():
    """Página inicial pública."""
    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>ZankonYTB - Sistema</title>
        <style>
            body { background: #1a1a2e; color: #eee; font-family: Arial, sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
            .container { text-align: center; max-width: 600px; padding: 20px; background: #16213e; border-radius: 10px; }
            h1 { color: #e94560; }
            .btn { display: inline-block; padding: 12px 24px; background: #5865F2; color: white; text-decoration: none; border-radius: 5px; margin-top: 20px; }
            .btn:hover { background: #4752c4; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Bem-vindo ao Sistema ZankonYTB</h1>
            <p>Faça login com o Discord para acessar sua área.</p>
            <a href="{{ url_for('login') }}" class="btn">Entrar com Discord</a>
            <br><br>
            <a href="{{ url_for('regras') }}">📜 Regras do Sistema de Fidelidade</a>
        </div>
    </body>
    </html>
    """)

@app.route('/login')
def login():
    """Inicia o fluxo OAuth2 do Discord."""
    state = secrets.token_urlsafe(16)
    session['oauth_state'] = state
    url = f"https://discord.com/api/oauth2/authorize?client_id={DISCORD_CLIENT_ID}&redirect_uri={DISCORD_REDIRECT_URI}&response_type=code&scope=identify%20guilds&state={state}"
    return redirect(url)

@app.route('/callback')
def callback():
    """Callback OAuth2 do Discord."""
    code = request.args.get('code')
    state = request.args.get('state')
    if state != session.get('oauth_state'):
        return "Erro de segurança: state inválido.", 400

    # Trocar código por token
    data = {
        'client_id': DISCORD_CLIENT_ID,
        'client_secret': DISCORD_CLIENT_SECRET,
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': DISCORD_REDIRECT_URI
    }
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}
    resp = requests.post('https://discord.com/api/oauth2/token', data=data, headers=headers)
    if resp.status_code != 200:
        return "Erro ao obter token.", 400
    token_data = resp.json()
    access_token = token_data['access_token']

    # Obter informações do usuário
    user = get_discord_user(access_token)
    if not user:
        return "Erro ao obter dados do usuário.", 400

    user_id = user['id']
    session['user_id'] = user_id
    session['username'] = user['username']
    session['avatar'] = user.get('avatar')

    # Verificar se é admin
    if is_admin(user_id):
        session['admin'] = True
        return redirect(url_for('admin_panel'))
    else:
        # Verificar se é membro do servidor
        if not is_member_of_guild(user_id):
            return "Você não é membro do servidor Discord configurado.", 403
        # Redirecionar para área do cliente
        return redirect(url_for('cliente_area'))

@app.route('/logout')
def logout():
    """Logout."""
    session.clear()
    return redirect(url_for('index'))

# =========================================================
# ÁREA DO CLIENTE
# =========================================================

@app.route('/cliente')
@login_required
@member_required
def cliente_area():
    """Página principal do cliente."""
    user_id = session['user_id']
    data = load_data()
    user_data = data['clientes'].get(str(user_id))

    # Se não tiver cadastro, redirecionar para cadastro
    if not user_data:
        return redirect(url_for('cadastro'))

    # Calcular progresso para próximo prêmio
    pontos = user_data.get('pontos', 0)
    recompensas = sorted(data['fidelidade']['recompensas'], key=lambda x: x['pontos'])
    proximo = None
    progresso = 0
    for r in recompensas:
        if r['pontos'] > pontos:
            proximo = r
            break
    if proximo:
        progresso = (pontos / proximo['pontos']) * 100

    return render_template_string(CLIENTE_TEMPLATE, 
                                  user=session,
                                  user_data=user_data,
                                  proximo=proximo,
                                  progresso=progresso,
                                  servicos_ativos=[s for s in data['servicos'].values() if s.get('ativo', True)],
                                  solicitacoes_pendentes=[s for s in data['solicitacoes'].values() if s['cliente_discord'] == str(user_id) and s['status'] == 'pendente'])

CLIENTE_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Área do Cliente - ZankonYTB</title>
    <style>
        * { box-sizing: border-box; }
        body { background: #1a1a2e; color: #eee; font-family: 'Segoe UI', Arial, sans-serif; margin: 0; padding: 20px; }
        .container { max-width: 1200px; margin: 0 auto; background: #16213e; padding: 20px; border-radius: 10px; }
        .header { display: flex; justify-content: space-between; align-items: center; border-bottom: 2px solid #e94560; padding-bottom: 10px; }
        .user-info { display: flex; align-items: center; gap: 15px; }
        .user-info img { width: 50px; height: 50px; border-radius: 50%; border: 2px solid #e94560; }
        .username { color: #e94560; font-weight: bold; }
        .btn { display: inline-block; padding: 8px 16px; background: #e94560; color: white; text-decoration: none; border-radius: 5px; border: none; cursor: pointer; font-size: 14px; }
        .btn-outline { background: transparent; border: 1px solid #e94560; color: #e94560; }
        .btn-outline:hover { background: #e94560; color: white; }
        .btn-success { background: #2ecc71; }
        .btn-danger { background: #e74c3c; }
        .btn-warning { background: #f39c12; }
        .btn-info { background: #3498db; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; margin-top: 20px; }
        .card { background: #0f3460; padding: 15px; border-radius: 8px; }
        .card h3 { margin-top: 0; color: #e94560; border-bottom: 1px solid #e94560; padding-bottom: 5px; }
        .progress-bar { width: 100%; background: #2c3e50; height: 20px; border-radius: 10px; overflow: hidden; margin: 10px 0; }
        .progress-fill { height: 100%; background: #e94560; width: 0%; }
        .badge { background: #e94560; padding: 2px 8px; border-radius: 12px; font-size: 12px; }
        .solicitacao-item { background: #1a1a2e; padding: 10px; margin: 5px 0; border-radius: 5px; }
        .flex { display: flex; gap: 10px; flex-wrap: wrap; }
        .mt-20 { margin-top: 20px; }
        .text-center { text-align: center; }
        table { width: 100%; border-collapse: collapse; margin-top: 10px; }
        th, td { padding: 8px; border-bottom: 1px solid #2c3e50; text-align: left; }
        th { color: #e94560; }
        .modal { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.7); justify-content: center; align-items: center; z-index: 1000; }
        .modal-content { background: #16213e; padding: 30px; border-radius: 10px; max-width: 500px; width: 90%; max-height: 80vh; overflow-y: auto; }
        .modal-content input, .modal-content select, .modal-content textarea { width: 100%; padding: 8px; margin: 5px 0 15px; border: 1px solid #2c3e50; background: #0f3460; color: #eee; border-radius: 5px; }
        .modal-content label { font-weight: bold; color: #e94560; }
        .close { float: right; cursor: pointer; font-size: 24px; color: #e94560; }
        @media (max-width: 600px) { .header { flex-direction: column; align-items: start; } .grid { grid-template-columns: 1fr; } }
    </style>
</head>
<body>
<div class="container">
    <div class="header">
        <div class="user-info">
            <img src="https://cdn.discordapp.com/avatars/{{ user.user_id }}/{{ user.avatar }}.png" alt="Avatar">
            <div>
                <span class="username">{{ user.username }}</span><br>
                <small>UID: {{ user_data.uid if user_data else 'Não cadastrado' }}</small>
            </div>
        </div>
        <div>
            <a href="{{ url_for('logout') }}" class="btn btn-danger">Sair</a>
        </div>
    </div>

    <!-- Cards de resumo -->
    <div class="grid">
        <div class="card">
            <h3>💰 Pontos</h3>
            <p><strong>Saldo:</strong> {{ user_data.pontos }}</p>
            <p><strong>Total acumulado:</strong> {{ user_data.total_ganhos }}</p>
            <p><strong>Total utilizado:</strong> {{ user_data.total_usados }}</p>
            <p><strong>Última compra:</strong> {{ user_data.ultima_compra or 'Nenhuma' }}</p>
            <p><strong>Último resgate:</strong> {{ user_data.ultimo_resgate or 'Nenhum' }}</p>
        </div>
        <div class="card">
            <h3>🎯 Próximo Prêmio</h3>
            {% if proximo %}
                <p><strong>{{ proximo.nome }}</strong> - {{ proximo.pontos }} pontos</p>
                <div class="progress-bar"><div class="progress-fill" style="width: {{ progresso }}%;"></div></div>
                <p>{{ progresso|round(1) }}% concluído</p>
            {% else %}
                <p>Parabéns! Você já pode resgatar todos os prêmios disponíveis.</p>
            {% endif %}
        </div>
        <div class="card">
            <h3>📦 Serviços em Andamento</h3>
            <ul>
            {% for s in user_data.historico if s.status == 'em_andamento' %}
                <li>{{ s.servico_nome }} - Iniciado em {{ s.data_inicio }}</li>
            {% else %}
                <li>Nenhum serviço em andamento.</li>
            {% endfor %}
            </ul>
        </div>
    </div>

    <!-- Solicitações pendentes -->
    <div class="card mt-20">
        <h3>⏳ Solicitações Pendentes</h3>
        {% if solicitacoes_pendentes %}
            <ul>
            {% for s in solicitacoes_pendentes %}
                <li>{{ s.servico_nome }} - Aguardando aprovação ({{ s.data }})</li>
            {% endfor %}
            </ul>
        {% else %}
            <p>Nenhuma solicitação pendente.</p>
        {% endif %}
    </div>

    <!-- Histórico -->
    <div class="card mt-20">
        <h3>📜 Histórico Completo</h3>
        <table>
            <thead><tr><th>Serviço</th><th>Valor</th><th>Pontos</th><th>Data</th><th>Status</th></tr></thead>
            <tbody>
            {% for item in user_data.historico|reverse %}
                <tr><td>{{ item.servico_nome }}</td><td>R$ {{ item.valor }}</td><td>{{ item.pontos }}</td><td>{{ item.data_conclusao or item.data_inicio }}</td><td>{{ item.status }}</td></tr>
            {% else %}
                <tr><td colspan="5">Nenhum histórico.</td></tr>
            {% endfor %}
            </tbody>
        </table>
    </div>

    <!-- Cupons -->
    <div class="card mt-20">
        <h3>🎟️ Meus Cupons</h3>
        <table>
            <thead><tr><th>Código</th><th>Tipo</th><th>Valor</th><th>Validade</th><th>Status</th></tr></thead>
            <tbody>
            {% for cod, cupom in data.fidelidade.cupons.items() if cupom.usuario_discord == user.user_id %}
                <tr><td>{{ cod }}</td><td>{{ cupom.tipo }}</td><td>{{ cupom.valor }}</td><td>{{ cupom.validade }}</td><td>{{ cupom.status }}</td></tr>
            {% else %}
                <tr><td colspan="5">Nenhum cupom.</td></tr>
            {% endfor %}
            </tbody>
        </table>
    </div>

    <!-- Botões de ação -->
    <div class="flex mt-20">
        <button class="btn" onclick="abrirModal('solicitar')">📩 Solicitar Serviço</button>
        <a href="{{ url_for('loja_fidelidade') }}" class="btn btn-success">🏪 Loja de Fidelidade</a>
        <a href="{{ url_for('regras') }}" class="btn btn-info">📜 Regras</a>
    </div>
</div>

<!-- Modal Solicitar Serviço -->
<div id="modalSolicitar" class="modal">
    <div class="modal-content">
        <span class="close" onclick="fecharModal('modalSolicitar')">&times;</span>
        <h2>Solicitar Serviço</h2>
        <form method="POST" action="{{ url_for('solicitar_servico') }}">
            <label>Serviço</label>
            <select name="servico_id" required>
                {% for s in servicos_ativos %}
                    <option value="{{ s.id }}">{{ s.nome }} - R$ {{ s.valor }} ({{ s.pontos }} pts)</option>
                {% endfor %}
            </select>
            <label>Jogo</label>
            <input type="text" name="jogo" placeholder="Ex: Genshin Impact" required>
            <label>Observações</label>
            <textarea name="observacoes" rows="3"></textarea>
            <label>Cupom (opcional)</label>
            <input type="text" name="cupom" placeholder="Código do cupom">
            <button type="submit" class="btn btn-success">Enviar Solicitação</button>
        </form>
    </div>
</div>

<script>
function abrirModal(id) {
    document.getElementById('modal' + id.charAt(0).toUpperCase() + id.slice(1)).style.display = 'flex';
}
function fecharModal(id) {
    document.getElementById(id).style.display = 'none';
}
// Fechar modal ao clicar fora
window.onclick = function(event) {
    if (event.target.classList.contains('modal')) {
        event.target.style.display = 'none';
    }
}
</script>
</body>
</html>
"""

# =========================================================
# CADASTRO
# =========================================================

@app.route('/cadastro', methods=['GET', 'POST'])
@login_required
@member_required
def cadastro():
    """Página de cadastro inicial (UID e Nick)."""
    user_id = session['user_id']
    data = load_data()
    if request.method == 'POST':
        uid = request.form.get('uid')
        nick = request.form.get('nick')
        if not uid or not nick:
            return "Preencha todos os campos.", 400
        # Verificar se UID já está cadastrado
        for cliente in data['clientes'].values():
            if cliente.get('uid') == uid:
                return "Este UID já está cadastrado.", 400
        # Salvar
        data['clientes'][str(user_id)] = {
            'uid': uid,
            'nick': nick,
            'pontos': 0,
            'total_ganhos': 0,
            'total_usados': 0,
            'ultima_compra': None,
            'ultimo_resgate': None,
            'historico': [],
            'solicitacoes': [],
            'cupons': []
        }
        save_data(data)
        return redirect(url_for('cliente_area'))
    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Cadastro - ZankonYTB</title>
        <style>
            body { background: #1a1a2e; color: #eee; font-family: Arial, sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
            .container { background: #16213e; padding: 30px; border-radius: 10px; width: 100%; max-width: 400px; }
            h2 { color: #e94560; text-align: center; }
            label { display: block; margin-top: 15px; color: #ccc; }
            input { width: 100%; padding: 8px; border: 1px solid #2c3e50; background: #0f3460; color: #eee; border-radius: 5px; }
            .btn { display: block; width: 100%; padding: 10px; background: #e94560; color: white; border: none; border-radius: 5px; margin-top: 20px; cursor: pointer; }
            .btn:hover { background: #c73152; }
        </style>
    </head>
    <body>
        <div class="container">
            <h2>Cadastro Inicial</h2>
            <p>Preencha seus dados para continuar.</p>
            <form method="POST">
                <label>UID (seu ID no jogo)</label>
                <input type="text" name="uid" required>
                <label>Nick do jogo</label>
                <input type="text" name="nick" required>
                <button type="submit" class="btn">Cadastrar</button>
            </form>
        </div>
    </body>
    </html>
    """)

# =========================================================
# SOLICITAR SERVIÇO (POST)
# =========================================================

@app.route('/solicitar_servico', methods=['POST'])
@login_required
@member_required
def solicitar_servico():
    """Processa a solicitação de serviço."""
    user_id = session['user_id']
    data = load_data()
    servico_id = request.form.get('servico_id')
    jogo = request.form.get('jogo')
    observacoes = request.form.get('observacoes', '')
    cupom_cod = request.form.get('cupom', '').strip()

    servico = data['servicos'].get(servico_id)
    if not servico or not servico.get('ativo', True):
        return "Serviço inválido ou inativo.", 400

    # Verificar cupom
    cupom_valido = None
    if cupom_cod:
        cupom = data['fidelidade']['cupons'].get(cupom_cod)
        if cupom and cupom['usuario_discord'] == user_id and cupom['status'] == 'ativo':
            # Verificar validade
            validade = cupom.get('validade')
            if validade:
                try:
                    dt_validade = datetime.datetime.strptime(validade, "%Y-%m-%d")
                    if dt_validade < datetime.datetime.now():
                        cupom['status'] = 'expirado'
                        save_data(data)
                        return "Cupom expirado.", 400
                except:
                    pass
            cupom_valido = cupom
        else:
            return "Cupom inválido ou não pertence a você.", 400

    # Criar solicitação
    solicitacao_id = get_next_id('solicitacoes')
    solicitacao = {
        'id': solicitacao_id,
        'cliente_discord': user_id,
        'uid': data['clientes'][str(user_id)]['uid'],
        'servico_id': servico_id,
        'servico_nome': servico['nome'],
        'jogo': jogo,
        'observacoes': observacoes,
        'data': datetime.datetime.now().strftime("%d/%m/%Y %H:%M"),
        'status': 'pendente',
        'motivo': None,
        'admin_responsavel': None,
        'cupom_usado': cupom_cod if cupom_valido else None,
        'valor': servico['valor'],
        'pontos': servico['pontos']
    }
    data['solicitacoes'][solicitacao_id] = solicitacao

    # Se cupom válido, já marcar como usado (opcional, mas pode ser feito na aprovação)
    if cupom_valido:
        cupom_valido['status'] = 'usado'
        # Registrar no histórico do cliente? faremos depois na conclusão.

    save_data(data)
    return redirect(url_for('cliente_area'))

# =========================================================
# LOJA DE FIDELIDADE
# =========================================================

@app.route('/loja_fidelidade')
@login_required
@member_required
def loja_fidelidade():
    """Página da loja de fidelidade."""
    user_id = session['user_id']
    data = load_data()
    user_data = data['clientes'].get(str(user_id))
    if not user_data:
        return redirect(url_for('cadastro'))
    recompensas = data['fidelidade']['recompensas']
    return render_template_string(LOJA_TEMPLATE, 
                                  user=session,
                                  user_data=user_data,
                                  recompensas=recompensas,
                                  pontos=user_data['pontos'])

LOJA_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Loja de Fidelidade - ZankonYTB</title>
    <style>
        body { background: #1a1a2e; color: #eee; font-family: Arial, sans-serif; padding: 20px; }
        .container { max-width: 1200px; margin: 0 auto; background: #16213e; padding: 20px; border-radius: 10px; }
        h1 { color: #e94560; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 20px; margin-top: 20px; }
        .card { background: #0f3460; padding: 15px; border-radius: 8px; text-align: center; }
        .card h3 { color: #e94560; }
        .btn { display: inline-block; padding: 8px 16px; background: #e94560; color: white; text-decoration: none; border-radius: 5px; border: none; cursor: pointer; }
        .btn:disabled { opacity: 0.5; cursor: not-allowed; }
        .btn-success { background: #2ecc71; }
        .btn-danger { background: #e74c3c; }
        .mt-20 { margin-top: 20px; }
        .flex { display: flex; gap: 10px; flex-wrap: wrap; justify-content: center; }
    </style>
</head>
<body>
<div class="container">
    <div class="flex">
        <a href="{{ url_for('cliente_area') }}" class="btn">← Voltar</a>
        <h1>🏪 Loja de Fidelidade</h1>
        <p><strong>Seus pontos:</strong> {{ pontos }}</p>
    </div>
    <div class="grid">
        {% for r in recompensas %}
        <div class="card">
            <h3>{{ r.nome }}</h3>
            <p>{{ r.pontos }} pontos</p>
            <form method="POST" action="{{ url_for('resgatar') }}">
                <input type="hidden" name="recompensa_id" value="{{ r.id }}">
                <button type="submit" class="btn btn-success" {% if pontos < r.pontos %}disabled{% endif %}>Resgatar</button>
            </form>
        </div>
        {% endfor %}
    </div>
</div>
</body>
</html>
"""

# =========================================================
# RESGATAR (POST)
# =========================================================

@app.route('/resgatar', methods=['POST'])
@login_required
@member_required
def resgatar():
    """Resgata uma recompensa da loja."""
    user_id = session['user_id']
    recompensa_id = request.form.get('recompensa_id')
    data = load_data()
    user_data = data['clientes'].get(str(user_id))
    if not user_data:
        return redirect(url_for('cadastro'))

    recompensa = None
    for r in data['fidelidade']['recompensas']:
        if r['id'] == recompensa_id:
            recompensa = r
            break
    if not recompensa:
        return "Recompensa inválida.", 400

    if user_data['pontos'] < recompensa['pontos']:
        return "Pontos insuficientes.", 400

    # Descontar pontos
    user_data['pontos'] -= recompensa['pontos']
    user_data['total_usados'] = user_data.get('total_usados', 0) + recompensa['pontos']
    user_data['ultimo_resgate'] = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")

    # Gerar cupom ou aplicar benefício
    if recompensa['tipo'] == 'cupom':
        codigo = generate_coupon_code()
        validade = (datetime.datetime.now() + datetime.timedelta(days=data['fidelidade']['validade_cupons_dias'])).strftime("%Y-%m-%d")
        data['fidelidade']['cupons'][codigo] = {
            'usuario_discord': user_id,
            'uid': user_data['uid'],
            'data_resgate': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'tipo': 'desconto',
            'valor': recompensa.get('valor', 0),
            'validade': validade,
            'status': 'ativo'
        }
        # Adicionar cupom à lista do cliente
        user_data['cupons'].append(codigo)
        mensagem = f"Cupom gerado: {codigo} (válido até {validade})"
    else:
        # Para serviços ou outros, pode-se adicionar diretamente ou criar uma solicitação automática
        # Vamos apenas registrar no histórico como resgate
        mensagem = f"Benefício '{recompensa['nome']}' resgatado."
        # Aqui poderia adicionar automaticamente na fila ou criar solicitação, mas deixamos como aviso

    save_data(data)
    return redirect(url_for('loja_fidelidade'))

# =========================================================
# PAINEL ADMINISTRATIVO
# =========================================================

@app.route('/admin')
@login_required
@admin_required
def admin_panel():
    """Painel administrativo principal."""
    data = load_data()
    return render_template_string(ADMIN_TEMPLATE, 
                                  user=session,
                                  servicos=data['servicos'],
                                  clientes=data['clientes'],
                                  solicitacoes=data['solicitacoes'],
                                  fila=data['fila'],
                                  fidelidade=data['fidelidade'],
                                  config=data['config'])

ADMIN_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Painel Admin - ZankonYTB</title>
    <style>
        body { background: #1a1a2e; color: #eee; font-family: Arial, sans-serif; margin: 0; padding: 20px; }
        .container { max-width: 1400px; margin: 0 auto; }
        .header { display: flex; justify-content: space-between; align-items: center; border-bottom: 2px solid #e94560; padding-bottom: 10px; }
        .tabs { display: flex; gap: 10px; margin: 20px 0; flex-wrap: wrap; }
        .tab { padding: 10px 20px; background: #0f3460; border-radius: 5px; cursor: pointer; }
        .tab.active { background: #e94560; }
        .tab-content { display: none; background: #16213e; padding: 20px; border-radius: 10px; margin-top: 20px; }
        .tab-content.active { display: block; }
        .btn { display: inline-block; padding: 6px 12px; background: #e94560; color: white; text-decoration: none; border-radius: 4px; border: none; cursor: pointer; font-size: 13px; }
        .btn-success { background: #2ecc71; }
        .btn-danger { background: #e74c3c; }
        .btn-warning { background: #f39c12; }
        .btn-info { background: #3498db; }
        .card { background: #0f3460; padding: 15px; border-radius: 8px; margin-bottom: 15px; }
        table { width: 100%; border-collapse: collapse; }
        th, td { padding: 8px; border-bottom: 1px solid #2c3e50; text-align: left; }
        th { color: #e94560; }
        .flex { display: flex; gap: 10px; flex-wrap: wrap; }
        .modal { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.7); justify-content: center; align-items: center; z-index: 1000; }
        .modal-content { background: #16213e; padding: 30px; border-radius: 10px; max-width: 500px; width: 90%; max-height: 80vh; overflow-y: auto; }
        .modal-content input, .modal-content select, .modal-content textarea { width: 100%; padding: 8px; margin: 5px 0 15px; border: 1px solid #2c3e50; background: #0f3460; color: #eee; border-radius: 5px; }
        .close { float: right; cursor: pointer; font-size: 24px; color: #e94560; }
        @media (max-width: 600px) { .tabs { flex-direction: column; } }
    </style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>Painel Administrativo</h1>
        <div>
            <span>Admin: {{ user.username }}</span>
            <a href="{{ url_for('logout') }}" class="btn btn-danger">Sair</a>
        </div>
    </div>

    <!-- Tabs -->
    <div class="tabs">
        <div class="tab active" onclick="showTab('servicos')">Serviços</div>
        <div class="tab" onclick="showTab('clientes')">Clientes</div>
        <div class="tab" onclick="showTab('solicitacoes')">Solicitações</div>
        <div class="tab" onclick="showTab('fila')">Fila</div>
        <div class="tab" onclick="showTab('fidelidade')">Fidelidade</div>
        <div class="tab" onclick="showTab('config')">Configurações</div>
    </div>

    <!-- Serviços -->
    <div id="tab-servicos" class="tab-content active">
        <div class="flex">
            <button class="btn" onclick="abrirModal('servico')">+ Novo Serviço</button>
        </div>
        <table>
            <thead><tr><th>ID</th><th>Nome</th><th>Categoria</th><th>Valor</th><th>Pontos</th><th>Status</th><th>Ações</th></tr></thead>
            <tbody>
            {% for id, s in servicos.items() %}
                <tr>
                    <td>{{ id }}</td>
                    <td>{{ s.nome }}</td>
                    <td>{{ s.categoria }}</td>
                    <td>R$ {{ s.valor }}</td>
                    <td>{{ s.pontos }}</td>
                    <td>{{ 'Ativo' if s.ativo else 'Inativo' }}</td>
                    <td>
                        <button class="btn btn-info" onclick="editarServico('{{ id }}')">Editar</button>
                        <button class="btn btn-warning" onclick="toggleServico('{{ id }}')">Toggle</button>
                        <button class="btn btn-danger" onclick="excluirServico('{{ id }}')">Excluir</button>
                    </td>
                </tr>
            {% endfor %}
            </tbody>
        </table>
    </div>

    <!-- Clientes -->
    <div id="tab-clientes" class="tab-content">
        <table>
            <thead><tr><th>Discord</th><th>UID</th><th>Nick</th><th>Pontos</th><th>Ações</th></tr></thead>
            <tbody>
            {% for id, c in clientes.items() %}
                <tr>
                    <td>{{ id }}</td>
                    <td>{{ c.uid }}</td>
                    <td>{{ c.nick }}</td>
                    <td>{{ c.pontos }}</td>
                    <td>
                        <button class="btn btn-info" onclick="verCliente('{{ id }}')">Ver</button>
                        <button class="btn btn-warning" onclick="editarCliente('{{ id }}')">Editar</button>
                    </td>
                </tr>
            {% endfor %}
            </tbody>
        </table>
    </div>

    <!-- Solicitações -->
    <div id="tab-solicitacoes" class="tab-content">
        <h3>Pendentes</h3>
        <table>
            <thead><tr><th>Cliente</th><th>UID</th><th>Serviço</th><th>Valor</th><th>Observações</th><th>Data</th><th>Ações</th></tr></thead>
            <tbody>
            {% for id, s in solicitacoes.items() if s.status == 'pendente' %}
                <tr>
                    <td>{{ s.cliente_discord }}</td>
                    <td>{{ s.uid }}</td>
                    <td>{{ s.servico_nome }}</td>
                    <td>R$ {{ s.valor }}</td>
                    <td>{{ s.observacoes }}</td>
                    <td>{{ s.data }}</td>
                    <td>
                        <form method="POST" action="{{ url_for('aprovar_solicitacao', id=id) }}" style="display:inline;">
                            <button type="submit" class="btn btn-success">Aceitar</button>
                        </form>
                        <button class="btn btn-danger" onclick="recusarSolicitacao('{{ id }}')">Recusar</button>
                    </td>
                </tr>
            {% else %}
                <tr><td colspan="7">Nenhuma solicitação pendente.</td></tr>
            {% endfor %}
            </tbody>
        </table>
        <h3>Aceitas</h3>
        <table>
            <thead><tr><th>Cliente</th><th>Serviço</th><th>Data</th><th>Admin</th></tr></thead>
            <tbody>
            {% for id, s in solicitacoes.items() if s.status == 'aceita' %}
                <tr><td>{{ s.cliente_discord }}</td><td>{{ s.servico_nome }}</td><td>{{ s.data }}</td><td>{{ s.admin_responsavel }}</td></tr>
            {% else %}
                <tr><td colspan="4">Nenhuma solicitação aceita.</td></tr>
            {% endfor %}
            </tbody>
        </table>
        <h3>Recusadas</h3>
        <table>
            <thead><tr><th>Cliente</th><th>Serviço</th><th>Motivo</th><th>Data</th></tr></thead>
            <tbody>
            {% for id, s in solicitacoes.items() if s.status == 'recusada' %}
                <tr><td>{{ s.cliente_discord }}</td><td>{{ s.servico_nome }}</td><td>{{ s.motivo }}</td><td>{{ s.data }}</td></tr>
            {% else %}
                <tr><td colspan="4">Nenhuma solicitação recusada.</td></tr>
            {% endfor %}
            </tbody>
        </table>
    </div>

    <!-- Fila -->
    <div id="tab-fila" class="tab-content">
        <table>
            <thead><tr><th>#</th><th>Cliente</th><th>Serviço</th><th>Data</th><th>Ações</th></tr></thead>
            <tbody>
            {% for item in fila %}
                <tr>
                    <td>{{ loop.index0 }}</td>
                    <td>{{ item.cliente }}</td>
                    <td>{{ item.servico }}</td>
                    <td>{{ item.data }}</td>
                    <td>
                        <button class="btn btn-success" onclick="concluirFila({{ loop.index0 }})">Concluir</button>
                        <button class="btn btn-danger" onclick="removerFila({{ loop.index0 }})">Remover</button>
                    </td>
                </tr>
            {% else %}
                <tr><td colspan="5">Fila vazia.</td></tr>
            {% endfor %}
            </tbody>
        </table>
    </div>

    <!-- Fidelidade -->
    <div id="tab-fidelidade" class="tab-content">
        <h3>Configurações de Fidelidade</h3>
        <form method="POST" action="{{ url_for('admin_config_fidelidade') }}">
            <label>Pontos por Real:</label>
            <input type="number" name="pontos_por_real" value="{{ fidelidade.pontos_por_real }}" step="0.1">
            <label>Validade dos Pontos (dias):</label>
            <input type="number" name="validade_pontos_dias" value="{{ fidelidade.validade_pontos_dias }}">
            <label>Validade dos Cupons (dias):</label>
            <input type="number" name="validade_cupons_dias" value="{{ fidelidade.validade_cupons_dias }}">
            <button type="submit" class="btn">Salvar</button>
        </form>
        <h3>Recompensas</h3>
        <table>
            <thead><tr><th>Nome</th><th>Pontos</th><th>Tipo</th><th>Ações</th></tr></thead>
            <tbody>
            {% for r in fidelidade.recompensas %}
                <tr>
                    <td>{{ r.nome }}</td>
                    <td>{{ r.pontos }}</td>
                    <td>{{ r.tipo }}</td>
                    <td>
                        <button class="btn btn-info" onclick="editarRecompensa('{{ r.id }}')">Editar</button>
                        <button class="btn btn-danger" onclick="excluirRecompensa('{{ r.id }}')">Excluir</button>
                    </td>
                </tr>
            {% endfor %}
            </tbody>
        </table>
        <button class="btn" onclick="abrirModal('recompensa')">+ Nova Recompensa</button>
    </div>

    <!-- Configurações -->
    <div id="tab-config" class="tab-content">
        <form method="POST" action="{{ url_for('admin_config') }}">
            <label>Administradores (IDs Discord separados por vírgula):</label>
            <input type="text" name="admin_ids" value="{{ config.admin_ids|join(', ') }}">
            <button type="submit" class="btn">Salvar</button>
        </form>
    </div>
</div>

<!-- Modais -->
<div id="modalServico" class="modal">
    <div class="modal-content">
        <span class="close" onclick="fecharModal('modalServico')">&times;</span>
        <h2 id="servicoModalTitle">Novo Serviço</h2>
        <form method="POST" action="{{ url_for('admin_servico') }}">
            <input type="hidden" name="id" id="servicoId">
            <label>Nome</label>
            <input type="text" name="nome" id="servicoNome" required>
            <label>Categoria</label>
            <input type="text" name="categoria" id="servicoCategoria">
            <label>Descrição</label>
            <textarea name="descricao" id="servicoDescricao"></textarea>
            <label>Valor (R$)</label>
            <input type="number" name="valor" id="servicoValor" step="0.01" required>
            <label>Pontos</label>
            <input type="number" name="pontos" id="servicoPontos" required>
            <label>Ativo</label>
            <select name="ativo" id="servicoAtivo">
                <option value="true">Sim</option>
                <option value="false">Não</option>
            </select>
            <button type="submit" class="btn">Salvar</button>
        </form>
    </div>
</div>

<div id="modalRecompensa" class="modal">
    <div class="modal-content">
        <span class="close" onclick="fecharModal('modalRecompensa')">&times;</span>
        <h2>Nova Recompensa</h2>
        <form method="POST" action="{{ url_for('admin_recompensa') }}">
            <label>Nome</label>
            <input type="text" name="nome" required>
            <label>Pontos</label>
            <input type="number" name="pontos" required>
            <label>Tipo (cupom/servico/quests)</label>
            <input type="text" name="tipo" required>
            <label>Valor (para cupom)</label>
            <input type="number" name="valor" step="0.01">
            <button type="submit" class="btn">Salvar</button>
        </form>
    </div>
</div>

<script>
function showTab(tab) {
    document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.tab').forEach(el => el.classList.remove('active'));
    document.getElementById('tab-' + tab).classList.add('active');
    document.querySelector(`.tab[onclick="showTab('${tab}')"]`).classList.add('active');
}

function abrirModal(id) {
    document.getElementById('modal' + id.charAt(0).toUpperCase() + id.slice(1)).style.display = 'flex';
}
function fecharModal(id) {
    document.getElementById(id).style.display = 'none';
}
window.onclick = function(event) {
    if (event.target.classList.contains('modal')) {
        event.target.style.display = 'none';
    }
}

function editarServico(id) {
    // Preencher modal com dados do serviço via fetch
    fetch('/admin/servico/' + id)
        .then(res => res.json())
        .then(data => {
            document.getElementById('servicoId').value = id;
            document.getElementById('servicoNome').value = data.nome;
            document.getElementById('servicoCategoria').value = data.categoria;
            document.getElementById('servicoDescricao').value = data.descricao;
            document.getElementById('servicoValor').value = data.valor;
            document.getElementById('servicoPontos').value = data.pontos;
            document.getElementById('servicoAtivo').value = data.ativo ? 'true' : 'false';
            document.getElementById('servicoModalTitle').innerText = 'Editar Serviço';
            abrirModal('servico');
        });
}

function toggleServico(id) {
    fetch('/admin/servico/toggle/' + id, { method: 'POST' })
        .then(() => location.reload());
}

function excluirServico(id) {
    if (confirm('Tem certeza?')) {
        fetch('/admin/servico/' + id, { method: 'DELETE' })
            .then(() => location.reload());
    }
}

function recusarSolicitacao(id) {
    let motivo = prompt('Motivo da recusa:');
    if (motivo !== null) {
        fetch('/admin/solicitacao/recusar/' + id, {
            method: 'POST',
            headers: {'Content-Type': 'application/x-www-form-urlencoded'},
            body: 'motivo=' + encodeURIComponent(motivo)
        }).then(() => location.reload());
    }
}

function concluirFila(index) {
    if (confirm('Concluir serviço da fila?')) {
        fetch('/admin/fila/concluir/' + index, { method: 'POST' })
            .then(() => location.reload());
    }
}

function removerFila(index) {
    if (confirm('Remover da fila?')) {
        fetch('/admin/fila/remover/' + index, { method: 'POST' })
            .then(() => location.reload());
    }
}

function verCliente(id) {
    window.location.href = '/admin/cliente/' + id;
}
function editarCliente(id) {
    // Implementar edição via modal
    alert('Editar cliente: ' + id);
}
function editarRecompensa(id) {
    alert('Editar recompensa: ' + id);
}
function excluirRecompensa(id) {
    if (confirm('Tem certeza?')) {
        fetch('/admin/recompensa/' + id, { method: 'DELETE' })
            .then(() => location.reload());
    }
}
</script>
</body>
</html>
"""

# =========================================================
# ADMIN - ROTAS DE SERVIÇOS
# =========================================================

@app.route('/admin/servico', methods=['POST'])
@login_required
@admin_required
def admin_servico():
    """Cria ou edita um serviço."""
    data = load_data()
    servico_id = request.form.get('id')
    nome = request.form.get('nome')
    categoria = request.form.get('categoria', '')
    descricao = request.form.get('descricao', '')
    valor = float(request.form.get('valor', 0))
    pontos = int(request.form.get('pontos', 0))
    ativo = request.form.get('ativo') == 'true'

    if servico_id and servico_id in data['servicos']:
        # Editar
        s = data['servicos'][servico_id]
        s.update({'nome': nome, 'categoria': categoria, 'descricao': descricao, 'valor': valor, 'pontos': pontos, 'ativo': ativo})
    else:
        # Criar
        new_id = get_next_id('servicos')
        data['servicos'][new_id] = {
            'id': new_id,
            'nome': nome,
            'categoria': categoria,
            'descricao': descricao,
            'valor': valor,
            'pontos': pontos,
            'ativo': ativo,
            'imagem': None
        }
    save_data(data)
    return redirect(url_for('admin_panel'))

@app.route('/admin/servico/<id>', methods=['GET'])
@login_required
@admin_required
def admin_get_servico(id):
    data = load_data()
    s = data['servicos'].get(id)
    if s:
        return jsonify(s)
    return {'error': 'not found'}, 404

@app.route('/admin/servico/toggle/<id>', methods=['POST'])
@login_required
@admin_required
def admin_toggle_servico(id):
    data = load_data()
    if id in data['servicos']:
        data['servicos'][id]['ativo'] = not data['servicos'][id]['ativo']
        save_data(data)
    return '', 204

@app.route('/admin/servico/<id>', methods=['DELETE'])
@login_required
@admin_required
def admin_delete_servico(id):
    data = load_data()
    if id in data['servicos']:
        del data['servicos'][id]
        save_data(data)
    return '', 204

# =========================================================
# ADMIN - SOLICITAÇÕES
# =========================================================

@app.route('/admin/solicitacao/aprovar/<id>', methods=['POST'])
@login_required
@admin_required
def aprovar_solicitacao(id):
    """Aprova uma solicitação e adiciona à fila."""
    data = load_data()
    solicitacao = data['solicitacoes'].get(id)
    if not solicitacao or solicitacao['status'] != 'pendente':
        return "Solicitação inválida.", 400

    # Atualizar status
    solicitacao['status'] = 'aceita'
    solicitacao['admin_responsavel'] = session['user_id']

    # Adicionar à fila (usando estrutura existente)
    item_fila = {
        'cliente': solicitacao['cliente_discord'],
        'servico': solicitacao['servico_nome'],
        'data': datetime.datetime.now().strftime("%d/%m/%Y %H:%M"),
        'solicitacao_id': id,
        'uid': solicitacao['uid']
    }
    add_to_fila(item_fila)

    # Se houver cupom, já marcar como usado (se não tiver sido)
    if solicitacao.get('cupom_usado'):
        cupom = data['fidelidade']['cupons'].get(solicitacao['cupom_usado'])
        if cupom and cupom['status'] == 'ativo':
            cupom['status'] = 'usado'

    save_data(data)
    return redirect(url_for('admin_panel'))

@app.route('/admin/solicitacao/recusar/<id>', methods=['POST'])
@login_required
@admin_required
def recusar_solicitacao(id):
    """Recusa uma solicitação."""
    data = load_data()
    solicitacao = data['solicitacoes'].get(id)
    if not solicitacao or solicitacao['status'] != 'pendente':
        return "Solicitação inválida.", 400

    motivo = request.form.get('motivo', 'Sem motivo informado')
    solicitacao['status'] = 'recusada'
    solicitacao['motivo'] = motivo
    solicitacao['admin_responsavel'] = session['user_id']

    # Mover para histórico do cliente? (opcional)
    # Vamos adicionar ao histórico com status 'recusado'
    user_id = solicitacao['cliente_discord']
    user_data = data['clientes'].get(user_id)
    if user_data:
        user_data['historico'].append({
            'servico_nome': solicitacao['servico_nome'],
            'valor': solicitacao['valor'],
            'pontos': 0,
            'status': 'recusado',
            'data_inicio': solicitacao['data'],
            'data_conclusao': datetime.datetime.now().strftime("%d/%m/%Y %H:%M"),
            'motivo': motivo,
            'admin': session['user_id']
        })
    save_data(data)
    return redirect(url_for('admin_panel'))

# =========================================================
# ADMIN - FILA (CONCLUSÃO/REMOÇÃO)
# =========================================================

@app.route('/admin/fila/concluir/<int:index>', methods=['POST'])
@login_required
@admin_required
def concluir_fila(index):
    """Conclui um serviço da fila e atualiza pontos/histórico."""
    data = load_data()
    if index >= len(data['fila']):
        return "Índice inválido.", 400
    item = data['fila'].pop(index)

    # Buscar solicitação relacionada
    solicitacao_id = item.get('solicitacao_id')
    if solicitacao_id and solicitacao_id in data['solicitacoes']:
        solicitacao = data['solicitacoes'][solicitacao_id]
        cliente_id = solicitacao['cliente_discord']
        servico_id = solicitacao['servico_id']
        servico = data['servicos'].get(servico_id)
        if servico:
            pontos_ganhos = servico['pontos']
            valor = servico['valor']
            # Atualizar cliente
            user_data = data['clientes'].get(cliente_id)
            if user_data:
                user_data['pontos'] = user_data.get('pontos', 0) + pontos_ganhos
                user_data['total_ganhos'] = user_data.get('total_ganhos', 0) + pontos_ganhos
                user_data['ultima_compra'] = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
                # Adicionar ao histórico
                user_data['historico'].append({
                    'servico_nome': servico['nome'],
                    'valor': valor,
                    'pontos': pontos_ganhos,
                    'status': 'concluído',
                    'data_inicio': solicitacao['data'],
                    'data_conclusao': datetime.datetime.now().strftime("%d/%m/%Y %H:%M"),
                    'admin': session['user_id'],
                    'cupom_usado': solicitacao.get('cupom_usado')
                })
                # Atualizar status da solicitação
                solicitacao['status'] = 'concluída'
                save_data(data)
    return redirect(url_for('admin_panel'))

@app.route('/admin/fila/remover/<int:index>', methods=['POST'])
@login_required
@admin_required
def remover_fila(index):
    """Remove um item da fila sem concluir."""
    remove_from_fila(index)
    return redirect(url_for('admin_panel'))

# =========================================================
# ADMIN - CLIENTES (VISUALIZAÇÃO, EDIÇÃO)
# =========================================================

@app.route('/admin/cliente/<id>')
@login_required
@admin_required
def admin_ver_cliente(id):
    """Visualiza perfil do cliente."""
    data = load_data()
    user_data = data['clientes'].get(id)
    if not user_data:
        return "Cliente não encontrado.", 404
    return render_template_string(CLIENTE_ADMIN_TEMPLATE, 
                                  cliente_id=id,
                                  user_data=user_data,
                                  solicitacoes=[s for s in data['solicitacoes'].values() if s['cliente_discord'] == id],
                                  cupons=[c for c in data['fidelidade']['cupons'].values() if c['usuario_discord'] == id])

CLIENTE_ADMIN_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Cliente - Admin</title>
    <style>
        body { background: #1a1a2e; color: #eee; font-family: Arial, sans-serif; padding: 20px; }
        .container { max-width: 1000px; margin: 0 auto; background: #16213e; padding: 20px; border-radius: 10px; }
        .btn { display: inline-block; padding: 6px 12px; background: #e94560; color: white; text-decoration: none; border-radius: 4px; border: none; cursor: pointer; }
        .btn-success { background: #2ecc71; }
        table { width: 100%; border-collapse: collapse; margin-top: 10px; }
        th, td { padding: 8px; border-bottom: 1px solid #2c3e50; text-align: left; }
        th { color: #e94560; }
    </style>
</head>
<body>
<div class="container">
    <a href="{{ url_for('admin_panel') }}" class="btn">← Voltar</a>
    <h1>Cliente: {{ cliente_id }}</h1>
    <p><strong>UID:</strong> {{ user_data.uid }}</p>
    <p><strong>Nick:</strong> {{ user_data.nick }}</p>
    <p><strong>Pontos:</strong> {{ user_data.pontos }}</p>
    <p><strong>Total ganhos:</strong> {{ user_data.total_ganhos }}</p>
    <p><strong>Total usados:</strong> {{ user_data.total_usados }}</p>
    <p><strong>Última compra:</strong> {{ user_data.ultima_compra or 'Nenhuma' }}</p>
    <p><strong>Último resgate:</strong> {{ user_data.ultimo_resgate or 'Nenhum' }}</p>

    <h2>Histórico</h2>
    <table>
        <thead><tr><th>Serviço</th><th>Valor</th><th>Pontos</th><th>Status</th><th>Data</th></tr></thead>
        <tbody>
        {% for h in user_data.historico %}
            <tr><td>{{ h.servico_nome }}</td><td>{{ h.valor }}</td><td>{{ h.pontos }}</td><td>{{ h.status }}</td><td>{{ h.data_conclusao or h.data_inicio }}</td></tr>
        {% endfor %}
        </tbody>
    </table>

    <h2>Solicitações</h2>
    <table>
        <thead><tr><th>Serviço</th><th>Status</th><th>Data</th></tr></thead>
        <tbody>
        {% for s in solicitacoes %}
            <tr><td>{{ s.servico_nome }}</td><td>{{ s.status }}</td><td>{{ s.data }}</td></tr>
        {% endfor %}
        </tbody>
    </table>

    <h2>Cupons</h2>
    <table>
        <thead><tr><th>Código</th><th>Status</th><th>Validade</th></tr></thead>
        <tbody>
        {% for c in cupons %}
            <tr><td>{{ c.codigo }}</td><td>{{ c.status }}</td><td>{{ c.validade }}</td></tr>
        {% endfor %}
        </tbody>
    </table>

    <!-- Formulário para editar UID/Nick/Pontos -->
    <h2>Editar</h2>
    <form method="POST" action="{{ url_for('admin_editar_cliente', id=cliente_id) }}">
        <label>UID</label>
        <input type="text" name="uid" value="{{ user_data.uid }}">
        <label>Nick</label>
        <input type="text" name="nick" value="{{ user_data.nick }}">
        <label>Pontos (saldo)</label>
        <input type="number" name="pontos" value="{{ user_data.pontos }}">
        <button type="submit" class="btn btn-success">Salvar</button>
    </form>
</div>
</body>
</html>
"""

@app.route('/admin/cliente/<id>/editar', methods=['POST'])
@login_required
@admin_required
def admin_editar_cliente(id):
    """Edita dados do cliente."""
    data = load_data()
    if id not in data['clientes']:
        return "Cliente não encontrado.", 404
    user_data = data['clientes'][id]
    uid = request.form.get('uid')
    nick = request.form.get('nick')
    pontos = request.form.get('pontos')
    if uid:
        user_data['uid'] = uid
    if nick:
        user_data['nick'] = nick
    if pontos is not None:
        user_data['pontos'] = int(pontos)
    save_data(data)
    return redirect(url_for('admin_ver_cliente', id=id))

# =========================================================
# ADMIN - FIDELIDADE (CONFIGURAÇÕES)
# =========================================================

@app.route('/admin/config_fidelidade', methods=['POST'])
@login_required
@admin_required
def admin_config_fidelidade():
    """Atualiza configurações de fidelidade."""
    data = load_data()
    data['fidelidade']['pontos_por_real'] = float(request.form.get('pontos_por_real', 1))
    data['fidelidade']['validade_pontos_dias'] = int(request.form.get('validade_pontos_dias', 90))
    data['fidelidade']['validade_cupons_dias'] = int(request.form.get('validade_cupons_dias', 30))
    save_data(data)
    return redirect(url_for('admin_panel'))

@app.route('/admin/recompensa', methods=['POST'])
@login_required
@admin_required
def admin_recompensa():
    """Adiciona uma recompensa."""
    data = load_data()
    nome = request.form.get('nome')
    pontos = int(request.form.get('pontos', 0))
    tipo = request.form.get('tipo')
    valor = request.form.get('valor')
    if valor:
        valor = float(valor)
    else:
        valor = None
    new_id = str(uuid.uuid4())[:8]
    data['fidelidade']['recompensas'].append({
        'id': new_id,
        'nome': nome,
        'pontos': pontos,
        'tipo': tipo,
        'valor': valor
    })
    save_data(data)
    return redirect(url_for('admin_panel'))

@app.route('/admin/recompensa/<id>', methods=['DELETE'])
@login_required
@admin_required
def admin_delete_recompensa(id):
    data = load_data()
    data['fidelidade']['recompensas'] = [r for r in data['fidelidade']['recompensas'] if r['id'] != id]
    save_data(data)
    return '', 204

# =========================================================
# ADMIN - CONFIGURAÇÕES GERAIS
# =========================================================

@app.route('/admin/config', methods=['POST'])
@login_required
@admin_required
def admin_config():
    """Atualiza configurações gerais (admin_ids)."""
    data = load_data()
    admin_ids = request.form.get('admin_ids', '')
    data['config']['admin_ids'] = [id.strip() for id in admin_ids.split(',') if id.strip()]
    save_data(data)
    return redirect(url_for('admin_panel'))

# =========================================================
# PÁGINA PÚBLICA DE REGRAS
# =========================================================

@app.route('/regras')
def regras():
    """Página pública com regras do sistema de fidelidade."""
    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Regras - Sistema de Fidelidade</title>
        <style>
            body { background: #1a1a2e; color: #eee; font-family: Arial, sans-serif; padding: 20px; }
            .container { max-width: 800px; margin: 0 auto; background: #16213e; padding: 30px; border-radius: 10px; }
            h1 { color: #e94560; text-align: center; }
            ul { list-style: none; padding: 0; }
            li { margin: 15px 0; padding-left: 20px; border-left: 3px solid #e94560; }
            .btn { display: inline-block; padding: 8px 16px; background: #e94560; color: white; text-decoration: none; border-radius: 5px; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>📜 Regras de Uso - Sistema de Fidelidade ZankonYTB</h1>
            <ul>
                <li><strong>1. Pontos Pessoais e Vinculados ao UID</strong><br>
                Seus pontos são pessoais, intransferíveis e atrelados diretamente ao seu cadastro e ao seu UID do jogo.<br>
                Não é permitido transferir pontos para amigos ou juntar o saldo de compras de contas diferentes para resgatar prêmios.</li>
                <li><strong>2. Cupons de Uso Único</strong><br>
                Ao trocar seus pontos, o sistema gera um código exclusivo para você.<br>
                Esse token é de uso único.<br>
                Uma vez inserido e validado no seu pedido, ele é consumido automaticamente e não poderá ser reutilizado.</li>
                <li><strong>3. Um Benefício por Pedido</strong><br>
                Os descontos e resgates não são cumulativos.<br>
                É permitido utilizar apenas um benefício por pedido.<br>
                Não é possível utilizar vários cupons juntos.</li>
                <li><strong>4. Validade</strong><br>
                Saldo de pontos expira após 90 dias sem novos serviços concluídos.<br>
                Cupons possuem validade de 30 dias após o resgate.</li>
            </ul>
            <div style="text-align: center; margin-top: 30px;">
                <a href="{{ url_for('index') }}" class="btn">← Voltar</a>
            </div>
        </div>
    </body>
    </html>
    """)

# =========================================================
# INICIALIZAÇÃO E EXECUÇÃO
# =========================================================

if __name__ == '__main__':
    # Carregar dados iniciais para criar estrutura se não existir
    data = load_data()
    # Garantir que as chaves existam
    for key in DEFAULT_DATA:
        if key not in data:
            data[key] = DEFAULT_DATA[key]
    save_data(data)

    # Iniciar o bot Discord (se token fornecido) em uma thread separada?
    # Como não é obrigatório, vamos apenas iniciar o Flask.
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
