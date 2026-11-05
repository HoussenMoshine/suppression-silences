#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
suppression-silences (v10.4)
Coupe les silences d'une vidéo (vidéo + audio ensemble, donc synchronisés).
Agnostique : format, dimensions, fps, codec d'entrée.

Nouveauté v10.4 (qualité audio) :
- --debruitage utilise désormais sox par défaut : soustraction spectrale à
  partir d'une empreinte du silence de début, exactement l'algorithme
  d'Audacity. Mesuré sur une vraie vidéo (fenêtre ouverte, bruit présent) :
  bruit -40,0 -> -53,1 dB, parole préservée (-22,9), grave 20-100 Hz conservé
  (9,9 contre 10,1 à l'original, là où un coupe-bas le faisait tomber à 2,0),
  et modulation de la voix 1,55 dB contre 3,09 avec l'ancienne chaîne.
  Un léger shelf à 3,5 kHz (+2,5 dB) rapproche la brillance de la référence
  Audacity sans relever le plancher de bruit.
  Repli automatique sur anlmdn si sox est absent ou si le début du fichier
  n'est pas un vrai silence (garde-fou 10 dB : on refuse de prendre la voix
  comme modèle de bruit).
  À utiliser À LA PLACE du débruiteur de Kdenlive, jamais en plus : deux
  passes de débruitage successives étouffent la voix.
  --debruitage-moteur auto|sox|anlmdn  (défaut auto)
  --debruitage-reduction 0.18        montant sox (plus bas = plus naturel)
  --debruitage-duree-profil 2.0      secondes de silence utilisées
  --debruitage-sans-eq               désactive le shelf à 3,5 kHz
  --debruitage-force N               force d'anlmdn (défaut 2)
  --debruitage-sans-porte            anlmdn : garde le plancher tel quel

Nouveauté v10.3 :
- --debruitage : débruitage de la voix pendant l'encodage, sans aucune passe
  supplémentaire (la chaîne s'insère dans l'audio déjà réencodé). Chaîne :
  coupe-bas 90 Hz + anlmdn + porte de bruit calibrée sur le niveau de parole
  mesuré (EBU R128), donc jamais une valeur en dur.
  Mesures sur un enregistrement réel (ffmpeg 4.4.2) : plancher de bruit
  -44,4 -> -83,6 dB, parole inchangée (-22,5 dB), et seulement -1,9 dB sur
  les passages parlés les plus faibles.
  À utiliser À LA PLACE du débruiteur de Kdenlive, jamais en plus : deux
  passes de débruitage successives étouffent la voix.
  --debruitage-force N   : force d'anlmdn (défaut 2 ; sature au-delà de 3)
  --debruitage-sans-porte: garde le plancher tel quel (plus naturel, moins net)

Nouveauté v10.2 :
- --speech-aware (alias --respirations) : analyse fine des pauses (plancher 0.12 s)
  pour distinguer les respirations courtes des pauses de phrase. Seules les
  pauses >= --duree-min sont coupées ; les respirations sont conservées et
  comptées dans l'analyse. Comportement de coupe identique par défaut : le
  flag ajoute la mesure et le rapport, il ne modifie pas les points de coupe
  tant que --duree-min reste le seuil de coupe.

Correctif v10.1 (efficacité) :
- Retour aux réglages de détection de la v9 qui coupaient réellement :
  seuil auto = plancher + 10 dB, highpass 120 Hz, marge 0.3 s (0.12 s en --court).
- La protection par second seuil (v10) est DÉSACTIVÉE par défaut (--protection 0).
  Près du bruit de fond, ce second seuil ne trouvait presque aucune borne et
  bloquait toutes les coupes. Elle reste disponible en option, avec repli sur
  le seuil principal si aucune borne basse fiable n'est trouvée.
- L'affichage montre à nouveau les silences réellement détectés (comme la v9)
  au lieu des bornes après application de la marge.

Nouveautés v10 :
- Seuil --seuil validé et normalisé en dB (ex. -30 accepté comme -30dB).

Nouveautés v9 :
- --dual : master MKV/PCM (Kdenlive + WAV/Audacity) + copie MP4/AAC 48 kHz
  stéréo (FilmCraft/Premiere), fabriquée depuis le master sans réencoder la vidéo.
- Fusion des silences désactivée par défaut (elle coupait la parole entre
  deux silences proches). Réactivable avec --fusion.
- Compatibilité conteneur/codec vérifiée avant encodage (PCM en MP4 = erreur).
- --anti-clic non inclus : à étudier après avoir localisé les grésillements.
- Codes retour ffmpeg vérifiés lors de l'analyse.

v8 : sortie MP4 par défaut, --mkv pour forcer MKV.
v7 : audio AAC par défaut, --pcm pour forcer PCM.
v6 : suppression / corbeille locale de l'original après succès.

Utilisation :
    suppression_silences.py video.mkv --dual --conserver-original
    suppression_silences.py video.mkv --analyse
    suppression_silences.py video.mkv --dual --corbeille
    suppression_silences.py video.mkv sortie.mp4
    suppression_silences.py video.mkv --mkv --pcm
    suppression_silences.py *.mkv -y

Python 3.8+ / ffmpeg + ffprobe.
"""

import argparse
import atexit
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SEP = "-" * 62

RESPIRATION_MAX = 1.0  # au-delà, un gap court est une pause, pas une respiration

SANS_BITRATE = {"pcm_s16le", "pcm_s24le", "flac", "alac"}
CODECS_ADMIS = SANS_BITRATE | {"aac", "libopus", "vorbis"}

# Le script encode toujours la vidéo en H.264 (libx264).
AUDIO_PAR_CONTENEUR = {
    ".mp4": {"aac", "alac"},
    ".m4v": {"aac", "alac"},
    ".mov": {"aac", "alac", "pcm_s16le", "pcm_s24le"},
    ".mkv": {"aac", "flac", "pcm_s16le", "pcm_s24le", "libopus", "vorbis"},
    ".avi": {"aac", "pcm_s16le", "mp3"},
    ".ts":  {"aac", "mp2", "ac3"},
}


def erreur(msg):
    sys.exit(f"ERREUR : {msg}")


def nettoyer_chemin(s):
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
        s = s[1:-1]
    # Glisser-déposer dans un terminal échappe les espaces avec un backslash.
    s = s.replace("\\ ", " ")
    return Path(s).expanduser()


def verifier_outils():
    for outil in ("ffmpeg", "ffprobe"):
        if not shutil.which(outil):
            erreur(f"{outil} introuvable. Installe-le : sudo apt install ffmpeg")


def lancer(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                          stdin=subprocess.DEVNULL)


def lancer_ou_erreur(cmd, contexte):
    res = subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                         stdin=subprocess.DEVNULL)
    if res.returncode != 0:
        fin = "\n".join((res.stderr or "").strip().splitlines()[-8:])
        erreur(f"{contexte} : ffmpeg a échoué (code {res.returncode}).\n{fin}")
    return res


def fmt(t):
    m, s = divmod(t, 60)
    h, m = divmod(m, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:05.2f}"


def fmt_duree(t):
    m, s = divmod(int(round(t)), 60)
    return f"{m} min {s:02d} s"


def duree_fichier(p):
    out = lancer(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                  "-of", "json", str(p)])
    try:
        return float(json.loads(out.stdout)["format"]["duration"])
    except (ValueError, KeyError):
        erreur(f"impossible de lire la durée de {p.name}")


def duree_stream(p, select):
    """Durée d'un flux, ou None si le conteneur ne la fournit pas."""
    out = lancer(["ffprobe", "-v", "error", "-select_streams", select,
                  "-show_entries", "stream=duration", "-of", "csv=p=0", str(p)])
    try:
        return float(out.stdout.strip().splitlines()[0])
    except (ValueError, IndexError):
        return None


def nb_pistes_audio(p):
    out = lancer(["ffprobe", "-v", "error", "-select_streams", "a",
                  "-show_entries", "stream=index", "-of", "csv=p=0", str(p)])
    return len(out.stdout.strip().splitlines())


def plancher_de_bruit(p, highpass, piste):
    out = lancer_ou_erreur(
        ["ffmpeg", "-hide_banner", "-i", str(p), "-map", f"0:a:{piste}",
         "-af", f"highpass=f={highpass},astats", "-f", "null", "-"],
        "Mesure du bruit de fond")
    vals = [float(m) for m in re.findall(
        r"Noise floor(?: dB)?:\s*(-?\d+(?:\.\d+)?)", out.stderr)]
    vals = [v for v in vals if math.isfinite(v)]
    return max(vals) if vals else None


def niveau_parole(p, piste):
    """Volume intégré EBU R128 du fichier, en LUFS.

    EBU R128 applique un portillon qui écarte les silences : la valeur obtenue
    décrit la parole, pas le silence qui l'entoure. C'est ce qui permet de
    caler la porte de bruit sur le fichier réel au lieu d'une constante.
    """
    out = lancer_ou_erreur(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(p),
         "-map", f"0:a:{piste}", "-af", "ebur128=peak=none",
         "-f", "null", "-"],
        "Mesure du niveau de parole")
    vals = []
    for m in re.findall(r"I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", out.stderr):
        try:
            v = float(m)
        except ValueError:
            continue
        if math.isfinite(v):
            vals.append(v)
    return vals[-1] if vals else None


def chaine_debruitage(p, piste, force=2.0, avec_porte=True):
    """Chaîne de débruitage de la voix, calibrée sur le fichier à traiter.

    Justification des réglages, tous mesurés sur ffmpeg 4.4.2 :
      - coupe-bas 90 Hz : le bruit de pièce est massivement dans le grave ;
      - anlmdn s=2 : sature au-delà (s=3 -> +0,5 dB, s=10 -> +0,7 dB), donc
        monter la force ne retire rien de plus ;
      - porte à 21,5 dB sous le niveau de parole : c'est elle qui rend la voix
        nette. Sans elle, le plancher résiduel masque les fins de syllabes et
        la voix paraît "étouffée" alors qu'elle a, en réalité, plus d'aigus.
        Mesuré : -1,9 dB seulement sur les passages parlés les plus faibles.

    La porte est un expandeur doux (ratio 2), pas un interrupteur : elle ne
    peut pas avaler un mot, seulement baisser ce qui est déjà presque inaudible.
    L'attaque est courte (5 ms) pour ne pas rogner les consonnes.

    Coordonnées : le seuil est absolu (dBFS), donc il DOIT suivre le niveau du
    fichier. On le déduit du volume intégré, jamais d'une constante.
    """
    lufs = niveau_parole(p, piste)
    if lufs is None:
        lufs = -24.0
        print("  ℹ Niveau de parole non mesurable : calibration par défaut "
              "(-24 LUFS).")

    if avec_porte:
        # 21,5 dB sous le volume intégré : c'est le décalage mesuré sur un
        # enregistrement réel entre -20,5 LUFS et le seuil retenu à l'écoute.
        # Le garde-fou est large : il ne sert qu'à écarter une mesure aberrante.
        # Un plafond plus serré (ex. -55) rendrait la porte PLUS agressive sur
        # les enregistrements faibles, alors que le rapport parole/bruit, lui,
        # ne change pas : la calibration doit rester purement relative.
        seuil_db = max(-80.0, min(-20.0, lufs - 21.5))
        seuil = 10 ** (seuil_db / 20.0)
        porte = f",agate=threshold={seuil:.5f}:ratio=2:attack=5:release=300"
        print(f"  Débruitage : parole {lufs:.1f} LUFS -> porte de bruit "
              f"à {seuil_db:.1f} dBFS")
    else:
        porte = ""
        print("  Débruitage : sans porte de bruit (plancher laissé tel quel)")

    return f"highpass=f=90,anlmdn=s={force:g}{porte}"


def zone_silence_initiale(p, piste, lufs, duree_profil):
    """Zone de silence au début du fichier, ou None.

    On cherche un vrai silence (>= 0,4 s) dans les 5 premières secondes. Si
    silencedetect n'en trouve pas — ce qui arrive quand le silence contient
    des clics (souris, chaise) qui dépassent le seuil par instants — on tente
    le tout début. Dans tous les cas un garde-fou RMS vérifie que la zone est
    au moins 10 dB sous la parole : si c'est de la voix, on refuse plutôt que
    de bâtir un profil de bruit avec la voix.
    """
    seuil = lufs - 18.0
    out = lancer(["ffmpeg", "-hide_banner", "-nostats",
                  "-i", str(p), "-map", f"0:a:{piste}", "-af",
                  f"highpass=f=120,silencedetect=noise={seuil:.1f}dB:d=0.3",
                  "-f", "null", "-"])
    debuts, silences = [], []
    for ligne in out.stderr.splitlines():
        m = re.search(r"silence_start:\s*([0-9.]+)", ligne)
        if m:
            debuts.append(float(m.group(1)))
            continue
        m = re.search(r"silence_end:\s*([0-9.]+)", ligne)
        if m and debuts:
            silences.append((debuts.pop(), float(m.group(1))))
    silences = [(s, e) for s, e in silences if s < 5.0 and e - s >= 0.4]
    if silences:
        debut, fin = max(silences, key=lambda z: z[1] - z[0])
    else:
        debut, fin = 0.0, duree_profil
    fin = min(fin, debut + duree_profil)
    if fin - debut < 0.4:
        return None

    res = lancer(["ffmpeg", "-hide_banner", "-nostats",
                  "-ss", f"{debut:.3f}", "-t", f"{fin - debut:.3f}",
                  "-i", str(p), "-map", f"0:a:{piste}",
                  "-af", "astats", "-f", "null", "-"])
    vals = [float(v) for v in re.findall(
        r"RMS level dB:\s*(-?[0-9.]+)", res.stderr)]
    if not vals:
        return None
    if lufs - vals[-1] < 10.0:
        print(f"  ℹ Le début n'est pas un silence ({lufs - vals[-1]:.1f} dB "
              f"sous la parole) : profil refusé, on garde anlmdn.")
        return None
    return debut, fin


def preparer_audio_sox(entree, piste, dossier, reduction, duree_profil,
                       avec_eq):
    """Débruitage sox (soustraction spectrale, algorithme d'Audacity).

    Retourne (chemin_wav, description) ou (None, raison). Le WAV produit fait
    exactement la durée de la piste audio source, pour que les decoupages
    atrim/concat restent alignés à l'image.
    """
    sox = shutil.which("sox")
    if not sox:
        return None, "sox introuvable"
    lufs = niveau_parole(entree, piste)
    if lufs is None:
        return None, "niveau de parole non mesurable"
    zone = zone_silence_initiale(entree, piste, lufs, duree_profil)
    if zone is None:
        return None, "aucun silence initial exploitable"
    debut, fin = zone

    complet = dossier / "complet.wav"
    silence = dossier / "silence.wav"
    propre = dossier / "propre.wav"
    final = dossier / "propre_final.wav"

    lancer_ou_erreur(
        ["ffmpeg", "-y", "-hide_banner", "-v", "error", "-i", str(entree),
         "-map", f"0:a:{piste}", "-c:a", "pcm_s16le", str(complet)],
        "Extraction audio pour le débruitage")
    lancer_ou_erreur(
        ["ffmpeg", "-y", "-hide_banner", "-v", "error",
         "-ss", f"{debut:.3f}", "-t", f"{fin - debut:.3f}",
         "-i", str(entree), "-map", f"0:a:{piste}",
         "-c:a", "pcm_s16le", str(silence)],
        "Extraction du silence de début")

    profil = dossier / "bruit.prof"
    res = lancer([sox, str(silence), "-n", "noiseprof", str(profil)])
    if res.returncode != 0 or not profil.exists():
        return None, "sox noiseprof a échoué"
    res = lancer([sox, str(complet), str(propre), "noisered", str(profil),
                  f"{reduction:.2f}"])
    if res.returncode != 0:
        return None, "sox noisered a échoué"

    duree = duree_stream(entree, f"a:{piste}") or duree_fichier(entree)
    chaine = []
    if avec_eq:
        # +2,5 dB au-dessus de 3,5 kHz : rapproche la brillance de la
        # référence Audacity (mesuré -19,7 contre -17,8 en aigus relatifs)
        # sans relever le plancher de bruit (-53,1 dB avant comme après).
        chaine.append("highshelf=f=3500:g=2.5:width_type=o:width=2")
    # apad + -t : sox peut rendre quelques millisecondes de moins ; on recale
    # sur la durée exacte de la source pour ne pas dériver à la fin.
    chaine.append("apad")
    lancer_ou_erreur(
        ["ffmpeg", "-y", "-hide_banner", "-v", "error", "-i", str(propre),
         "-af", ",".join(chaine), "-t", f"{duree:.3f}",
         "-c:a", "pcm_s16le", str(final)],
        "Finalisation de l'audio débruité")

    # Sur une longue vidéo, chaque WAV pèse lourd ; on ne garde que le final.
    for inter in (complet, silence, propre, profil):
        try:
            inter.unlink(missing_ok=True)
        except OSError:
            pass

    desc = (f"profil {debut:.2f}-{fin:.2f} s, réduction {reduction:.2f}"
            + (" + shelf 3,5 kHz" if avec_eq else ""))
    return final, desc


def detecter_silences(p, seuil, highpass, denoise, piste, d):
    chaine = f"highpass=f={highpass}"
    if denoise:
        chaine += ",afftdn"
    chaine += f",silencedetect=noise={seuil}:d={d}"
    out = lancer_ou_erreur(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(p),
         "-map", f"0:a:{piste}", "-af", chaine, "-f", "null", "-"],
        "Détection des silences")
    debuts, silences = [], []
    for ligne in out.stderr.splitlines():
        m = re.search(r"silence_start:\s*([0-9.]+)", ligne)
        if m:
            debuts.append(float(m.group(1)))
            continue
        m = re.search(r"silence_end:\s*([0-9.]+)", ligne)
        if m and debuts:
            silences.append((debuts.pop(), float(m.group(1))))
    if debuts:
        silences.append((debuts[0], duree_fichier(p)))
    return sorted(silences)


def classifier_pauses_respirations(silences, duree_min):
    """Sépare des silences détectés finement en deux catégories.

    - pauses : durée >= duree_min, ce sont des pauses de phrase -> à couper ;
    - respirations : durée < duree_min, à conserver pour garder un débit naturel.

    Un silence qui court jusqu'à la fin du fichier (borne infinie) est une pause.
    """
    pauses, respirations = [], []
    for s, e in silences:
        longueur = (e - s) if e != float("inf") else float("inf")
        if longueur >= duree_min:
            pauses.append((s, e))
        else:
            respirations.append((s, e))
    return pauses, respirations


def fusionner_silences(silences, fusion):
    """
    ATTENTION : le gap entre deux silences est de la PAROLE.
    fusion > 0 fusionne donc deux silences en englobant la parole intermédiaire,
    qui sera supprimée. Défaut volontaire : 0.0 (ne fusionne que l'exact contigu).
    """
    if not silences:
        return []
    out = [list(silences[0])]
    for s, e in silences[1:]:
        if s - out[-1][1] <= fusion:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def seuil_inferieur(seuil, plancher, protection):
    """Seuil plus bas servant de borne de coupe, ou None si impossible."""
    if protection <= 0:
        return None
    try:
        haut = float(str(seuil).lower().replace("db", "").strip())
    except ValueError:
        return None
    bas = haut - protection
    if plancher is not None and math.isfinite(plancher) and plancher >= -90:
        # Ne jamais descendre dans le bruit de fond : le silence n'y serait
        # plus détecté et la protection deviendrait inefficace.
        bas = max(bas, plancher + 2.0)
    if bas >= haut - 1.0:
        return None
    return f"{bas:.1f}dB"


def coupes_sures(hauts, basses, duree_min):
    """
    Retient les pauses assez longues. Si un seuil bas est fourni, on affine
    les bornes avec les silences détectés à ce seuil, mais on ne bloque
    JAMAIS une coupe : sans borne basse fiable, on coupe au seuil principal
    (comportement v9, efficace).
    """
    valides = [(s, e) for s, e in hauts if e - s >= duree_min]
    if basses is None:
        return valides

    coupes = []
    for s, e in valides:
        sous = []
        for bs, be in basses:
            cs, ce = max(s, bs), min(e, be)
            if ce - cs >= 0.05:
                sous.append((cs, ce))
        if not sous:
            coupes.append((s, e))
            continue
        sous.sort()
        fusion = [list(sous[0])]
        for a, b in sous[1:]:
            if a <= fusion[-1][1] + 0.01:
                fusion[-1][1] = max(fusion[-1][1], b)
            else:
                fusion.append([a, b])
        coupes.extend((a, b) for a, b in fusion)
    return sorted(coupes)


def segments_a_garder(duree, silences, duree_min, marge, debut=0.0):
    keeps, pos = [], 0.0
    for s, e in silences:
        if e - s < duree_min:
            continue
        rs, re_ = max(s + marge, debut), e - marge
        if re_ <= rs:
            continue
        if rs > pos:
            keeps.append((pos, rs))
        pos = max(pos, re_)
    if pos < duree:
        keeps.append((pos, duree))
    return [(s, e) for s, e in keeps if e - s > 0.05]


def afficher_analyse(coupes, silences, duree_min, keeps, duree, marge=0.3,
                     debut=0.0, respirations=None):
    effectives = []
    for s, e in coupes:
        rs, re_ = max(s + marge, debut), e - marge
        if re_ > rs:
            effectives.append((s, e))
    print(f"Silences détectés : {len(silences)}  |  coupés : {len(effectives)}\n")
    resp_set = set(respirations or [])
    if respirations is not None:
        courtes = [x for x in respirations if (x[1] - x[0]) <= RESPIRATION_MAX]
        pauses = [x for x in respirations if (x[1] - x[0]) > RESPIRATION_MAX]
        total_c = sum((e - s) for s, e in courtes if e != float("inf"))
        total_p = sum((e - s) for s, e in pauses if e != float("inf"))
        print(f"Respirations conservées : {len(courtes)} ({fmt(total_c)})  |  "
              f"pauses courtes conservées : {len(pauses)} ({fmt(total_p)})\n")
    for s, e in effectives:
        print(f"  [COUPÉ  ] {fmt(s)} -> {fmt(e)}  ({e-s:.1f} s)")
    for s, e in silences:
        if any(cs >= s - 0.15 and ce <= e + 0.15 for cs, ce in effectives):
            continue
        if s < debut:
            motif = "début conservé"
        elif (s, e) in resp_set:
            motif = ("respiration conservée" if (e - s) <= RESPIRATION_MAX
                     else "pause courte conservée")
        elif e - s < duree_min:
            motif = "pause naturelle"
        elif any(bs >= s - 0.15 and be <= e + 0.15 for bs, be in coupes):
            motif = "trop courte après marge"
        else:
            motif = "pas de borne basse sûre, pause conservée"
        print(f"  [gardé  ] {fmt(s)} -> {fmt(e)}  ({e-s:.1f} s)  {motif}")
    attendue = sum(e - s for s, e in keeps)
    print(f"\nDurée : {fmt(duree)} -> {fmt(attendue)} "
          f"(gagné : {fmt(duree - attendue)})")
    return attendue


def choisir_codec(sortie, codec, bitrate):
    suffixe = sortie.suffix.lower()
    if suffixe == ".webm":
        erreur("Le WebM n'accepte pas le H.264 produit ici. Utilise .mkv, .mp4 ou .mov.")
    admis = AUDIO_PAR_CONTENEUR.get(suffixe)
    if admis is not None and codec not in admis:
        print(f"  ℹ {codec} incompatible avec {suffixe} -> AAC utilisé à la place.")
        codec = "aac"
    if codec in SANS_BITRATE:
        bitrate = ""
    elif not bitrate:
        bitrate = "192k"
    return codec, bitrate


def encoder(entree, sortie, keeps, preset, crf, piste, audio_codec, audio_bitrate,
            debruitage=None, audio_entree=None):
    if not keeps:
        erreur("aucun segment à encoder")

    # Si un audio déjà débruité est fourni (moteur sox), il arrive en entrée 1
    # et remplace la piste audio de la source. Les atrim gardent les mêmes
    # bornes : le WAV a exactement la durée de la piste d'origine.
    source_audio = "1:a:0" if audio_entree is not None else f"0:a:{piste}"

    parts, concat = [], []
    for i, (s, e) in enumerate(keeps):
        # Dimensions paires imposées par yuv420p (arrondi vers le bas).
        parts.append(f"[0:v:0]trim=start={s:.3f}:end={e:.3f},setpts=PTS-STARTPTS,"
                     f"scale=trunc(iw/2)*2:trunc(ih/2)*2[v{i}]")
        parts.append(f"[{source_audio}]atrim=start={s:.3f}:end={e:.3f},"
                     f"asetpts=PTS-STARTPTS[a{i}]")
        concat.append(f"[v{i}][a{i}]")
    parts.append("".join(concat) + f"concat=n={len(keeps)}:v=1:a=1[outv][outa]")

    # Le débruitage s'applique APRÈS le concat, sur la timeline finale : une
    # seule fois. Appliqué à chaque segment, le filtre redémarrerait à chaque
    # jonction et la porte se réarmerait brutalement à chaque coupe.
    etiquette_audio = "[outa]"
    if debruitage:
        parts.append(f"[outa]{debruitage}[outa_propre]")
        etiquette_audio = "[outa_propre]"

    fd, filtre = tempfile.mkstemp(suffix=".txt", prefix="silences-")
    with open(fd, "w", encoding="utf-8") as f:
        f.write(";".join(parts))

    cmd = ["ffmpeg", "-y", "-hide_banner", "-i", str(entree)]
    if audio_entree is not None:
        cmd += ["-i", str(audio_entree)]
    cmd += ["-filter_complex_script", filtre,
            "-map", "[outv]", "-map", etiquette_audio,
            "-map_metadata", "0",
            "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
            "-pix_fmt", "yuv420p",
            "-c:a", audio_codec]

    if audio_bitrate and audio_codec not in SANS_BITRATE:
        cmd += ["-b:a", audio_bitrate]

    if sortie.suffix.lower() in (".mp4", ".m4v", ".mov"):
        cmd += ["-movflags", "+faststart"]

    cmd.append(str(sortie))

    try:
        t0 = time.monotonic()
        res = subprocess.run(cmd, stdin=subprocess.DEVNULL)
        dt = time.monotonic() - t0
    finally:
        Path(filtre).unlink(missing_ok=True)

    if res.returncode != 0:
        erreur(f"ffmpeg a échoué (code {res.returncode}). "
               f"Copie le message ci-dessus si tu veux de l'aide.")
    return dt


def creer_proxy(master, proxy, bitrate="192k"):
    """Remux vidéo + transcodage audio 48 kHz stéréo. Aucun réencodage vidéo."""
    cmd = ["ffmpeg", "-y", "-hide_banner", "-i", str(master),
           "-map", "0:v:0", "-map", "0:a:0",
           "-c:v", "copy", "-c:a", "aac", "-b:a", bitrate,
           "-ar", "48000", "-ac", "2",
           "-movflags", "+faststart", str(proxy)]
    t0 = time.monotonic()
    res = subprocess.run(cmd, stdin=subprocess.DEVNULL)
    dt = time.monotonic() - t0
    if res.returncode != 0:
        try:
            if proxy.exists():
                proxy.unlink()
        except OSError:
            pass
        return None
    return dt


def lire_float(msg, defaut):
    while True:
        rep = input(f"{msg} [{defaut}] : ").strip().replace(",", ".")
        if not rep:
            return defaut
        try:
            v = float(rep)
            if v < 0:
                raise ValueError
            return v
        except ValueError:
            print("  Valeur invalide, réessaie.")


def lire_seuil(msg, defaut):
    while True:
        rep = input(f"{msg} [{defaut}] : ").strip()
        if not rep:
            return defaut
        if rep.lower().endswith("db"):
            rep = rep[:-2]
        try:
            v = float(rep.replace(",", "."))
            if -90 <= v <= 0:
                return f"{v}dB"
        except ValueError:
            pass
        print("  Valeur invalide (ex: -35 ou -35dB).")


def lire_bool(msg, defaut=False):
    while True:
        rep = input(f"{msg} [{'O/n' if defaut else 'o/N'}] : ").strip().lower()
        if not rep:
            return defaut
        if rep in ("o", "oui", "y", "yes", "1", "vrai"):
            return True
        if rep in ("n", "non", "0", "faux"):
            return False
        print("  Réponds par o ou n.")


def deplacer_vers_corbeille_locale(fichier):
    corbeille = fichier.parent / ".suppression-silences-corbeille"
    corbeille.mkdir(exist_ok=True)
    dest = corbeille / fichier.name
    i = 1
    while dest.exists():
        dest = corbeille / f"{fichier.stem}-{i}{fichier.suffix}"
        i += 1
    fichier.rename(dest)
    return dest


def journaliser_action(entree, sortie, action):
    try:
        log = Path.home() / ".suppression-silences.log"
        with log.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {action}    "
                    f"{entree}    ->  {sortie}\n")
    except OSError:
        pass


def confirmer_ecrasement(p, entree, oui):
    if p.resolve() == entree.resolve():
        erreur("le fichier de sortie doit être différent de l'entrée.")
    if p.exists() and not oui:
        rep = input(f"  {p.name} existe déjà. Écraser ? [o/N] : ").strip().lower()
        if rep != "o":
            return False
    return True


def traiter_fichier(entree, args, oui):
    print(f"\n{SEP}\nFichier : {entree}")
    if not entree.exists():
        print("  Introuvable, fichier ignoré.")
        return False

    npistes = nb_pistes_audio(entree)
    if npistes == 0:
        print("  Pas de piste audio, fichier ignoré.")
        return False
    if args.piste >= npistes:
        print(f"  Piste audio {args.piste} inexistante ({npistes} piste(s)), "
              f"fichier ignoré.")
        return False

    duree = duree_fichier(entree)
    supprimer_original = args.supprimer_original

    plancher = plancher_de_bruit(entree, args.highpass, args.piste)
    plancher_ok = (plancher is not None and math.isfinite(plancher)
                   and plancher >= -90)
    if args.seuil:
        seuil = args.seuil
        if plancher_ok:
            print(f"  Seuil imposé : {seuil} "
                  f"(bruit de fond mesuré : {plancher:.1f} dB)")
    elif not plancher_ok:
        seuil = "-35dB"
        print("  Plancher non mesurable, seuil par défaut : -35dB")
    else:
        seuil = f"{min(round(plancher + args.offset_plancher, 1), -10)}dB"
        print(f"  Bruit de fond : {plancher:.1f} dB -> seuil : {seuil}")

    debut = args.debut
    if debut > 0:
        print(f"  Début protégé : 00:00 -> {fmt(debut)} (prise de bruit Audacity)")

    if args.court:
        def_dm, def_mg, def_fu = 0.7, 0.12, 0.0
    else:
        def_dm, def_mg, def_fu = 2.5, 0.3, 0.0

    duree_min = args.duree_min if args.duree_min is not None else def_dm
    marge = args.marge if args.marge is not None else def_mg
    fusion = args.fusion if args.fusion is not None else def_fu

    if args.speech_aware:
        print(f"  Mode speech-aware : analyse fine 0.12 s, pauses de phrase "
              f">= {duree_min:g} s coupées, respirations conservées.")

    if fusion > 0:
        if args.protection > 0:
            print(f"  ℹ Fusion = {fusion} s : les pauses proches sont regroupées, "
                  f"mais seules les parties sous le seuil bas sont coupées.")
        else:
            print(f"  ⚠ Fusion = {fusion} s : la parole entre deux silences proches "
                  f"sera fusionnée puis COUPÉE. 0 est conseillé.")

    def recalculer():
        respirations = None
        if args.speech_aware:
            # Analyse fine : on mesure aussi les pauses courtes (respirations)
            # afin de ne couper que les pauses de phrase >= duree_min.
            plancher_fin = min(0.12, duree_min) if duree_min > 0 else 0.12
            fins = detecter_silences(entree, seuil, args.highpass, args.denoise,
                                     args.piste, plancher_fin)
            pauses, respirations = classifier_pauses_respirations(fins, duree_min)
            sil = fusionner_silences(pauses, fusion)
            affichage = sorted(fins)
        else:
            bruts = detecter_silences(entree, seuil, args.highpass, args.denoise,
                                      args.piste, args.d)
            sil = fusionner_silences(bruts, fusion)
            affichage = sil
        seuil_bas = seuil_inferieur(seuil, plancher, args.protection)
        basses = None
        if seuil_bas:
            basses = detecter_silences(entree, seuil_bas, args.highpass,
                                       args.denoise, args.piste, args.d)
            basses = fusionner_silences(basses, 0.0)
        coupes = coupes_sures(sil, basses, duree_min)
        kp = segments_a_garder(duree, coupes, 0.0, marge, debut)
        return affichage, coupes, kp, respirations

    silences, coupes, keeps, respirations = recalculer()

    def ajuster_reglages():
        nonlocal seuil, duree_min, marge, fusion, debut, supprimer_original
        nonlocal silences, coupes, keeps, respirations

        seuil = lire_seuil("  Seuil de détection", seuil)
        duree_min = lire_float("  Durée min d'un silence coupé (s)", duree_min)
        marge = lire_float("  Marge gardée autour de la voix (s)", marge)
        fusion = lire_float("  Fusion des silences (s, 0 = conseillé)", fusion)
        debut = lire_float("  Secondes protégées au début (s)", debut)

        question = ("  Déplacer l'original vers la corbeille locale après traitement ?"
                    if args.corbeille else
                    "  Supprimer l'original après traitement ?")
        supprimer_original = lire_bool(question, supprimer_original)

        silences, coupes, keeps, respirations = recalculer()

    while True:
        attendue = afficher_analyse(coupes, silences, duree_min, keeps, duree,
                                    marge, debut, respirations)

        if args.analyse:
            return True

        if supprimer_original:
            if args.corbeille:
                print("  ♻ Déplacement vers corbeille locale après encodage : ACTIVÉ")
            else:
                print("  🗑 Suppression de l'original après encodage : ACTIVÉE")

        if not keeps or duree - attendue < 0.05:
            print("  Rien à couper avec ces réglages.")
            if oui:
                return False
            rep = input("  [a] ajuster | [n] passer : ").strip().lower() or "n"
            if rep == "a":
                ajuster_reglages()
                continue
            return False

        if oui:
            choix = "o"
        else:
            choix = input("  [o] encoder | [a] ajuster | [n] passer : ").strip().lower() or "o"

        if choix == "n":
            return False
        if choix == "a":
            ajuster_reglages()
            continue
        if choix == "o":
            break
        print("  Choix invalide.")

    # ---------- Débruitage ----------
    db = None
    audio_sox = None
    dossier_sox = None
    if args.debruitage:
        moteur = args.debruitage_moteur
        if moteur == "auto":
            moteur = "sox" if shutil.which("sox") else "anlmdn"
        if moteur == "sox":
            dossier_sox = tempfile.mkdtemp(prefix="debruitage-")
            # Nettoyage garanti même si erreur() appelle sys.exit.
            atexit.register(shutil.rmtree, dossier_sox, True)
            audio_sox, description = preparer_audio_sox(
                entree, args.piste, Path(dossier_sox),
                args.debruitage_reduction, args.debruitage_duree_profil,
                not args.debruitage_sans_eq)
            if audio_sox is None:
                print(f"  ℹ Débruitage sox impossible ({description}) : "
                      f"repli sur anlmdn.")
                shutil.rmtree(dossier_sox, ignore_errors=True)
                dossier_sox = None
                moteur = "anlmdn"
            else:
                print(f"  Débruitage sox : {description}")
        if moteur == "anlmdn":
            db = chaine_debruitage(entree, args.piste, args.debruitage_force,
                                   not args.debruitage_sans_porte)
            print(f"  Chaîne appliquée : {db}")
        print("  ⚠ Désactive le débruiteur de Kdenlive pour ce fichier : "
              "deux passes successives étouffent la voix.")

    # ---------- Préparation des sorties ----------
    if args.dual:
        master = entree.with_name(entree.stem + "-sans-silences.mkv")
        proxy = entree.with_name(entree.stem + "-sans-silences.mp4")
        for p in (master, proxy):
            if not confirmer_ecrasement(p, entree, oui):
                print("  Fichier ignoré.")
                return False
        master.parent.mkdir(parents=True, exist_ok=True)

        print("\n  Mode double : master MKV/PCM + copie MP4/AAC 48 kHz stéréo.")
        print(f"  1/2 Encodage du master : {master.name}")
        dt_master = encoder(entree, master, keeps, args.preset, args.crf,
                            args.piste, "pcm_s16le", "", debruitage=db,
                            audio_entree=audio_sox)
        print(f"  Master terminé en {fmt_duree(dt_master)}.")

        print(f"  2/2 Copie MP4/AAC : {proxy.name}")
        dt_proxy = creer_proxy(master, proxy, args.audio_bitrate or "192k")
        if dt_proxy is None:
            print("  ⚠ Le master MKV est valide, mais la copie MP4 a échoué.")
            print("    Original conservé. Tu peux refaire la copie manuellement.")
            return False
        print(f"  Copie terminée en {fmt_duree(dt_proxy)}.")

        dt = dt_master + dt_proxy
        cible = master
        a_verifier = [master, proxy]

    else:
        suffixe = ".mkv" if args.mkv else ".mp4"
        if args.sortie:
            sortie = args.sortie
            if sortie.is_dir():
                sortie = sortie / (entree.stem + "-sans-silences" + suffixe)
        else:
            sortie = entree.with_name(entree.stem + "-sans-silences" + suffixe)

        if not confirmer_ecrasement(sortie, entree, oui):
            print("  Fichier ignoré.")
            return False

        sortie.parent.mkdir(parents=True, exist_ok=True)
        audio_codec, audio_bitrate = choisir_codec(
            sortie, args.audio_codec, args.audio_bitrate)

        if sortie.suffix.lower() == ".mkv" and audio_codec in SANS_BITRATE:
            print("  ⚠ PCM/FLAC dans MKV : idéal pour Kdenlive/Audacity, "
                  "mais FilmCraft peut grésiller. --dual est recommandé.")

        print(f"\n  Encodage vers {sortie.name} ...")
        dt = encoder(entree, sortie, keeps, args.preset, args.crf,
                     args.piste, audio_codec, audio_bitrate, debruitage=db,
                     audio_entree=audio_sox)

        cible = sortie
        a_verifier = [sortie]

    # L'audio sox n'est plus nécessaire une fois l'encodage terminé.
    if dossier_sox:
        shutil.rmtree(dossier_sox, ignore_errors=True)

    # ---------- Contrôles ----------
    reelle = duree_fichier(cible)
    print(f"\n  Terminé en {fmt_duree(dt)}.")
    print(f"  Durée attendue ~{fmt(attendue)} | durée réelle : {fmt(reelle)}")

    if abs(reelle - attendue) > 1.0:
        print("  ATTENTION : durée inattendue, vérifie le fichier.")
        return False

    # La sortie ne contient QU'UNE piste vidéo et UNE piste audio,
    # quelle que soit la piste choisie à l'entrée -> toujours :0.
    for p in a_verifier:
        dv = duree_stream(p, "v:0")
        da = duree_stream(p, "a:0")
        if dv is not None and da is not None and abs(dv - da) > 0.15:
            print(f"  ⚠ Écart vidéo/audio de {abs(dv-da):.2f} s dans {p.name} : "
                  f"vérifie la synchro.")

    print("  ✔ OK")

    if supprimer_original:
        for p in a_verifier:
            if not p.exists() or p.stat().st_size == 0:
                print("  Original NON supprimé/déplacé : fichier de sortie invalide.")
                return False

        try:
            taille_entree = entree.stat().st_size
            taille_sortie = cible.stat().st_size
            if taille_entree > 0 and taille_sortie > taille_entree * 1.5:
                print(f"  ⚠ Sortie nettement plus volumineuse que l'original "
                      f"({taille_sortie / (1024*1024):.1f} Mio contre "
                      f"{taille_entree / (1024*1024):.1f} Mio).")
        except OSError:
            pass

        try:
            if args.corbeille:
                dest = deplacer_vers_corbeille_locale(entree)
                print(f"  ♻ Original déplacé vers : {dest}")
                journaliser_action(entree, cible, f"DEPLACE vers {dest}")
            else:
                if entree.is_symlink():
                    print("  (Original est un lien symbolique : "
                          "suppression du lien uniquement)")
                entree.unlink()
                print(f"  🗑 Original supprimé : {entree.name}")
                journaliser_action(entree, cible, "SUPPRIME")
        except OSError as e:
            print(f"  AVERTISSEMENT : impossible de supprimer/déplacer "
                  f"{entree.name} : {e}")

    return True


def normaliser_argv(argv):
    """Rend `--seuil -35dB` accepté par argparse.

    argparse croit qu'une valeur négative suffixée de "dB" est une option. On
    la réécrit en `--seuil=-35dB`, seule forme qu'il comprend sans ambiguïté.
    """
    motif = re.compile(r"^-[0-9]+(?:[.,][0-9]+)?d[bB]$")
    out, i = [], 0
    while i < len(argv):
        if argv[i] == "--seuil" and i + 1 < len(argv) and motif.match(argv[i + 1]):
            out.append(f"--seuil={argv[i + 1]}")
            i += 2
            continue
        out.append(argv[i])
        i += 1
    return out


def main():
    ap = argparse.ArgumentParser(
        description="Coupe les silences d'une vidéo (vidéo+audio synchronisés).")

    ap.add_argument("entrees", nargs="*", help="fichier(s) vidéo à traiter")
    ap.add_argument("sortie", nargs="?", help="fichier de sortie (1 seule entrée)")

    ap.add_argument("--analyse", action="store_true", help="affiche sans encoder")
    ap.add_argument("--court", action="store_true",
                    help="style short/Reels : coupes serrées")
    ap.add_argument("--debut", type=float, default=0,
                    help="secondes à protéger au début (prise de bruit Audacity)")
    ap.add_argument("--seuil", help="seuil en dB, ex: -30dB "
                    "(défaut : auto = bruit de fond + 10 dB)")
    ap.add_argument("--offset-plancher", type=float, default=10,
                    help="marge au-dessus du bruit de fond pour le seuil auto "
                         "(défaut : 10 dB)")
    ap.add_argument("--protection", type=float, default=0,
                    help="dB sous le seuil de détection pour affiner les bornes "
                         "de coupe (défaut : 0 = désactivé, comme la v9). "
                         "Valeurs > 0 : expérimental, peut réduire les coupes.")
    ap.add_argument("--duree-min", type=float,
                    help="durée mini d'un silence coupé (défaut 2.5 ; 0.7 --court)")
    ap.add_argument("--speech-aware", "--respirations", dest="speech_aware",
                    action="store_true",
                    help="analyse fine (0.12 s) des pauses : ne coupe que les pauses "
                         "de phrase >= --duree-min et compte les respirations "
                         "conservées. Ne modifie pas les points de coupe.")
    ap.add_argument("--fusion", type=float,
                    help="fusionne des silences séparés par une courte portion de "
                         "parole ; avec --protection, cette parole reste protégée. "
                         "Défaut 0 (conseillé).")
    ap.add_argument("--marge", type=float,
                    help="silence gardé autour de la voix (défaut 0.3 ; 0.12 --court)")
    ap.add_argument("--d", type=float, default=None,
                    help="granularité de détection (défaut 0.5, 0.2 en --court)")
    ap.add_argument("--highpass", type=float, default=120,
                    help="filtre passe-haut de l'analyse (défaut : 120 Hz)")
    ap.add_argument("--denoise", action="store_true",
                    help="débruitage à l'ANALYSE seulement (aide la détection des "
                         "silences ; n'affecte pas le son du fichier produit). "
                         "Pour le son, voir --debruitage.")
    ap.add_argument("--debruitage", action="store_true",
                    help="débruitage de la voix. Moteur auto : sox si disponible "
                         "(soustraction spectrale type Audacity, meilleure "
                         "qualité), sinon anlmdn. À utiliser à la place du "
                         "débruiteur de Kdenlive.")
    ap.add_argument("--debruitage-moteur", choices=("auto", "sox", "anlmdn"),
                    default="auto",
                    help="auto (défaut) = sox si présent et si un silence de "
                         "début est exploitable, sinon anlmdn")
    ap.add_argument("--debruitage-reduction", type=float, default=0.18,
                    help="montant de réduction sox (défaut 0.18 ; plus bas = "
                         "plus naturel, plus haut = plus agressif ; 0.30 fait "
                         "apparaître des artefacts)")
    ap.add_argument("--debruitage-duree-profil", type=float, default=2.0,
                    help="secondes de silence initial servant d'empreinte au "
                         "moteur sox (défaut 2 s)")
    ap.add_argument("--debruitage-sans-eq", action="store_true",
                    help="désactive le shelf +2,5 dB au-dessus de 3,5 kHz du "
                         "moteur sox (garde le timbre d'origine, plus mat)")
    ap.add_argument("--debruitage-force", type=float, default=2.0,
                    help="force d'anlmdn (moteur anlmdn uniquement ; défaut 2, "
                         "sature au-delà de 3)")
    ap.add_argument("--debruitage-sans-porte", action="store_true",
                    help="moteur anlmdn : désactive la porte de bruit (garde le "
                         "plancher tel quel, plus naturel mais moins net)")
    ap.add_argument("--piste", type=int, default=0, help="piste audio à utiliser")
    ap.add_argument("--preset", default="veryfast", help="preset x264")
    ap.add_argument("--crf", type=int, default=20)

    ap.add_argument("--audio-codec", default="aac",
                    help="codec audio (défaut aac) : aac, pcm_s16le, pcm_s24le, "
                         "flac, alac, libopus, vorbis. 'copy' est impossible ici.")
    ap.add_argument("--pcm", action="store_const", const="pcm_s16le",
                    dest="audio_codec",
                    help="forcer le PCM non compressé (conteneur adapté requis)")
    ap.add_argument("--audio-bitrate", default="",
                    help="débit audio, ex: 192k (ignoré pour les codecs lossless)")

    ap.add_argument("--mkv", action="store_true",
                    help="forcer le format de sortie MKV (défaut : MP4)")
    ap.add_argument("--dual", action="store_true",
                    help="produit MASTER.mkv en PCM (Kdenlive/Audacity) + copie "
                         ".mp4 en AAC 48 kHz stéréo (FilmCraft/Premiere). "
                         "Ignore --mkv, --audio-codec et --pcm. "
                         "Pas de fichier de sortie explicite autorisé.")

    ap.add_argument("--supprimer-original", dest="supprimer_original",
                    action="store_true", default=True,
                    help="supprimer/déplacer l'original après succès (défaut : oui)")
    ap.add_argument("--conserver-original", dest="supprimer_original",
                    action="store_false",
                    help="ne pas supprimer/déplacer l'original après succès")
    ap.add_argument("--corbeille", action="store_true",
                    help="déplacer dans un dossier corbeille local plutôt que supprimer")

    ap.add_argument("-y", "--oui", action="store_true",
                    help="répond oui partout (traitement par lot)")

    args = ap.parse_args(normaliser_argv(sys.argv[1:]))

    if args.audio_codec == "copy":
        ap.error("--audio-codec copy est impossible : les coupes par filter_complex "
                 "nécessitent un réencodage audio.")
    if args.seuil:
        valeur = args.seuil.strip()
        if valeur.lower().endswith("db"):
            valeur = valeur[:-2].strip()
        try:
            niveau = float(valeur.replace(",", "."))
        except ValueError:
            ap.error("--seuil invalide (ex : -30 ou -30dB).")
        if not -90 <= niveau <= 0:
            ap.error("--seuil doit être entre -90 et 0 dB.")
        args.seuil = f"{niveau}dB"
    if args.audio_codec not in CODECS_ADMIS:
        ap.error(f"codec audio inconnu : {args.audio_codec}. "
                 f"Choix : {', '.join(sorted(CODECS_ADMIS))}")
    if args.piste < 0:
        ap.error("--piste doit être supérieur ou égal à 0.")
    if args.dual and args.sortie:
        ap.error("--dual ne prend pas de fichier de sortie explicite : "
                 "les noms sont dérivés du fichier d'entrée.")
    if args.d and args.d <= 0:
        ap.error("--d doit être strictement positif.")
    if args.protection < 0:
        ap.error("--protection doit être positif ou nul.")
    if not 0.01 <= args.debruitage_force <= 10:
        ap.error("--debruitage-force doit être entre 0.01 et 10 (2 suffit ; "
                 "anlmdn sature au-delà de 3).")
    if not 0.0 < args.debruitage_reduction <= 1.0:
        ap.error("--debruitage-reduction doit être entre 0 et 1 (0.18 conseillé).")
    if not 0.4 <= args.debruitage_duree_profil <= 10.0:
        ap.error("--debruitage-duree-profil doit être entre 0.4 et 10 secondes.")
    if args.debruitage_sans_porte and not args.debruitage:
        ap.error("--debruitage-sans-porte n'a de sens qu'avec --debruitage.")
    if (args.debruitage and args.debruitage_moteur == "sox"
            and not shutil.which("sox")):
        ap.error("--debruitage-moteur sox demandé mais sox est introuvable.\n"
                 "  sudo apt install sox   (ou utilise --debruitage-moteur anlmdn)")
    if args.dual and (args.mkv or args.audio_codec != "aac"):
        print("  ℹ --dual : master PCM/MKV forcé ; "
              "--mkv / --audio-codec / --pcm ignorés.")

    if args.d is None:
        args.d = 0.2 if args.court else 0.5

    if args.audio_codec not in SANS_BITRATE and not args.audio_bitrate:
        args.audio_bitrate = "192k"

    if args.sortie and len(args.entrees) > 1:
        ap.error("un seul fichier d'entrée quand une sortie est précisée.")

    verifier_outils()

    entrees = [nettoyer_chemin(s) for s in args.entrees]
    if not entrees:
        rep = input("Chemin de la vidéo (glisse-dépose le fichier ici) : ")
        if not rep.strip():
            erreur("aucun fichier indiqué.")
        entrees = [nettoyer_chemin(rep)]

    args.sortie = nettoyer_chemin(args.sortie) if args.sortie else None

    if args.supprimer_original and not args.analyse:
        if args.corbeille:
            print("⚠ Option active : les originaux seront déplacés vers la "
                  "corbeille locale après encodage réussi (défaut : Oui).")
        else:
            print("⚠ Option active : les originaux seront SUPPRIMÉS après encodage "
                  "réussi (défaut : Oui). Utilise --conserver-original pour désactiver.")

    ok = 0
    for e in entrees:
        if traiter_fichier(e, args, args.oui):
            ok += 1

    if len(entrees) > 1:
        print(f"\n{SEP}\nTraités : {ok}/{len(entrees)}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("\nInterrompu.")
    except EOFError:
        sys.exit("\nTerminal non interactif : utilise le mode -y.")