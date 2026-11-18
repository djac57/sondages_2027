# IFOP — reports mesurés entre candidats du centre et de la droite

Source : sondage IFOP (diapositive « Vers qui se reportent les électeurs du centre et de la droite ? », transmise par l'utilisateur le 18/11/2026). **Données de croisement avec l'hypothèse à quatre candidats du centre et/ou de la droite.** C'est une MESURE (reports déclarés), à utiliser à la place / en complément de la matrice d'affinités inférée (`affinity_flow.py`).

Le % sous chaque candidat = son score de 1er tour dans l'hypothèse testée. Les lignes = vers qui vont ses électeurs s'il n'est pas candidat.

## Si ATTAL n'est pas candidat (Attal 4 %)
- Philippe 52 · Lisnard 23 · Autres 12 · Glucksmann 9 · Le Pen 2 · Retailleau 1 · Zemmour 1

## Si PHILIPPE n'est pas candidat (Philippe 11 %)
- Attal 42 · Retailleau 17 · Glucksmann 14 · Lisnard 12 · Autres 8 · Le Pen 7

## Si LISNARD n'est pas candidat (Lisnard 9 % ; Attal pas candidat dans cette hypothèse)
- Retailleau 30 · Philippe 20 · Le Pen 19 · Autres 14 · Zemmour 10 · Glucksmann 7

## Si RETAILLEAU n'est pas candidat (Retailleau 4 % ; Attal pas candidat dans cette hypothèse)
- Lisnard 39 · Le Pen 33 · Philippe 28

## Lecture pour le site
- **Mesure, pas inférence** : remplace les flux « affinités » pour ces quatre candidats. Étiqueter « reports IFOP » (sourcé) vs le reste « inféré ».
- Confirme : Attal → Philippe très fort (52) ; Philippe → Attal (42) ; Lisnard et Retailleau se renvoient (Lisnard→Retailleau 30, Retailleau→Lisnard 39) ; fuite vers Le Pen surtout depuis Lisnard (19) et Retailleau (33), peu depuis Attal/Philippe.
- Limites : deux hypothèses (Lisnard, Retailleau) supposent Attal déjà absent → non directement comparables aux deux autres. Reports vers « Autres »/Zemmour à garder tels quels. Pas de report du RN mesuré ici (hypothèse centre/droite).
