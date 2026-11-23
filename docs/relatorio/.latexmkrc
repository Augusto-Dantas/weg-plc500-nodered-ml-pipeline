# Configuração do latexmk para compilação com pdflatex + bibtex (abntex2)
$pdf_mode = 1;
$bibtex_use = 2;
$pdflatex = 'pdflatex -interaction=nonstopmode -halt-on-error -synctex=1 %O %S';
$clean_ext = 'bbl blg loq lof lot out toc aux fls fdb_latexmk synctex.gz nav snm vrb brf';
