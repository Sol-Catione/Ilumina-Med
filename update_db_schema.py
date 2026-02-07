import sqlite3

def update_db():
    try:
        conn = sqlite3.connect('ilumina_med.db')
        cursor = conn.cursor()
        
        # Colunas para a tabela 'vendas'
        vendas_columns = [
            ("procedimentos_anteriores", "TEXT"),
            ("uso_acidos", "TEXT"),
            ("rotina_skincare", "TEXT"),
            ("fotos_caminho", "TEXT"),
            ("diabetes", "TEXT"),
            ("pressao_alta", "TEXT"),
            ("doencas_vasculares", "TEXT"),
            ("consumo_alcool", "TEXT"),
            ("num_lesoes", "INTEGER"),
            ("doencas_infectiosas", "TEXT")
        ]
        
        # Colunas para a tabela 'parceiros'
        parceiro_columns = [
            ("modo_atendimento", "TEXT"),
            ("endereco", "TEXT")
        ]
        
        # Atualiza vendas
        for col, type_ in vendas_columns:
            try:
                cursor.execute(f"ALTER TABLE vendas ADD COLUMN {col} {type_}")
                print(f"vendas: Coluna {col} adicionada.")
            except sqlite3.OperationalError:
                pass # Coluna já existe
        
        # Atualiza parceiros
        for col, type_ in parceiro_columns:
            try:
                cursor.execute(f"ALTER TABLE parceiros ADD COLUMN {col} {type_}")
                print(f"parceiros: Coluna {col} adicionada.")
            except sqlite3.OperationalError:
                pass # Coluna já existe

        conn.commit()
        conn.close()
        print("Banco de dados atualizado com sucesso!")
    except Exception as e:
        print(f"Erro geral: {e}")

if __name__ == "__main__":
    update_db()
