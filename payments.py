import mercadopago
import os
import dotenv
import re

dotenv.load_dotenv()

# Tenta obter o token do ambiente
MP_ACCESS_TOKEN = os.getenv("MP_ACCESS_TOKEN", "")

def get_sdk():
    if not MP_ACCESS_TOKEN:
        return None
    return mercadopago.SDK(MP_ACCESS_TOKEN)

def criar_preferencia(venda_id, valor, descricao, email_cliente="cliente@email.com"):
    """
    Cria uma preferência de pagamento no Mercado Pago e retorna o link (init_point).
    """
    sdk = get_sdk()
    if not sdk:
        return None
    
    base_url = os.getenv("BASE_URL", "http://localhost:5000").rstrip("/")
    
    preference_data = {
        "items": [
            {
                "id": str(venda_id),
                "title": descricao,
                "quantity": 1,
                "currency_id": "BRL",
                "unit_price": float(valor)
            }
        ],
        "payer": {
            "email": email_cliente
        },
        "back_urls": {
            "success": f"{base_url}/confirmar_pagamento_mp",
            "failure": f"{base_url}/pagamento_falhou",
            "pending": f"{base_url}/pagamento_pendente"
        },
        "auto_return": "approved",
        "external_reference": str(venda_id)
    }

    try:
        preference_response = sdk.preference().create(preference_data)
        pe = preference_response.get("response")
        return pe.get("init_point") if pe else None
    except:
        return None

def gerar_pix_pagamento(venda_id, valor, descricao, email_cliente="cliente@email.com", nome_cliente="Cliente"):
    """
    Gera um pagamento via PIX usando a API do Mercado Pago.
    Retorna (qr_code_copia_e_cola, qr_code_base64_image).
    """
    sdk = get_sdk()
    # Se não tiver SDK configurado, retorna mock para não quebrar a demo
    if not sdk:
        return _gerar_pix_mock(valor)
        
    payment_data = {
        "transaction_amount": float(valor),
        "description": descricao,
        "payment_method_id": "pix",
        "payer": {
            "email": email_cliente,
            "first_name": nome_cliente.split(" ")[0],
            "last_name": " ".join(nome_cliente.split(" ")[1:]) if " " in nome_cliente else "Silva"
        },
        "external_reference": str(venda_id)
    }

    try:
        payment_response = sdk.payment().create(payment_data)
        payment = payment_response.get("response")

        if payment and payment.get("status") == "pending":
            poi = payment.get('point_of_interaction', {})
            td = poi.get('transaction_data', {})
            return td.get('qr_code'), td.get('qr_code_base64')
    except:
        pass
    
    return None, None

def _gerar_pix_mock(valor):
    # Fallback caso não tenha credenciais (mantém o comportamento anterior)
    payload = f"00020126580014br.gov.bcb.pix0136RANDOMKEY520400005303986540{valor:.2f}5802BR5911Ilumina6007Cascavel62070503***6304"
    return payload, gerar_qr_code_base64_lib(payload)

def gerar_qr_code_base64_lib(payload_pix):
    import qrcode
    import io
    import base64
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(payload_pix)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffered = io.BytesIO()
    img.save(buffered, format="PNG")
    img_str = base64.b64encode(buffered.getvalue()).decode()
    return f"data:image/png;base64,{img_str}"

def calcular_crc16(payload):
    crc = 0xFFFF
    poly = 0x1021
    for b in payload.encode('utf-8'):
        crc ^= (b << 8)
        for _ in range(8):
            if (crc & 0x8000):
                crc = (crc << 1) ^ poly
            else:
                crc <<= 1
        crc &= 0xFFFF
    return f"{crc:04X}"

def gerar_pix_estatico(chave_pix, valor, nome_beneficiario="Ilumina Med", cidade="Cascavel", txid="***"):
    """
    Gera um Payload Pix Estático (QRCPS) válido.
    """
    try:
        # Limpa a chave PIX (remove pontos, traços, barras e espaços)
        chave_pix = re.sub(r'[\.\-\/\s]', '', str(chave_pix))
        
        valor_str = f"{float(valor):.2f}"
        
        # Montagem do Payload (IDs do Banco Central)
        payload = (
            f"000201"
            f"26{len(chave_pix) + 22:02}0014br.gov.bcb.pix01{len(chave_pix):02}{chave_pix}"
            f"52040000"
            f"5303986"
            f"54{len(valor_str):02}{valor_str}"
            f"5802BR"
            f"59{len(nome_beneficiario):02}{nome_beneficiario}"
            f"60{len(cidade):02}{cidade}"
            f"62070503{txid}"
            f"6304"
        )
        
        crc = calcular_crc16(payload)
        payload_completo = f"{payload}{crc}"
        
        qr_b64 = gerar_qr_code_base64_lib(payload_completo)
        return payload_completo, qr_b64
    except Exception as e:
        import traceback
        print(f"Erro ao gerar Pix Estático: {e}")
        traceback.print_exc()
        return None, None

def calcular_parcelas(valor_total, max_parcelas=12):
    # Simulação de juros da maquininha (ex: 2.99% a.m + taxa fixa)
    # Ajustado para refletir "juros da maquininha" comum
    taxa_maquininha = 0.0299  # 2.99%
    parcelas = []
    
    # 1x no Débito/Crédito direto geralmente não tem juros pro cliente na visualização
    # mas aqui seguimos a lógica de repassar ou mostrar o custo
    parcelas.append({"n": 1, "valor": valor_total, "total": valor_total})
    
    for i in range(2, max_parcelas + 1):
        # Juros compostos simples ou tabela da máquina
        fator = (1 + taxa_maquininha) ** i
        total_com_juros = valor_total * fator
        
        parcelas.append({
            "n": i,
            "valor": round(total_com_juros / i, 2),
            "total": round(total_com_juros, 2)
        })
    return parcelas