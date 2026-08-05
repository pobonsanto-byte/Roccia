# =========================================================
# MÓDULO DE FIDELIDADE, CLIENTES E SERVIÇOS
# =========================================================
import json
import secrets
import re
from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, session, jsonify
from main import (
    salvar_dados_github,
    obter_dados_fila,
    agora_br,
    adicionar_log,
    bot,
    GUILD_ID,
    dados,
    escape_html
)

# =========================================================
# BLUEPRINT
# =========================================================
fidelidade_bp = Blueprint('fidelidade', __name__)

# =========================================================
# FUNÇÕES AUXILIARES DE INICIALIZAÇÃO E MANIPULAÇÃO
# =========================================================
def _inicializar_chave(chave, valor_padrao):
    if chave not in dados:
        dados[chave] = valor_padrao
        salvar_dados_github(f"Inicializada chave {chave}")

def _obter_clientes():
    _inicializar_chave("clientes", {})
    return dados["clientes"]

def _obter_servicos():
    _inicializar_chave("servicos", {})
    return dados["servicos"]

def _obter_solicitacoes():
    _inicializar_chave("solicitacoes", {})
    return dados["solicitacoes"]

def _obter_fidelidade_config():
    _inicializar_chave("fidelidade", {
        "pontos_por_real": 1,
        "recompensas": [
            {"id": "recompensa_60", "nome": "1 Dia de Quests Diárias Grátis", "pontos": 60, "tipo": "servico"},
            {"id": "recompensa_100", "nome": "Desafio Rápido | Portinha | Hologramas de Huanglong", "pontos": 100, "tipo": "servico"},
            {"id": "recompensa_100b", "nome": "Cupom R$ 5", "pontos": 100, "tipo": "cupom", "valor": 5},
            {"id": "recompensa_200", "nome": "Análise de Conta | Companion Quest", "pontos": 200, "tipo": "servico"},
            {"id": "recompensa_200b", "nome": "Cupom R$ 10", "pontos": 200, "tipo": "cupom", "valor": 10},
            {"id": "recompensa_400", "nome": "Build Completa", "pontos": 400, "tipo": "servico"},
            {"id": "recompensa_400b", "nome": "Cupom R$ 20", "pontos": 400, "tipo": "cupom", "valor": 20}
        ],
        "cupons_gerados": {}
    })
    return dados["fidelidade"]

def _gerar_token():
    return f"ZANKON-{secrets.token_hex(4).upper()}"

def _obter_cliente_por_discord(discord_id):
    clientes = _obter_clientes()
    return clientes.get(str(discord_id))

def _criar_ou_atualizar_cliente(discord_id, game_nick, uid):
    clientes = _obter_clientes()
    discord_id_str = str(discord_id)
    if discord_id_str not in clientes:
        clientes[discord_id_str] = {
            "uid": uid,
            "game_nick": game_nick,
            "pontos_atuais": 0,
            "pontos_acumulados": 0,
            "pontos_utilizados": 0,
            "ultima_compra": None,
            "ultimo_resgate": None
        }
    else:
        # Atualiza dados básicos (mantém pontos)
        clientes[discord_id_str]["uid"] = uid
        clientes[discord_id_str]["game_nick"] = game_nick
    salvar_dados_github(f"Cliente {discord_id} cadastrado/atualizado")
    return clientes[discord_id_str]

def _cliente_ja_possui_uid(uid):
    clientes = _obter_clientes()
    for c in clientes.values():
        if c.get("uid") == uid:
            return True
    return False

def _usuario_eh_membro(user_id):
    if not bot.is_ready():
        return False
    guild = bot.get_guild(int(GUILD_ID))
    if not guild:
        return False
    member = guild.get_member(int(user_id))
    return member is not None

def _usuario_eh_admin():
    return session.get('usuario', {}).get('eh_admin', False)

# =========================================================
# FUNÇÃO DE PROCESSAMENTO DE CONCLUSÃO DE SERVIÇO
# (chamada pelo main.py após concluir um serviço na fila)
# =========================================================
def processar_conclusao_servico(entrada_fila):
    """
    Entrada_fila é o dicionário retornado por concluir_servico().
    Espera-se que contenha 'usuario_id' (discord_id) e 'servico' (nome do serviço).
    """
    if not entrada_fila:
        return

    discord_id = entrada_fila.get("usuario_id")
    if not discord_id:
        # Se não tiver ID, tenta encontrar pelo nome (fallback)
        # Mas é melhor que o admin tenha preenchido o usuario_id
        adicionar_log(f"Conclusão sem usuario_id: {entrada_fila.get('nome_usuario')}")
        return

    cliente = _obter_cliente_por_discord(discord_id)
    if not cliente:
        adicionar_log(f"Cliente {discord_id} não encontrado para conclusão de serviço.")
        return

    # Busca o serviço pelo nome (pode ser aprimorado com ID)
    servicos = _obter_servicos()
    servico_encontrado = None
    nome_servico = entrada_fila.get("servico", "").strip()
    for sid, svc in servicos.items():
        if svc.get("nome", "").lower() == nome_servico.lower():
            servico_encontrado = svc
            break

    if not servico_encontrado:
        # Tenta buscar por serviço com nome parecido? Por enquanto, usa pontos padrão 10
        pontos_ganhos = 10
        adicionar_log(f"Serviço '{nome_servico}' não encontrado, usando 10 pontos padrão.")
    else:
        pontos_ganhos = servico_encontrado.get("pontos_gerados", 10)

    # Atualiza pontos do cliente
    cliente["pontos_atuais"] = cliente.get("pontos_atuais", 0) + pontos_ganhos
    cliente["pontos_acumulados"] = cliente.get("pontos_acumulados", 0) + pontos_ganhos
    cliente["ultima_compra"] = agora_br().strftime("%Y-%m-%d")

    salvar_dados_github(f"Conclusão de serviço: +{pontos_ganhos} pontos para {discord_id}")
    adicionar_log(f"Serviço concluído: {nome_servico} para {discord_id}, +{pontos_ganhos} pontos")

# =========================================================
# ROTAS PÚBLICAS
# =========================================================
@fidelidade_bp.route("/regras-fidelidade")
def regras_fidelidade():
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Regras do Sistema de Fidelidade</title>
        <style>
            body { font-family: 'Segoe UI', sans-serif; background: #121212; color: #e0e0e0; padding: 20px; }
            .container { max-width: 800px; margin: 0 auto; background: #1a1a1a; padding: 30px; border-radius: 10px; border: 1px solid #333; }
            h1 { color: #5865F2; }
            h2 { color: #f59e0b; margin-top: 20px; }
            ul { list-style: none; padding: 0; }
            li { padding: 8px 0; border-bottom: 1px solid #333; }
            .highlight { color: #4ade80; }
            .footer { margin-top: 30px; text-align: center; color: #888; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>📋 Regras do Sistema de Fidelidade</h1>
            <p>Bem-vindo ao nosso programa de fidelidade! Aqui estão as regras e como funcionam os pontos e cupons.</p>

            <h2>🔄 Como Funciona</h2>
            <ul>
                <li>✅ Cada R$ 1,00 gasto em serviços gera <span class="highlight">1 ponto</span>.</li>
                <li>✅ Pontos podem ser acumulados e trocados por recompensas na Loja de Fidelidade.</li>
                <li>✅ Os pontos têm validade indefinida, mas recomenda-se usar antes de 1 ano.</li>
            </ul>

            <h2>🎁 Tabela de Recompensas</h2>
            <ul>
                <li><strong>60 pontos:</strong> 1 Dia de Quests Diárias Grátis</li>
                <li><strong>100 pontos:</strong> Desafio Rápido | Portinha | Hologramas de Huanglong <strong>OU</strong> Cupom R$ 5</li>
                <li><strong>200 pontos:</strong> Análise de Conta | Companion Quest <strong>OU</strong> Cupom R$ 10</li>
                <li><strong>400 pontos:</strong> Build Completa <strong>OU</strong> Cupom R$ 20</li>
            </ul>

            <h2>🎟️ Cupons</h2>
            <ul>
                <li>🎫 Cupons são gerados com um token único (ex: ZANKON-4A8F92BC).</li>
                <li>📅 Validade de <span class="highlight">30 dias</span> a partir da data de resgate.</li>
                <li>💳 Podem ser aplicados em solicitações de serviços para obter desconto.</li>
                <li>⚠️ Cada cupom só pode ser usado uma única vez.</li>
            </ul>

            <h2>📝 Solicitação de Serviços</h2>
            <ul>
                <li>📌 Após a aprovação da solicitação, o serviço é colocado na fila de espera.</li>
                <li>⏳ Quando o serviço for concluído, os pontos são creditados automaticamente.</li>
                <li>🛑 Serviços recusados não geram pontos.</li>
            </ul>

            <div class="footer">
                <p>Em caso de dúvidas, entre em contato com a administração.</p>
                <a href="/" style="color: #5865F2;">← Voltar ao início</a>
            </div>
        </div>
    </body>
    </html>
    '''

# =========================================================
# ROTAS DO CLIENTE
# =========================================================
@fidelidade_bp.route("/cliente")
def cliente_painel():
    if 'usuario' not in session:
        return redirect(url_for('login'))

    user_id = session['usuario']['id']
    if not _usuario_eh_membro(user_id):
        return "<h2>⛔ Acesso negado</h2><p>Você não faz parte do servidor oficial.</p><a href='/'>Voltar</a>", 403

    cliente = _obter_cliente_por_discord(user_id)
    if not cliente:
        return redirect(url_for('fidelidade.cliente_cadastro'))

    # Busca solicitações do cliente
    solicitacoes = _obter_solicitacoes()
    minhas_solicitacoes = [s for s in solicitacoes.values() if s.get("cliente_discord_id") == user_id]
    minhas_solicitacoes.sort(key=lambda x: x.get("data_solicitacao", ""), reverse=True)

    # Cupons do cliente
    fidelidade_config = _obter_fidelidade_config()
    cupons = fidelidade_config.get("cupons_gerados", {})
    meus_cupons = [c for c in cupons.values() if c.get("discord_id") == user_id and c.get("status") == "ativo"]

    # Serviços ativos
    servicos = _obter_servicos()
    servicos_ativos = [s for s in servicos.values() if s.get("status") == "ativo"]

    # Pontos e progresso
    pontos_atuais = cliente.get("pontos_atuais", 0)
    recompensas = fidelidade_config.get("recompensas", [])
    proxima_recompensa = None
    for r in sorted(recompensas, key=lambda x: x.get("pontos", 999)):
        if r.get("pontos", 0) > pontos_atuais:
            proxima_recompensa = r
            break

    # Barra de progresso
    if proxima_recompensa:
        pontos_necessarios = proxima_recompensa["pontos"]
        progresso = int((pontos_atuais / pontos_necessarios) * 100) if pontos_necessarios else 0
    else:
        progresso = 100

    return f'''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Painel do Cliente</title>
        <style>
            :root {{ --primary: #5865F2; --dark: #1a1a1a; --darker: #121212; --light: #e0e0e0; }}
            * {{ margin:0; padding:0; box-sizing:border-box; }}
            body {{ font-family: 'Segoe UI', sans-serif; background: var(--darker); color: var(--light); padding:20px; }}
            .container {{ max-width:1200px; margin:0 auto; }}
            .header {{ display:flex; justify-content:space-between; align-items:center; padding:15px; background:var(--dark); border-radius:10px; margin-bottom:20px; border:1px solid #333; }}
            .user-info {{ display:flex; align-items:center; gap:15px; }}
            .avatar {{ width:50px; height:50px; border-radius:50%; border:2px solid var(--primary); }}
            .btn {{ padding:8px 16px; border:none; border-radius:5px; cursor:pointer; text-decoration:none; display:inline-block; font-weight:600; transition:0.2s; }}
            .btn-primary {{ background:var(--primary); color:white; }}
            .btn-primary:hover {{ background:#4752C4; }}
            .btn-success {{ background:#10b981; color:white; }}
            .btn-danger {{ background:#ef4444; color:white; }}
            .btn-warning {{ background:#f59e0b; color:white; }}
            .btn-sm {{ padding:4px 10px; font-size:0.8rem; }}
            .card {{ background:var(--dark); border-radius:10px; padding:20px; margin-bottom:20px; border:1px solid #333; }}
            .grid-2 {{ display:grid; grid-template-columns:repeat(2,1fr); gap:20px; }}
            .grid-3 {{ display:grid; grid-template-columns:repeat(3,1fr); gap:20px; }}
            .stat-card {{ background:linear-gradient(135deg, var(--primary), #4752C4); padding:15px; border-radius:8px; text-align:center; color:white; }}
            .stat-card h3 {{ font-size:2rem; }}
            .progress-bar {{ background:#333; border-radius:20px; height:20px; overflow:hidden; margin:10px 0; }}
            .progress-fill {{ background:linear-gradient(90deg, #f59e0b, #ef4444); height:100%; width:0%; }}
            table {{ width:100%; border-collapse:collapse; }}
            th, td {{ padding:10px; text-align:left; border-bottom:1px solid #333; }}
            th {{ background:#333; }}
            .tag {{ background:var(--primary); padding:2px 8px; border-radius:10px; font-size:0.7rem; }}
            .flex {{ display:flex; gap:10px; flex-wrap:wrap; }}
            @media (max-width:768px) {{ .grid-2, .grid-3 {{ grid-template-columns:1fr; }} }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <div class="user-info">
                    <img src="https://cdn.discordapp.com/avatars/{session['usuario']['id']}/{session['usuario'].get('avatar', '')}.png" class="avatar" onerror="this.src='https://cdn.discordapp.com/embed/avatars/0.png'">
                    <div>
                        <strong>{escape_html(session['usuario']['nome_usuario'])}</strong>
                        <br><span style="font-size:0.8rem;color:#888;">UID: {escape_html(cliente.get('uid', ''))} | Nick: {escape_html(cliente.get('game_nick', ''))}</span>
                    </div>
                </div>
                <div>
                    <a href="/cliente/solicitar" class="btn btn-success">➕ Solicitar Serviço</a>
                    <a href="/cliente/cupons" class="btn btn-warning">🎟️ Cupons</a>
                    <a href="/cliente/historico" class="btn btn-primary">📜 Histórico</a>
                    <a href="/" class="btn">🏠 Início</a>
                </div>
            </div>

            <div class="grid-3">
                <div class="stat-card"><h3>{cliente.get('pontos_atuais', 0)}</h3><p>Pontos Atuais</p></div>
                <div class="stat-card"><h3>{cliente.get('pontos_acumulados', 0)}</h3><p>Pontos Acumulados</p></div>
                <div class="stat-card"><h3>{cliente.get('pontos_utilizados', 0)}</h3><p>Pontos Utilizados</p></div>
            </div>

            <div class="card">
                <h4>Progresso para próxima recompensa</h4>
                {'<p>Próxima: ' + escape_html(proxima_recompensa['nome']) + ' (' + str(proxima_recompensa['pontos']) + ' pontos)</p>' if proxima_recompensa else '<p>🎉 Você já alcançou todas as recompensas disponíveis!</p>'}
                <div class="progress-bar">
                    <div class="progress-fill" style="width:{progresso}%;"></div>
                </div>
                <span style="font-size:0.8rem;color:#888;">{progresso}%</span>
            </div>

            <div class="grid-2">
                <div class="card">
                    <h3>📋 Serviços Ativos</h3>
                    <ul style="list-style:none;padding:0;">
                        {''.join(f'<li style="padding:5px 0;border-bottom:1px solid #333;">{escape_html(s["nome"])} - {s.get("pontos_gerados",0)} pts</li>' for s in servicos_ativos[:5])}
                        {'' if servicos_ativos else '<li>Nenhum serviço ativo no momento.</li>'}
                    </ul>
                </div>
                <div class="card">
                    <h3>⏳ Solicitações em Andamento</h3>
                    <ul style="list-style:none;padding:0;">
                        {''.join(f'<li style="padding:5px 0;border-bottom:1px solid #333;">#{escape_html(s["servico_id"])} - {escape_html(s.get("status",""))} <span class="tag">{escape_html(s.get("jogo",""))}</span></li>' for s in minhas_solicitacoes[:5] if s.get("status") in ["Aguardando Aprovação", "Em Andamento"])}
                        {'' if any(s.get("status") in ["Aguardando Aprovação", "Em Andamento"] for s in minhas_solicitacoes) else '<li>Nenhuma solicitação pendente.</li>'}
                    </ul>
                </div>
            </div>
        </div>
    </body>
    </html>
    '''

@fidelidade_bp.route("/cliente/cadastro", methods=["GET", "POST"])
def cliente_cadastro():
    if 'usuario' not in session:
        return redirect(url_for('login'))

    user_id = session['usuario']['id']
    if not _usuario_eh_membro(user_id):
        return "<h2>⛔ Acesso negado</h2><p>Você não faz parte do servidor oficial.</p><a href='/'>Voltar</a>", 403

    cliente = _obter_cliente_por_discord(user_id)
    if cliente:
        return redirect(url_for('fidelidade.cliente_painel'))

    if request.method == "POST":
        uid = request.form.get("uid", "").strip()
        game_nick = request.form.get("game_nick", "").strip()
        if not uid or not game_nick:
            return "<p>Preencha todos os campos.</p><a href='/cliente/cadastro'>Voltar</a>", 400

        # Verifica se UID já está vinculado a outra conta
        if _cliente_ja_possui_uid(uid):
            return "<p>❌ Este UID já está cadastrado em outra conta do Discord.</p><a href='/cliente/cadastro'>Voltar</a>", 400

        _criar_ou_atualizar_cliente(user_id, game_nick, uid)
        return redirect(url_for('fidelidade.cliente_painel'))

    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Cadastro de Cliente</title>
        <style>
            body { font-family: 'Segoe UI', sans-serif; background: #121212; color: #e0e0e0; display:flex; align-items:center; justify-content:center; height:100vh; }
            .card { background: #1a1a1a; padding: 30px; border-radius: 10px; border: 1px solid #333; max-width: 400px; width:100%; }
            label { display:block; margin:10px 0 5px; font-weight:600; color: #5865F2; }
            input { width:100%; padding:10px; background: #121212; border:1px solid #333; border-radius:5px; color:white; }
            button { margin-top:20px; width:100%; padding:10px; background: #5865F2; border:none; border-radius:5px; color:white; font-weight:bold; cursor:pointer; }
            button:hover { background: #4752C4; }
            .info { font-size:0.8rem; color:#888; margin-top:10px; }
        </style>
    </head>
    <body>
        <div class="card">
            <h2>📝 Cadastro Obrigatório</h2>
            <p>Para acessar o sistema de fidelidade, informe seus dados do jogo.</p>
            <form method="POST">
                <label>UID do Jogo</label>
                <input type="text" name="uid" placeholder="Ex: 123456789" required>
                <label>Nick do Jogo</label>
                <input type="text" name="game_nick" placeholder="Seu nome no jogo" required>
                <button type="submit">Cadastrar</button>
            </form>
            <div class="info">⚠️ Cada conta Discord pode ter apenas um UID vinculado.</div>
        </div>
    </body>
    </html>
    '''

@fidelidade_bp.route("/cliente/solicitar", methods=["GET", "POST"])
def cliente_solicitar():
    if 'usuario' not in session:
        return redirect(url_for('login'))

    user_id = session['usuario']['id']
    if not _usuario_eh_membro(user_id):
        return "<h2>⛔ Acesso negado</h2>", 403

    cliente = _obter_cliente_por_discord(user_id)
    if not cliente:
        return redirect(url_for('fidelidade.cliente_cadastro'))

    servicos = _obter_servicos()
    servicos_ativos = {sid: s for sid, s in servicos.items() if s.get("status") == "ativo"}

    if request.method == "POST":
        servico_id = request.form.get("servico_id")
        jogo = request.form.get("jogo", "").strip()
        observacoes = request.form.get("observacoes", "").strip()
        cupom_codigo = request.form.get("cupom", "").strip()

        if not servico_id or servico_id not in servicos_ativos:
            return "<p>Serviço inválido.</p><a href='/cliente/solicitar'>Voltar</a>", 400

        # Valida cupom, se fornecido
        fidelidade_config = _obter_fidelidade_config()
        cupons = fidelidade_config.get("cupons_gerados", {})
        cupom_valido = None
        if cupom_codigo:
            for cod, cupom in cupons.items():
                if cupom.get("codigo") == cupom_codigo and cupom.get("discord_id") == user_id and cupom.get("status") == "ativo":
                    # Verifica validade
                    validade_str = cupom.get("validade")
                    if validade_str:
                        validade = datetime.strptime(validade_str, "%Y-%m-%d")
                        if validade >= datetime.now():
                            cupom_valido = cupom
                            break
            if not cupom_valido:
                return "<p>❌ Cupom inválido, expirado ou não pertence a você.</p><a href='/cliente/solicitar'>Voltar</a>", 400

        # Cria solicitação
        solicitacoes = _obter_solicitacoes()
        solic_id = str(int(datetime.now().timestamp() * 1000))
        solicitacoes[solic_id] = {
            "cliente_discord_id": user_id,
            "servico_id": servico_id,
            "jogo": jogo,
            "observacoes": observacoes,
            "cupom_aplicado": cupom_codigo if cupom_valido else None,
            "status": "Aguardando Aprovação",
            "data_solicitacao": agora_br().isoformat()
        }

        # Se cupom foi usado, marca como utilizado
        if cupom_valido:
            cupom_valido["status"] = "utilizado"
            salvar_dados_github(f"Cupom {cupom_codigo} utilizado por {user_id}")

        salvar_dados_github(f"Solicitação criada: {solic_id} por {user_id}")
        adicionar_log(f"Solicitação {solic_id} criada por {user_id}")

        return redirect(url_for('fidelidade.cliente_painel'))

    # GET: exibe formulário
    cupons = _obter_fidelidade_config().get("cupons_gerados", {})
    meus_cupons = [c for c in cupons.values() if c.get("discord_id") == user_id and c.get("status") == "ativo"]

    html_servicos = ''.join(f'<option value="{sid}">{escape_html(s["nome"])} - {s.get("pontos_gerados",0)} pts</option>' for sid, s in servicos_ativos.items())
    html_cupons = ''.join(f'<option value="{c.get("codigo")}">{c.get("codigo")} (válido até {c.get("validade")})</option>' for c in meus_cupons)

    return f'''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Solicitar Serviço</title>
        <style>
            body {{ font-family: 'Segoe UI', sans-serif; background: #121212; color: #e0e0e0; padding:20px; }}
            .container {{ max-width:600px; margin:0 auto; background: #1a1a1a; padding:30px; border-radius:10px; border:1px solid #333; }}
            label {{ display:block; margin:10px 0 5px; font-weight:600; color: #5865F2; }}
            select, input, textarea {{ width:100%; padding:10px; background:#121212; border:1px solid #333; border-radius:5px; color:white; }}
            button {{ margin-top:20px; padding:10px 20px; background:#5865F2; border:none; border-radius:5px; color:white; font-weight:bold; cursor:pointer; }}
            button:hover {{ background:#4752C4; }}
            .info {{ font-size:0.8rem; color:#888; margin-top:10px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h2>📝 Solicitar Serviço</h2>
            <form method="POST">
                <label>Serviço</label>
                <select name="servico_id" required>
                    <option value="">Selecione</option>
                    {html_servicos}
                </select>
                <label>Jogo</label>
                <input type="text" name="jogo" placeholder="Ex: Wuthering Waves, Genshin Impact...">
                <label>Observações</label>
                <textarea name="observacoes" rows="3" placeholder="Detalhes adicionais..."></textarea>
                <label>Cupom de Desconto (opcional)</label>
                <select name="cupom">
                    <option value="">Nenhum</option>
                    {html_cupons}
                </select>
                <button type="submit">Enviar Solicitação</button>
            </form>
            <div class="info">A solicitação será analisada e, após aprovação, entrará na fila de espera.</div>
            <p><a href="/cliente" style="color:#5865F2;">← Voltar ao Painel</a></p>
        </div>
    </body>
    </html>
    '''

@fidelidade_bp.route("/cliente/historico")
def cliente_historico():
    if 'usuario' not in session:
        return redirect(url_for('login'))

    user_id = session['usuario']['id']
    if not _usuario_eh_membro(user_id):
        return "<h2>⛔ Acesso negado</h2>", 403

    solicitacoes = _obter_solicitacoes()
    minhas = [s for s in solicitacoes.values() if s.get("cliente_discord_id") == user_id]
    minhas.sort(key=lambda x: x.get("data_solicitacao", ""), reverse=True)

    rows = ''.join(f'''
        <tr>
            <td>{escape_html(s.get("servico_id", ""))}</td>
            <td>{escape_html(s.get("jogo", ""))}</td>
            <td><span class="tag">{escape_html(s.get("status", ""))}</span></td>
            <td>{escape_html(s.get("data_solicitacao", ""))}</td>
            <td>{escape_html(s.get("cupom_aplicado") or "Nenhum")}</td>
        </tr>
    ''' for s in minhas)

    return f'''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Histórico de Solicitações</title>
        <style>
            body {{ font-family: 'Segoe UI', sans-serif; background: #121212; color: #e0e0e0; padding:20px; }}
            .container {{ max-width:1000px; margin:0 auto; background:#1a1a1a; padding:20px; border-radius:10px; border:1px solid #333; }}
            table {{ width:100%; border-collapse:collapse; }}
            th, td {{ padding:10px; text-align:left; border-bottom:1px solid #333; }}
            th {{ background:#333; }}
            .tag {{ background:#5865F2; padding:2px 8px; border-radius:10px; font-size:0.7rem; }}
            a {{ color:#5865F2; text-decoration:none; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h2>📜 Histórico de Solicitações</h2>
            <table>
                <thead><tr><th>Serviço</th><th>Jogo</th><th>Status</th><th>Data</th><th>Cupom</th></tr></thead>
                <tbody>
                    {rows if rows else '<tr><td colspan="5">Nenhuma solicitação encontrada.</td></tr>'}
                </tbody>
            </table>
            <p><a href="/cliente">← Voltar ao Painel</a></p>
        </div>
    </body>
    </html>
    '''

@fidelidade_bp.route("/cliente/cupons")
def cliente_cupons():
    if 'usuario' not in session:
        return redirect(url_for('login'))

    user_id = session['usuario']['id']
    if not _usuario_eh_membro(user_id):
        return "<h2>⛔ Acesso negado</h2>", 403

    fidelidade_config = _obter_fidelidade_config()
    cupons = fidelidade_config.get("cupons_gerados", {})
    meus_cupons = [c for c in cupons.values() if c.get("discord_id") == user_id]

    rows = ''.join(f'''
        <tr>
            <td><code>{escape_html(c.get("codigo", ""))}</code></td>
            <td>{escape_html(c.get("tipo_recompensa", ""))}</td>
            <td>{escape_html(c.get("validade", ""))}</td>
            <td><span class="tag {'ativo' if c.get('status')=='ativo' else 'inativo'}">{escape_html(c.get("status", ""))}</span></td>
        </tr>
    ''' for c in meus_cupons)

    return f'''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Meus Cupons</title>
        <style>
            body {{ font-family: 'Segoe UI', sans-serif; background: #121212; color: #e0e0e0; padding:20px; }}
            .container {{ max-width:900px; margin:0 auto; background:#1a1a1a; padding:20px; border-radius:10px; border:1px solid #333; }}
            table {{ width:100%; border-collapse:collapse; }}
            th, td {{ padding:10px; text-align:left; border-bottom:1px solid #333; }}
            th {{ background:#333; }}
            .tag {{ padding:2px 8px; border-radius:10px; font-size:0.7rem; }}
            .ativo {{ background:#10b981; }}
            .inativo {{ background:#ef4444; }}
            a {{ color:#5865F2; text-decoration:none; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h2>🎟️ Meus Cupons</h2>
            <table>
                <thead><tr><th>Código</th><th>Tipo</th><th>Validade</th><th>Status</th></tr></thead>
                <tbody>
                    {rows if rows else '<tr><td colspan="4">Nenhum cupom encontrado.</td></tr>'}
                </tbody>
            </table>
            <p><a href="/cliente">← Voltar ao Painel</a></p>
        </div>
    </body>
    </html>
    '''

# =========================================================
# ROTAS ADMIN
# =========================================================
@fidelidade_bp.route("/admin/servicos", methods=["GET", "POST"])
def admin_servicos():
    if not _usuario_eh_admin():
        return "Acesso restrito a administradores.", 403

    servicos = _obter_servicos()

    if request.method == "POST":
        # Adicionar ou atualizar
        servico_id = request.form.get("servico_id")
        nome = request.form.get("nome")
        categoria = request.form.get("categoria")
        descricao = request.form.get("descricao")
        valor_reais = float(request.form.get("valor_reais", 0))
        pontos_gerados = int(request.form.get("pontos_gerados", 0))
        status = request.form.get("status", "ativo")
        imagem_url = request.form.get("imagem_url", "")

        if servico_id and servico_id in servicos:
            # Atualização
            servicos[servico_id].update({
                "nome": nome, "categoria": categoria, "descricao": descricao,
                "valor_reais": valor_reais, "pontos_gerados": pontos_gerados,
                "status": status, "imagem_url": imagem_url
            })
            mensagem = f"Serviço {servico_id} atualizado"
        else:
            # Novo
            novo_id = str(int(datetime.now().timestamp() * 1000))
            servicos[novo_id] = {
                "nome": nome, "categoria": categoria, "descricao": descricao,
                "valor_reais": valor_reais, "pontos_gerados": pontos_gerados,
                "status": status, "imagem_url": imagem_url
            }
            mensagem = f"Serviço {novo_id} criado"

        salvar_dados_github(mensagem)
        adicionar_log(mensagem)
        return redirect(url_for('fidelidade.admin_servicos'))

    # GET: exibe lista e formulário
    rows = ''.join(f'''
        <tr>
            <td>{escape_html(s.get("nome", ""))}</td>
            <td>{escape_html(s.get("categoria", ""))}</td>
            <td>R$ {s.get("valor_reais", 0):.2f}</td>
            <td>{s.get("pontos_gerados", 0)} pts</td>
            <td><span class="tag {s.get('status', '')}">{escape_html(s.get("status", ""))}</span></td>
            <td>
                <button onclick="editar('{sid}')" class="btn btn-primary btn-sm">✏️</button>
                <form method="POST" style="display:inline;" onsubmit="return confirm('Remover este serviço?')">
                    <input type="hidden" name="_method" value="DELETE">
                    <input type="hidden" name="servico_id" value="{sid}">
                    <button type="submit" class="btn btn-danger btn-sm">🗑️</button>
                </form>
            </td>
        </tr>
    ''' for sid, s in servicos.items())

    return f'''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Gerenciar Serviços</title>
        <style>
            body {{ font-family: 'Segoe UI', sans-serif; background: #121212; color: #e0e0e0; padding:20px; }}
            .container {{ max-width:1200px; margin:0 auto; }}
            .card {{ background:#1a1a1a; padding:20px; border-radius:10px; border:1px solid #333; margin-bottom:20px; }}
            table {{ width:100%; border-collapse:collapse; }}
            th, td {{ padding:10px; text-align:left; border-bottom:1px solid #333; }}
            th {{ background:#333; }}
            .tag {{ padding:2px 8px; border-radius:10px; font-size:0.7rem; }}
            .ativo {{ background:#10b981; }}
            .inativo {{ background:#ef4444; }}
            .btn {{ padding:6px 12px; border:none; border-radius:5px; cursor:pointer; text-decoration:none; display:inline-block; font-weight:600; }}
            .btn-primary {{ background:#5865F2; color:white; }}
            .btn-danger {{ background:#ef4444; color:white; }}
            .btn-sm {{ padding:4px 8px; font-size:0.8rem; }}
            .form-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:10px; }}
            label {{ display:block; margin:5px 0; font-weight:600; color:#5865F2; }}
            input, select, textarea {{ width:100%; padding:8px; background:#121212; border:1px solid #333; border-radius:5px; color:white; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>📦 Gerenciar Serviços</h1>
            <a href="/dashboard" class="btn btn-primary">← Voltar ao Painel</a>

            <div class="card">
                <h2>Cadastrar / Editar Serviço</h2>
                <form method="POST" id="servicoForm">
                    <input type="hidden" name="servico_id" id="edit_id">
                    <div class="form-grid">
                        <div><label>Nome</label><input type="text" name="nome" id="edit_nome" required></div>
                        <div><label>Categoria</label><input type="text" name="categoria" id="edit_categoria"></div>
                        <div><label>Descrição</label><textarea name="descricao" id="edit_descricao" rows="2"></textarea></div>
                        <div><label>Valor (R$)</label><input type="number" step="0.01" name="valor_reais" id="edit_valor" required></div>
                        <div><label>Pontos Gerados</label><input type="number" name="pontos_gerados" id="edit_pontos" required></div>
                        <div><label>Status</label>
                            <select name="status" id="edit_status">
                                <option value="ativo">Ativo</option>
                                <option value="inativo">Inativo</option>
                            </select>
                        </div>
                        <div style="grid-column:span 2;"><label>URL da Imagem</label><input type="url" name="imagem_url" id="edit_imagem" placeholder="https://..."></div>
                    </div>
                    <button type="submit" class="btn btn-primary">💾 Salvar</button>
                    <button type="button" onclick="document.getElementById('servicoForm').reset(); document.getElementById('edit_id').value='';" class="btn">🔄 Limpar</button>
                </form>
            </div>

            <div class="card">
                <h2>Lista de Serviços</h2>
                <table>
                    <thead><tr><th>Nome</th><th>Categoria</th><th>Valor</th><th>Pontos</th><th>Status</th><th>Ações</th></tr></thead>
                    <tbody>
                        {rows if rows else '<tr><td colspan="6">Nenhum serviço cadastrado.</td></tr>'}
                    </tbody>
                </table>
            </div>
        </div>
        <script>
            function editar(id) {{
                fetch('/admin/servicos/api/' + id)
                    .then(r => r.json())
                    .then(data => {{
                        if(data.sucesso) {{
                            document.getElementById('edit_id').value = id;
                            document.getElementById('edit_nome').value = data.servico.nome || '';
                            document.getElementById('edit_categoria').value = data.servico.categoria || '';
                            document.getElementById('edit_descricao').value = data.servico.descricao || '';
                            document.getElementById('edit_valor').value = data.servico.valor_reais || 0;
                            document.getElementById('edit_pontos').value = data.servico.pontos_gerados || 0;
                            document.getElementById('edit_status').value = data.servico.status || 'ativo';
                            document.getElementById('edit_imagem').value = data.servico.imagem_url || '';
                        }}
                    }});
            }}
        </script>
    </body>
    </html>
    '''

@fidelidade_bp.route("/admin/servicos/api/<servico_id>")
def admin_servicos_api(servico_id):
    if not _usuario_eh_admin():
        return jsonify({"sucesso": False}), 403
    servicos = _obter_servicos()
    servico = servicos.get(servico_id)
    if not servico:
        return jsonify({"sucesso": False}), 404
    return jsonify({"sucesso": True, "servico": servico})

@fidelidade_bp.route("/admin/servicos", methods=["DELETE"])
def admin_servicos_delete():
    if not _usuario_eh_admin():
        return jsonify({"sucesso": False}), 403
    servico_id = request.form.get("servico_id")
    if servico_id and servico_id in _obter_servicos():
        del dados["servicos"][servico_id]
        salvar_dados_github(f"Serviço {servico_id} removido")
        adicionar_log(f"Serviço {servico_id} removido")
    return redirect(url_for('fidelidade.admin_servicos'))

@fidelidade_bp.route("/admin/solicitacoes")
def admin_solicitacoes():
    if not _usuario_eh_admin():
        return "Acesso restrito.", 403

    solicitacoes = _obter_solicitacoes()
    servicos = _obter_servicos()
    clientes = _obter_clientes()

    # Ordenar por data decrescente
    sorted_solic = sorted(solicitacoes.items(), key=lambda x: x[1].get("data_solicitacao", ""), reverse=True)

    rows = ''
    for sid, s in sorted_solic:
        cliente = clientes.get(s.get("cliente_discord_id", ""), {})
        servico = servicos.get(s.get("servico_id", ""), {})
        status = s.get("status", "")
        acoes = ''
        if status == "Aguardando Aprovação":
            acoes = f'''
                <button onclick="aprovar('{sid}')" class="btn btn-success btn-sm">✅ Aprovar</button>
                <button onclick="recusar('{sid}')" class="btn btn-danger btn-sm">❌ Recusar</button>
            '''
        elif status == "Em Andamento":
            acoes = f'<span class="tag">Em execução</span>'
        else:
            acoes = f'<span class="tag">{escape_html(status)}</span>'

        rows += f'''
        <tr>
            <td>{escape_html(cliente.get("game_nick", "N/A"))}</td>
            <td>{escape_html(servico.get("nome", "N/A"))}</td>
            <td>{escape_html(s.get("jogo", ""))}</td>
            <td>{escape_html(s.get("observacoes", ""))}</td>
            <td><span class="tag {status}">{escape_html(status)}</span></td>
            <td>{escape_html(s.get("cupom_aplicado") or "Nenhum")}</td>
            <td>{acoes}</td>
        </tr>
        '''

    return f'''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Solicitações</title>
        <style>
            body {{ font-family: 'Segoe UI', sans-serif; background: #121212; color: #e0e0e0; padding:20px; }}
            .container {{ max-width:1200px; margin:0 auto; }}
            .card {{ background:#1a1a1a; padding:20px; border-radius:10px; border:1px solid #333; }}
            table {{ width:100%; border-collapse:collapse; }}
            th, td {{ padding:10px; text-align:left; border-bottom:1px solid #333; }}
            th {{ background:#333; }}
            .tag {{ padding:2px 8px; border-radius:10px; font-size:0.7rem; }}
            .Aguardando\\ Aprovação {{ background:#f59e0b; }}
            .Em\\ Andamento {{ background:#5865F2; }}
            .Concluído {{ background:#10b981; }}
            .Recusado {{ background:#ef4444; }}
            .btn {{ padding:4px 8px; border:none; border-radius:5px; cursor:pointer; text-decoration:none; display:inline-block; font-weight:600; font-size:0.8rem; }}
            .btn-success {{ background:#10b981; color:white; }}
            .btn-danger {{ background:#ef4444; color:white; }}
            a {{ color:#5865F2; text-decoration:none; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>📋 Gerenciar Solicitações</h1>
            <a href="/dashboard" class="btn" style="background:#5865F2;color:white;padding:8px 16px;border-radius:5px;">← Voltar</a>
            <div class="card">
                <table>
                    <thead><tr><th>Cliente</th><th>Serviço</th><th>Jogo</th><th>Observações</th><th>Status</th><th>Cupom</th><th>Ações</th></tr></thead>
                    <tbody>
                        {rows if rows else '<tr><td colspan="7">Nenhuma solicitação.</td></tr>'}
                    </tbody>
                </table>
            </div>
        </div>
        <script>
            function aprovar(id) {{
                if(!confirm('Aprovar esta solicitação? Ela será colocada na fila.')) return;
                fetch('/admin/solicitacoes/aprovar', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{solicitacao_id: id}})
                }}).then(() => location.reload());
            }}
            function recusar(id) {{
                const motivo = prompt('Motivo da recusa:');
                if(motivo === null) return;
                fetch('/admin/solicitacoes/recusar', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{solicitacao_id: id, motivo: motivo}})
                }}).then(() => location.reload());
            }}
        </script>
    </body>
    </html>
    '''

@fidelidade_bp.route("/admin/solicitacoes/aprovar", methods=["POST"])
def admin_solicitacoes_aprovar():
    if not _usuario_eh_admin():
        return jsonify({"sucesso": False}), 403

    data = request.json
    solicitacao_id = data.get("solicitacao_id")
    solicitacoes = _obter_solicitacoes()
    s = solicitacoes.get(solicitacao_id)
    if not s:
        return jsonify({"sucesso": False, "mensagem": "Solicitação não encontrada"}), 404

    if s.get("status") != "Aguardando Aprovação":
        return jsonify({"sucesso": False, "mensagem": "Status inválido"}), 400

    # Atualiza status
    s["status"] = "Em Andamento"
    salvar_dados_github(f"Solicitação {solicitacao_id} aprovada")

    # Adiciona à fila
    from main import adicionar_fila
    cliente_discord_id = s.get("cliente_discord_id")
    cliente = _obter_cliente_por_discord(cliente_discord_id)
    nome_cliente = cliente.get("game_nick", "Cliente") if cliente else "Cliente"
    servico = _obter_servicos().get(s.get("servico_id"), {})
    nome_servico = servico.get("nome", "Serviço")
    jogo = s.get("jogo", "")

    sucesso, _ = adicionar_fila(
        nome_usuario=nome_cliente,
        servico=nome_servico,
        jogo=jogo,
        usuario_id=cliente_discord_id  # importante para associar pontos na conclusão
    )

    if not sucesso:
        # Se falhar ao adicionar na fila, reverte status?
        s["status"] = "Aguardando Aprovação"
        salvar_dados_github(f"Falha ao adicionar {solicitacao_id} na fila")
        return jsonify({"sucesso": False, "mensagem": "Erro ao adicionar na fila"}), 500

    adicionar_log(f"Solicitação {solicitacao_id} aprovada e adicionada à fila")
    return jsonify({"sucesso": True})

@fidelidade_bp.route("/admin/solicitacoes/recusar", methods=["POST"])
def admin_solicitacoes_recusar():
    if not _usuario_eh_admin():
        return jsonify({"sucesso": False}), 403

    data = request.json
    solicitacao_id = data.get("solicitacao_id")
    motivo = data.get("motivo", "Sem motivo")
    solicitacoes = _obter_solicitacoes()
    s = solicitacoes.get(solicitacao_id)
    if not s:
        return jsonify({"sucesso": False}), 404

    if s.get("status") != "Aguardando Aprovação":
        return jsonify({"sucesso": False}), 400

    s["status"] = "Recusado"
    s["motivo_recusa"] = motivo
    salvar_dados_github(f"Solicitação {solicitacao_id} recusada: {motivo}")
    adicionar_log(f"Solicitação {solicitacao_id} recusada: {motivo}")
    return jsonify({"sucesso": True})

@fidelidade_bp.route("/admin/clientes")
def admin_clientes():
    if not _usuario_eh_admin():
        return "Acesso restrito.", 403

    clientes = _obter_clientes()
    rows = ''.join(f'''
        <tr>
            <td>{escape_html(c.get("uid", ""))}</td>
            <td>{escape_html(c.get("game_nick", ""))}</td>
            <td>{c.get("pontos_atuais", 0)}</td>
            <td>{c.get("pontos_acumulados", 0)}</td>
            <td>{c.get("ultima_compra") or "Nunca"}</td>
            <td>
                <button onclick="editar('{did}')" class="btn btn-primary btn-sm">✏️</button>
            </td>
        </tr>
    ''' for did, c in clientes.items())

    return f'''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Gerenciar Clientes</title>
        <style>
            body {{ font-family: 'Segoe UI', sans-serif; background: #121212; color: #e0e0e0; padding:20px; }}
            .container {{ max-width:1200px; margin:0 auto; }}
            .card {{ background:#1a1a1a; padding:20px; border-radius:10px; border:1px solid #333; }}
            table {{ width:100%; border-collapse:collapse; }}
            th, td {{ padding:10px; text-align:left; border-bottom:1px solid #333; }}
            th {{ background:#333; }}
            .btn {{ padding:4px 8px; border:none; border-radius:5px; cursor:pointer; text-decoration:none; display:inline-block; font-weight:600; font-size:0.8rem; }}
            .btn-primary {{ background:#5865F2; color:white; }}
            a {{ color:#5865F2; text-decoration:none; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>👥 Gerenciar Clientes</h1>
            <a href="/dashboard" class="btn" style="background:#5865F2;color:white;padding:8px 16px;border-radius:5px;">← Voltar</a>
            <div class="card">
                <table>
                    <thead><tr><th>UID</th><th>Nick</th><th>Pontos Atuais</th><th>Pontos Acumulados</th><th>Última Compra</th><th>Ações</th></tr></thead>
                    <tbody>
                        {rows if rows else '<tr><td colspan="6">Nenhum cliente cadastrado.</td></tr>'}
                    </tbody>
                </table>
            </div>
        </div>
        <script>
            function editar(id) {{
                // Implementar edição de pontos manual via prompt
                const novosPontos = prompt('Digite a nova quantidade de pontos para este cliente:');
                if(novosPontos !== null) {{
                    fetch('/admin/clientes/editar', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{discord_id: id, pontos_atuais: parseInt(novosPontos)}})
                    }}).then(() => location.reload());
                }}
            }}
        </script>
    </body>
    </html>
    '''

@fidelidade_bp.route("/admin/clientes/editar", methods=["POST"])
def admin_clientes_editar():
    if not _usuario_eh_admin():
        return jsonify({"sucesso": False}), 403

    data = request.json
    discord_id = data.get("discord_id")
    pontos_atuais = data.get("pontos_atuais")
    if pontos_atuais is None:
        return jsonify({"sucesso": False}), 400

    clientes = _obter_clientes()
    if discord_id not in clientes:
        return jsonify({"sucesso": False}), 404

    clientes[discord_id]["pontos_atuais"] = int(pontos_atuais)
    salvar_dados_github(f"Pontos de {discord_id} atualizados manualmente")
    return jsonify({"sucesso": True})

@fidelidade_bp.route("/admin/fidelidade", methods=["GET", "POST"])
def admin_fidelidade():
    if not _usuario_eh_admin():
        return "Acesso restrito.", 403

    config = _obter_fidelidade_config()

    if request.method == "POST":
        pontos_por_real = int(request.form.get("pontos_por_real", 1))
        config["pontos_por_real"] = max(1, pontos_por_real)
        salvar_dados_github("Configuração de fidelidade atualizada")
        return redirect(url_for('fidelidade.admin_fidelidade'))

    # Lista recompensas
    recompensas_html = ''.join(f'''
        <tr>
            <td>{escape_html(r.get("nome", ""))}</td>
            <td>{r.get("pontos", 0)}</td>
            <td>{escape_html(r.get("tipo", ""))}</td>
        </tr>
    ''' for r in config.get("recompensas", []))

    return f'''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Configuração Fidelidade</title>
        <style>
            body {{ font-family: 'Segoe UI', sans-serif; background: #121212; color: #e0e0e0; padding:20px; }}
            .container {{ max-width:800px; margin:0 auto; }}
            .card {{ background:#1a1a1a; padding:20px; border-radius:10px; border:1px solid #333; margin-bottom:20px; }}
            label {{ display:block; margin:10px 0 5px; font-weight:600; color:#5865F2; }}
            input, select {{ width:100%; padding:8px; background:#121212; border:1px solid #333; border-radius:5px; color:white; }}
            button {{ padding:8px 20px; background:#5865F2; border:none; border-radius:5px; color:white; font-weight:bold; cursor:pointer; }}
            table {{ width:100%; border-collapse:collapse; }}
            th, td {{ padding:10px; text-align:left; border-bottom:1px solid #333; }}
            th {{ background:#333; }}
            a {{ color:#5865F2; text-decoration:none; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>⚙️ Configuração de Fidelidade</h1>
            <a href="/dashboard" class="btn" style="background:#5865F2;color:white;padding:8px 16px;border-radius:5px;text-decoration:none;">← Voltar</a>
            <div class="card">
                <h2>Multiplicador de Pontos</h2>
                <form method="POST">
                    <label>Pontos por R$ 1,00</label>
                    <input type="number" name="pontos_por_real" value="{config.get('pontos_por_real', 1)}" min="1" required>
                    <button type="submit">Salvar</button>
                </form>
            </div>
            <div class="card">
                <h2>📋 Tabela de Recompensas</h2>
                <table>
                    <thead><tr><th>Nome</th><th>Pontos</th><th>Tipo</th></tr></thead>
                    <tbody>
                        {recompensas_html if recompensas_html else '<tr><td colspan="3">Nenhuma recompensa cadastrada.</td></tr>'}
                    </tbody>
                </table>
                <p style="margin-top:10px;font-size:0.8rem;color:#888;">As recompensas são fixas e podem ser ajustadas diretamente no JSON, se necessário.</p>
            </div>
        </div>
    </body>
    </html>
    '''

# =========================================================
# ROTA PARA RESGATE DE RECOMPENSAS (CLIENTE)
# =========================================================
@fidelidade_bp.route("/cliente/resgatar", methods=["POST"])
def cliente_resgatar():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401

    user_id = session['usuario']['id']
    cliente = _obter_cliente_por_discord(user_id)
    if not cliente:
        return jsonify({"sucesso": False, "mensagem": "Cliente não cadastrado"}), 400

    recompensa_id = request.json.get("recompensa_id")
    config = _obter_fidelidade_config()
    recompensa = None
    for r in config.get("recompensas", []):
        if r.get("id") == recompensa_id:
            recompensa = r
            break

    if not recompensa:
        return jsonify({"sucesso": False, "mensagem": "Recompensa inválida"}), 400

    pontos_necessarios = recompensa.get("pontos", 0)
    if cliente.get("pontos_atuais", 0) < pontos_necessarios:
        return jsonify({"sucesso": False, "mensagem": "Pontos insuficientes"}), 400

    # Desconta pontos
    cliente["pontos_atuais"] -= pontos_necessarios
    cliente["pontos_utilizados"] = cliente.get("pontos_utilizados", 0) + pontos_necessarios
    cliente["ultimo_resgate"] = agora_br().strftime("%Y-%m-%d")

    # Gera cupom se for do tipo cupom
    if recompensa.get("tipo") == "cupom":
        codigo = _gerar_token()
        validade = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
        cupom = {
            "codigo": codigo,
            "discord_id": user_id,
            "tipo_recompensa": recompensa.get("nome"),
            "validade": validade,
            "status": "ativo"
        }
        config["cupons_gerados"][codigo] = cupom
        mensagem = f"Cupom {codigo} gerado para {user_id}"
    else:
        # Para recompensas de serviço, pode-se registrar que foi resgatado e depois o admin aplicar manualmente
        mensagem = f"Recompensa '{recompensa.get('nome')}' resgatada por {user_id}"

    salvar_dados_github(f"Resgate: {mensagem}")
    adicionar_log(mensagem)
    return jsonify({"sucesso": True, "mensagem": "Recompensa resgatada com sucesso!", "cupom": codigo if recompensa.get("tipo") == "cupom" else None})

# =========================================================
# ROTA PARA VER CUPONS DISPONÍVEIS (API)
# =========================================================
@fidelidade_bp.route("/api/cupons/disponiveis")
def api_cupons_disponiveis():
    if 'usuario' not in session:
        return jsonify({"sucesso": False}), 401

    user_id = session['usuario']['id']
    config = _obter_fidelidade_config()
    cupons = config.get("cupons_gerados", {})
    ativos = [c for c in cupons.values() if c.get("discord_id") == user_id and c.get("status") == "ativo"]
    return jsonify({"sucesso": True, "cupons": ativos})
