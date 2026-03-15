# Référence Complète des Codes Horaires — ASR Planification
**Sécurité Riviera – C6 Ambulance Riviera**

> Compilé à partir de TOUTES les légendes PDF : décembre 2025, janvier 2026, février 2026, mars 2026, avril 2026, mai 2026, juin 2026 + legende.pdf + captures d'écran utilisateur.

---

## 🔵 FONCTIONS (Horaires de Travail)

### Horaires 23h – Jour (12h10)

| Code | Pictogramme PDF | Description | Horaire | Normalisé |
|------|-----------------|-------------|---------|-----------|
| **AMJ1** | Fond **cyan/turquoise**, texte `AMJ1` | 23 C6 Jour 1 | **06:30 – 18:40** | → AMJP |
| **AMJ2** | Fond **cyan/turquoise**, texte `AMJ2` | 23 C6 Jour 2 | **06:30 – 18:40** | → AMJP |
| **AMJP** | Fond **cyan/turquoise**, texte `AMJP` | 23 C6 Jour Planif. | **06:30 – 18:40** | AMJP |

### Horaires 23h – Nuit (12h10)

| Code | Pictogramme PDF | Description | Horaire | Normalisé |
|------|-----------------|-------------|---------|-----------|
| **AMN1** | Fond bleu foncé/navy, texte `AMN1` | 23 C6 Nuit 1 | **18:40 – 06:30 (+1)** | → AMNP |
| **AMN2** | Fond bleu foncé/navy, texte `AMN2` | 23 C6 Nuit 2 | **18:40 – 06:30 (+1)** | → AMNP |
| **AMNP** | Fond bleu foncé/navy, texte `AMNP` | 23 C6 Nuit Planif. | **18:40 – 06:30 (+1)** | AMNP |

> ⚠️ **Exception :** pour les employés `cs` et `sec`, AMNP est reclassifié → **A1** (ils ne font pas de nuits).

### Horaires Spéciaux

| Code | Pictogramme PDF | Description | Horaire | Normalisé |
|------|-----------------|-------------|---------|-----------|
| **AMHS** | Cercle **mauve/lilas** avec lettre `S` | 23 C6 Horaire S | **09:55 – 19:55** | AMHS |
| **AMHR** | Cercle **rouge/magenta** avec lettre `R` | 23 C6 Horaire R | **08:55 – 16:55** | → AMHS |

### Horaires Administratifs (lettre A avec variante numérotée)

> Dans le PDF : cellule avec un grand **A** et un petit chiffre en dessous (1*, 2*, 3*, 4*). Les horaires effectifs varient par mois/employé mais le code est toujours « A ».

| Variante | Horaire typique | Mois observés |
|----------|-----------------|---------------|
| **A** (sans variante) | 08:00 – 16:00 | mar/avr/mai/jun |
| **A1** | 08:00-12:00 + 13:00-17:00 | déc, légende |
| **A1** | 17:00-19:15 | jan |
| **A1** | 10:00-12:00 | fév |
| **A2** | 08:00 – 16:00 | tous les mois |
| **A3** | 13:00 – 23:00 | jan |
| **A3** | 08:00 – 10:00 | fév |
| **A4** | 13:00 – 23:40 | jan |

> ⚠️ **Important :** La variante numérotée de « A » a des horaires différents selon le mois. Pour le solver, tous sont normalisés comme horaire **A** (non opérationnel ambulance).

### Horaires Flexibles

| Code | Pictogramme PDF | Description | Horaire |
|------|-----------------|-------------|---------|
| **6P1** | Fond gris moyen (~170,170,170), texte `6P1` | C6 Flexible jour complet | **07:00–11:00 + 13:30–17:30** |
| **6FM** | Fond gris clair (~210,210,210), texte `6FM` | C6 Flexible matin (matin seul) | **08:00 – 12:00** |

### Rapid

| Code | Pictogramme PDF | Description | Horaire de base |
|------|-----------------|-------------|-----------------|
| **R** | Fond **rouge vif** (~252,33,0) | C6 Rapid Responder | — |
| **RS** | Fond **orange vif** (~255,127,0), texte `RS` + numéro | C6 Rapid Spécial | **09:00 – 17:00** (base) |

> Les variantes RS ont des horaires personnalisés : RS5–RS10 → tous normalisés à **RS**
> RS5=06:21-19:09, RS6=03:18-09:50, RS7=02:52-11:28, RS8=09:00-17:00, RS9=06:30-17:00, RS10=10:00-20:00

---

## 🟢 ABSENCES

### Congé / Vacances (⚠️ icônes raster — voir section dédiée)

| Code | Pictogramme PDF | Description | Normalisé |
|------|-----------------|-------------|-----------|
| **C** | ☀️ Icône soleil raster sur fond **jaune vif** (R>230, G>230, B<60) | Congé, repos (jour libre) | C |
| **C1** | ☀️ Icône soleil raster sur fond **ambre/orange** (défaut) | Congé prioritaire | → QC1 |
| **QC1** | Texte `QC1` fond clair | Demande Congé Prioritaire (demande) | QC1 |
| **VA** | ☀️ Icône soleil raster sur fond **bleu-gris** (R<160, B>150) | Vacances | VA |

### Autres Absences

| Code | Pictogramme PDF | Description | Normalisé |
|------|-----------------|-------------|-----------|
| **CMHN** | Cercle **jaune vif** (~222,207,52) | Compensation majoration nuit | CMHN |
| **HC** | Cercle traitillé **vert d'eau** ◎ | Heures compensées (soldes heures) | HC |
| **HS** | — | Heures rendues (soldes HS) | HS |
| **ML** | Fond **rouge foncé** (~138,0,0) | Maladie non prof. | → M |
| **M** | Fond **rouge foncé** (~112,6,6) | Maladie (alias) | M |
| **ANP** | Cercle **orange-rouge** (~191,81,44) avec icône drapeau/croix 🚩 | Accident non-professionnel | ANP |
| **AP** | Fond **rose/saumon** (~255,187,172) | Accident professionnel | AP |
| **FO9** | Cercle **violet/pourpre** (~160,120,200) avec `L` | Formation, cours (9h) | FO9 |
| **E** | Carré **olive/jaune-vert** (~64,90,1) avec `E` grand | École (ambulance) – formation école | E |
| **FIN** | — | Fin tour nuit CSU | FIN |
| **AMBCE** | Texte `AMBCE` | Ambulances CE | AMBCE |
| **SM** | — | Service Militaire (?) – vu en avril | SM |
| **MR** | — | Maternité/Repos (?) – vu en mai | MR |
| **QFO** | Texte `QFO` | Demande Formation (?) – vu en juin | QFO |

---

## 🟡 MARQUAGES (Overlays)

> Ce sont des superpositions au horaire de base, pas des horaires autonomes.

| Code | Pictogramme PDF | Description |
|------|-----------------|-------------|
| **PE** | Carré **magenta/fuchsia** (~129,0,127) 🟪 | C6 – Permutation effectuée |
| **RJ** | Cercle **rouge/orange** 🔴 | C6 – Responsable du jour |

---

## ⚠️ Encodage à Icônes Raster (Critique pour l'Extraction)

> **Découverte critique :** Les codes d'absence **C, C1, VA** n'apparaissent pas comme **texte** dans le PDF.
> Ils sont encodés comme des **icônes raster (images JPEG)** incorporées dans les bandes de fond de la grille.
> L'extraction via le parseur texte (`get_text()`) **ne les capture PAS**.

### Comment ça fonctionne

1. Chaque cellule de la grille fait partie d'une **grande image JPEG** (bande horizontale)
2. Les cellules de congé/vacances contiennent une **icône soleil ☀️** dessinée en pixels dans l'image
3. L'icône est identique pour C, C1 et VA — ce qui change est le **fond derrière le soleil**

### Discrimination par couleur de fond

| Type | Fond de l'icône | Signature chromatique des coins |
|------|-----------------|--------------------------------|
| **C** (Congé) | Jaune vif | R>230, G>230, B<60 |
| **C1** (Congé prio.) | Ambre/orange | Défaut (ni jaune, ni bleu) |
| **VA** (Vacances) | Bleu-gris | R<160, B>150 |

### Algorithme de Détection (3 priorités)

```
Pour chaque cellule (employé, jour) :

  Priorité 1 : TEXTE OVERLAY
    - Chercher le texte (get_text) dans la zone de la cellule
    - Comparer avec le jeu _TEXT_OVERLAY_SHIFT_CODES
    - Si correspondance → utiliser ce code, normaliser, TERMINÉ

  Priorité 2 : HISTOGRAMME ICÔNE SOLEIL  ← CRITIQUE pour C/C1/VA
    - Échantillonner TOUS les pixels dans la zone de la cellule (±8pt × ±5pt, pas 0.5pt)
    - Compter les pixels « chauds » (orange/jaune du soleil) :
        R > 180 ET G > 80 ET B < 120 ET (R - B) > 80
    - Si warm_ratio > 10% → icône soleil détectée
    - Échantillonner les coins de la cellule (loin du soleil) :
        • VA : R<160, B>150 (bleu-gris)
        • C :  R>230, G>230, B<60 (jaune vif)
        • C1 : défaut (ambre/orange)
    - TERMINÉ

  Priorité 3 : COULEUR PIXEL CENTRAL (repli pour cellules à fond plein)
    - Échantillonner le pixel (col_x, row_y) × ZOOM
    - Classifier par plus-proche-voisin euclidien vs KNOWN_COLORS
    - Si ambigu → échantillonner grille 5×5 et vote majoritaire
```

### Seuils validés (à partir des PDF réels)

- Cellules avec icône soleil : **>30% pixels chauds** (typiquement 80-90%)
- Cellules avec texte (ex. 6P1) : **<5% pixels chauds** (typiquement 0%)
- Cellules opérationnelles (AMJP, AMNP) : **<5% pixels chauds** (couleur uniforme)
- Seuil décisionnel : **>10%** warm_ratio

---

## 🔄 Normalisation des Codes

> La fonction `_normalize_shift_code()` mappe les variantes aux codes canoniques utilisés par le solver :

| Entrée | Sortie | Raison |
|--------|--------|--------|
| AMJ1, AMJ2 | AMJP | Même horaire jour |
| AMN1, AMN2 | AMNP | Même horaire nuit |
| AMHR | AMHS | Même type horaire spécial |
| RS5–RS10 | RS | Même fonction Rapid Spécial |
| C1 | QC1 | Congé prioritaire |
| ML | M | Maladie (alias court) |

---

## 📊 Catalogue Complet par type

### Horaires Opérationnels Ambulance (ceux qui comptent pour le solver)
```
AMJP  → Jour (1/2/P)    06:30–18:40
AMNP  → Nuit (1/2/P)    18:40–06:30
AMHS  → Horaire S/R     09:55–19:55 / 08:55–16:55
R     → Rapid            (variable)
RS    → Rapid Spécial    09:00–17:00 (base)
```

### Horaires Non-Opérationnels (administratifs/flexibles)
```
A1–A4 → Administratif   (horaires variables par mois)
6P1   → Flexible jour   07:00–11:00 + 13:30–17:30
6FM   → Flexible mat    08:00–12:00
```

### Absences (aucune couverture ambulance)
```
C, QC1, VA, CMHN, HC, HS, M, ANP, AP, FO9, E, FIN, AMBCE, SM, MR, QFO
```

### Marquages (superpositions)
```
PE → Permutation effectuée
RJ → Responsable du jour
```

---

## 🎨 Carte des Couleurs Pixel → Code

> Référence rapide des couleurs RGB utilisées dans `KNOWN_COLORS` pour la classification pixel.

| Code | Famille chromatique | RGB représentatifs |
|------|--------------------|--------------------|
| AMNP | Bleu foncé / near-black | (4,4,14) (51,54,99) (71,58,140) |
| AMJP | Cyan / turquoise / aqua | (0,82,68) (70,226,255) (88,157,232) |
| AMHS | Mauve / lilas / violet pâle | (186,156,211) (214,187,215) |
| R | Rouge vif | (252,33,0) |
| RS | Orange vif | (255,127,0) |
| 6P1 | Gris moyen | (155,155,155) (170,170,170) |
| 6FM | Gris clair | (195,195,195) (210,210,210) |
| A1 | Gris très foncé | (55,55,55) (70,70,70) |
| A2 | Gris foncé | (105,105,105) (120,120,120) |
| C | Vert | (0,128,0) (154,255,182) |
| QC1 | Orange-brun | (190,145,77) (225,116,83) |
| CMHN | Jaune vif | (222,207,52) (255,252,8) |
| VA | Jaune-orangé | (255,217,105) (255,177,40) |
| E | Olive / jaune-vert | (64,90,1) (152,157,57) |
| M | Rouge foncé | (112,6,6) (138,0,0) |
| ANP | Orange-rouge | (191,81,44) |
| AP | Rose / saumon | (255,187,172) |
| FO9 | Violet / pourpre | (160,120,200) |
| PE | Magenta / fuchsia | (129,0,127) |

> Seuil de distance euclidienne pour correspondance : **65** (`_COLOR_MATCH_THRESHOLD`)

---

*Mis à jour le 2026-03-14 – source : tous les PDF (déc25–jun26) + legende.pdf + captures d'écran utilisateur + analyse histogramme pixel + validation janvier2026.pdf (618 C1, 91 C détectés).*
