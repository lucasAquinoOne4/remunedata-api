    # Guia de Testes Direto ao Ponto 

Aqui estão os passos para você validar todas as funcionalidades do projeto.

> **Nota para Windows (PowerShell):** Use `curl.exe` em vez de apenas `curl` (que no PS é um alias para `Invoke-WebRequest` e possui parâmetros diferentes).

## 1. Validar Ambiente
Certifique-se que os 5 containers estão rodando:
```bash
make status
```

## 2. Rodar Testes Automáticos
Rode a suíte completa (mais de 150 testes). Ela roda no banco isolado `df_remuneration_test` e é bloqueada em qualquer outro banco:
```bash
make test
```

## 3. Disparar uma Coleta (API)
Acesse o Swagger em `http://localhost:8000/docs` (só existe fora de produção) ou use o terminal:

**Trigger para 2025:**
```bash
curl.exe -X POST "http://localhost:8000/api/v1/executions/?ano=2025"
```
*Isso retornará um `id` (ex: 1) e iniciará o trabalho em background.*

## 4. Acompanhar em Tempo Real
Veja o worker trabalhando nos logs:
```bash
make logs
```
Você verá mensagens como "Iniciando coleta do mês XX".

## 5. Consultar Progresso
Veja se os meses estão sendo preenchidos e o status mudando para `success`:
```bash
curl.exe -X GET "http://localhost:8000/api/v1/executions/1"
```

## 6. Buscar Dados Coletados
Procure por um nome (parcial) no banco:
```bash
curl.exe -X GET "http://localhost:8000/api/v1/remuneration/?nome=ALBERTO"
```

## 7. Exportar Resultados (Planilha)
Baixe os dados processados em formato Excel:
```bash
curl.exe -O -J -L "http://localhost:8000/api/v1/executions/1/export?format=xlsx"
```
*Dica: Você também pode clicar no botão "Download" dentro do Swagger (`/docs`) na rota de export.*

---
**Status:** Pronto para validação final! 
