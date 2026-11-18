# 539 — Présidentielle 2027

Probabilité de victoire : ce que les **sondages** impliquent contre ce que paie le **marché**. Présidentielle française 2027.

**En ligne :** https://djac57.github.io/sondages_2027/

## Données (live, pas figées)
- **Sondages 1er tour** : moyenne des 8 derniers sondages testant **la même hypothèse de casting** (la plus complète du dernier sondage), scrapés sur Wikipédia (« Liste de sondages sur l'élection présidentielle française de 2027 ») ; plus les duels de 2nd tour et les événements datés de la page (`raw/polls.json`).
- **Marché** : Polymarket, proba de victoire (event *Next French Presidential Election*) + variation sur 30 j (API CLOB) ; historique quotidien cumulé dans `raw/market_history.json` (la courbe se construit jour après jour).
- **Reports mesurés** : `raw/ifop-reports-centre-droite.md` (IFOP) fixe la clé de répartition d'Attal, Philippe, Lisnard, Retailleau dans « Où vont les voix » ; le reste est inféré.
- **Proba implicite (modèle 539)** : simulation Monte-Carlo (200 000 tirages) du 1er tour (parts sondagières + incertitude) puis du 2nd tour (duels, avec plafond « front républicain » pour l'extrême et faiblesse de duel de LFI — hypothèses documentées).

## Fonctionnement
- `scripts/update539.py` : `fetch` (→ `raw/*.json`) puis `build` (modèle → données **inline** dans `site/index.html`). Résilient : si une source tombe, le dernier `raw` committé est réutilisé et le site se déploie quand même.
- **Page unique** : `page_template.html` (source, à la racine) contient le gabarit ; `build` y injecte `window.DATA` et écrit `site/index.html` (généré — ne pas l'éditer à la main ; éditer `page_template.html`). Un seul fichier = pas de cache mixte.
- `.github/workflows/update.yml` : **mise à jour automatique quotidienne** (06:17 UTC) + à chaque push, puis déploiement GitHub Pages.

Mettre à jour à la main : `python scripts/update539.py` puis commit. Rien n'est codé en dur : tout vient des sources live.

Projet personnel, sans lien avec une activité professionnelle.
