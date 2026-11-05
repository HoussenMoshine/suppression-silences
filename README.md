# suppression-silences

**Coupe automatiquement les silences de vos vidéos, sans désynchroniser le son.**

![Python](https://img.shields.io/badge/Python-3.8%2B-blue)
![ffmpeg](https://img.shields.io/badge/ffmpeg-4.4%2B-green)
![Licence](https://img.shields.io/badge/licence-MIT-yellow)

Un script en ligne de commande, léger et sans dépendance Python externe, qui
détecte les blancs d'une vidéo, les retire, puis ré-encode l'image et le son
ensemble. Le résultat est un fichier plus rythmé, prêt à publier ou à monter.

Il fonctionne avec n'importe quel format lu par ffmpeg (MP4, MKV, MOV, AVI,
TS…), quelle que soit la résolution, la fréquence d'image ou le codec.

---

## Sommaire

- [Démarrage rapide](#démarrage-rapide)
- [À qui ça sert](#à-qui-ça-sert)
- [Ce que fait le script](#ce-que-fait-le-script)
- [Comment ça marche](#comment-ça-marche)
- [Prérequis](#prérequis)
- [Installation](#installation)
- [Guide pas à pas pour débutants](#guide-pas-à-pas-pour-débutants)
- [Recettes prêtes à copier](#recettes-prêtes-à-copier)
- [Les options expliquées](#les-options-expliquées)
- [Le débruitage en détail](#le-débruitage-en-détail)
- [Fichiers produits et journal](#fichiers-produits-et-journal)
- [Sécurité : que devient l'original ?](#sécurité--que-devient-loriginal-)
- [Dépannage](#dépannage)
- [Performances et limites](#performances-et-limites)
- [Structure du dépôt](#structure-du-dépôt)
- [Licence](#licence)

---

## Démarrage rapide

Pour les utilisateurs de **Linux** qui ont déjà un terminal :

```bash
# 1. Installer les outils
sudo apt install ffmpeg sox python3

# 2. Récupérer le script
git clone https://github.com/HoussenMoshine/suppression-silences.git
cd suppression-silences
chmod +x suppression_silences.py

# 3. Voir ce qui serait coupé, sans rien modifier
./suppression_silences.py ma-video.mp4 --analyse

# 4. Traiter en gardant l'original
./suppression_silences.py ma-video.mp4 --conserver-original
```

Sous macOS ou Windows, adaptez l'installation : voir
[Installation](#installation). Si vous n'avez jamais utilisé de terminal, le
[guide pas à pas](#guide-pas-à-pas-pour-débutants) reprend chaque étape en
détail.

---

## À qui ça sert

- YouTubeurs qui veulent supprimer les hésitations et les temps morts.
- Créateurs de Shorts / Reels qui ont besoin d'un rythme serré.
- Formateurs, podcasteurs, intervieweurs qui enregistrent de longues sessions.
- Toute personne qui a une vidéo parlée et ne veut pas couper les silences à la
  main dans un logiciel de montage.

Le script ne fait aucune analyse en ligne : tout se passe sur votre machine.
Aucune vidéo n'est envoyée sur Internet.

---

## Ce que fait le script

1. Il mesure le **bruit de fond réel** de la vidéo (ventilateur, pièce, micro).
2. Il en déduit un **seuil de détection** adapté au fichier, au lieu d'une
   valeur fixe.
3. Il repère les passages sous ce seuil pendant assez longtemps pour être des
   silences.
4. Il **supprime ces passages** en gardant une petite marge autour de la voix
   pour ne pas couper les débuts et fins de mots.
5. Il ré-encode la vidéo en H.264 et l'audio dans un format compatible avec le
   conteneur de sortie, **image et son restant synchronisés**.
6. En option, il **débruite la voix** : traitement audio séparé avec `sox`, ou
   filtre intégré à l'encodage avec `anlmdn`. Dans les deux cas, la vidéo n'est
   réencodée qu'une seule fois.
7. Il vérifie la durée et la synchronisation du fichier produit avant de
   supprimer l'original.

Fonctionnalités notables :

- seuil automatique calculé sur le bruit de fond ;
- mode `--court` pour les formats verticaux et les vidéos rapides ;
- mode `--speech-aware` qui distingue les respirations des vraies pauses ;
- débruitage `sox` (soustraction spectrale, proche d'Audacity) ou `anlmdn` ;
- sortie **double** : un master MKV/PCM pour le montage + une copie MP4/AAC
  allégée ;
- traitement par lot de plusieurs fichiers ;
- corbeille locale et journal des suppressions.

---

## Comment ça marche

Le script s'appuie entièrement sur **ffmpeg** et **ffprobe**. Il n'utilise
aucune bibliothèque Python tierce.

```
ffprobe      → durée, pistes audio, durée de chaque flux
ffmpeg astats → niveau du bruit de fond (Noise floor)
ffmpeg ebur128 → niveau de parole (LUFS), pour calibrer le débruitage
ffmpeg silencedetect → début et fin de chaque silence
ffmpeg filter_complex → trim / atrim / concat : on ne garde que les segments parlés
ffmpeg libx264 + AAC/PCM → ré-encodage final
```

Concrètement :

1. **Mesure du plancher de bruit** avec un filtre passe-haut à 120 Hz, pour
   ignorer les bruits graves (ventilation, rumble).
2. **Seuil automatique** = plancher + 10 dB, plafonné à −10 dB. Exemple :
   plancher à −52 dB → seuil à −42 dB.
3. **Détection** des silences d'au moins 2,5 s par défaut (0,7 s en `--court`).
4. **Calcul des segments à garder** : chaque silence retenu est retiré, sauf
   une marge de 0,3 s de chaque côté (0,12 s en `--court`).
5. **Encodage** : les segments sont découpés avec `trim` (vidéo) et `atrim`
   (audio) puis recollés avec `concat`. Comme les deux flux subissent les mêmes
   bornes, ils restent synchronisés.
6. **Contrôles finaux** : durée réelle comparée à la durée attendue, écart
   éventuel entre les flux vidéo et audio.

> **À savoir** : le fichier de sortie ne contient qu'**une piste vidéo et une
> piste audio**. Les sous-titres et les autres pistes audio ne sont pas
> conservés. Le script ré-encode toujours la vidéo (il ne peut pas couper sans
> réencoder), donc utilisez un `--crf` bas si la qualité compte.

---

## Prérequis

| Outil | Obligatoire | Rôle |
|---|---|---|
| Python 3.8 ou plus | Oui | exécuter le script |
| ffmpeg | Oui | découper, mesurer, encoder |
| ffprobe | Oui | lire les informations du fichier (fourni avec ffmpeg) |
| sox | Non | meilleur débruitage ; sinon repli automatique sur `anlmdn` |

Vérifiez ce qui est déjà installé :

```bash
python3 --version
ffmpeg -version
ffprobe -version
sox --version        # facultatif
```

Si une commande affiche `command not found`, installez l'outil correspondant
dans la section [Installation](#installation).

---

## Installation

### Linux Mint / Ubuntu / Debian

```bash
sudo apt update
sudo apt install ffmpeg sox python3
```

Puis récupérez le projet :

```bash
git clone https://github.com/HoussenMoshine/suppression-silences.git
cd suppression-silences
chmod +x suppression_silences.py
```

Si vous préférez ne pas utiliser Git : sur la page GitHub, cliquez sur
**Code → Download ZIP**, décompressez l'archive, puis ouvrez un terminal dans
le dossier obtenu.

### macOS

```bash
brew install ffmpeg sox python3
git clone https://github.com/HoussenMoshine/suppression-silences.git
cd suppression-silences
chmod +x suppression_silences.py
```

### Windows

1. Installez **Python** depuis [python.org](https://www.python.org/downloads/)
   en cochant **« Add python.exe to PATH »** pendant l'installation.
2. Installez **ffmpeg** avec winget :

   ```powershell
   winget install --id Gyan.FFmpeg -e
   ```

3. `sox` est facultatif : sans lui, le script utilise automatiquement le
   débruitage `anlmdn` intégré à ffmpeg.
4. Ouvrez **PowerShell** dans le dossier du projet et lancez le script avec
   `python` (pas besoin de `chmod`) :

   ```powershell
   python suppression_silences.py --help
   ```

### Vérifier que tout fonctionne

```bash
./suppression_silences.py --help
```

Vous devez voir s'afficher l'aide avec toutes les options. Si c'est le cas,
l'installation est terminée.

---

## Guide pas à pas pour débutants

Ce guide part du principe que vous n'avez jamais utilisé de terminal. Les
commandes sont à copier-coller telles quelles, en remplaçant seulement le nom
de la vidéo.

### Étape 1 — ouvrir un terminal dans le bon dossier

Le terminal doit être « placé » dans le dossier qui contient le script.

- **Linux Mint** : ouvrez le dossier dans le gestionnaire de fichiers, faites
  un clic droit dans le vide, puis **« Ouvrir dans un terminal »**.
- **Windows** : ouvrez le dossier dans l'explorateur, cliquez dans la barre
  d'adresse, tapez `powershell` et appuyez sur Entrée.
- **macOS** : clic droit sur le dossier → **Services → Nouveau terminal au
  dossier**.

Vous pouvez aussi utiliser la commande `cd` :

```bash
cd ~/Téléchargements/suppression-silences
```

> **Astuce** : dans le terminal, la touche **Tab** complète automatiquement les
> noms de fichiers. Pour les chemins contenant des espaces, mettez le chemin
> entre guillemets : `"ma vidéo finale.mp4"`.

### Étape 2 — vérifier que le script répond

```bash
./suppression_silences.py --help
```

Sous Windows :

```powershell
python suppression_silences.py --help
```

### Étape 3 — analyser la vidéo sans rien modifier

C'est l'étape la plus importante. Elle n'encode rien et ne touche à aucun
fichier :

```bash
./suppression_silences.py ma-video.mp4 --analyse
```

Exemple de rapport :

```
Fichier : ma-video.mp4
  Bruit de fond : -52.3 dB -> seuil : -42.3dB
Silences détectés : 12  |  coupés : 9

  [COUPÉ  ] 00:00:03.20 -> 00:00:08.40  (5.2 s)
  [gardé  ] 00:00:12.10 -> 00:00:13.60  (1.5 s)  pause naturelle
  ...
Durée : 00:12:34.00 -> 00:09:12.00 (gagné : 00:03:22.00)
```

- `Bruit de fond` : le niveau sonore de la pièce, mesuré automatiquement.
- `seuil` : le niveau sous lequel un passage est considéré comme un silence.
- `[COUPÉ]` : le passage sera retiré.
- `[gardé]` : le passage reste, avec la raison entre parenthèses.
- La dernière ligne indique le temps gagné.

Si le résultat vous convient, passez à l'étape 4. Sinon, voyez
[« le résultat ne me convient pas »](#étape-6--le-résultat-ne-me-convient-pas).

### Étape 4 — premier vrai traitement, sans risque

On garde l'original pour ce premier essai :

```bash
./suppression_silences.py ma-video.mp4 --conserver-original
```

Le script affiche le même rapport, puis demande :

```
  [o] encoder | [a] ajuster | [n] passer :
```

Tapez **`o`** puis **Entrée** pour lancer l'encodage. Tapez **`a`** pour
modifier les réglages avant d'encoder. Tapez **`n`** pour ne rien faire.

Le fichier produit s'appelle :

```
ma-video-sans-silences.mp4
```

Il est créé **à côté de la vidéo d'origine**.

> Sans `--conserver-original`, le script supprime l'original après un encodage
> réussi. C'est le comportement par défaut, pensé pour les utilisateurs
> avertis. Pour un premier essai, gardez toujours l'original.

### Étape 5 — vérifier le résultat

1. Ouvrez `ma-video-sans-silences.mp4` dans votre lecteur habituel.
2. Vérifiez que le son et l'image sont bien synchronisés.
3. Écoutez le début et la fin : les mots ne doivent pas être coupés.
4. Regardez la durée : elle doit être plus courte que l'originale.

Si tout est bon, vous pouvez relancer sans `--conserver-original` (ou avec
`--corbeille` pour plus de sécurité).

### Étape 6 — le résultat ne me convient pas

| Symptôme | Réglage à essayer |
|---|---|
| Il reste trop de blancs | Baisser `--duree-min` : `--duree-min 1.5` |
| Des fins de mots sont coupées | Augmenter `--marge` : `--marge 0.5` |
| Les respirations disparaissent | Ajouter `--speech-aware` |
| Le script ne coupe presque rien | Vérifier le seuil avec `--analyse`, puis essayer `--seuil -35dB` |
| La voix est étouffée | Retirer `--debruitage` ou baisser `--debruitage-reduction 0.10` |
| Des clics apparaissent aux coupes | Augmenter `--marge` ; les jonctions brutales sont une limite connue |
| Le rendu est trop lent | Utiliser `--preset ultrafast` |

Pour changer les réglages avant l'encodage, répondez **`a`** au menu
interactif : le script vous demande le seuil, la durée minimale, la marge, la
fusion et la protection du début, puis recalcule l'analyse.

### Étape 7 — traiter plusieurs vidéos d'un coup

```bash
./suppression_silences.py *.mp4 --conserver-original -y
```

- `*.mp4` traite tous les MP4 du dossier.
- `-y` répond « oui » à toutes les questions, y compris l'écrasement d'un
  fichier existant.
- **Attention** : sans `--conserver-original`, `-y` supprime aussi les
  originaux. Pour un lot, préférez :

```bash
./suppression_silences.py *.mp4 --corbeille -y
```

Les originaux sont alors déplacés dans un dossier `.suppression-silences-corbeille`
au lieu d'être effacés.

---

## Recettes prêtes à copier

| Objectif | Commande |
|---|---|
| Analyser sans encoder | `./suppression_silences.py video.mp4 --analyse` |
| Coupe classique (YouTube, tuto) | `./suppression_silences.py video.mp4 --conserver-original` |
| Short / Reels / TikTok | `./suppression_silences.py video.mp4 --court --conserver-original` |
| Garder les respirations | `./suppression_silences.py video.mp4 --speech-aware --conserver-original` |
| Débruiter la voix | `./suppression_silences.py video.mp4 --debruitage --conserver-original` |
| Master montage + copie légère | `./suppression_silences.py video.mp4 --dual --conserver-original` |
| Forcer MKV + PCM | `./suppression_silences.py video.mp4 --mkv --pcm --conserver-original` |
| Choisir la 2ᵉ piste audio | `./suppression_silences.py video.mp4 --piste 1 --conserver-original` |
| Sortie avec un nom précis | `./suppression_silences.py video.mp4 resultat.mp4` |
| Protéger les 3 premières secondes | `./suppression_silences.py video.mp4 --debut 3 --conserver-original` |
| Traiter un dossier entier | `./suppression_silences.py *.mkv --corbeille -y` |
| Remplacer l'original par la version coupée | `./suppression_silences.py video.mp4` |

> `--dual` ne prend pas de nom de sortie explicite : les deux fichiers sont
> nommés automatiquement à partir de l'entrée.

---

## Les options expliquées

### Détection des silences

| Option | Défaut | Rôle |
|---|---|---|
| `--seuil -35dB` | auto | Force le seuil de détection. À utiliser si l'automatique se trompe. Valeur entre −90 et 0 dB. |
| `--offset-plancher 10` | `10` | Marge ajoutée au bruit de fond pour calculer le seuil auto. |
| `--highpass 120` | `120` | Filtre passe-haut utilisé pour l'analyse. Ignore les bruits graves. |
| `--d 0.5` | `0.5` (0.2 en `--court`) | Granularité de détection. Plus petit = analyse plus fine, plus lent. |
| `--denoise` | désactivé | Débruite **uniquement pour l'analyse**, afin de mieux repérer les silences. Ne change pas le son du fichier produit. |

### Coupe et rythme

| Option | Défaut | Rôle |
|---|---|---|
| `--duree-min 2.5` | `2.5` (0.7 en `--court`) | Durée minimale d'un silence pour être coupé. |
| `--marge 0.3` | `0.3` (0.12 en `--court`) | Silence conservé autour de la voix, de chaque côté. |
| `--court` | désactivé | Réglages serrés pour les vidéos courtes : 0,7 s min, 0,12 s de marge, détection plus fine. |
| `--speech-aware` | désactivé | Analyse fine des pauses : coupe les pauses de phrase et compte les respirations conservées. Alias : `--respirations`. |
| `--fusion 0` | `0` | Fusionne des silences séparés par un court bout de parole. **Déconseillé** : peut supprimer la parole intermédiaire. |
| `--debut 0` | `0` | Protège les N premières secondes (utile si le début contient une prise de bruit). |
| `--protection 0` | `0` | Seuil secondaire pour affiner les bornes de coupe. Expérimental ; peut réduire le nombre de coupes. |

### Audio

| Option | Défaut | Rôle |
|---|---|---|
| `--debruitage` | désactivé | Débruite la voix lors du traitement. À utiliser **à la place** du débruitage de Kdenlive, jamais en plus. |
| `--debruitage-moteur auto` | `auto` | `sox` (meilleure qualité, si disponible et si un silence initial est exploitable) ou `anlmdn`. |
| `--debruitage-reduction 0.18` | `0.18` | Montant de réduction sox. Plus bas = plus naturel. Au-delà de 0,30, des artefacts apparaissent. |
| `--debruitage-duree-profil 2.0` | `2.0` | Secondes de silence initial servant d'empreinte au bruit. |
| `--debruitage-sans-eq` | désactivé | Désactive le léger shelf +2,5 dB à 3,5 kHz appliqué par le moteur sox. |
| `--debruitage-force 2` | `2` | Force d'`anlmdn` (sature au-delà de 3). |
| `--debruitage-sans-porte` | désactivé | Désactive la porte de bruit d'`anlmdn` : plus naturel, mais moins net. |
| `--piste 0` | `0` | Piste audio à utiliser (la première est 0). |
| `--audio-codec aac` | `aac` | Codec audio : `aac`, `pcm_s16le`, `pcm_s24le`, `flac`, `alac`, `libopus`, `vorbis`. |
| `--pcm` | — | Raccourci pour `--audio-codec pcm_s16le` (non compressé). |
| `--audio-bitrate 192k` | `192k` | Débit audio. Ignoré pour les codecs sans perte. |

### Sortie et qualité vidéo

| Option | Défaut | Rôle |
|---|---|---|
| `--mkv` | MP4 | Force la sortie MKV. |
| `--dual` | désactivé | Produit un master MKV/PCM (`Kdenlive`, `Audacity`) **et** une copie MP4/AAC 48 kHz stéréo (`FilmCraft`, `Premiere`). |
| `--preset veryfast` | `veryfast` | Preset x264 : `ultrafast` (rapide, plus gros), `veryfast`, `medium` (plus lent, plus petit). |
| `--crf 20` | `20` | Qualité vidéo. Plus bas = meilleure qualité et fichier plus gros (18 = haute qualité, 23 = correct). |

### Sécurité et confort

| Option | Défaut | Rôle |
|---|---|---|
| `--analyse` | désactivé | Affiche le résultat sans encoder. |
| `--conserver-original` | — | Ne supprime pas l'original après succès. **Recommandé pour débuter.** |
| `--supprimer-original` | actif | Supprime l'original après succès (comportement par défaut). |
| `--corbeille` | désactivé | Déplace l'original dans `.suppression-silences-corbeille` au lieu de le supprimer. |
| `-y`, `--oui` | désactivé | Répond « oui » partout : écrasement et suppression. Mode traitement par lot. |

---

## Le débruitage en détail

Le script propose deux choses différentes, souvent confondues :

- **`--denoise`** : agit seulement pendant l'**analyse**. Il aide à détecter
  les silences dans un enregistrement bruité, mais le son du fichier produit
  n'est pas modifié.
- **`--debruitage`** : agit sur le **son final**. Selon le moteur, `sox` retire
  le bruit avant l'encodage, ou `anlmdn` le fait pendant l'encodage. Dans les
  deux cas, la vidéo n'est réencodée qu'une seule fois.

### Moteur `sox` (par défaut si disponible)

Le script cherche un vrai silence au début du fichier (au moins 0,4 s dans les
5 premières secondes). Il en extrait une empreinte du bruit, puis applique une
**soustraction spectrale** — le même principe que la réduction de bruit
d'Audacity. Un garde-fou de 10 dB refuse de prendre de la voix pour un modèle
de bruit.

Réglages par défaut :

- profil de bruit : 2 s de silence initial ;
- réduction : `0.18` ;
- léger shelf +2,5 dB au-dessus de 3,5 kHz pour compenser la perte de brillance.

Si `sox` est absent, ou si le début n'est pas un vrai silence, le script se
replie automatiquement sur `anlmdn`.

### Moteur `anlmdn`

Chaîne appliquée : coupe-bas à 90 Hz + `anlmdn` + porte de bruit calibrée sur
le niveau de parole mesuré en LUFS (EBU R128). La porte est un expandeur doux
(ratio 2), pas un interrupteur : elle ne peut pas avaler un mot.

### Bonnes pratiques

- Utilisez `--debruitage` **à la place** du débruiteur de Kdenlive, jamais en
  plus : deux passes successives étouffent la voix.
- Commencez avec les réglages par défaut.
- Si la voix paraît étouffée : baissez `--debruitage-reduction` à `0.10`, ou
  essayez `--debruitage-sans-porte`.
- Si des artefacts apparaissent : ne montez pas au-delà de `0.30`.

---

## Fichiers produits et journal

### Noms par défaut

| Cas | Fichier(s) produit(s) |
|---|---|
| Sortie simple | `ma-video-sans-silences.mp4` |
| Avec `--mkv` | `ma-video-sans-silences.mkv` |
| Avec une sortie explicite | le nom demandé, par exemple `resultat.mp4` |
| Avec `--dual` | `ma-video-sans-silences.mkv` + `ma-video-sans-silences.mp4` |

Les fichiers sont créés dans le même dossier que la vidéo d'origine, sauf si
vous indiquez un autre chemin.

### Contrôles automatiques

Après l'encodage, le script vérifie :

- que la durée réelle correspond à la durée attendue (tolérance de 1 s) ;
- qu'il n'y a pas plus de 0,15 s d'écart entre les flux vidéo et audio ;
- que le fichier de sortie existe et n'est pas vide avant toute suppression.

En cas de durée incohérente ou de fichier de sortie invalide, l'original n'est
pas supprimé. Un simple avertissement de synchronisation ou de taille ne
bloque pas la suppression : vérifiez toujours le résultat à l'œil et à
l'oreille.

### Journal

Les suppressions et déplacements sont enregistrés dans :

```
~/.suppression-silences.log
```

### Corbeille locale

Avec `--corbeille`, l'original est déplacé dans un sous-dossier du dossier de
la vidéo :

```
.suppression-silences-corbeille/
```

Vous pouvez le restaurer ou le supprimer manuellement quand vous êtes sûr du
résultat.

---

## Sécurité : que devient l'original ?

> **Important** : par défaut, le script **supprime la vidéo d'origine** après
> un encodage réussi. C'est volontaire : cela évite d'accumuler des fichiers
> volumineux. Mais c'est irréversible.

Pour éviter toute mauvaise surprise :

```bash
# Premier essai : on garde tout
./suppression_silences.py video.mp4 --conserver-original

# Variante plus sûre : l'original va dans une corbeille locale
./suppression_silences.py video.mp4 --corbeille

# Traitement par lot sans risque
./suppression_silences.py *.mp4 --corbeille -y

# Comportement par défaut, à réserver aux fichiers dont vous avez une copie
./suppression_silences.py video.mp4
```

L'original n'est supprimé qu'après un encodage réussi et une vérification de
durée. Si le fichier de sortie est invalide, l'original est conservé.

---

## Dépannage

### `ffmpeg introuvable` ou `ffprobe introuvable`

Installez ffmpeg :

```bash
sudo apt install ffmpeg        # Linux Mint / Ubuntu / Debian
brew install ffmpeg            # macOS
winget install --id Gyan.FFmpeg -e   # Windows
```

### `sox introuvable`

Ce n'est pas bloquant : le script utilise automatiquement `anlmdn`. Pour
installer sox :

```bash
sudo apt install sox           # Linux
brew install sox               # macOS
```

### « Rien à couper avec ces réglages »

- Le fichier est peut-être déjà propre.
- Lancez `--analyse` et vérifiez le seuil affiché.
- Baissez `--duree-min` (par exemple `1.2`).
- Si le seuil est trop bas, forcez-le : `--seuil -35dB`.
- Dans le menu interactif, tapez `a` pour ajuster sans relancer.

### Le script coupe trop de choses / mange des fins de mots

- Augmentez la marge : `--marge 0.5`.
- Augmentez la durée minimale : `--duree-min 3`.
- N'utilisez pas `--fusion`, qui peut englober de la parole.
- Vérifiez que `--seuil` n'est pas trop haut (par exemple `-20dB`).

### Les respirations sont coupées

Ajoutez `--speech-aware` : le script conserve les pauses courtes et ne coupe
que les pauses de phrase d'au moins `--duree-min`.

### La voix paraît étouffée après débruitage

- Ne cumulez pas avec le débruitage de Kdenlive.
- Baissez `--debruitage-reduction` à `0.10`.
- Essayez `--debruitage-sans-porte`.
- Ou retirez complètement `--debruitage` : le script coupe déjà les silences
  sans toucher au timbre.

### Des clics ou des grésillements aux jonctions

Les coupes sont des collages bruts ; le script n'applique pas encore de fondu
enchaîné (`--anti-clic` n'est pas implémenté). Augmentez `--marge` pour laisser
un peu d'air autour de la voix. Pour un montage critique, utilisez le master
`--dual` et faites les jonctions dans un logiciel de montage.

### Le fichier FilmCraft / Premiere grésille

Le PCM dans MKV est excellent pour Kdenlive et Audacity, mais peut poser
problème ailleurs. Utilisez :

```bash
./suppression_silences.py video.mp4 --dual --conserver-original
```

et importez la copie `.mp4` (AAC 48 kHz stéréo).

### L'encodage est lent

Le script encode en H.264 sur le CPU, sans accélération matérielle. Sur une
machine ancienne :

- gardez `--preset veryfast` (défaut) ou essayez `--preset ultrafast` ;
- montez `--crf` à `23` pour un fichier plus léger et un encodage plus rapide ;
- traitez les fichiers un par un plutôt qu'un gros lot.

### Le terminal affiche « Terminal non interactif : utilise le mode -y »

Le script est lancé sans terminal interactif (par exemple depuis un script ou
un planificateur). Ajoutez `-y` pour répondre automatiquement.

### Un chemin avec des espaces ou des accents ne fonctionne pas

Mettez le chemin entre guillemets :

```bash
./suppression_silences.py "Mes vidéos/ma vidéo finale.mp4" --analyse
```

Le script accepte aussi le glisser-déposer : si vous ne passez aucun fichier,
il vous demande le chemin.

### Le format WebM est refusé

Le script encode en H.264, incompatible avec WebM. Choisissez `.mp4`, `.mkv`
ou `.mov` en sortie.

### Mes sous-titres ou mes autres pistes audio ont disparu

C'est une limite actuelle : la sortie ne contient qu'une piste vidéo et une
piste audio. Exportez les sous-titres séparément et refusionnez-les avec
ffmpeg ou votre logiciel de montage si nécessaire. Si le fichier d'origine
contenait des chapitres, leurs repères temporels ne sont pas recalculés après
les coupes.

---

## Performances et limites

- **CPU uniquement** : libx264, pas de NVENC/QSV. Le temps d'encodage dépend
  de la durée, de la résolution et du preset.
- **Réencodage obligatoire** : couper un fichier sans réencoder n'est pas
  possible avec ce fonctionnement ; il y a donc une légère perte de qualité
  vidéo, contrôlable avec `--crf`.
- **Espace disque** : prévoyez environ la taille de l'entrée plus celle de la
  sortie. En mode `--dual`, deux fichiers sont produits.
- **Une seule piste audio** est conservée, celle choisie avec `--piste`.
- **Pas de normalisation de volume**, pas de recadrage vertical, pas de
  suppression automatique des hésitations (« euh »).
- **Pas de fondu aux jonctions** : des clics restent possibles sur des coupes
  très serrées.
- Testé avec ffmpeg 4.4.2 et Python 3.10 ; ffmpeg 4.4 ou plus récent est
  recommandé.

---

## Structure du dépôt

```
suppression-silences/
├── suppression_silences.py   # le script (aucune dépendance Python)
├── README.md                 # ce guide
├── LICENSE                   # licence MIT
├── .gitattributes            # fins de ligne normalisées (LF)
└── .gitignore                # fichiers générés ignorés
```

---

## Licence

Distribué sous licence **MIT**. Vous pouvez l'utiliser, le modifier et le
redistribuer, y compris commercialement, à condition de conserver la mention
de copyright. Voir le fichier [LICENSE](LICENSE).

---

## Auteur

**Houssen Moshine** — [github.com/HoussenMoshine](https://github.com/HoussenMoshine)

Version du script : **10.4**
