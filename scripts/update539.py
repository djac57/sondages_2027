#!/usr/bin/env python3
"""539 — agrégateur sondages + marché pour la présidentielle 2027.
fetch() : ping Polymarket (proba de victoire + delta 30 j) et les sondages 1er tour (Wikipédia).
build() : modèle implicite (Monte-Carlo 1er/2nd tour) + écrit site/data.js.
Résilient : si une source tombe, on garde le dernier raw committé et le site se déploie quand même.
"""
import io, json, os, sys, time, datetime, unicodedata
import requests, numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RAW  = os.path.join(ROOT, "raw")
SITE = os.path.join(ROOT, "site")
os.makedirs(RAW, exist_ok=True)
UA = {"User-Agent": "djac57-539/1.0 (personal polls+markets aggregator; https://djac57.github.io/sondages_2027/)"}
WIKI = "https://fr.wikipedia.org/wiki/Liste_de_sondages_sur_l%27%C3%A9lection_pr%C3%A9sidentielle_fran%C3%A7aise_de_2027"
POLY_SLUG = "next-french-presidential-election"

# id canonique -> (nom affiché, parti, bloc)
CAND = {
 'le pen':('Marine Le Pen','Rassemblement national','ed'),
 'bardella':('Jordan Bardella','Rassemblement national','ed'),
 'zemmour':('Éric Zemmour','Reconquête','ed'),
 'dupont-aignan':('Nicolas Dupont-Aignan','Debout la France','ed'),
 'philippe':('Édouard Philippe','Horizons','c'),
 'attal':('Gabriel Attal','Renaissance','c'),
 'lagarde':('Christine Lagarde','Sans étiquette','c'),
 'lisnard':('David Lisnard','Nouvelle Énergie','d'),
 'retailleau':('Bruno Retailleau','Les Républicains','d'),
 'baroin':('François Baroin','Les Républicains','d'),
 'villepin':('Dominique de Villepin','Divers','d'),
 'melenchon':('Jean-Luc Mélenchon','La France insoumise','gr'),
 'roussel':('Fabien Roussel','Parti communiste','gr'),
 'arthaud':('Nathalie Arthaud','Lutte ouvrière','gr'),
 'glucksmann':('Raphaël Glucksmann','Place publique · PS','rose'),
 'hollande':('François Hollande','Parti socialiste','rose'),
 'tondelier':('Marine Tondelier','Les Écologistes','eco'),
}
# force de 2nd tour par candidat : plafond "front républicain" (extrême-droite),
# faiblesse connue de Mélenchon/LFI en duel, centre et PS forts. Hypothèses — voir méthodo.
RUNOFF = {'le pen':0.46,'bardella':0.50,'zemmour':0.30,'dupont-aignan':0.38,
          'philippe':0.60,'attal':0.56,'lagarde':0.58,
          'lisnard':0.56,'retailleau':0.55,'baroin':0.55,'villepin':0.55,
          'melenchon':0.33,'roussel':0.42,'arthaud':0.38,
          'glucksmann':0.60,'hollande':0.57,'tondelier':0.47}
SIG_FR = 4.0          # incertitude 1er tour (points), horizon long
N_SIM  = 200000
POLL_RECENT = 16      # nb de sondages récents retenus pour l'agrégat (table triée récent->ancien)

# étiquettes courtes pour la matrice de transferts
NAMES = {
 'le pen':('Le Pen','LP'),'bardella':('Bardella','JB'),'zemmour':('Zemmour','EZ'),
 'dupont-aignan':('Dupont-Aignan','NDA'),'philippe':('Philippe','EP'),'attal':('Attal','GA'),
 'lagarde':('Lagarde','CL'),'lisnard':('Lisnard','DL'),'retailleau':('Retailleau','BR'),
 'baroin':('Baroin','FB'),'villepin':('Villepin','DdV'),'melenchon':('Mélenchon','JLM'),
 'roussel':('Roussel','FR'),'arthaud':('Arthaud','NA'),'glucksmann':('Glucksmann','RG'),
 'hollande':('Hollande','FH'),'tondelier':('Tondelier','MT'),'abstention':('Abstention','Abs'),
}
# affinité (porosité) entre blocs — symétrique. Plus haut = transfert plus plausible.
AFF_BLOC = {
 ('ed','ed'):0.9,('d','d'):0.9,('c','c'):0.9,('rose','rose'):0.9,('gr','gr'):0.9,('eco','eco'):0.9,
 ('ed','d'):0.50,('d','c'):0.60,('c','rose'):0.50,('rose','gr'):0.50,('rose','eco'):0.70,('gr','eco'):0.60,
 ('ed','gr'):0.35,  # gaucho-lepénisme (remonté, correction d'époque)
 ('ed','c'):0.20,('d','rose'):0.15,('c','gr'):0.15,('d','gr'):0.10,('d','eco'):0.15,
 ('c','eco'):0.30,('ed','eco'):0.05,('ed','rose'):0.05,('gr','ed'):0.35,
}
AFF_ABST = 0.40                       # porosité candidat <-> Abstention (mobilisation/démobilisation)
BAN = {('lisnard','hollande')}        # paires interdites (correction d'époque) ; symétrique
def aff_bloc(a, b):
    return AFF_BLOC.get((a, b)) or AFF_BLOC.get((b, a)) or 0.10

def affinity_flows(cands):
    """Décompose les mouvements NETS du marché (d30) en transferts plausibles,
    par flux guidé par affinités (Sinkhorn), transbordement via le nœud Abstention.
    Limite assumée : seuls les flux NETS sont identifiables (pas les bruts)."""
    import numpy as np
    losers  = [c for c in cands if c["d30"] <= -0.3]
    winners = [c for c in cands if c["d30"] >=  0.3]
    if not losers or not winners:
        return []
    S  = sum(-c["d30"] for c in losers)
    Dm = sum( c["d30"] for c in winners)
    src = [(c["id"], c["bloc"], -c["d30"]) for c in losers]
    snk = [(c["id"], c["bloc"],  c["d30"]) for c in winners]
    if Dm - S > 1e-6: src.append(("abstention", "autres", Dm - S))
    if S - Dm > 1e-6: snk.append(("abstention", "autres", S - Dm))
    a = np.array([x[2] for x in src]); b = np.array([x[2] for x in snk])
    K = np.zeros((len(src), len(snk)))
    for i,(si,sb,_) in enumerate(src):
        for j,(tj,tb,_) in enumerate(snk):
            if si == tj:                      # pas de flux vers soi-même
                continue
            if (si,tj) in BAN or (tj,si) in BAN:
                continue
            K[i,j] = AFF_ABST if (si=="abstention" or tj=="abstention") else aff_bloc(sb,tb)
    u = np.ones(len(src)); v = np.ones(len(snk)); eps=1e-9
    for _ in range(800):
        u = a / (K @ v + eps)
        v = b / (K.T @ u + eps)
    T = u[:,None] * K * v[None,:]
    def lab(k): return {"name":NAMES[k][0],"short":NAMES[k][1]}
    out=[]
    for j,(tj,tb,_) in enumerate(snk):           # gagnants : d'où vient le gain
        if tj=="abstention": continue
        parts=sorted([(src[i][0],src[i][1],T[i,j]) for i in range(len(src)) if T[i,j]>=0.25],
                     key=lambda p:-p[2])[:4]
        if parts:
            out.append({"who":NAMES[tj][0],"bloc":tb,"d":round(float(b[j]),1),"gain":True,
                        "parts":[{**lab(i),"bloc":ib,"v":round(float(v_),1)} for i,ib,v_ in parts]})
    for i,(si,sb,_) in enumerate(src):           # perdants : où est parti le poids
        if si=="abstention": continue
        parts=sorted([(snk[j][0],snk[j][1],T[i,j]) for j in range(len(snk)) if T[i,j]>=0.25],
                     key=lambda p:-p[2])[:4]
        if parts:
            out.append({"who":NAMES[si][0],"bloc":sb,"d":round(float(-a[i]),1),"gain":False,
                        "parts":[{**lab(j),"bloc":jb,"v":round(float(v_),1)} for j,jb,v_ in parts]})
    out.sort(key=lambda e:-e["d"])
    return out

def _strip(s):
    return ''.join(c for c in unicodedata.normalize('NFD', str(s)) if unicodedata.category(c) != 'Mn').lower()
def keyof(name):
    n = _strip(name)
    for k in CAND:
        if k in n:
            return k
    return None
def save_atomic(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

# ---------------------------------------------------------------- FETCH
def fetch_market():
    ev = requests.get("https://gamma-api.polymarket.com/events",
                      params={"slug": POLY_SLUG}, headers=UA, timeout=30).json()
    ev = ev[0] if isinstance(ev, list) else ev
    out = {}
    for m in ev.get("markets", []):
        if m.get("closed"):
            continue
        try:
            pr = json.loads(m.get("outcomePrices", "[]"))
            toks = json.loads(m.get("clobTokenIds", "[]"))
        except Exception:
            continue
        if not pr or not toks:
            continue
        k = keyof(m.get("groupItemTitle") or m.get("question") or "")
        if not k:
            continue
        yes = float(pr[0])
        d30 = 0.0
        try:
            h = requests.get("https://clob.polymarket.com/prices-history",
                             params={"market": toks[0], "interval": "1m", "fidelity": 720},
                             headers=UA, timeout=30).json().get("history", [])
            if h:
                d30 = (yes - float(h[0]["p"])) * 100
        except Exception:
            pass
        out[k] = {"mkt": round(yes * 100, 2), "d30": round(d30, 2)}
    if len(out) < 3:
        raise RuntimeError("marché: trop peu de candidats, abandon")
    save_atomic(os.path.join(RAW, "market.json"),
                {"fetched": datetime.date.today().isoformat(), "volume": ev.get("volume"),
                 "candidates": out})
    print(f"[fetch] marché OK : {len(out)} candidats")

def fetch_polls():
    html = requests.get(WIKI, headers=UA, timeout=40).text
    tables = pd.read_html(io.StringIO(html), decimal=",", thousands="\xa0")
    t = tables[0]
    colkey = {}
    for c in t.columns:
        lvl1 = c[1] if isinstance(c, tuple) else c
        k = keyof(lvl1)
        if k:
            colkey[c] = k
    sond = [c for c in t.columns if str(c[0]).startswith("Sondeur")][0]
    t = t[t[sond].notna() & (t[sond] != "Sondeur")]
    recent = t.head(POLL_RECENT)
    polls, n = {}, {}
    for c, k in colkey.items():
        vals = pd.to_numeric(recent[c], errors="coerce").dropna()
        if len(vals):
            polls[k] = round(float(vals.mean()), 2)
            n[k] = int(len(vals))
    if len(polls) < 4:
        raise RuntimeError("sondages: trop peu de colonnes, abandon")
    save_atomic(os.path.join(RAW, "polls.json"),
                {"fetched": datetime.date.today().isoformat(),
                 "n_polls": int(len(recent)), "polls": polls, "n": n})
    print(f"[fetch] sondages OK : {len(polls)} candidats, {len(recent)} sondages")

# ---------------------------------------------------------------- MODEL
def model_implied(pollavg):
    field = [k for k in pollavg if pollavg[k] >= 1.0]
    if len(field) < 3:
        return {}
    base = np.array([pollavg[k] for k in field], float)
    base = base / base.sum() * 100
    rng = np.random.default_rng(42)
    draw = np.clip(base + rng.normal(0, SIG_FR, size=(N_SIM, len(field))), 0, None)
    top2 = np.argsort(-draw, axis=1)[:, :2]
    strength = np.array([RUNOFF.get(k, 0.50) for k in field])
    reach = np.zeros(len(field)); wins = np.zeros(len(field))
    a = top2[:, 0]; b = top2[:, 1]
    np.add.at(reach, a, 1); np.add.at(reach, b, 1)
    sa = strength[a]; sb = strength[b]
    pa = sa / (sa + sb)
    a_wins = rng.random(N_SIM) < pa
    np.add.at(wins, a[a_wins], 1)
    np.add.at(wins, b[~a_wins], 1)
    return {field[i]: {"impl": round(wins[i] / N_SIM * 100, 2),
                       "reach": round(reach[i] / N_SIM * 100, 1),
                       "first": round(float(base[i]), 1)} for i in range(len(field))}

# ---------------------------------------------------------------- BUILD
def build():
    market = load_json(os.path.join(RAW, "market.json"), {"candidates": {}, "fetched": None})
    pollsf = load_json(os.path.join(RAW, "polls.json"), {"polls": {}, "fetched": None, "n_polls": 0})
    mk = market.get("candidates", {})
    pa = pollsf.get("polls", {})
    implied = model_implied(pa)

    ids = sorted(set(mk) | set(pa),
                 key=lambda k: (mk.get(k, {}).get("mkt", 0)), reverse=True)
    cands = []
    for k in ids:
        mkt = mk.get(k, {}).get("mkt")
        d30 = mk.get(k, {}).get("d30", 0.0)
        im = implied.get(k, {})
        poll = pa.get(k)
        if (mkt is None or mkt < 1) and (poll is None or poll < 3):
            continue
        cands.append({
            "id": k, "full": CAND[k][0], "pty": CAND[k][1], "bloc": CAND[k][2],
            "poll": im.get("first", poll), "reach": im.get("reach"),
            "impl": im.get("impl"), "mkt": mkt, "d30": round(d30, 1),
        })
    decomp = affinity_flows(cands)
    vals = [c["impl"] for c in cands if c["impl"]] + [c["mkt"] for c in cands if c["mkt"]]
    scale = max(40, int(np.ceil(max(vals) / 10.0) * 10)) if vals else 50

    def frdate(iso):
        if not iso: return "—"
        y, m, d = map(int, iso.split("-"))
        mois = ["janv.","févr.","mars","avril","mai","juin","juil.","août","sept.","oct.","nov.","déc."]
        return f"{d} {mois[m-1]} {y}"

    data = {
        "asof": {"market": frdate(market.get("fetched")), "polls": frdate(pollsf.get("fetched"))},
        "n_polls": pollsf.get("n_polls", 0),
        "tour1": "18 avril 2027",
        "scale": scale,
        "candidates": cands,
        "decomp": decomp,
    }
    os.makedirs(SITE, exist_ok=True)
    # Données INLINE dans une page unique auto-cohérente (pas de data.js séparé = pas de cache mixte).
    tmpl = open(os.path.join(ROOT, "page_template.html"), encoding="utf-8").read()
    html = tmpl.replace("__DATA539__", json.dumps(data, ensure_ascii=False))
    with open(os.path.join(SITE, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)
    old = os.path.join(SITE, "data.js")
    if os.path.exists(old):
        os.remove(old)
    print(f"[build] index.html écrit (données inline) : {len(cands)} candidats, échelle {scale}, marché {data['asof']['market']}, sondages {data['asof']['polls']}")

# ---------------------------------------------------------------- MAIN
if __name__ == "__main__":
    do = sys.argv[1] if len(sys.argv) > 1 else "all"
    if do in ("all", "fetch"):
        try: fetch_market()
        except Exception as e: print(f"[fetch] marché ECHEC (on garde le dernier) : {e}", file=sys.stderr)
        try: fetch_polls()
        except Exception as e: print(f"[fetch] sondages ECHEC (on garde le dernier) : {e}", file=sys.stderr)
    if do in ("all", "build"):
        build()
