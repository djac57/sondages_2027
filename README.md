# 539 — Présidentielle 2027

Probabilité de victoire : ce que les **sondages** impliquent contre ce que paie le **marché**. Présidentielle française 2027.

**En ligne :** https://djac57.github.io/sondages_2027/

## Données (live, pas figées)
- **Sondages 1er tour** : moyenne des derniers sondages, scrapés sur Wikipédia (« Liste de sondages sur l'élection présidentielle française de 2027 »).
- **Marché** : Polymarket, proba de victoire (event *Next French Presidential Election*) + variation sur 30 j (API CLOB).
- **Proba implicite (modèle 539)** : simulation Monte-Carlo (200 000 tirages) du 1er tour (parts sondagières + incertitude) puis du 2nd tour (duels, avec plafond « front républicain » pour l'extrême et faiblesse de duel de LFI — hypothèses documentées).

## Fonctionnement
- `scripts/update539.py` : `fetch` (→ `raw/*.json`) puis `build` (modèle → `site/data.js`). Résilient : si une source tombe, le dernier `raw` committé est réutilisé et le site se déploie quand même.
- `site/index.html` lit `site/data.js` (rien n'est codé en dur).
- `.github/workflows/update.yml` : **mise à jour automatique quotidienne** (06:17 UTC) + à chaque push, puis déploiement GitHub Pages.

Mettre à jour à la main : `python scripts/update539.py` puis commit.

Projet personnel, sans lien avec une activité professionnelle.
