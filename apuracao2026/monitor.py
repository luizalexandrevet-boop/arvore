#!/usr/bin/env python3
"""Monitor da apuração do 2º turno para Presidente (Eleições 2026) com projeção estatística.

PROJEÇÃO ESTATÍSTICA, NÃO OFICIAL. Só o TSE totaliza e proclama o resultado.

Uso:
    python monitor.py --once          # uma rodada só
    python monitor.py                 # rodada inicial + loop (padrão 90 s, mínimo 60 s)
    python monitor.py --intervalo 120

Estrutura confirmada nos JSONs do TSE (resultados.tse.jus.br/oficial, ciclo 2026):
  arquivo: <base>/oficial/<ciclo>/<cd_eleicao>/dados/<uf>/<uf>-c<cargo:4>-e<ele:6>-u.json
  dg/hg ............ data/hora de geração do arquivo; dt/ht: data/hora da totalização
  s.pstn ........... % de seções totalizadas
  e.te / e.est ..... eleitorado total / eleitorado das seções já totalizadas (e.pestn = %)
  e.c / e.a ........ comparecimento / abstenção (nas seções totalizadas)
  v.vv / v.vb / v.tvn  votos válidos / brancos / nulos (total)
  carg[0].agr[].par[].cand[]: n (número), nmu (nome de urna), vap (votos), pvapn (% válidos)
"""
import argparse
import json
import math
import random
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

BASE = "https://resultados.tse.jus.br"
AMBIENTE = "oficial"
CONFIG_URL = f"{BASE}/{AMBIENTE}/comum/config/ele-c.json"
ANO = "2026"
TZ = ZoneInfo("America/Sao_Paulo")

REGIOES = {
    "N": ["ac", "am", "ap", "pa", "ro", "rr", "to"],
    "NE": ["al", "ba", "ce", "ma", "pb", "pe", "pi", "rn", "se"],
    "CO": ["df", "go", "ms", "mt"],
    "SE": ["es", "mg", "rj", "sp"],
    "S": ["pr", "rs", "sc"],
}
UFS = [u for r in REGIOES.values() for u in r]
REGIAO_DA_UF = {u: r for r, us in REGIOES.items() for u in us}
REGIAO_DA_UF["zz"] = "EX"
ABRANGENCIAS = ["br"] + UFS + ["zz"]

# Rótulos curtos só para exibição (chave = nome de urna do TSE); sem entrada, usa o nome de urna.
NOMES_CURTOS = {"LULA": "Lula", "FLAVIO BOLSONARO": "Flávio"}

MIN_UFS_MULTI = 15          # UFs mínimas para a regressão com eliminados separados
MIN_APURADO_SWING = 5.0      # % mínimo de eleitorado apurado para usar o swing da própria UF
N_SIM = 10_000
# Desvio-padrão (em pontos da fatia de válidos) do swing nos votos restantes, multiplicado pela
# fração ainda não apurada. Maior quando o swing vem da região/país e não da própria UF.
SIGMA_SWING = {"UF": 0.04, "região": 0.07, "nacional": 0.10}
SIGMA_REGIONAL = 0.015       # choque comum a todas as UFs da região
SIGMA_NACIONAL = 0.015       # choque comum a todas as UFs
SIGMA_VOLUME = 0.05          # incerteza relativa no número de válidos restantes

PASTA = Path(__file__).resolve().parent
SNAP_DIR = PASTA / "snapshots"
T1_DIR = PASTA / "dados_1turno"
HIST_CSV = PASTA / "historico.csv"
PAINEL = PASTA / "painel.html"

AVISO = ("Projeção estatística, não oficial. Só o TSE totaliza e proclama o resultado "
         "da eleição.")


# ---------------------------------------------------------------- coleta

class Coletor:
    """GET com pausa entre requisições, retry em 429/5xx e GET condicional (ETag)."""

    def __init__(self, pausa=1.5):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = "monitor-apuracao/1.0 (python-requests)"
        self.pausa = pausa
        self.cache = {}  # url -> (etag, last_modified, json)

    def get(self, url, tentativas=4):
        hdr = {}
        if url in self.cache:
            etag, lm, _ = self.cache[url]
            if etag:
                hdr["If-None-Match"] = etag
            if lm:
                hdr["If-Modified-Since"] = lm
        espera = 4.0
        erro = None
        for _ in range(tentativas):
            time.sleep(self.pausa + random.uniform(0, 0.5))
            try:
                r = self.s.get(url, headers=hdr, timeout=20)
            except requests.RequestException as ex:
                erro = str(ex)
            else:
                if r.status_code == 304 and url in self.cache:
                    return self.cache[url][2]
                if r.status_code == 200:
                    try:
                        dado = r.json()
                    except ValueError:
                        erro = "resposta não-JSON"
                    else:
                        self.cache[url] = (r.headers.get("ETag"), r.headers.get("Last-Modified"), dado)
                        return dado
                elif r.status_code == 404:
                    raise FileNotFoundError(url)
                else:
                    erro = f"HTTP {r.status_code}"
            time.sleep(espera + random.uniform(0, 2))
            espera *= 2
        raise ConnectionError(f"{url}: {erro}")


def descobrir_eleicoes(col):
    """Acha no ele-c.json as eleições de presidente (cargo 'Presidente') do 1º e 2º turnos de 2026."""
    cfg = col.get(CONFIG_URL)
    achadas = {}
    for pl in cfg["pl"]:
        if not pl["c"].endswith(ANO) or not pl["dt"].endswith(ANO):
            continue
        for e in pl["e"]:
            for abr in e.get("abr", []):
                for cp in abr.get("cp", []):
                    if cp["ds"].strip().lower() == "presidente" and abr["cd"] == "br":
                        achadas[e["t"]] = {"ciclo": pl["c"], "ele": e["cd"], "cargo": cp["cd"],
                                           "nome": e["nm"], "data": pl["dt"], "cdt2": e.get("cdt2", "")}
    if "1" not in achadas or "2" not in achadas:
        raise RuntimeError(f"Eleição de presidente de {ANO} não encontrada no config: {achadas}")
    if achadas["1"]["cdt2"] and achadas["1"]["cdt2"] != achadas["2"]["ele"]:
        print(f"[aviso] cdt2 do 1º turno ({achadas['1']['cdt2']}) difere do código do 2º turno "
              f"({achadas['2']['ele']})", file=sys.stderr)
    tmpl = next(a["dir"] for a in cfg["arq"] if a["tp"] == "e")
    return achadas["1"], achadas["2"], tmpl


def url_dados(tmpl, el, uf):
    d = (tmpl.replace("<base>", BASE).replace("<ambiente>", AMBIENTE)
         .replace("<ciclo>", el["ciclo"]).replace("<cd_eleicao>", el["ele"]).replace("<uf>", uf))
    return f"{d}/{uf}-c{int(el['cargo']):04d}-e{int(el['ele']):06d}-u.json"


# ---------------------------------------------------------------- parser

def num(x):
    if x in (None, ""):
        return 0.0
    return float(str(x).replace(",", "."))


def parse(js):
    cands = {}
    for carg in js.get("carg", []):
        for agr in carg.get("agr", []):
            for par in agr.get("par", []):
                for c in par.get("cand", []):
                    cands[c["n"]] = {"nome": c["nmu"], "votos": num(c["vap"])}
    s, e, v = js.get("s", {}), js.get("e", {}), js.get("v", {})
    return {
        "dg": js.get("dg", ""), "hg": js.get("hg", ""),
        "pst": num(s.get("pstn")), "pest": num(e.get("pestn")),
        "te": num(e.get("te")), "est": num(e.get("est")),
        "comp": num(e.get("c")), "abst": num(e.get("a")),
        "vv": num(v.get("vv")), "vb": num(v.get("vb")), "vn": num(v.get("tvn")),
        "cands": cands,
    }


# ---------------------------------------------------------------- 1º turno (uma vez)

def carregar_1turno(col, tmpl, el1):
    T1_DIR.mkdir(exist_ok=True)
    dados = {}
    for uf in ABRANGENCIAS:
        f = T1_DIR / f"{uf}.json"
        if f.exists():
            js = json.loads(f.read_text())
        else:
            js = col.get(url_dados(tmpl, el1, uf))
            f.write_text(json.dumps(js, ensure_ascii=False))
        dados[uf] = parse(js)
    return dados


# ---------------------------------------------------------------- 2º turno (cada rodada)

def ultimo_snapshot():
    arqs = sorted(SNAP_DIR.glob("*.json"))
    return json.loads(arqs[-1].read_text()) if arqs else None


def coletar_2turno(col, tmpl, el2, anterior):
    agora = datetime.now(TZ)
    snap = {"coleta": agora.isoformat(timespec="seconds"), "eleicao": el2["ele"], "abrangencias": {}}
    for uf in ABRANGENCIAS:
        try:
            js = col.get(url_dados(tmpl, el2, uf))
            snap["abrangencias"][uf] = {"status": "ok", "coletado_em": snap["coleta"], "json": js}
        except (ConnectionError, FileNotFoundError) as ex:
            prev = (anterior or {}).get("abrangencias", {}).get(uf)
            if prev and prev.get("json"):
                snap["abrangencias"][uf] = {"status": "atrasado", "coletado_em": prev["coletado_em"],
                                            "erro": str(ex), "json": prev["json"]}
            else:
                snap["abrangencias"][uf] = {"status": "sem dados", "erro": str(ex), "json": None}
    SNAP_DIR.mkdir(exist_ok=True)
    (SNAP_DIR / f"{agora:%Y%m%d_%H%M%S}.json").write_text(json.dumps(snap, ensure_ascii=False))
    return snap


# ---------------------------------------------------------------- análises

def media_pond(valores, pesos):
    valores, pesos = np.asarray(valores, float), np.asarray(pesos, float)
    return float((valores * pesos).sum() / pesos.sum()) if pesos.sum() > 0 else float("nan")


def analisar(snap, t1, hist):
    ab = snap["abrangencias"]
    if not ab.get("br", {}).get("json"):
        raise RuntimeError("Arquivo nacional (br) do 2º turno indisponível e sem snapshot anterior.")
    br = parse(ab["br"]["json"])
    nums = sorted(br["cands"], key=int)
    if len(nums) != 2:
        raise RuntimeError(f"Esperados 2 candidatos no 2º turno, achados {nums}")
    A, B = nums
    nome = {n: NOMES_CURTOS.get(br["cands"][n]["nome"], br["cands"][n]["nome"].title()) for n in nums}
    outros1 = [n for n in t1["br"]["cands"] if n not in nums]

    # ---- tabela por UF
    linhas = []
    for uf in UFS + ["zz"]:
        a1 = t1[uf]
        r1 = {"uf": uf, "regiao": REGIAO_DA_UF[uf], "te1": a1["te"], "comp1": a1["comp"], "vv1": a1["vv"],
              "s1A": a1["cands"].get(A, {"votos": 0})["votos"] / a1["vv"] if a1["vv"] else 0,
              "s1B": a1["cands"].get(B, {"votos": 0})["votos"] / a1["vv"] if a1["vv"] else 0}
        for n in outros1:
            r1[f"s1_{n}"] = a1["cands"].get(n, {"votos": 0})["votos"] / a1["vv"] if a1["vv"] else 0
        st = ab.get(uf, {}).get("status", "sem dados")
        js = ab.get(uf, {}).get("json")
        p = parse(js) if js else None
        r1.update({
            "status": st,
            "te": p["te"] if p and p["te"] else a1["te"],
            "est": p["est"] if p else 0, "pest": p["pest"] if p else 0, "pst": p["pst"] if p else 0,
            "comp": p["comp"] if p else 0, "vv": p["vv"] if p else 0,
            "vb": p["vb"] if p else 0, "vn": p["vn"] if p else 0,
            "vA": p["cands"].get(A, {"votos": 0})["votos"] if p else 0,
            "vB": p["cands"].get(B, {"votos": 0})["votos"] if p else 0,
            "hg": p["hg"] if p else "",
        })
        linhas.append(r1)
    df = pd.DataFrame(linhas).set_index("uf")
    df["pA"] = np.where(df.vv > 0, df.vA / df.vv.where(df.vv > 0, 1), np.nan)
    df["pB"] = np.where(df.vv > 0, df.vB / df.vv.where(df.vv > 0, 1), np.nan)
    df["outros1"] = 1 - df.s1A - df.s1B
    df["proprio"] = (df.pest >= MIN_APURADO_SWING) & (df.vv > 0)
    df["swA"] = np.where(df.proprio, df.pA - df.s1A, np.nan)
    df["swB"] = np.where(df.proprio, df.pB - df.s1B, np.nan)
    dom = df.drop(index="zz")

    # ---- 1. placar bruto (arquivo br do TSE)
    vvb = br["vv"] or 1
    bruto = {"pst": br["pst"], "pest": br["pest"], "pA": br["cands"][A]["votos"] / vvb,
             "pB": br["cands"][B]["votos"] / vvb, "vA": br["cands"][A]["votos"], "vB": br["cands"][B]["votos"]}
    bruto["dif"] = bruto["vA"] - bruto["vB"]

    # ---- 2. correção da mistura regional (27 UFs, peso = eleitorado; exterior à parte, item 8)
    p_reg_pool = {r: (g.vA.sum() / g.vv.sum() if g.vv.sum() > 0 else np.nan) for r, g in dom.groupby("regiao")}
    p_nac_pool = dom.vA.sum() / dom.vv.sum() if dom.vv.sum() > 0 else np.nan
    pA_uso = dom.pA.copy()
    for uf in pA_uso.index[pA_uso.isna()]:
        alt = p_reg_pool.get(dom.at[uf, "regiao"], np.nan)
        pA_uso[uf] = alt if not np.isnan(alt) else p_nac_pool
    peso_el = dom.te / dom.te.sum()
    corr_pA = float((pA_uso * peso_el).sum())
    df["peso_eleitorado"] = df.te / dom.te.sum()

    # ---- 3. swing por UF -> fatia esperada de A nos votos restantes
    sw_reg = {}
    for r, g in dom[dom.proprio].groupby("regiao"):
        sw_reg[r] = (media_pond(g.swA, g.vv), media_pond(g.swB, g.vv))
    gnac = dom[dom.proprio]
    sw_nac = (media_pond(gnac.swA, gnac.vv), media_pond(gnac.swB, gnac.vv)) if len(gnac) else None
    if sw_nac is None:  # nenhuma UF com 5%: usa o swing agregado do que já foi apurado
        g = dom[dom.vv > 0]
        if len(g):
            sw_nac = (media_pond(g.pA - g.s1A, g.vv), media_pond(g.pB - g.s1B, g.vv))
        else:
            sw_nac = (0.0, 0.0)
    fonte, qA, swA_uso, swB_uso = {}, {}, {}, {}
    for uf, r in df.iterrows():
        if r.proprio:
            fonte[uf], (a, b) = "UF", (r.swA, r.swB)
        elif uf != "zz" and r.regiao in sw_reg:
            fonte[uf], (a, b) = "região", sw_reg[r.regiao]
        else:
            fonte[uf], (a, b) = "nacional", sw_nac
        swA_uso[uf], swB_uso[uf] = a, b
        xa, xb = max(r.s1A + a, 1e-6), max(r.s1B + b, 1e-6)
        qA[uf] = xa / (xa + xb)
    df["fonte_swing"] = pd.Series(fonte)
    df["swA_uso"], df["swB_uso"] = pd.Series(swA_uso), pd.Series(swB_uso)
    df["qA"] = pd.Series(qA)

    # ---- 5. votos válidos restantes (eleitorado x comparecimento projetado - já apurados)
    turn_reg2 = {}
    for r, g in df.groupby("regiao"):
        gp = g[g.est > 0]
        t2 = gp.comp.sum() / gp.est.sum() if gp.est.sum() > 0 else np.nan
        t1g = gp.comp1.sum() / gp.te1.sum() if len(gp) else np.nan
        turn_reg2[r] = t2 / t1g if t1g and not np.isnan(t2) else np.nan
    g = dom[dom.est > 0]
    razao_nac = ((g.comp.sum() / g.est.sum()) / (g.comp1.sum() / g.te1.sum())) if len(g) else 1.0
    vr_nac = dom.vv.sum() / dom.comp.sum() if dom.comp.sum() > 0 else float((t1["br"]["vv"]) / t1["br"]["comp"])
    comp_proj, vrate = {}, {}
    for uf, r in df.iterrows():
        t1uf = r.comp1 / r.te1 if r.te1 else 0
        if r.proprio:
            comp_proj[uf] = r.comp / r.est
            vrate[uf] = r.vv / r.comp
        else:
            rz = turn_reg2.get(r.regiao, np.nan)
            comp_proj[uf] = t1uf * (rz if not np.isnan(rz) else razao_nac)
            vrate[uf] = vr_nac
    df["comp_proj"], df["valid_rate"] = pd.Series(comp_proj), pd.Series(vrate)
    df["vv_final"] = np.maximum(df.te * df.comp_proj * df.valid_rate, df.vv)
    df["R"] = df.vv_final - df.vv
    df["projA"] = df.vA + df.R * df.qA
    df["projB"] = df.vB + df.R * (1 - df.qA)

    R_tot = float(df.R.sum())
    vA_tot, vB_tot = float(df.vA.sum()), float(df.vB.sum())
    lider, atras = (A, B) if vA_tot >= vB_tot else (B, A)
    D = abs(vA_tot - vB_tot)
    precisa = (0.5 + D / (2 * R_tot)) if R_tot > 0 else float("nan")

    # ---- 4. transferência dos eliminados (regressão entre UFs com >= 5% apurado)
    transf = regressao_transferencia(dom[dom.proprio], outros1, t1, nome, A)

    # ---- 7. comparecimento por região, 2º x 1º turno
    comp_reg = []
    for r, g in df.groupby("regiao", sort=False):
        gp = g[g.est > 0]
        t2 = gp.comp.sum() / gp.est.sum() if gp.est.sum() else np.nan
        t1r = g.comp1.sum() / g.te1.sum()
        t1p = gp.comp1.sum() / gp.te1.sum() if len(gp) else np.nan  # mesmas UFs com dado no 2º turno
        comp_reg.append({"regiao": r, "comp2": t2, "comp1": t1r, "delta_pp": (t2 - t1p) * 100 if len(gp) else np.nan})

    # ---- tabela por região
    tab_reg = []
    for r in list(REGIOES) + ["EX"]:
        g = df[df.regiao == r]
        gp = g[g.proprio]
        tab_reg.append({
            "regiao": r, "apurado": 100 * g.est.sum() / g.te.sum() if g.te.sum() else 0,
            "pA": 100 * g.vA.sum() / g.vv.sum() if g.vv.sum() else np.nan,
            "pB": 100 * g.vB.sum() / g.vv.sum() if g.vv.sum() else np.nan,
            "swA": 100 * media_pond(gp.swA, gp.vv) if len(gp) else np.nan,
            "swB": 100 * media_pond(gp.swB, gp.vv) if len(gp) else np.nan,
            "ufs_swing": len(gp), "ufs": len(g),
        })

    # ---- projeção pontual + Monte Carlo
    vv_proj = float(df.vv_final.sum())
    ponto_pA = float(df.projA.sum() / vv_proj)
    mc = monte_carlo(df)

    # ---- 8. exterior
    zz = df.loc["zz"]
    ext = {"status": zz.status, "apurado": zz.pest, "pA": zz.pA, "pB": zz.pB, "vA": zz.vA, "vB": zz.vB,
           "R": zz.R, "saldo_proj": zz.projA - zz.projB, "s1A": zz.s1A, "s1B": zz.s1B}

    # ---- 6. curva da vantagem (histórico + rodada atual)
    tendencia = ajustar_tendencia(hist, bruto)

    return {"A": A, "B": B, "nome": nome, "br": br, "bruto": bruto, "corr_pA": corr_pA, "df": df,
            "sw_reg": sw_reg, "sw_nac": sw_nac, "R_tot": R_tot, "lider": lider, "atras": atras, "D": D,
            "precisa": precisa, "transf": transf, "comp_reg": comp_reg, "tab_reg": tab_reg,
            "ponto_pA": ponto_pA, "vv_proj": vv_proj, "mc": mc, "ext": ext, "tendencia": tendencia,
            "atrasados": [u for u, r in df.iterrows() if r.status != "ok"] +
                         (["br"] if ab["br"]["status"] != "ok" else [])}


def regressao_transferencia(g, outros1, t1, nome, A):
    """swing_A = beta * (fatia dos outros no 1º turno), sem intercepto, ponderada pelos válidos.
    Como swing_A + swing_B = fatia dos outros em cada UF, beta_B = 1 - beta_A.
    Também uma versão com os 3 maiores eliminados separados (indicativa)."""
    if len(g) < 3:
        return None
    w = g.vv.values
    x, y = g.outros1.values, g.swA.values
    beta = float((w * x * y).sum() / (w * x * x).sum())
    res = y - beta * x
    ybar = media_pond(y, w)
    r2 = 1 - (w * res ** 2).sum() / (w * (y - ybar) ** 2).sum() if (w * (y - ybar) ** 2).sum() > 0 else float("nan")
    out = {"n_ufs": len(g), "beta_A": beta, "beta_B": 1 - beta, "r2": float(r2), "multi": None}
    top = sorted(outros1, key=lambda n: -t1["br"]["cands"][n]["votos"])[:3]
    if len(g) >= MIN_UFS_MULTI:
        cols = [f"s1_{n}" for n in top]
        X = g[cols].copy()
        X["demais"] = g.outros1 - X.sum(axis=1)
        sw = np.sqrt(w / w.mean())
        coef, *_ = np.linalg.lstsq(X.values * sw[:, None], y * sw, rcond=None)
        rotulos = [t1["br"]["cands"][n]["nome"].title() for n in top] + ["Demais"]
        out["multi"] = [{"cand": r, "beta_A": float(c)} for r, c in zip(rotulos, coef)]
        out["multi_estavel"] = bool(np.all((coef > -0.25) & (coef < 1.25)))
    return out


def monte_carlo(df, n=N_SIM, seed=None):
    rng = np.random.default_rng(seed)
    ufs = df.index.tolist()
    f = (df.pest.values / 100).clip(0, 1)
    falta = 1 - f
    sig = np.array([SIGMA_SWING[s] for s in df.fonte_swing]) * falta
    reg = df.regiao.values
    regs = sorted(set(reg))
    f_nac = df.est.sum() / df.te.sum()
    choque_nac = rng.normal(0, SIGMA_NACIONAL * (1 - f_nac), size=(n, 1))
    choque_reg = np.zeros((n, len(ufs)))
    for r in regs:
        m = reg == r
        fr = df.est[m].sum() / df.te[m].sum()
        choque_reg[:, m] = rng.normal(0, SIGMA_REGIONAL * (1 - fr), size=(n, 1))
    q = df.qA.values[None, :] + rng.normal(0, 1, (n, len(ufs))) * sig + choque_nac + choque_reg
    q = q.clip(0, 1)
    R = df.R.values[None, :] * np.exp(rng.normal(0, SIGMA_VOLUME, (n, len(ufs))) * falta)
    vA = df.vA.values[None, :] + R * q
    vT = df.vv.values[None, :] + R
    pA = vA.sum(1) / vT.sum(1)
    return {"med": float(np.median(pA)), "p05": float(np.percentile(pA, 5)), "p95": float(np.percentile(pA, 95)),
            "probA": float((pA > 0.5).mean()), "probB": float((pA < 0.5).mean()),
            "dif_med": float(np.median(vA.sum(1) - (vT.sum(1) - vA.sum(1))))}


def ajustar_tendencia(hist, bruto):
    pts = [(bruto["pst"], bruto["dif"])]
    if hist is not None and len(hist):
        pts = list(zip(hist.pst, hist.dif_votos)) + pts
    d = pd.DataFrame(pts, columns=["pst", "dif"]).drop_duplicates()
    if len(d[d.pst >= 5]) >= 3:
        d = d[d.pst >= 5]
    if len(d) < 3 or d.pst.nunique() < 3:
        return {"n": len(d), "coef": None, "dif_100": None}
    grau = 2 if len(d) >= 8 else 1
    coef = np.polyfit(d.pst, d.dif, grau)
    return {"n": len(d), "coef": coef.tolist(), "grau": grau, "dif_100": float(np.polyval(coef, 100))}


# ---------------------------------------------------------------- saída

def pct(x, casas=2):
    return "  —  " if x is None or (isinstance(x, float) and math.isnan(x)) else dec(100 * x, casas) + "%"


def dec(x, casas=2, sinal=False):
    """Número com vírgula decimal."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    return (f"{x:+.{casas}f}" if sinal else f"{x:.{casas}f}").replace(".", ",")


def fmt_n(x):
    return f"{x:,.0f}".replace(",", ".")


def imprimir(res, snap):
    br, bruto, mc, nome, A, B = res["br"], res["bruto"], res["mc"], res["nome"], res["A"], res["B"]
    nA, nB = nome[A], nome[B]
    print("\n" + "=" * 78)
    print(f"Coleta {snap['coleta']} | horário TSE: {br['dg']} {br['hg']} | seções totalizadas: {dec(br['pst'])}%"
          f" (eleitorado: {dec(br['pest'])}%)")
    print(AVISO)
    if res["atrasados"]:
        print(f"ATRASADO (usando último snapshot ou sem dados): {', '.join(u.upper() for u in res['atrasados'])}")
    print("-" * 78)
    print(f"Placar bruto        {nA} {pct(bruto['pA'])}  x  {nB} {pct(bruto['pB'])}   "
          f"diferença {nA}−{nB}: {fmt_n(bruto['dif'])} votos")
    print(f"Placar corrigido    {nA} {pct(res['corr_pA'])}  x  {nB} {pct(1 - res['corr_pA'])}   "
          "(27 UFs no mesmo ritmo, peso = eleitorado)")
    print(f"Projeção (swing)    {nA} {pct(res['ponto_pA'])}  x  {nB} {pct(1 - res['ponto_pA'])}")
    print(f"Monte Carlo {N_SIM:,}".replace(",", ".") +
          f"  {nA} {pct(mc['med'])} [IC90 {pct(mc['p05'])} – {pct(mc['p95'])}]   "
          f"{nB} {pct(1 - mc['med'])} [IC90 {pct(1 - mc['p95'])} – {pct(1 - mc['p05'])}]")
    print(f"Probabilidade       {nA} {pct(mc['probA'], 1)}   {nB} {pct(mc['probB'], 1)}")
    print(f"Válidos restantes   ~{fmt_n(res['R_tot'])} (total projetado {fmt_n(res['vv_proj'])}); "
          f"{nome[res['atras']]} precisa de {pct(res['precisa'], 1)} deles para virar"
          + (" (acima de 100%: inalcançável pela estimativa)" if res["precisa"] > 1 else ""))
    t = res["tendencia"]
    if t["coef"]:
        print(f"Curva da vantagem   tendência (grau {t['grau']}, {t['n']} pontos) → diferença em 100%: "
              f"{fmt_n(t['dif_100'])} votos")
    else:
        print(f"Curva da vantagem   pontos insuficientes para tendência ({t['n']})")
    print("-" * 78)
    print(f"{'Região':<7}{'% apur.':>8}{nA + ' %':>11}{nB + ' %':>11}{'swing ' + nA:>13}{'swing ' + nB:>14}  UFs≥5%")
    for r in res["tab_reg"]:
        print(f"{r['regiao']:<7}{dec(r['apurado']):>8}{dec(r['pA']):>11}{dec(r['pB']):>11}"
              f"{dec(r['swA'], sinal=True):>13}{dec(r['swB'], sinal=True):>14}  {r['ufs_swing']}/{r['ufs']}")
    print("(swing = variação em pontos percentuais dos válidos, 2º turno − 1º turno; média ponderada das UFs ≥5%)")
    print("-" * 78)
    tr = res["transf"]
    if tr:
        print(f"Transferência (regressão, {tr['n_ufs']} UFs): dos votos dos demais candidatos do 1º turno, "
              f"~{pct(tr['beta_A'], 0)} → {nA}, ~{pct(tr['beta_B'], 0)} → {nB} (R² {dec(tr['r2'])})")
        if tr["multi"] and not tr["multi_estavel"]:
            print("  por eliminado: estimativa instável (colinearidade entre UFs), omitida")
        elif tr["multi"]:
            print("  por eliminado (indicativo): " + "; ".join(
                f"{m['cand']} {pct(m['beta_A'], 0)}→{nA}" for m in tr["multi"]))
    else:
        print("Transferência: menos de 3 UFs com ≥5% apurado, regressão ainda não calculada")
    print("Comparecimento 2º turno (Δ vs 1º turno nas mesmas UFs): " + "  ".join(
        f"{c['regiao']} {pct(c['comp2'], 1)} ({fmt_delta(c['delta_pp'])})" for c in res["comp_reg"]))
    e = res["ext"]
    print(f"Exterior [{e['status']}]: {dec(e['apurado'])}% apurado, {nA} {pct(e['pA'])} x {nB} {pct(e['pB'])}; "
          f"saldo projetado {nA}−{nB}: {fmt_n(e['saldo_proj'])} votos")
    print("Nota: as primeiras seções apuradas não são amostra aleatória da UF; no início o swing\n"
          "      e a projeção tendem a oscilar mais do que o intervalo sugere.")
    print("=" * 78, flush=True)


def fmt_delta(x):
    return "—" if x is None or math.isnan(x) else dec(x, 1, sinal=True) + " pp"


def salvar_historico(res, snap):
    b, mc = res["bruto"], res["mc"]
    linha = {
        "coleta": snap["coleta"], "data_tse": res["br"]["dg"], "hora_tse": res["br"]["hg"],
        "pst": round(b["pst"], 4), "pest": round(b["pest"], 4),
        "votos_A": int(b["vA"]), "votos_B": int(b["vB"]),
        "pct_A": round(100 * b["pA"], 4), "pct_B": round(100 * b["pB"], 4), "dif_votos": int(b["dif"]),
        "pct_A_corrigido": round(100 * res["corr_pA"], 4), "pct_A_swing": round(100 * res["ponto_pA"], 4),
        "mc_med_A": round(100 * mc["med"], 4), "mc_p05_A": round(100 * mc["p05"], 4),
        "mc_p95_A": round(100 * mc["p95"], 4), "prob_A": round(mc["probA"], 4), "prob_B": round(mc["probB"], 4),
        "validos_restantes": int(res["R_tot"]), "pct_necessario_atras": round(100 * res["precisa"], 3),
        "atrasados": " ".join(res["atrasados"]),
    }
    pd.DataFrame([linha]).to_csv(HIST_CSV, mode="a", header=not HIST_CSV.exists(), index=False)


def ler_historico():
    return pd.read_csv(HIST_CSV) if HIST_CSV.exists() else None


def escrever_painel(res, hist, intervalo):
    nome, A, B = res["nome"], res["A"], res["B"]
    df = res["df"]
    t = res["tendencia"]
    trend = []
    if t["coef"]:
        xs = np.linspace(max(0, float(hist.pst.min())), 100, 40)
        trend = [{"x": float(x), "y": float(np.polyval(t["coef"], x))} for x in xs]
    ufs = []
    for uf, r in df.iterrows():
        ufs.append({"uf": uf.upper(), "regiao": r.regiao, "status": r.status, "apurado": r.pest,
                    "peso": 100 * r.peso_eleitorado, "pA": None if np.isnan(r.pA) else 100 * r.pA,
                    "swA": 100 * r.swA_uso, "fonte": r.fonte_swing, "qA": 100 * r.qA, "R": r.R,
                    "proj": 100 * (r.projA / (r.projA + r.projB)) if (r.projA + r.projB) else None})

    def limpo(x):
        if isinstance(x, float) and math.isnan(x):
            return None
        return x
    dados = {
        "nA": nome[A], "nB": nome[B], "aviso": AVISO, "intervalo": intervalo,
        "hora_tse": f"{res['br']['dg']} {res['br']['hg']}", "bruto": res["bruto"], "corr": res["corr_pA"],
        "ponto": res["ponto_pA"], "mc": res["mc"], "R": res["R_tot"], "precisa": res["precisa"],
        "atras": nome[res["atras"]], "atrasados": [u.upper() for u in res["atrasados"]],
        "hist": [{k: limpo(v) for k, v in row.items()} for row in hist.to_dict("records")],
        "trend": trend, "dif100": t.get("dif_100"),
        "regioes": [{k: limpo(v) for k, v in r.items()} for r in res["tab_reg"]],
        "comp": [{k: limpo(v) for k, v in c.items()} for c in res["comp_reg"]],
        "transf": res["transf"], "ufs": [{k: limpo(v) for k, v in u.items()} for u in ufs],
        "ext": {k: limpo(float(v)) if not isinstance(v, str) else v for k, v in res["ext"].items()},
        "gerado": datetime.now(TZ).strftime("%d/%m/%Y %H:%M:%S"),
    }
    html = (PASTA / "painel_modelo.html").read_text()
    html = html.replace("__INTERVALO__", str(intervalo)).replace(
        "__DADOS__", json.dumps(dados, ensure_ascii=False, default=float).replace("</", "<\\/"))
    PAINEL.write_text(html)


# ---------------------------------------------------------------- principal

def rodada(col, tmpl, el2, t1, intervalo):
    snap = coletar_2turno(col, tmpl, el2, ultimo_snapshot())
    hist = ler_historico()
    res = analisar(snap, t1, hist)
    imprimir(res, snap)
    salvar_historico(res, snap)
    escrever_painel(res, ler_historico(), intervalo)
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true", help="roda uma vez e sai")
    ap.add_argument("--intervalo", type=int, default=90, help="segundos entre rodadas (mínimo 60)")
    args = ap.parse_args()
    intervalo = max(60, args.intervalo)

    col = Coletor()
    el1, el2, tmpl = descobrir_eleicoes(col)
    print(f"1º turno: eleição {el1['ele']} ({el1['nome']}, {el1['data']}); "
          f"2º turno: eleição {el2['ele']} ({el2['nome']}, {el2['data']}); cargo {el2['cargo']}")
    t1 = carregar_1turno(col, tmpl, el1)

    while True:
        inicio = time.monotonic()
        try:
            res = rodada(col, tmpl, el2, t1, intervalo)
        except Exception as ex:  # uma rodada com falha não derruba o loop
            print(f"[erro na rodada] {type(ex).__name__}: {ex}", file=sys.stderr, flush=True)
            res = None
        if args.once:
            break
        if res and res["bruto"]["pst"] >= 100 and not res["atrasados"]:
            print("Apuração em 100% das seções. Encerrando o monitor.")
            break
        time.sleep(max(60, intervalo - (time.monotonic() - inicio)))


if __name__ == "__main__":
    main()
