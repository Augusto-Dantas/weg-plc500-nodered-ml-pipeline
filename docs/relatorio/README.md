# Relatório Técnico em LaTeX (WEG PLC500 + Modbus TCP/IP + Node-RED)

**Projeto (Situação de Aprendizagem de IoT — Escopo Livre com Aprovação Prévia do Professor):** Pipeline Híbrida de Análise de Dados Industriais Integrando WEG PLC500, Comunicação Modbus TCP/IP e Orquestração com Node-RED (Sensores Simulados por Botoeiras e Atuadores Simulados por LEDs), incluindo histórico de evolução e conformidade a partir do projeto inicial de Servidor NTP com Antena GNSS e ESP32.

## Estrutura do Projeto

- `main.tex` — Documento raiz (`abntex2`).
- `config/metadados.tex` — **Edite aqui** os dados de capa e folha de rosto (instituição, autor, orientador, cidade, código e revisão).
- `config/pacotes.tex` — Pacotes, estilos de código (`listings`), ambiente `quadro` e bibliotecas TikZ.
- `pretextual/` — Capa, folha de rosto, resumo, abstract, siglas e símbolos.
- `capitulos/` — Capítulos 1 a 7, incluindo 4 diagramas vetoriais TikZ nativos, tabelas técnicas e 4 listagens de código (Structured Text IEC 61131-3, JavaScript do nó `function` do Node-RED, JSON Schema e DDL PostgreSQL).
- `apendices/apendice_a_checklist.tex` — Checklist de comissionamento em bancada.
- `referencias.bib` — Referências técnicas e científicas do domínio (CLPs, Modbus, Node-RED, NTPv4/RFC 5905, ESP32, IA e cibersegurança OT).

## Como Compilar o PDF

### Opção 1 — Compilação Direta via TinyTeX
```bash
PATH="$HOME/.TinyTeX/bin/x86_64-linux:$PATH" make pdf
```

### Opção 2 — Overleaf
1. Faça upload do arquivo `overleaf_relatorio_abnt_plc500_n8n.zip` (ou gere-o com `make overleaf-zip`) no Overleaf (*New Project* -> *Upload Project*).
2. Em **Menu -> Compiler**, mantenha **pdfLaTeX** e **Main document** = `main.tex`.
3. Clique em **Recompile**.
