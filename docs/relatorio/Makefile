# ==============================================================================
# Makefile — Compilação e empacotamento do Relatório Técnico ABNT (abntex2)
# ==============================================================================

MAIN := main
FINAL_PDF := Arquitetura e Especificação Técnica de uma Pipeline Híbrida de Análise de Dados Industriais Integrando WEG PLC500, Comunicação Modbus TCP-IP e Orquestração com Node-RED.pdf
OVERLEAF_ZIP := overleaf_relatorio_abnt_plc500_nodered.zip
TEX_DOCKER_IMAGE := texlive/texlive:latest

.PHONY: pdf docker overleaf-zip clean distclean

pdf:
	latexmk -pdf $(MAIN).tex
	mv -f $(MAIN).pdf "$(FINAL_PDF)"

docker:
	docker run --rm -v "$(CURDIR):/workdir" -w /workdir $(TEX_DOCKER_IMAGE) \
		latexmk -pdf $(MAIN).tex
	mv -f $(MAIN).pdf "$(FINAL_PDF)"

overleaf-zip:
	python3 -c "import os, zipfile; \
	files = ['main.tex', 'referencias.bib', '.latexmkrc']; \
	dirs = ['config', 'pretextual', 'capitulos', 'apendices']; \
	zf = zipfile.ZipFile('$(OVERLEAF_ZIP)', 'w', zipfile.ZIP_DEFLATED); \
	[zf.write(f) for f in files if os.path.exists(f)]; \
	[zf.write(os.path.join(r, fn)) for d in dirs for r, _, fns in os.walk(d) for fn in sorted(fns) if fn.endswith(('.tex', '.bib'))]; \
	zf.close(); \
	print('Pacote gerado:', '$(OVERLEAF_ZIP)')"

clean:
	rm -f $(MAIN).aux $(MAIN).bbl $(MAIN).blg $(MAIN).fdb_latexmk $(MAIN).fls \
	      $(MAIN).idx $(MAIN).ilg $(MAIN).ind $(MAIN).lof $(MAIN).log \
	      $(MAIN).loq $(MAIN).lot $(MAIN).out $(MAIN).synctex.gz $(MAIN).toc \
	      $(MAIN).brf capitulos/*.aux apendices/*.aux pretextual/*.aux

distclean: clean
	rm -f "$(FINAL_PDF)" $(MAIN).pdf $(OVERLEAF_ZIP)
