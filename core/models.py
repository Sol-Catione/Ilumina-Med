from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime, Text, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime
import secrets

Base = declarative_base()

class Parceiro(Base):
    __tablename__ = 'parceiros'
    id = Column(Integer, primary_key=True)
    nome_fantasia = Column(String, nullable=False)
    whatsapp_professional = Column(String)
    eh_interno = Column(Boolean, default=False)
    modo_atendimento = Column(String, default="Domiciliar") # Domiciliar, Consultório ou Ambos
    endereco = Column(Text) # Endereço do consultório ou área de atendimento
    vendas = relationship("Venda", back_populates="parceiro")
    servicos = relationship("Servico", back_populates="parceiro")

class Servico(Base):
    __tablename__ = 'servicos'
    id = Column(Integer, primary_key=True)
    nome = Column(String, nullable=False)
    valor = Column(Float, nullable=False)
    descricao = Column(Text, nullable=True)
    tipo_ficha = Column(String, default="feridas")
    parceiro_id = Column(Integer, ForeignKey('parceiros.id'))
    parceiro = relationship("Parceiro", back_populates="servicos")

class Venda(Base):
    __tablename__ = 'vendas'
    id = Column(Integer, primary_key=True)
    cliente_nome = Column(String, nullable=False)
    whatsapp = Column(String, nullable=False)
    email = Column(String, nullable=True)
    servico_nome = Column(String, nullable=False)
    valor_total = Column(Float, nullable=False)
    data_sugerida = Column(String)
    data_registro = Column(DateTime, default=datetime.utcnow)
    token_acesso = Column(String, unique=True, default=lambda: secrets.token_urlsafe(16))
    avaliacao_concluida = Column(Boolean, default=False)
    outras_comorbidades = Column(Text)
    alergias = Column(Text)
    tamanho_lesao = Column(Text)
    historico_doenca = Column(Text)
    sintomas_atuais = Column(Text)
    mobilidade = Column(String)
    observacoes_adicionais = Column(Text)
    parceiro_id = Column(Integer, ForeignKey('parceiros.id'))
    parceiro = relationship("Parceiro", back_populates="vendas")
    
    # Novos campos de Anamnese (Revisão)
    diabetes = Column(String)
    pressao_alta = Column(String)
    doencas_vasculares = Column(String)
    consumo_alcool = Column(String)
    num_lesoes = Column(Integer, default=1)
    doencas_infectiosas = Column(Text)
    
    # Novos campos para Estética (Existentes)
    procedimentos_anteriores = Column(Text)
    uso_acidos = Column(Text)
    rotina_skincare = Column(Text)
    fotos_caminho = Column(Text)  # Caminhos separados por ponto e vírgula

class Gasto(Base):
    __tablename__ = 'gastos'
    id = Column(Integer, primary_key=True)
    descricao = Column(String, nullable=False)
    valor = Column(Float, nullable=False)
    data = Column(DateTime, default=datetime.utcnow)

class Investimento(Base):
    __tablename__ = 'investimentos'
    id = Column(Integer, primary_key=True)
    origem = Column(String, nullable=False)
    valor = Column(Float, nullable=False)
    data = Column(DateTime, default=datetime.utcnow)