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
    movers = sorted([c for c in cands if c["mkt"] is not None],
                    key=lambda c: -abs(c["d30"]))[:8]
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
        "movers": [{"id": c["id"], "full": c["full"], "bloc": c["bloc"], "d30": c["d30"]} for c in movers],
    }
    os.makedirs(SITE, exist_ok=True)
    with open(os.path.join(SITE, "data.js"), "w", encoding="utf-8") as f:
        f.write("/* Généré par scripts/update539.py — ne pas éditer à la main. */\n")
        f.write("window.DATA=" + json.dumps(data, ensure_ascii=False, indent=1) + ";\n")
    print(f"[build] data.js écrit : {len(cands)} candidats, échelle {scale}, marché {data['asof']['market']}, sondages {data['asof']['polls']}")

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
