# Monitor da apuração — 2º turno Presidente 2026

**Projeção estatística, não oficial. Só o TSE totaliza e proclama o resultado.**

```bash
pip install requests pandas numpy
python monitor.py --once      # uma rodada
python monitor.py             # rodada + loop a cada 90 s (mínimo 60 s)
```

## Fonte
- Códigos das eleições descobertos em `comum/config/ele-c.json` (cargo "Presidente", abrangência `br`,
  ciclo 2026, turnos 1 e 2). O diretório vem do próprio config (`arq`, tipo `e`).
- No ciclo 2026 os dados simplificados ficam em `<ciclo>/<eleição>/dados/<uf>/<uf>-c0001-e<eleição>-u.json`
  (o caminho antigo `dados-simplificados/` responde 404).
- Campos usados: `s.pstn` (% seções), `e.te`/`e.est`/`e.pestn` (eleitorado total/apurado), `e.c`
  (comparecimento), `v.vv`/`v.vb`/`v.tvn` (válidos/brancos/nulos), `carg[].agr[].par[].cand[]` com
  `n`, `nmu`, `vap`; horário em `dg`/`hg`.
- O servidor limita requisições (HTTP 429): há pausa entre arquivos, retry com backoff e GET condicional.
  UF que não responde usa o último snapshot e aparece como "atrasado".

## Saídas
- `snapshots/AAAAMMDD_HHMMSS.json` — JSON bruto de cada abrangência na rodada.
- `historico.csv` — uma linha por rodada (A = menor número de candidato = Lula, B = Flávio).
- `painel.html` — recarrega sozinho; abrir no navegador.
- `dados_1turno/` — cache do 1º turno (baixado uma vez).

## Método (resumo)
1. **Placar bruto**: arquivo `br` do TSE.
2. **Placar corrigido**: % de cada UF ponderado pelo peso da UF no eleitorado (27 UFs; exterior à parte).
   UF sem votos usa a média da região.
3. **Swing**: (% válidos no 2º t.) − (% válidos no 1º t.) por UF com ≥5% do eleitorado apurado; demais
   UFs usam o swing médio da região ponderado pelos válidos (ou o nacional). A fatia esperada nos votos
   restantes é normalizada para somar 100%.
4. **Transferência**: regressão sem intercepto, entre UFs, do swing de Lula contra a fatia dos demais
   candidatos no 1º turno (o de Flávio é o complemento). Com ≥15 UFs, também por eliminado (indicativo).
5. **Votos restantes**: eleitorado × comparecimento projetado × taxa de válidos − válidos já apurados.
6. **Curva da vantagem**: diferença em votos × % apurado; reta (ou parábola com ≥8 pontos) a partir de 5%.
7. **Comparecimento** do 2º turno (seções totalizadas) contra o 1º turno das mesmas UFs.
8. **Exterior** (`zz`) tratado como UF separada, com fallback no swing nacional.

**Monte Carlo** (10.000): fatia de cada UF nos votos restantes ~ Normal(projeção, σ·fração não apurada),
σ = 4 pp (swing próprio), 7 pp (regional), 10 pp (nacional), mais choques comuns por região e nacional
(1,5 pp × fração não apurada) e incerteza de 5% no volume restante. Parâmetros no topo de `monitor.py`.

Limitação: só há dados por UF; as primeiras seções apuradas não são amostra aleatória da UF, então o
swing inicial pode estar enviesado pela ordem de totalização.
