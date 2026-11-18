#!/usr/bin/env python3
"""539 — agrégateur sondages + marché pour la présidentielle 2027.
fetch() : ping Polymarket (proba de victoire + delta 30 j) et les sondages 1er tour (Wikipédia).
build() : modèle implicite (Monte-Carlo 1er/2nd tour) + écrit site/data.js.
Résilient : si une source tombe, on garde le dernier raw committé et le site se déploie quand même.
"""
import io, json, os, re, sys, time, datetime, unicodedata
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
POLL_RECENT = 8       # nb de SONDAGES (pas de lignes) récents retenus pour l'agrégat, tous testant la même hypothèse
POLL_MIN = 3          # minimum de sondages testant l'hypothèse de référence
POLL_MINCOLS = 6      # nb minimal de colonnes de candidats connus pour reconnaître la table du 1er tour
DUEL_RECENT = 5       # nb de sondages récents par duel de 2nd tour
MOIS = {'janvier':1,'fevrier':2,'mars':3,'avril':4,'mai':5,'juin':6,'juillet':7,'aout':8,
        'septembre':9,'octobre':10,'novembre':11,'decembre':12}

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

IFOP_MD = "ifop-reports-centre-droite.md"
IFOP_ABST = 0.05      # IFOP ne mesure pas l'abstention (reports entre exprimés) : poids faible
IFOP_EPS  = 0.005     # destination absente d'une ligne IFOP sans « Autres » : quasi nulle
def load_ifop(path=None):
    """Reports MESURÉS (IFOP) lus dans raw/ifop-reports-centre-droite.md :
    {source: {"to": {dest|'autres': %}, "note": hypothèse}}. Sections « ## Si X n'est pas candidat (...) »
    suivies d'une ligne « - Nom 52 · Nom 23 · Autres 12 »."""
    path = path or os.path.join(RAW, IFOP_MD)
    try:
        txt = open(path, encoding="utf-8").read()
    except Exception:
        return {}
    out, cur = {}, None
    for line in txt.splitlines():
        m = re.match(r"##\s+Si\s+(.+?)\s+n'est pas candidat\s*\((.*)\)", line)
        if m:
            cur = keyof(m.group(1).title())
            if cur:
                out[cur] = {"to": {}, "note": m.group(2).strip()}
            continue
        if cur and line.startswith("- "):
            for part in line[2:].split("·"):
                mm = re.match(r"\s*(.+?)\s+(\d+(?:[.,]\d+)?)\s*$", part)
                if not mm:
                    continue
                nom, v = mm.group(1), float(mm.group(2).replace(",", "."))
                k = "autres" if _strip(nom) == "autres" else keyof(nom)
                if k:
                    out[cur]["to"][k] = v
            cur = None
    return {k: v for k, v in out.items() if v["to"]}

def affinity_flows(cands, ifop=None):
    """Décompose les mouvements NETS du marché (d30) en transferts plausibles,
    par flux guidé par affinités (Sinkhorn), transbordement via le nœud Abstention.
    Pour les candidats dont les reports sont MESURÉS (IFOP), la clé de répartition de leur
    ligne est la mesure IFOP (destinations citées = %, le reste se partage « Autres » par affinité) ;
    les paires citées par IFOP sont marquées m=True (mesuré), tout le reste est inféré.
    Limite assumée : seuls les flux NETS sont identifiables (pas les bruts)."""
    import numpy as np
    ifop = ifop or {}
    losers  = [c for c in cands if c["d30"] is not None and c["d30"] <= -0.3]   # d30 None = tendance inconnue
    winners = [c for c in cands if c["d30"] is not None and c["d30"] >=  0.3]
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
        mes = ifop.get(si, {}).get("to")
        if mes:   # ligne MESURÉE (IFOP) : clé de répartition = reports déclarés
            autres = mes.get("autres", 0.0)
            libres = [j for j,(tj,tb,_) in enumerate(snk) if tj != "abstention" and tj != si and tj not in mes
                      and (si,tj) not in BAN and (tj,si) not in BAN]
            wl = {j: aff_bloc(sb, snk[j][1]) for j in libres}
            for j,(tj,tb,_) in enumerate(snk):
                if tj == si or (si,tj) in BAN or (tj,si) in BAN:
                    continue
                if tj == "abstention":
                    K[i,j] = IFOP_ABST
                elif tj in mes:
                    K[i,j] = mes[tj] / 100.0
                else:     # destination non citée : sa part de « Autres » (par affinité), sinon quasi nulle
                    K[i,j] = max(IFOP_EPS, autres / 100.0 * wl[j] / (sum(wl.values()) or 1))
            continue
        for j,(tj,tb,_) in enumerate(snk):
            if si == tj:                      # pas de flux vers soi-même
                continue
            if (si,tj) in BAN or (tj,si) in BAN:
                continue
            K[i,j] = AFF_ABST if (si=="abstention" or tj=="abstention") else aff_bloc(sb,tb)
    u = np.ones(len(src)); v = np.ones(len(snk)); eps=1e-9
    for _ in range(2000):
        u = a / (K @ v + eps)
        v = b / (K.T @ u + eps)
    T = u[:,None] * K * v[None,:]
    err = max(float(np.abs(T.sum(1) - a).max()), float(np.abs(T.sum(0) - b).max()))
    if err > 0.05:
        print(f"[build] décomposition : marges non tenues à {err:.2f} pt près", file=sys.stderr)
    TH = 0.15
    def lab(k): return {"name":NAMES[k][0],"short":NAMES[k][1]}
    def mpart(s, t):   # paire mesurée par IFOP ?
        m = ifop.get(s, {}).get("to", {})
        return t in m
    def part(k, kb, v_, s, t):
        p = {**lab(k), "bloc": kb, "v": round(float(v_), 1)}
        if mpart(s, t):
            p["m"] = True
            p["pct"] = ifop[s]["to"][t]
            p["of"] = NAMES[s][0]
            p["note"] = ifop[s]["note"]
        return p
    out=[]
    for j,(tj,tb,_) in enumerate(snk):           # gagnants : d'où vient le gain (abstention = ligne dédiée, pas ici)
        if tj=="abstention": continue
        parts=sorted([(src[i][0],src[i][1],T[i,j]) for i in range(len(src)) if src[i][0]!="abstention" and T[i,j]>=TH],
                     key=lambda p:-p[2])[:5]
        if parts:
            out.append({"id":tj,"who":NAMES[tj][0],"bloc":tb,"d":round(float(b[j]),1),"gain":True,
                        "parts":[part(i,ib,v_,i,tj) for i,ib,v_ in parts]})
    for i,(si,sb,_) in enumerate(src):           # perdants : où est parti le poids
        if si=="abstention": continue
        parts=sorted([(snk[j][0],snk[j][1],T[i,j]) for j in range(len(snk)) if snk[j][0]!="abstention" and T[i,j]>=TH],
                     key=lambda p:-p[2])[:5]
        if parts:
            out.append({"id":si,"who":NAMES[si][0],"bloc":sb,"d":round(float(-a[i]),1),"gain":False,
                        "mes":si in ifop,
                        "parts":[part(j,jb,v_,si,j) for j,jb,v_ in parts]})
    # ligne dédiée Abstention = résidu net seulement (>= 0.3)
    for i,(si,_,_) in enumerate(src):            # abstention mobilisée -> où sont allés ces électeurs
        if si=="abstention" and a[i]>=0.3:
            parts=sorted([(snk[j][0],snk[j][1],T[i,j]) for j in range(len(snk)) if snk[j][0]!="abstention" and T[i,j]>=TH],key=lambda p:-p[2])[:6]
            out.append({"who":"Abstention","bloc":"autres","d":round(float(-a[i]),1),"gain":False,
                        "parts":[{**lab(k),"bloc":kb,"v":round(float(v_),1)} for k,kb,v_ in parts]})
    for j,(tj,_,_) in enumerate(snk):            # démobilisation : candidats -> abstention
        if tj=="abstention" and b[j]>=0.3:
            parts=sorted([(src[i][0],src[i][1],T[i,j]) for i in range(len(src)) if src[i][0]!="abstention" and T[i,j]>=TH],key=lambda p:-p[2])[:6]
            out.append({"who":"Abstention","bloc":"autres","d":round(float(b[j]),1),"gain":True,
                        "parts":[{**lab(k),"bloc":kb,"v":round(float(v_),1)} for k,kb,v_ in parts]})
    out.sort(key=lambda e:-e["d"])
    return out

def _strip(s):
    return ''.join(c for c in unicodedata.normalize('NFD', str(s)) if unicodedata.category(c) != 'Mn').lower()
# Appariement explicite, prioritaire : nom nettoyé (sans accents, minuscules) -> id, ou None pour exclure.
ALIAS = {
 'jean-marie le pen': None,          # homonyme : ne doit pas aller à Marine Le Pen
}
def keyof(name):
    """Nom affiché (Wikipédia « Philippe (HOR) », Polymarket « Édouard Philippe ») -> id de CAND.
    Le nom de famille doit TERMINER le nom, en mot entier : « Philippe Poutou », « Philippe de
    Villiers » ou « Jean-Philippe Tanguy » ne vont pas à Édouard Philippe."""
    n = _strip(name)
    n = re.sub(r'\[[^\]]*\]|\([^)]*\)', ' ', n)     # appels de note [c], parti entre parenthèses
    n = re.sub(r'\s+', ' ', n).strip()
    if n in ALIAS:
        return ALIAS[n]
    for k in CAND:
        if n == k or n.endswith(' ' + k):
            return k
    return None
def colkeyof(col):
    """Colonne de table (éventuellement à plusieurs niveaux d'en-tête) -> id de CAND.
    Le nom du candidat n'est pas toujours au même niveau (Wikipédia l'a déplacé le 17/11/2026)."""
    for lvl in (col if isinstance(col, tuple) else (col,)):
        k = keyof(lvl)
        if k:
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
    out, hist = {}, {}
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
        nom = m.get("groupItemTitle") or m.get("question") or ""
        k = keyof(nom)
        yes = float(pr[0])
        if not k:
            if yes > 0.01:   # candidat du marché absent de CAND : on le dit dès qu'il pèse plus de 1 %
                print(f"[fetch] marché : « {nom} » absent de CAND, ignoré ({yes * 100:.1f} %)")
            continue
        if k in out:
            print(f"[fetch] marché : « {nom} » apparié à '{k}' déjà pris, ignoré", file=sys.stderr)
            continue
        d30 = None           # None = tendance inconnue (à ne pas confondre avec 0 = stable)
        try:
            h = requests.get("https://clob.polymarket.com/prices-history",
                             params={"market": toks[0], "interval": "1m", "fidelity": 720},
                             headers=UA, timeout=30).json().get("history", [])
            if h:
                d30 = (yes - float(h[0]["p"])) * 100
            else:
                print(f"[fetch] marché : historique vide pour '{k}', tendance 30 j inconnue", file=sys.stderr)
        except Exception as e:
            print(f"[fetch] marché : historique en échec pour '{k}', tendance 30 j inconnue : {e}", file=sys.stderr)
        try:   # historique COMPLET (1 point/jour) pour la courbe : un échec ne coûte que la courbe
            hh = requests.get("https://clob.polymarket.com/prices-history",
                              params={"market": toks[0], "interval": "max", "fidelity": 1440},
                              headers=UA, timeout=30).json().get("history", [])
            if hh:
                hist[k] = hh
        except Exception as e:
            print(f"[fetch] marché : historique complet en échec pour '{k}' (courbe seulement) : {e}", file=sys.stderr)
        out[k] = {"mkt": round(yes * 100, 2), "d30": None if d30 is None else round(d30, 2)}
    if len(out) < 3:
        raise RuntimeError("marché: trop peu de candidats, abandon")
    today = datetime.date.today().isoformat()
    save_atomic(os.path.join(RAW, "market.json"),
                {"fetched": today, "volume": ev.get("volume"), "candidates": out})
    print(f"[fetch] marché OK : {len(out)} candidats")
    try:      # l'historique ne doit JAMAIS faire échouer la récupération
        save_history(hist, {k: v["mkt"] for k, v in out.items()}, today)
    except Exception as e:
        print(f"[fetch] historique du marché non enregistré : {e}", file=sys.stderr)

HIST_PATH = os.path.join(RAW, "market_history.json")
def save_history(hist, spot, today):
    """Courbe du marché, qui se construit jour après jour : {"series": {id: {"AAAA-MM-JJ": proba %}}}.
    Upsert : l'API (clôture quotidienne) complète/écrase les jours qu'elle couvre ; le relevé du jour
    (spot) fixe la date du jour. Les jours anciens que l'API ne renverrait plus sont conservés."""
    hj = load_json(HIST_PATH, {})
    ser = hj.get("series", {})
    for k, h in hist.items():
        s = ser.setdefault(k, {})
        for x in h:
            d = datetime.datetime.fromtimestamp(int(x["t"]), datetime.timezone.utc).date().isoformat()
            s[d] = round(float(x["p"]) * 100, 2)
    for k, p in spot.items():
        ser.setdefault(k, {})[today] = p
    ser = {k: dict(sorted(v.items())) for k, v in ser.items()}
    save_atomic(HIST_PATH, {"source": "Polymarket, event « Next French Presidential Election » "
                            "(clôture quotidienne CLOB + relevé 539 du jour)",
                            "updated": today, "series": ser})
    print(f"[fetch] historique du marché : {len(ser)} candidats, "
          f"{max((len(v) for v in ser.values()), default=0)} jours au plus")

def _col(t, prefix):
    return next((c for c in t.columns if str(c[0] if isinstance(c, tuple) else c).startswith(prefix)), None)

def _polls_of(t, colkey, sond):
    """Lignes de la table -> liste de SONDAGES (récent -> ancien). Un sondage = (sondeur, date,
    échantillon) ; il porte plusieurs lignes = plusieurs hypothèses de casting.
    Chaque hypothèse : {id: score} des candidats testés (cellule numérique)."""
    dcol, ecol = _col(t, "Date"), _col(t, "Échantillon")
    out, idx = [], {}
    for _, r in t.iterrows():
        s = r[sond]
        if not isinstance(s, str) or s == "Sondeur" or len(s) > 40:   # ligne vide / en-tête / événement
            continue
        key = (_strip(s).replace(" ", ""), str(r[dcol]) if dcol is not None else "",
               str(r[ecol]) if ecol is not None else "")
        hyp = {}
        for c, k in colkey.items():
            v = pd.to_numeric(r[c], errors="coerce")
            if pd.notna(v):
                hyp[k] = float(v)
        if not hyp:
            continue
        if key not in idx:
            idx[key] = len(out)
            out.append({"sondeur": s.strip(), "date": key[1], "hyps": []})
        out[idx[key]]["hyps"].append(hyp)
    return out

def coherent_average(polls, order=None):
    """Moyenne COHÉRENTE : une hypothèse de casting de référence, la même pour tous les sondages.
    Référence = l'hypothèse la plus complète du dernier sondage (repli : la plus complète testée par
    au moins POLL_MIN sondages). On garde les POLL_RECENT derniers sondages qui la testent (une ligne
    contenant tous ses candidats ; plusieurs lignes d'un même sondage = moyennées entre elles), puis
    chaque candidat est moyenné sur ce MÊME ensemble de sondages."""
    def matches(casting):
        sel = []
        for p in polls:
            rows = [h for h in p["hyps"] if casting <= set(h)]
            if rows:
                sel.append((p, rows))
        return sel
    cands = []
    if polls:
        cands.append(set(max(polls[0]["hyps"], key=len)))
    for p in polls[:12]:
        for h in sorted(p["hyps"], key=len, reverse=True):
            cands.append(set(h))
    casting, sel = None, []
    for c in cands:
        s = matches(c)
        if len(s) >= POLL_MIN:
            casting, sel = c, s
            break
    if casting is None:
        raise RuntimeError("sondages : aucune hypothèse de casting testée par assez de sondages, abandon")
    sel = sel[:POLL_RECENT]
    per = []
    for p, rows in sel:
        per.append({k: float(np.mean([h[k] for h in rows])) for k in casting})
    keys = [k for k in (order or sorted(casting)) if k in casting]   # ordre FIXE (colonnes) : tirages reproductibles
    avg = {k: round(float(np.mean([x[k] for x in per])), 2) for k in keys}
    return avg, casting, [{"sondeur": p["sondeur"], "date": p["date"]} for p, _ in sel]

def _iso_fr(txt):
    """« (7 juillet 2026) » -> '2026-07-07' (dernière date française entre parenthèses)."""
    ms = re.findall(r"\((\d{1,2})(?:er)?\s+([a-zéèûô]+)\s+(\d{4})\)", txt)
    for d, m, y in reversed(ms):
        mo = MOIS.get(_strip(m))
        if mo:
            try:
                return datetime.date(int(y), mo, int(d)).isoformat()
            except ValueError:
                pass
    return None

def parse_wiki(html):
    tables = pd.read_html(io.StringIO(html), decimal=",", thousands="\xa0")
    res = {"duels": [], "events": []}
    seen_ev = set()
    for t in tables:      # événements datés que Wikipédia insère dans les tables (sourcés sur la page)
        sd = _col(t, "Sondeur")
        if sd is None:
            continue
        for s in t[sd]:
            if isinstance(s, str) and len(s) > 40:
                d = _iso_fr(s)
                txt = re.sub(r"\[[^\]]*\]", "", s).strip()
                if d and txt not in seen_ev:
                    seen_ev.add(txt)
                    res["events"].append({"d": d, "t": txt})
    res["events"].sort(key=lambda e: e["d"])
    for t in tables:      # duels de 2nd tour : tables à exactement 2 candidats connus
        sd = _col(t, "Sondeur")
        if sd is None:
            continue
        ck = {}
        for c in t.columns:
            k = colkeyof(c)
            if k and k not in ck.values():
                ck[c] = k
        if len(ck) != 2:
            continue
        ps = [p for p in _polls_of(t, ck, sd)
              if any(len(h) == 2 and 95 <= sum(h.values()) <= 105 for h in p["hyps"])][:DUEL_RECENT]
        if not ps:
            continue
        (ka, kb) = list(ck.values())
        va = np.mean([next(h for h in p["hyps"] if len(h) == 2)[ka] for p in ps])
        vb = np.mean([next(h for h in p["hyps"] if len(h) == 2)[kb] for p in ps])
        res["duels"].append({"a": ka, "b": kb, "va": round(float(va), 1), "vb": round(float(vb), 1),
                             "n": len(ps), "last": f"{ps[0]['sondeur']} {ps[0]['date']}"})
    return tables, res

def fetch_polls(html=None, save=True):
    html = html if html is not None else requests.get(WIKI, headers=UA, timeout=40).text
    tables, extra = parse_wiki(html)
    # La table est choisie par ses en-têtes, pas par sa position : la première (= la plus récente)
    # qui a une colonne « Sondeur » et au moins POLL_MINCOLS colonnes de candidats connus.
    t = colkey = sond = None
    for cand_t in tables:
        ck = {}
        for c in cand_t.columns:
            k = colkeyof(c)
            if not k:
                continue
            if k in ck.values():
                print(f"[fetch] sondages : colonne {c!r} appariée à '{k}' déjà pris, ignorée", file=sys.stderr)
                continue
            ck[c] = k
        sd = [c for c in cand_t.columns if str(c[0] if isinstance(c, tuple) else c).startswith("Sondeur")]
        if sd and len(ck) >= POLL_MINCOLS:
            t, colkey, sond = cand_t, ck, sd[0]
            break
    if t is None:
        raise RuntimeError(f"sondages: aucune table Wikipédia avec une colonne « Sondeur » et au moins "
                           f"{POLL_MINCOLS} candidats connus (la page a changé de structure ?), abandon")
    def titre(c):   # premier niveau d'en-tête qui porte un vrai libellé
        return next((str(l) for l in (c if isinstance(c, tuple) else (c,)) if not str(l).startswith("Unnamed")), str(c))
    libre = [c for c in t.columns if c not in colkey and c != sond]
    print("[fetch] sondages : colonnes appariées : " + ", ".join(f"{titre(c)} -> {k}" for c, k in colkey.items()))
    print("[fetch] sondages : colonnes non appariées : " + (", ".join(titre(c) for c in libre) or "aucune"))
    polls_list = _polls_of(t, colkey, sond)
    polls, casting, used = coherent_average(polls_list, list(colkey.values()))
    if len(polls) < 4:
        raise RuntimeError("sondages: trop peu de candidats dans l'hypothèse de référence, abandon")
    out = {"fetched": datetime.date.today().isoformat(),
           "n_polls": len(used), "polls": polls, "n": {k: len(used) for k in polls},
           "method": "hypothèse de casting commune à tous les sondages retenus",
           "used": used, "duels": extra["duels"], "events": extra["events"]}
    if save:
        save_atomic(os.path.join(RAW, "polls.json"), out)
    print(f"[fetch] sondages OK : {len(polls)} candidats ({', '.join(sorted(casting))}), "
          f"{len(used)} sondages ({used[0]['sondeur']} {used[0]['date']} -> {used[-1]['sondeur']} {used[-1]['date']}), "
          f"{len(extra['duels'])} duels, {len(extra['events'])} événements")
    return out

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
    ifop = load_ifop()

    ids = sorted(set(mk) | set(pa),
                 key=lambda k: (mk.get(k, {}).get("mkt", 0)), reverse=True)
    cands = []
    for k in ids:
        mkt = mk.get(k, {}).get("mkt")
        d30 = mk.get(k, {}).get("d30")      # None = inconnu (historique en échec, ou candidat hors marché)
        im = implied.get(k, {})
        poll = pa.get(k)
        if (mkt is None or mkt < 1) and (poll is None or poll < 3):
            continue
        # pourquoi pas de proba implicite : non testé dans l'hypothèse de référence, ou < 1 % (hors simulation)
        why = None if im.get("impl") is not None else ("untested" if poll is None else "low")
        cands.append({
            "id": k, "full": CAND[k][0], "pty": CAND[k][1], "bloc": CAND[k][2],
            "poll": im.get("first", poll), "praw": poll, "reach": im.get("reach"),
            "impl": im.get("impl"), "mkt": mkt, "d30": None if d30 is None else round(d30, 1),
            "why": why,
        })
    decomp = affinity_flows(cands, ifop)

    # courbe du marché (raw/market_history.json) : candidats affichés, 5 premiers en tête
    hj = load_json(HIST_PATH, {})
    shown = {c["id"] for c in cands}
    hist = {k: [[d, round(p, 2)] for d, p in s.items()] for k, s in hj.get("series", {}).items()
            if k in shown and len(s) >= 2}
    top5 = [c["id"] for c in cands if c["mkt"] is not None and c["id"] in hist][:5]
    d0 = min((v[0][0] for v in hist.values()), default=None)
    events = [e for e in pollsf.get("events", []) if d0 and e["d"] >= d0]
    duels = [x for x in pollsf.get("duels", []) if x["a"] in shown or x["b"] in shown]
    for x in duels:
        x["na"], x["nb"] = NAMES[x["a"]][0], NAMES[x["b"]][0]
    stables = [c["full"] for c in cands if c["d30"] is not None and abs(c["d30"]) < 0.3]
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
        "stables": stables,
        "hist": hist, "top5": top5, "events": events, "duels": duels,
        "used": pollsf.get("used", []),
        "ifop": {NAMES[k][0]: v["note"] for k, v in ifop.items()},
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
    echecs = []
    if do in ("all", "fetch"):
        try: fetch_market()
        except Exception as e:
            echecs.append("marché"); print(f"[fetch] marché ECHEC (on garde le dernier) : {e}", file=sys.stderr)
        try: fetch_polls()
        except Exception as e:
            echecs.append("sondages"); print(f"[fetch] sondages ECHEC (on garde le dernier) : {e}", file=sys.stderr)
    if do in ("all", "build"):
        build()
    if echecs:   # le site reste construit sur le dernier raw, mais l'échec doit se voir (code ≠ 0)
        sys.exit(f"[fetch] ECHEC de : {', '.join(echecs)} — données précédentes conservées")
