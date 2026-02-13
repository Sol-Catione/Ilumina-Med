import sqlite3

def update_db_email():
    try:
        conn = sqlite3.connect('ilumina_med.db')
        cursor = conn.cursor()
        
        try:
            print("Adicionando coluna email...")
            cursor.execute("ALTER TABLE vendas ADD COLUMN email TEXT")
            print("Coluna email adicionada com sucesso.")
        except sqlite3.OperationalError as e:
            if "duplicate column" in str(e).lower():
                print("Coluna email já existe.")
            else:
                print(f"Erro ao adicionar email: {e}")

        conn.commit()
        conn.close()
        print("Banco de dados atualizado com sucesso!")
    except Exception as e:
        print(f"Erro geral: {e}")

if __name__ == "__main__":
    update_db_email()
