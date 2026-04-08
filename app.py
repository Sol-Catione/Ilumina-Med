from flask import Flask, render_template, request, redirect, url_for, jsonify, session, Response
from werkzeug.utils import secure_filename
from sqlalchemy import create_engine, Column, Integer, String, Text, Boolean, func
from sqlalchemy.orm import sessionmaker
from core.models import Base, Parceiro, Venda, Gasto, Investimento, Servico, PagamentoOnline  # Adicionado Servico
import os
import re
import io
import csv
from groq import Groq
import urllib.parse
from functools import wraps
from datetime import datetime
import dotenv
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from werkzeug.security import check_password_hash
# Imports básicos
import json


# Carregamento explícito do .env para garantir que variáveis como CHAVE_PIX_ILUMINA sejam lidas
env_path = os.path.join(os.path.dirname(__file__), '.env')
dotenv.load_dotenv(env_path)

# Defaults do projeto (sede atual).
DEFAULT_WHATSAPP_ILUMINA = os.getenv("WHATSAPP_ILUMINA", "554588244623")  # Somente dígitos (DDI+DDD+Número)
SEDE_ENDERECO = os.getenv("SEDE_ENDERECO", "").strip()  # Endereço completo (privado): configure no ambiente
SEDE_MODO_ATENDIMENTO = (os.getenv("SEDE_MODO_ATENDIMENTO", "Ambos") or "Ambos").strip()
ENDERECO_PUBLICO = "Curitiba - Paraná"


def normalize_phone_for_whatsapp(raw_phone, default_phone=None) -> str:
    """
    Normaliza para o formato aceito pelo WhatsApp (somente dígitos).

    Não tenta adivinhar/alterar DDD ou inserir dígitos: apenas remove símbolos.
    """
    if default_phone is None:
        default_phone = DEFAULT_WHATSAPP_ILUMINA

    digits = re.sub(r"\D", "", str(raw_phone or "")).strip()
    if digits:
        return digits
    return re.sub(r"\D", "", str(default_phone or "")).strip()


def texto_tem_local_antigo(txt: str) -> bool:
    low = (txt or "").lower()
    return any(k in low for k in ("foz", "iguaçu", "iguacu", "cascavel"))


def normalize_cpf(raw_cpf: str) -> str:
    return re.sub(r"\D", "", str(raw_cpf or "")).strip()


def is_valid_email(raw_email: str) -> bool:
    email = (raw_email or "").strip()
    return ("@" in email) and ("." in email) and (len(email) >= 5)

# Importamos o novo ficheiro de pagamentos
try:
    import payments
except ImportError as e:
    print(f"Erro ao importar payments: {e}")
    payments = None


# Definição da tabela de depoimentos
class Avaliacao(Base):
    __tablename__ = 'avaliacoes'
    id = Column(Integer, primary_key=True)
    nome = Column(String(100))
    nota = Column(Integer)
    comentario = Column(Text)
    exibir = Column(Boolean, default=True)


app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "ilumina_secret_2026")

ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASS_HASH = os.getenv("ADMIN_PASS_HASH", "scrypt:32768:8:1$25xoFthW3FkRyW1e$478456caf72a8b3a7db8a00acb16ffeb248636a85001d14ef1600d6f52febb539e679e531b51320612af094268febb4fb15367abc343ed83b0cd5997788700ea")
BASE_URL = os.getenv("BASE_URL", "http://localhost:5000").rstrip("/")


# Configuracao do Banco de Dados
# - No Render: usa Postgres (Neon) via DATABASE_URL
# - No PC: usa SQLite local por padrao (ignora DATABASE_URL para evitar usar Neon sem querer)
# - No PC (opcional): se USE_DATABASE_URL=true, usa DATABASE_URL (para testar local com o mesmo banco do Render/Neon)
is_render = (os.getenv("RENDER", "").lower() == "true") or bool(os.getenv("RENDER_SERVICE_ID"))
use_database_url = os.getenv("USE_DATABASE_URL", "").strip().lower() in ("1", "true", "yes", "y", "sim")

if is_render:
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        raise RuntimeError("DATABASE_URL nao configurada no Render (Neon).")
else:
    database_url = os.getenv("DATABASE_URL", "") if use_database_url else ""
    if not database_url:
        database_url = "sqlite:///ilumina_med.db"

# Correcao para o SQLAlchemy funcionar com links antigos (postgres:// -> postgresql://)
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

engine = create_engine(database_url)
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for('login'))
        return f(*args, **kwargs)

    return decorated_function


def inicializar():
    db_session = Session()
    try:
        p = db_session.query(Parceiro).filter_by(id=1).first()

        if not p:
            # Criamos o parceiro mestre já com a flag eh_interno=True
            p = Parceiro(
                nome_fantasia="Ilúmina Med",
                whatsapp_professional=normalize_phone_for_whatsapp(DEFAULT_WHATSAPP_ILUMINA),
                eh_interno=True,
                endereco=SEDE_ENDERECO,
                modo_atendimento=SEDE_MODO_ATENDIMENTO
            )
            db_session.add(p)
            db_session.commit()
        else:
            p.nome_fantasia = "Ilúmina Med"
            p.eh_interno = True  # Garante que a conta mestre seja sempre interna
            p.whatsapp_professional = normalize_phone_for_whatsapp(p.whatsapp_professional, DEFAULT_WHATSAPP_ILUMINA)
            # Mantém o endereço sempre atualizado para a sede atual.
            if SEDE_ENDERECO:
                if not (p.endereco or "").strip() or texto_tem_local_antigo(p.endereco):
                    p.endereco = SEDE_ENDERECO
            else:
                # Se a sede não foi configurada no ambiente, ao menos evita manter endereço antigo.
                if texto_tem_local_antigo(p.endereco):
                    p.endereco = ""
            # Sede atende em casa (endereço) e também em domicílio.
            if (p.modo_atendimento or "").strip().lower() in ("", "domiciliar"):
                p.modo_atendimento = SEDE_MODO_ATENDIMENTO
            db_session.commit()

        # RESTAURANDO SERVIÇOS PADRÃO DA ILÚMINA MED (ID 1)
        servicos_padrao = [
            ("Avaliação Presencial", 250.00, "feridas"),
            ("Consultoria Online", 200.00, "feridas"),
            ("Laserterapia sistêmica - ILIB (sessão de 30min)", 200.00, "feridas"),
            ("Pacote 3 trocas de curativos com laser", 712.50, "feridas"),
            ("Pacote 5 trocas de curativos com laser", 1125.00, "feridas"),
            ("Pacote 10 trocas de curativos com laser", 2125.00, "feridas")
        ]

        for nome_s, valor_s, ficha_s in servicos_padrao:
            # Busca ignorando erros de coluna caso o banco ainda não esteja 100% atualizado
            existe = db_session.query(Servico).filter_by(nome=nome_s, parceiro_id=p.id).first()
            if not existe:
                novo_s = Servico(nome=nome_s, valor=valor_s, parceiro_id=p.id, tipo_ficha=ficha_s)
                db_session.add(novo_s)
            else:
                # Atualiza o tipo_ficha se estiver vazio
                if not existe.tipo_ficha:
                    existe.tipo_ficha = ficha_s

        db_session.commit()
        # SEED DE DEPOIMENTOS INICIAIS
        if db_session.query(Avaliacao).count() == 0:
            depoimentos = [
                Avaliacao(nome="Maria Silva", nota=5, comentario="Atendimento impecável! A enfermeira é muito atenciosa e o laser ajudou muito na minha cicatrização."),
                Avaliacao(nome="João Rocha", nota=5, comentario="Equipe muito profissional. O ambiente é acolhedor e os resultados do tratamento foram além das expectativas.")
            ]
            db_session.add_all(depoimentos)
            db_session.commit()

    except Exception as e:

        print(f"Aviso na inicialização: {e}")
        db_session.rollback()
    finally:
        db_session.close()


@app.route('/')
def index():
    db_session = Session()
    avaliacoes = db_session.query(Avaliacao).filter_by(exibir=True).all()
    # Buscamos todos os serviços para o autocomplete do site, ordenados por nome
    servicos_lista = db_session.query(Servico).order_by(Servico.nome.asc()).all()
    # Evita expor serviços/profissionais que ainda estejam cadastrados em outra cidade.
    servicos_lista = [
        s for s in servicos_lista
        if not (s.parceiro and texto_tem_local_antigo(s.parceiro.endereco))
    ]
    db_session.close()
    return render_template('index.html', avaliacoes=avaliacoes, servicos_disponiveis=servicos_lista)


@app.route('/privacidade')
def privacidade():
    return render_template('privacidade.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user_input = request.form.get('user')
        pass_input = request.form.get('pass')
        if user_input == ADMIN_USER and check_password_hash(ADMIN_PASS_HASH, pass_input):
            session['logged_in'] = True
            return redirect(url_for('admin_dashboard'))
        return "Acesso Negado"

    return f'''
    <body style="background:#013241; font-family:'Poppins', sans-serif; display:flex; align-items:center; justify-content:center; height:100vh; margin:0;">
        <form method="post" style="background:white; padding:40px; border-radius:15px; box-shadow:0 10px 25px rgba(0,0,0,0.2); text-align:center; width:300px;">
            <h2 style="color:#013241; margin-bottom:30px;">Painel Ilúmina</h2>
            <input name="user" placeholder="Usuário" style="width:100%; padding:12px; margin-bottom:15px; border:1px solid #ddd; border-radius:8px; box-sizing:border-box;">
            <input type="password" name="pass" placeholder="Senha" style="width:100%; padding:12px; margin-bottom:25px; border:1px solid #ddd; border-radius:8px; box-sizing:border-box;">
            <button type="submit" style="width:100%; background:#c5a059; color:white; border:none; padding:12px; border-radius:8px; font-weight:700; cursor:pointer;">ENTRAR</button>
        </form>
    </body>
    '''


@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('index'))


@app.route('/admin')
@login_required
def admin_dashboard():
    db_session = Session()
    
    # Date Filtering
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    
    query = db_session.query(Venda)
    
    if start_date and end_date:
        try:
            s_date = datetime.strptime(start_date, '%Y-%m-%d')
            e_date = datetime.strptime(end_date, '%Y-%m-%d').replace(hour=23, minute=59, second=59)
            query = query.filter(Venda.data_registro.between(s_date, e_date))
        except ValueError:
            pass # Ignore invalid dates
            
    vendas_todas = query.order_by(Venda.data_registro.desc()).all()

    vendas_detalhadas = []
    for v in vendas_todas:
        prof = db_session.query(Parceiro).get(v.parceiro_id)
        vendas_detalhadas.append({
            "venda": v,
            "profissional": prof.nome_fantasia if prof else "Não atribuído"
        })

    vendas_pagas = [v for v in vendas_todas if v.avaliacao_concluida]
    parceiros = db_session.query(Parceiro).all()

    gastos = db_session.query(Gasto).all()
    investimentos = db_session.query(Investimento).all()

    lucro_servicos_proprios = 0
    lucro_comissoes_parceiros = 0

    for v in vendas_pagas:
        p_venda = db_session.query(Parceiro).get(v.parceiro_id)
        if p_venda and p_venda.eh_interno:
            lucro_servicos_proprios += v.valor_total
        else:
            lucro_comissoes_parceiros += (v.valor_total * 0.20)

    total_faturado = sum(v.valor_total for v in vendas_pagas)
    total_comissao = lucro_servicos_proprios + lucro_comissoes_parceiros

    total_gastos = sum(g.valor for g in gastos)
    total_invest = sum(i.valor for i in investimentos)

    lucro_real = total_comissao - total_gastos - total_invest
    cor_lucro = "#21a44a" if lucro_real >= 0 else "#e74c3c"

    parceiros_info = []
    for p in parceiros:
        vendas_parceiro = [v for v in vendas_todas if v.parceiro_id == p.id and v.avaliacao_concluida]
        if p.eh_interno:
            lucro_parceiro = sum(v.valor_total for v in vendas_parceiro)
        else:
            lucro_parceiro = sum((v.valor_total * 0.20) for v in vendas_parceiro)
        parceiros_info.append((p, lucro_parceiro))

    servicos_objetos = db_session.query(Servico).all()
    servicos_cadastrados = []
    for s in servicos_objetos:
        p_nome = db_session.query(Parceiro).get(s.parceiro_id).nome_fantasia if s.parceiro_id else "Indefinido"
        servicos_cadastrados.append({
            "id": s.id,
            "nome": s.nome,
            "valor": s.valor,
            "parceiro_nome": p_nome,
            "tipo_ficha": s.tipo_ficha
        })

    # Buscar Avaliações
    avaliacoes_todas = db_session.query(Avaliacao).order_by(Avaliacao.id.desc()).all()

    db_session.close()

    return render_template('admin.html',
                           vendas_todas=vendas_todas,
                           vendas_detalhadas=vendas_detalhadas,
                           total_faturado=total_faturado,
                           total_comissao=total_comissao,
                           lucro_servicos_proprios=lucro_servicos_proprios,
                           lucro_comissoes_parceiros=lucro_comissoes_parceiros,
                           total_gastos=total_gastos,
                           total_invest=total_invest,
                           lucro_real=lucro_real,
                           cor_lucro=cor_lucro,
                           parceiros_info=parceiros_info,
                           servicos_cadastrados=servicos_cadastrados,
                           avaliacoes_todas=avaliacoes_todas,
                           base_url=BASE_URL)


@app.route('/exportar_financeiro')
@login_required
def exportar_financeiro():
    db_session = Session()
    vendas_pagas = db_session.query(Venda).filter_by(avaliacao_concluida=True).all()
    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')
    writer.writerow(['Data', 'Categoria', 'Descricao', 'Valor (R$)'])
    for v in vendas_pagas:
        writer.writerow(
            [v.data_registro.strftime('%d/%m/%Y'), 'Receita', f"Venda: {v.cliente_nome}", f"{v.valor_total:.2f}"])
    db_session.close()
    output.seek(0)
    return Response(output.getvalue(), mimetype="text/csv",
                    headers={"Content-disposition": "attachment; filename=financeiro_ilumina.csv"})


@app.route('/confirmar_pagamento/<int:venda_id>')
@login_required
def confirmar_pagamento(venda_id):
    db_session = Session()
    venda = db_session.query(Venda).get(venda_id)
    if venda:
        venda.avaliacao_concluida = True
        db_session.commit()
    db_session.close()
    return redirect(url_for('admin_dashboard'))


@app.route('/add_parceiro', methods=['POST'])
@login_required
def add_parceiro():
    db_session = Session()
    interno = True if request.form.get('eh_interno') == 'true' else False
    novo = Parceiro(
        nome_fantasia=request.form.get('nome'),
        whatsapp_professional=request.form.get('zap'),
        eh_interno=interno,
        modo_atendimento=request.form.get('modo', 'Domiciliar'),
        endereco=request.form.get('endereco', '')
    )
    db_session.add(novo)
    db_session.commit()
    db_session.close()
    return redirect(url_for('admin_dashboard'))


@app.route('/add_servico', methods=['POST'])
@login_required
def add_servico():
    db_session = Session()
    novo_serv = Servico(
        nome=request.form.get('nome'),
        valor=float(request.form.get('valor')),
        parceiro_id=int(request.form.get('parceiro_id')),
        tipo_ficha=request.form.get('tipo_ficha', 'feridas')
    )
    db_session.add(novo_serv)
    db_session.commit()
    db_session.close()
    return redirect(url_for('admin_dashboard'))


@app.route('/update_parceiro/<int:p_id>', methods=['POST'])
@login_required
def update_parceiro(p_id):
    db_session = Session()
    p = db_session.query(Parceiro).get(p_id)
    if p:
        p.nome_fantasia = request.form.get('nome')
        p.whatsapp_professional = normalize_phone_for_whatsapp(request.form.get('zap'), DEFAULT_WHATSAPP_ILUMINA)
        p.eh_interno = True if request.form.get('eh_interno') == 'true' else False
        p.modo_atendimento = request.form.get('modo')
        p.endereco = request.form.get('endereco', '')
        db_session.commit()
    db_session.close()
    return redirect(url_for('admin_dashboard'))


@app.route('/del_parceiro/<int:id>')
@login_required
def del_parceiro(id):
    if id == 1: return "Não é possível excluir o parceiro mestre", 403
    db_session = Session()
    p = db_session.query(Parceiro).get(id)
    if p:
        db_session.query(Servico).filter_by(parceiro_id=id).delete()
        db_session.delete(p)
        db_session.commit()
    db_session.close()
    return redirect(url_for('admin_dashboard'))


@app.route('/del_servico/<int:id>')
@login_required
def del_servico(id):
    db_session = Session()
    s = db_session.query(Servico).get(id)
    if s:
        db_session.delete(s)
        db_session.commit()
    db_session.close()
    return redirect(url_for('admin_dashboard'))


@app.route('/update_servico', methods=['POST'])
@login_required
def update_servico():
    db_session = Session()
    s_id = request.form.get('id')
    s = db_session.query(Servico).get(s_id)
    if s:
        s.nome = request.form.get('nome')
        s.valor = float(request.form.get('valor'))
        s.tipo_ficha = request.form.get('tipo_ficha')
        db_session.commit()
    db_session.close()
    return redirect(url_for('admin_dashboard'))

@app.route('/toggle_avaliacao/<int:id>')
@login_required
def toggle_avaliacao(id):
    db_session = Session()
    a = db_session.query(Avaliacao).get(id)
    if a:
        a.exibir = not a.exibir
        db_session.commit()
    db_session.close()
    return redirect(url_for('admin_dashboard'))


@app.route('/del_avaliacao/<int:id>')
@login_required
def del_avaliacao(id):
    db_session = Session()
    a = db_session.query(Avaliacao).get(id)
    if a:
        db_session.delete(a)
        db_session.commit()
    db_session.close()
    return redirect(url_for('admin_dashboard'))


@app.route('/add_manual_avaliacao', methods=['POST'])
@login_required
def add_manual_avaliacao():
    db_session = Session()
    nova = Avaliacao(
        nome=request.form.get('nome'),
        nota=int(request.form.get('nota')),
        comentario=request.form.get('comentario'),
        exibir=True
    )
    db_session.add(nova)
    db_session.commit()
    db_session.close()
    return redirect(url_for('admin_dashboard'))


@app.route('/listar_servicos_json')

def listar_servicos_json():
    db_session = Session()
    servicos = db_session.query(Servico).order_by(Servico.nome.asc()).all()
    lista = []
    for s in servicos:
        if s.parceiro and texto_tem_local_antigo(s.parceiro.endereco):
            continue

        if s.parceiro and s.parceiro.id == 1:
            endereco_publico = ENDERECO_PUBLICO
        else:
            endereco_publico = s.parceiro.endereco if (s.parceiro and s.parceiro.endereco) else "A combinar"

        lista.append({
            "id": s.id,
            "nome": s.nome,
            "valor": s.valor,
            "tipo_ficha": s.tipo_ficha or "feridas",
            "profissional": s.parceiro.nome_fantasia if s.parceiro else "Ilúmina Med",
            "modo": s.parceiro.modo_atendimento if s.parceiro else "Consultório",
            "endereco": endereco_publico,
            "parceiro_id": s.parceiro_id or 1
        })
    db_session.close()
    return jsonify(lista)


@app.route('/iniciar_venda', methods=['POST'])
def iniciar_venda():
    db_session = Session()
    try:
        dados = request.get_json()
        if not dados:
            return jsonify({"erro": "Dados nao enviados"}), 400

        cliente_nome = (dados.get('cliente') or '').strip()
        whatsapp_bruto = (dados.get('whatsapp') or '').strip()
        if not cliente_nome or not whatsapp_bruto:
            return jsonify({"erro": "Nome e whatsapp sao obrigatorios"}), 400

        whatsapp_limpo = re.sub(r'\D', '', whatsapp_bruto)

        servico_info = (dados.get('servico_info') or '').strip()
        serv_parts = [p.strip() for p in servico_info.split('|') if p.strip()]
        if len(serv_parts) < 2:
            return jsonify({"erro": "Servico invalido"}), 400

        parceiro_id = 1
        if len(serv_parts) > 2:
            try:
                parceiro_id = int(serv_parts[2])
            except ValueError:
                parceiro_id = 1

        try:
            valor_total = float(serv_parts[1])
            servico_nome = serv_parts[0]
        except ValueError:
            try:
                valor_total = float(serv_parts[0])
                servico_nome = serv_parts[1]
            except ValueError:
                return jsonify({"erro": "Servico invalido"}), 400

        nova_venda = Venda(
            cliente_nome=cliente_nome,
            whatsapp=whatsapp_limpo,
            email=dados.get('email'),
            data_sugerida=dados.get('data_sugerida'),
            servico_nome=servico_nome,
            valor_total=valor_total,
            parceiro_id=parceiro_id,
            avaliacao_concluida=False
        )
        db_session.add(nova_venda)
        db_session.commit()
        return jsonify({"token": nova_venda.token_acesso, "nome": nova_venda.cliente_nome})
    except Exception as e:
        db_session.rollback()
        return jsonify({"erro": f"Erro ao processar: {str(e)}"}), 500
    finally:
        db_session.close()


@app.route('/finalizar_agendamento', methods=['POST'])
def finalizar_agendamento():
    db_session = Session()
    try:
        token = request.form.get('token')
        if not token:
            return jsonify({"erro": "Token não fornecido", "status": "erro"}), 400

        v = db_session.query(Venda).filter_by(token_acesso=token).first()
        if not v:
            return jsonify({"erro": "Venda não encontrada", "status": "erro"}), 404

        comorbidades_lista = request.form.getlist('comorbidades_lista')
        v.outras_comorbidades = ", ".join(comorbidades_lista) if comorbidades_lista else "Nenhuma"
        
        # Manter compatibilidade com colunas individuais
        v.diabetes = "Sim" if "Diabetes Mellitus" in comorbidades_lista else "Não"
        v.pressao_alta = "Sim" if "Pressão Alta" in comorbidades_lista else "Não"
        v.doencas_vasculares = "Sim" if "Doenças Vasculares" in comorbidades_lista else "Não"
        
        infecciosas_lista = request.form.getlist('infecciosas')
        v.doencas_infectiosas = ", ".join(infecciosas_lista) if infecciosas_lista else "Nenhuma"
        
        v.consumo_alcool = request.form.get('consumo_alcool', 'Não')
        v.observacoes_adicionais = request.form.get('outras_comorbidades_detalhado', '')
        v.num_lesoes = int(request.form.get('quantidade_lesoes', 1))

        v.alergias = request.form.get('alergias', '')
        v.tamanho_lesao = request.form.get('tamanho_ferida', '')
        v.historico_doenca = request.form.get('historico', '')
        v.sintomas_atuais = request.form.get('sintomas', '')
        v.mobilidade = request.form.get('mobilidade', 'Nenhuma')
        v.observacoes_adicionais = request.form.get('tratamentos_anteriores', '')
        
        # Novos campos de estética
        v.procedimentos_anteriores = request.form.get('estetica_anterior', '')
        v.uso_acidos = request.form.get('uso_acidos', '')
        v.rotina_skincare = request.form.get('rotina_skincare', '')
        
        # Upload de fotos
        fotos = request.files.getlist('fotos_lesao')
        caminhos = []
        if fotos:
            upload_folder = os.path.join(app.static_folder, 'uploads')
            os.makedirs(upload_folder, exist_ok=True)
            for file in fotos:
                if file.filename:
                    filename = secure_filename(f"{v.id}_{int(datetime.now().timestamp())}_{file.filename}")
                    file.save(os.path.join(upload_folder, filename))
                    caminhos.append(f"uploads/{filename}")
        
        v.fotos_caminho = ";".join(caminhos) if caminhos else ""

        db_session.commit()

        link_ficha = f"{BASE_URL}/avaliacao/{v.token_acesso}"

        mensagem = (
            f"*NOVA FICHA PRÉ-CONSULTA - ILÚMINA MED*\n\n"
            f"🩺 *Paciente:* {v.cliente_nome}\n"
            f"📋 *Serviço:* {v.servico_nome}\n"
            f"📅 *Previsão:* {v.data_sugerida}\n\n"
            f"🔗 *ACESSO À FICHA TÉCNICA:*\n"
            f"{link_ficha}"
        )

        phone_destino = normalize_phone_for_whatsapp(
            v.parceiro.whatsapp_professional if (v.parceiro and v.parceiro.whatsapp_professional) else None,
            DEFAULT_WHATSAPP_ILUMINA
        )
        link_zap = f"https://api.whatsapp.com/send?phone={phone_destino}&text={urllib.parse.quote(mensagem)}"

        return jsonify({
            "status": "sucesso",
            "link_whatsapp": link_zap
        })

    except Exception as e:
        db_session.rollback()
        return jsonify({"erro": f"Erro ao processar: {str(e)}", "status": "erro"}), 500
    finally:
        db_session.close()


@app.route('/enviar_whatsapp/<token>')
def enviar_whatsapp(token):
    db_session = Session()
    v = db_session.query(Venda).filter_by(token_acesso=token).first()
    if not v: return "Erro", 404
    
    # Busca whatsapp do parceiro dono do serviço
    phone_number = normalize_phone_for_whatsapp(
        v.parceiro.whatsapp_professional if (v.parceiro and v.parceiro.whatsapp_professional) else None,
        DEFAULT_WHATSAPP_ILUMINA
    )
    
    link_pagamento = f"{BASE_URL}/pagamento/{v.token_acesso}"
    mensagem = f"Olá {v.parceiro.nome_fantasia if v.parceiro else 'Ilúmina'}! Sou {v.cliente_nome}, preenchi a ficha e quero agendar meu procedimento. Link Pagamento: {link_pagamento}"
    
    db_session.close()
    return redirect(f"https://api.whatsapp.com/send?phone={phone_number}&text={urllib.parse.quote(mensagem)}")


@app.route('/iniciar_pagamento_online', methods=['POST'])
def iniciar_pagamento_online():
    """
    Inicia um pagamento diretamente pelo bloco "Pagamento Online".

    Cria um registro próprio (PagamentosOnline) com Nome/CPF/E-mail e usa esses dados no Mercado Pago.
    """
    db_session = Session()
    try:
        dados = request.get_json(silent=True) or {}
        servico_id = dados.get("servico_id")
        nome = (dados.get("nome") or "").strip()
        cpf = normalize_cpf(dados.get("cpf"))
        email = (dados.get("email") or "").strip()

        if not servico_id:
            return jsonify({"erro": "Serviço não informado"}), 400
        if not nome:
            return jsonify({"erro": "Nome é obrigatório"}), 400
        if cpf and len(cpf) != 11:
            return jsonify({"erro": "CPF inválido: precisa ter 11 dígitos"}), 400
        if email and not is_valid_email(email):
            return jsonify({"erro": "E-mail inválido"}), 400

        serv = db_session.query(Servico).get(int(servico_id))
        if not serv:
            return jsonify({"erro": "Serviço não encontrado"}), 404

        reg = PagamentoOnline(
            cliente_nome=nome,
            cpf=cpf or None,
            email=email or None,
            servico_nome=serv.nome,
            valor_total=float(serv.valor),
            status="iniciado",
        )
        db_session.add(reg)
        db_session.commit()

        valor = float(serv.valor)
        nome_serv = serv.nome

        chave_pix_fixa = os.getenv("CHAVE_PIX_ILUMINA") or "62706476000108"
        payload = None
        qr_code_b64 = None

        if payments:
            try:
                payload, qr_code_b64 = payments.gerar_pix_estatico(
                    chave_pix_fixa,
                    valor,
                    nome_beneficiario="Ilumina Med",
                    cidade="Curitiba",
                )
            except Exception as e:
                print(f"Falha ao gerar PIX estático: {e}")
                payload, qr_code_b64 = None, None

        # PIX via Mercado Pago (dinâmico), usando nome/email/CPF do pagador
        if not payload and payments and payments.get_sdk():
            payload, qr_code_b64 = payments.gerar_pix_pagamento(
                reg.id, valor, f"Pgto {nome_serv}", email_cliente=(email or "cliente@email.com"), nome_cliente=nome, cpf=cpf
            )

        # Link de cartão (preferência), usando nome/email/CPF do pagador
        card_url = None
        if payments and payments.get_sdk():
            card_url = payments.criar_preferencia(
                reg.id, valor, f"Serviço: {nome_serv}", email_cliente=(email or "cliente@email.com"), nome_cliente=nome, cpf=cpf
            )

        # Fallback final (QR Code Local Garantido)
        if not payload or not qr_code_b64:
            import qrcode
            import base64
            import io

            valor_str = f"{valor:.2f}"
            payload = (
                f"00020126330014br.gov.bcb.pix0111{chave_pix_fixa}"
                f"52040000530398654{len(valor_str):02}{valor_str}"
                f"5802BR5911Ilumina Med6008Curitiba62070503***6304"
            )
            qr = qrcode.QRCode(version=1, box_size=10, border=2)
            qr.add_data(payload)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            qr_code_b64 = f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}"

        parcelas = payments.calcular_parcelas(valor) if payments else [{"n": 1, "valor": valor, "total": valor}]

        return jsonify({
            "pagamento_id": reg.id,
            "payload": payload,
            "qr_code": qr_code_b64,
            "card_url": card_url,
            "parcelas": parcelas,
        })

    except Exception as e:
        db_session.rollback()
        return jsonify({"erro": f"Erro ao iniciar pagamento: {str(e)}"}), 500
    finally:
        db_session.close()


@app.route('/gerar_dados_pagamento')
def gerar_dados_pagamento():
    valor = float(request.args.get('valor', 0))
    nome = request.args.get('nome', 'Serviço')
    
    chave_pix_fixa = os.getenv("CHAVE_PIX_ILUMINA")
    payload = None
    qr_code_b64 = None

    # 1. Prioridade: Pix Estático (Se tiver chave configurada no .env ou hardcoded como fallback seguro)
    if not chave_pix_fixa:
        chave_pix_fixa = "62706476000108" # Hardcoded Fallback for Ilumina Med
        
    if payments:
        try:
            payload, qr_code_b64 = payments.gerar_pix_estatico(chave_pix_fixa, valor)
        except Exception as e:
            print(f"Falha ao gerar PIX: {e}")
            payload, qr_code_b64 = None, None
    
    # 2. Se não tiver chave fixa, tenta Mercado Pago SDK (Dinâmico)
    if not payload and payments and payments.get_sdk():
        temp_id = f"PRE-{int(datetime.now().timestamp())}"
        payload, qr_code_b64 = payments.gerar_pix_pagamento(temp_id, valor, f"Pgto {nome}")

    # 3. Gera Link de Cartão (Preferência)
    card_url = None
    if payments and payments.get_sdk():
        try:
            temp_id = f"PRE-{int(datetime.now().timestamp())}"
            card_url = payments.criar_preferencia(temp_id, valor, f"Serviço: {nome}")
        except Exception as e:
            print(f"Erro ao criar preferência de cartão: {e}")
    # 4. Fallback final (QR Code Local Garantido)
    if not payload or not qr_code_b64: 
        import qrcode
        import base64
        import io
        valor_str = f"{valor:.2f}"
        payload = f"00020126330014br.gov.bcb.pix0111{chave_pix_fixa}52040000530398654{len(valor_str):02}{valor_str}5802BR5911Ilumina Med6008Curitiba62070503***6304"
        qr = qrcode.QRCode(version=1, box_size=10, border=2)
        qr.add_data(payload)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        qr_code_b64 = f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}"

    if payments:
        parcelas = payments.calcular_parcelas(valor)
    else:
        parcelas = [{"n": 1, "valor": valor, "total": valor}]

    return jsonify({
        "payload": payload,
        "qr_code": qr_code_b64,
        "card_url": card_url,
        "parcelas": parcelas
    })


@app.route('/pagamento/<token>')
def tela_pagamento(token):
    db_session = Session()
    v = db_session.query(Venda).filter_by(token_acesso=token).first()
    db_session.close()
    if not v: return "Link inválido", 404

    # Gera Pix
    if not payments:
        return "Pagamentos indisponíveis no momento.", 503

    codigo_pix, qr_img = payments.gerar_pix_pagamento(
        v.id,
        v.valor_total,
        f"Pagamento {v.servico_nome}",
        email_cliente=(v.email or "cliente@email.com"),
        nome_cliente=v.cliente_nome
    )
    
    # Gera Link de Preferência (Cartão)
    url_preference = payments.criar_preferencia(
        v.id,
        v.valor_total,
        f"Serviço {v.servico_nome}",
        email_cliente=(v.email or "cliente@email.com"),
        nome_cliente=v.cliente_nome
    )
    
    parcelas = payments.calcular_parcelas(v.valor_total) if payments else []

    return f"""
    <!DOCTYPE html>
    <html lang="pt-br">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Pagamento Seguro - Ilúmina Med</title>
        <link href="https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;600;700&display=swap" rel="stylesheet">
        <style>
            :root {{ --primary: #0a3342; --accent: #c5a059; --bg: #f4f7f8; }}
            body {{ font-family: 'Poppins', sans-serif; background: var(--bg); margin: 0; padding: 20px; color: #333; }}
            .pay-container {{ max-width: 450px; margin: 40px auto; background: white; border-radius: 20px; overflow: hidden; box-shadow: 0 15px 35px rgba(0,0,0,0.1); }}
            .pay-header {{ background: var(--primary); color: white; padding: 30px; text-align: center; border-bottom: 5px solid var(--accent); }}
            .pay-title {{ margin: 0; font-size: 18px; text-transform: uppercase; letter-spacing: 1px; opacity: 0.8; }}
            .pay-amount {{ margin: 10px 0 0; font-size: 36px; font-weight: 700; color: var(--accent); }}
            .pay-body {{ padding: 30px; }}
            .method-box {{ background: #fdfdfd; border: 1px solid #eee; border-radius: 12px; padding: 20px; margin-bottom: 20px; text-align: center; }}
            .method-title {{ font-weight: 700; color: var(--primary); margin-bottom: 15px; display: block; font-size: 14px; text-transform: uppercase; }}
            .qr-code {{ width: 180px; height: 180px; border: 1px solid #ddd; padding: 10px; border-radius: 10px; margin-bottom: 15px; }}
            .pix-code {{ width: 100%; font-size: 10px; padding: 10px; border: 1px solid #eee; border-radius: 6px; background: #f9f9f9; color: #666; margin-bottom: 15px; resize: none; }}
            .btn {{ display: block; width: 100%; padding: 15px; border-radius: 10px; border: none; font-weight: 700; font-size: 14px; cursor: pointer; text-transform: uppercase; transition: 0.3s; text-decoration: none; text-align: center; box-sizing: border-box; }}
            .btn-pix {{ background: #00bfa5; color: white; }}
            .btn-card {{ background: #009ee3; color: white; }}
            .btn:hover {{ filter: brightness(1.1); transform: translateY(-2px); }}
            .footer {{ text-align: center; font-size: 11px; color: #999; margin-top: 20px; }}
        </style>
    </head>
    <body>
        <div class="pay-container">
            <div class="pay-header">
                <p class="pay-title">Total a Pagar</p>
                <h1 class="pay-amount">R$ {v.valor_total:.2f}</h1>
            </div>
            <div class="pay-body">
                <div class="method-box">
                    <span class="method-title">💠 Opção 1: PIX Instantâneo</span>
                    <img src="{qr_img}" class="qr-code">
                    <p style="font-size: 12px; color: #666; margin-bottom: 10px;">Aprovação imediata do agendamento</p>
                    <textarea id="codigoPix" class="pix-code" readonly>{codigo_pix}</textarea>
                    <button onclick="copyPix()" class="btn btn-pix">Copiar Código PIX</button>
                </div>

                <div class="method-box">
                    <span class="method-title">💳 Opção 2: Cartão de Crédito</span>
                    <p style="font-size: 12px; color: #666; margin-bottom: 15px;">Parcele em até 12x no cartão via Mercado Pago</p>
                    
                    {'<a href="' + url_preference + '" class="btn btn-card">Pagar com Cartão</a>' if url_preference else '<div style="color:red; font-size:12px;">Erro ao gerar link de pagamento. Use PIX.</div>'}
                </div>

                <div style="text-align: center;">
                    <a href="/confirmar_pagamento_simulado/{token}" style="font-size: 10px; color: #ddd; text-decoration: none;">[Simular Pagamento para Testes]</a>
                </div>
            </div>
        </div>
        <div class="footer">
            Ilúmina Med | Pagamento 100% Seguro através do Mercado Pago
        </div>
        <script>
            function copyPix() {{
                const el = document.getElementById('codigoPix');
                el.select();
                document.execCommand('copy');
                alert('Código PIX copiado!');
            }}
        </script>
    </body>
    </html>
    """



@app.route('/confirmar_pagamento_simulado/<token>')
def confirmar_pagamento_simulado(token):
    db_session = Session()
    v = db_session.query(Venda).filter_by(token_acesso=token).first()
    if v:
        v.avaliacao_concluida = True
        db_session.commit()
        enviar_email_confirmacao(v)
    db_session.close()
    return "<h2>Pagamento Confirmado! Aguarde nosso contato da Ilúmina Med.</h2>"


@app.route('/webhook/mercadopago', methods=['POST'])
def webhook_mercadopago():
    if not payments:
        return jsonify({"status": "ignored"}), 200
        
    topic = request.args.get('topic') or request.json.get('topic')
    id = request.args.get('id') or request.json.get('data', {}).get('id')
    
    if (topic == 'payment') and id:
        sdk = payments.get_sdk()
        if sdk:
            try:
                payment_info = sdk.payment().get(id)
                payment = payment_info.get("response")
                
                if payment and payment.get("status") == "approved":
                    external_ref = payment.get("external_reference")
                    # Se for referência de ID válido
                    if external_ref and not external_ref.startswith('PRE-'):
                        db_session = Session()
                        v = db_session.query(Venda).get(int(external_ref))
                        if v:
                            v.avaliacao_concluida = True
                            db_session.commit()
                            enviar_email_confirmacao(v)
                        db_session.close()
            except:
                pass
                    
    return jsonify({"status": "ok"}), 200

@app.route('/sitemap.xml')
def sitemap():
    """Gera sitemap XML para SEO"""
    pages = []
    # Paginas estáticas
    pages.append({"loc": url_for('index', _external=True), "changefreq": "daily", "priority": "1.0"})
    pages.append({"loc": url_for('login', _external=True), "changefreq": "monthly", "priority": "0.5"})
    pages.append({"loc": url_for('privacidade', _external=True), "changefreq": "yearly", "priority": "0.3"})
    
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    for page in pages:
        xml += '  <url>\n'
        xml += f'    <loc>{page["loc"]}</loc>\n'
        xml += f'    <changefreq>{page["changefreq"]}</changefreq>\n'
        xml += f'    <priority>{page["priority"]}</priority>\n'
        xml += '  </url>\n'
    xml += '</urlset>'
    
    return Response(xml, mimetype='application/xml')


def enviar_email_confirmacao(venda):
    """Envia email de confirmação de pagamento"""
    if not venda.email:
        return
        
    remetente = os.getenv("EMAIL_REMETENTE")
    senha = os.getenv("EMAIL_SENHA")
    
    if not remetente or not senha:
        print("Credenciais de email não configuradas.")
        return

    msg = MIMEMultipart()
    msg['From'] = remetente
    msg['To'] = venda.email
    msg['Subject'] = "Pagamento Confirmado - Ilúmina Med"

    corpo = f"""
    <h2>Olá, {venda.cliente_nome}!</h2>
    <p>Seu pagamento para o serviço <b>{venda.servico_nome}</b> foi confirmado com sucesso.</p>
    <p>Data agendada/sugerida: {venda.data_sugerida}</p>
    <p>Estamos ansiosos para atendê-lo(a).</p>
    <br>
    <p>Atenciosamente,<br>Equipe Ilúmina Med</p>
    """
    msg.attach(MIMEText(corpo, 'html'))

    try:
        # Configuração para Gmail (pode ajustar para Outlook/Outros)
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(remetente, senha)
        server.send_message(msg)
        server.quit()
        print(f"Email enviado para {venda.email}")
    except Exception as e:
        print(f"Erro ao enviar email: {e}")


@app.route('/avaliacao/<token>')
def ver_avaliacao(token):
    db_session = Session()
    v = db_session.query(Venda).filter_by(token_acesso=token).first()
    db_session.close()
    if not v: return "Link Inválido", 404

    return f"""
    <!DOCTYPE html>
    <html lang="pt-br">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Ficha Técnica - {v.cliente_nome}</title>
        <link href="https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;600;700&display=swap" rel="stylesheet">
        <style>
            body {{ font-family: 'Poppins', sans-serif; background: #f0f2f5; margin: 0; padding: 20px; color: #333; }}
            .container {{ max-width: 850px; margin: auto; background: white; border-radius: 20px; overflow: hidden; box-shadow: 0 10px 30px rgba(0,0,0,0.1); border-top: 10px solid #013241; }}
            .header {{ background: #013241; color: white; padding: 30px; text-align: center; border-bottom: 5px solid #c5a059; }}
            .header h1 {{ margin: 0; font-size: 24px; text-transform: uppercase; letter-spacing: 2px; color: #c5a059; }}
            .header p {{ margin: 5px 0 0; opacity: 0.8; font-size: 14px; }}
            .content {{ padding: 35px; }}
            .section {{ margin-bottom: 30px; padding: 20px; border-radius: 12px; background: #fdfdfd; border: 1px solid #f0f0f0; position: relative; }}
            .section-title {{ position: absolute; top: -12px; left: 20px; background: white; padding: 0 10px; color: #c5a059; font-weight: 700; font-size: 12px; text-transform: uppercase; letter-spacing: 1px; }}
            .info-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
            .data-label {{ font-size: 11px; color: #888; text-transform: uppercase; font-weight: 600; display: block; margin-bottom: 2px; }}
            .data-value {{ font-size: 16px; font-weight: 500; color: #0a3342; display: block; }}
            .full-width {{ grid-column: span 2; }}
            .obs-box {{ background: #fff; border: 1px solid #eee; padding: 15px; border-radius: 8px; margin-top: 5px; color: #444; line-height: 1.6; min-height: 40px; }}
            .status-tag {{ background: #c5a059; color: white; padding: 4px 12px; border-radius: 20px; font-size: 11px; font-weight: 700; text-transform: uppercase; }}
Gestão de Pacientes
            .highlight-blue {{ color: #0a3342; font-weight: 700; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>Ficha Pré-Consulta Completa</h1>
                <p>Paciente: {v.cliente_nome} | Protocolo: #{v.id}</p>
            </div>
            <div class="content">
                <div class="section">
                    <h3>📋 Histórico Clínico</h3>
                    <p><b>Condições de Saúde:</b> {v.outras_comorbidades or 'Nenhuma'}</p>
                    <p><b>Doenças Infectocontagiosas:</b> {v.doencas_infectiosas or 'Nenhuma'}</p>
                    <p><b>Consumo de Álcool:</b> {v.consumo_alcool or 'Não'}</p>
                    <p><b>Alergias:</b> {v.alergias or 'Não'}</p>
                    <p><b>Observações Adicionais:</b> {v.observacoes_adicionais or 'Nenhuma'}</p>
                </div>

                <div class="section">
                    <h3>🩹 Detalhes da Lesão</h3>
                    <p><b>Quantidade de Lesões:</b> {v.num_lesoes or 1}</p>
                    <p><b>Tamanho da Ferida:</b> {v.tamanho_lesao or 'Não informado'}</p>
                    <p><b>Tempo de Evolução:</b> {v.historico_doenca or 'Não informado'}</p>
                    <p><b>Sintomas Atuais:</b> {v.sintomas_atuais or 'Nenhum'}</p>
                    <p><b>Mobilidade:</b> {v.mobilidade or 'Normal'}</p>
                </div>
                <div class="section">
                    <span class="section-title">Identificação e Status</span>
                    <div class="info-grid">
                        <div>
                            <span class="data-label">Nome do Paciente</span>
                            <span class="data-value">{v.cliente_nome}</span>
                        </div>
                        <div>
                            <span class="data-label">Pagamento</span>
                            <span class="status-tag">{'Confirmado' if v.avaliacao_concluida else 'Pendente'}</span>
                        </div>
                    </div>
                </div>

                <div class="section">
                    <span class="section-title">Anamnese e Condições Médicas</span>
                    <div class="info-grid">
                        <div class="full-width">
                            <span class="data-label">Comorbidades e Infecções</span>
                            <div class="obs-box highlight-blue">{v.outras_comorbidades if v.outras_comorbidades else 'Nenhuma informada'}</div>
                        </div>
                        <div>
                            <span class="data-label">Alergias</span>
                            <div class="obs-box" style="border-left: 4px solid #e74c3c;">{v.alergias if v.alergias else 'Nega alergias'}</div>
                        </div>
                        <div>
                            <span class="data-label">Mobilidade</span>
                            <span class="data-value">{v.mobilidade if v.mobilidade else 'Normal'}</span>
                        </div>
                    </div>
                </div>

                <div class="section">
                    <span class="section-title">Detalhes da Lesão / Queixa</span>
                    <div class="info-grid">
                        <div>
                            <span class="data-label">Dimensões da Lesão</span>
                            <span class="data-value">{v.tamanho_lesao if v.tamanho_lesao else 'Não informado'}</span>
                        </div>
                        <div>
                            <span class="data-label">Sintomas (Dor, Odor, Secreção)</span>
                            <div class="obs-box">{v.sintomas_atuais if v.sintomas_atuais else 'Não relatado'}</div>
                        </div>
                        <div class="full-width">
                            <span class="data-label">Tempo de evolução (Histórico)</span>
                            <div class="obs-box">{v.historico_doenca if v.historico_doenca else 'Não informado'}</div>
                        </div>
                            <span class="data-label">Observações Adicionais</span>
                            <div class="obs-box">{v.observacoes_adicionais if v.observacoes_adicionais else 'Sem observações'}</div>
                        </div>
                    </div>
                </div>

                <div class="section">
                    <span class="section-title">Estética e Cuidados</span>
                    <div class="info-grid">
                        <div class="full-width">
                            <span class="data-label">Procedimentos Anteriores</span>
                            <div class="obs-box">{v.procedimentos_anteriores if v.procedimentos_anteriores else 'Nenhum relatado'}</div>
                        </div>
                        <div>
                            <span class="data-label">Uso de Ácidos</span>
                            <div class="obs-box">{v.uso_acidos if v.uso_acidos else 'Não'}</div>
                        </div>
                        <div>
                            <span class="data-label">Rotina Skincare</span>
                            <div class="obs-box">{v.rotina_skincare if v.rotina_skincare else 'Não informada'}</div>
                        </div>
                    </div>
                </div>

                <div class="section">
                    <span class="section-title">Fotos Anexadas</span>
                    <!-- Galeria de Fotos -->
                    {'<div style="display: flex; gap: 10px; flex-wrap: wrap;">' + 
                     ''.join([f'<a href="/static/{c}" target="_blank"><img src="/static/{c}" style="width: 100px; height: 100px; object-fit: cover; border-radius: 8px; border: 1px solid #ddd;"></a>' for c in v.fotos_caminho.split(';')]) 
                     + '</div>' if v.fotos_caminho else '<p style="color: #888; font-style: italic;">Nenhuma foto enviada.</p>'}
                </div>


                <div class="section" style="border: none; background: #0a3342; color: white; text-align: center; border-radius: 15px;">
                    <span class="data-label" style="color: rgba(255,255,255,0.6);">Serviço Solicitado</span>
                    <span class="data-value" style="color: #c5a059; font-size: 22px;">{v.servico_nome}</span>
                    <span class="data-value" style="color: white; font-size: 14px; margin-top: 8px;">Previsão: {v.data_sugerida}</span>
                </div>
            </div>
        </div>
    </body>
    </html>
    """


@app.route('/avaliar/<token>', methods=['GET', 'POST'])
def avaliar_atendimento(token):
    db_session = Session()
    venda = db_session.query(Venda).filter_by(token_acesso=token).first()
    
    if not venda:
        db_session.close()
        return "Link expirado ou inválido", 404
        
    if request.method == 'POST':
        nova = Avaliacao(
            nome=request.form.get('nome'),
            nota=int(request.form.get('nota')),
            comentario=request.form.get('comentario'),
            exibir=True
        )
        db_session.add(nova)
        db_session.commit()
        db_session.close()
        return "<h2>Obrigado! Sua avaliação foi enviada com sucesso.</h2><a href='/'>Voltar ao site</a>"

    db_session.close()
    return render_template('avaliar.html', venda=venda)


# Inicializa dados padrão também quando o app é carregado via Gunicorn/Render.
# (Pode ser desativado para tarefas específicas definindo SKIP_INIT=true).
if os.getenv("SKIP_INIT", "").strip().lower() not in ("1", "true", "yes", "y", "sim"):
    inicializar()


if __name__ == '__main__':

    app.run(debug=True)
