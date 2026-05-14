# Ilúmina Med 🏥✨

> **Status do Projeto:** 🚧 Em Desenvolvimento / Versão Beta Estrita

A **Ilúmina Med** é uma plataforma e marketplace de saúde multidisciplinar voltada para a intermediação, agendamento inteligente e gestão de atendimentos para profissionais de saúde autônomos. Sediada e focada inicialmente em **Curitiba - PR**, a plataforma conecta pacientes a especialistas altamente qualificados (como Estomaterapia, Laserterapia, Fisioterapia, Nutrição, entre outros).

O modelo de negócio opera como uma **Clínica Digital Híbrida / SaaS-enabled Marketplace**: os profissionais parceiros utilizam o ecossistema tecnológico e a infraestrutura de captação de clientes da plataforma, que retém uma porcentagem/comissão sobre as consultas e avaliações realizadas de forma automatizada.

---

## 🌟 Funcionalidades Principais

* **Marketplace Multidisciplinar:** Espaço virtual customizado para múltiplos profissionais gerenciarem seus horários e especialidades.
* **Agendamento Fluido e Híbrido:** Suporte para fluxos rápidos de contato via WhatsApp ou agendamento direto pelo sistema com preenchimento automatizado de prontuário e queixa.
* **Integração com Mercado Pago:** Checkout transparente e seguro para pagamentos online e cobrança de taxas de intermediação.
* **Inteligência Artificial (IA Groq):** Assistente digital integrada para triagem inicial, suporte ao paciente e otimização de queixas clínicas.
* **Persistência de Dados (Neon / PostgreSQL):** Conexão robusta e escalável em nuvem para salvar históricos de agendamento, dados de pacientes e registros em conformidade com as normas do COFEN/Coren e conselhos de classe.

---

## 🛠️ Tecnologias Utilizadas

* **Backend:** Python 3.x com framework Flask
* **Banco de Dados:** PostgreSQL (Instância em nuvem via Neon) / SQLite (Ambiente de desenvolvimento local)
* **Pagamentos:** Mercado Pago API SDK
* **Inteligência Artificial:** Groq Cloud API (Modelos Llama/Mistral)
* **Hospedagem/Deploy:** Render Cloud Platform
* **Controle de Versão:** Git / GitHub

---

## 🏗️ Estrutura Arquitetural (Variáveis de Ambiente)

Para garantir a máxima segurança de dados, todas as credenciais de produção (Mercado Pago, Groq e conexões SQL) são mantidas estritamente isoladas por meio de variáveis de ambiente (`.env`), impedindo a exposição indesejada no repositório público:

```env
