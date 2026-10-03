"""Costruisce gli effetti di combattimento del display dalle registrazioni CC0.

Ogni effetto è un piccolo montaggio: (file, presa, inizio_s, guadagno, durata_max_s).
presa = indice della presa nel file (segmenti separati da silenzio) oppure
"forte" per la più forte. Uscita: WAV 44.1 kHz mono 16 bit, picco -1 dBFS.

    python costruisci.py [cartella_uscita]
"""
import glob
import os
import sys
from math import gcd

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

SR = 44100
QUI = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.join(QUI, "estratti")
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(QUI, "uscita")

TT1 = r"medieval_sfx_textures_1_of_2"
TT2 = r"medieval_sfx_textures_2_of_2"
WW1 = r"medieval_sfx_weapon_on_weapon_1_of_2"
RPG = r"rpg_sound_pack\RPG Sound Pack"
KI = r"kenney_impact-sounds\Audio"
KR = r"RPGsounds_Kenney\OGG"

EFFETTI = {
    # freccia scoccata: corda dell'arco lungo, poi la freccia che passa
    "arrow": [(rf"{TT1}\English Longbow Shoot.wav", "forte", 0.00, 1.0, 0.6),
              (rf"{TT1}\Scythian Recurve Arrow Passby.wav", 0, 0.10, 0.55, 1.2)],
    "crossbow": [(rf"{TT1}\Crossbow Shoot.wav", "forte", 0.00, 1.0, 0.5),
                 (rf"{TT1}\Crossbow Bolt Pass.wav", 0, 0.12, 0.6, 0.6)],
    # colpo di spada: fendente e lame che si incontrano
    "sword": [(rf"{TT2}\Sabre Swing.wav", "forte", 0.00, 0.8, 0.45),
              (rf"{WW1}\Sabre Norse Sword Blade on Blade.wav", "forte", 0.20, 1.0, 1.3)],
    # martello / mazza: spostamento d'aria pesante, colpo su armatura
    "blunt": [(rf"{TT2}\Axe Swing.wav", 1, 0.00, 0.7, 0.45),
              (rf"{KI}\impactPlate_heavy_00*.ogg", 0, 0.30, 1.0, 0.8),
              (rf"{KI}\impactPunch_heavy_00*.ogg", 0, 0.30, 0.9, 0.6)],
    "axe": [(rf"{TT2}\Axe Swing.wav", 1, 0.00, 0.8, 0.45),
            (rf"{KR}\chop.ogg", 0, 0.30, 1.0, 0.8)],
    # parata con lo scudo: legno e borchie di metallo
    "shield": [(rf"{KI}\impactWood_heavy_00*.ogg", 0, 0.00, 1.0, 0.8),
               (rf"{KI}\impactMetal_heavy_00*.ogg", 0, 0.01, 0.5, 0.8)],
    # colpo a vuoto: solo il fendente
    "miss": [(rf"{TT1}\Katana Swing.wav", "forte", 0.00, 1.0, 0.6)],
    # pozione: vetro, poi due sorsi
    "potion": [(rf"{RPG}\inventory\bottle.wav", 0, 0.00, 0.9, 0.5),
               (rf"{RPG}\inventory\bubble.wav", 0, 0.40, 0.8, 0.5),
               (rf"{RPG}\inventory\bubble2.wav", 0, 0.70, 0.8, 0.5)],
    "spell": [(rf"{RPG}\battle\spell.wav", 0, 0.00, 1.0, 2.6)],
    # nemico che cade: corpo a terra e cotta di maglia
    "fall": [(rf"{KI}\impactSoft_heavy_00*.ogg", 0, 0.00, 1.0, 0.8),
             (rf"{RPG}\inventory\chainmail1.wav", 0, 0.04, 0.7, 0.6),
             (rf"{KI}\impactPunch_heavy_00*.ogg", 1, 0.02, 0.5, 0.5)],
}


def carica(rel):
    pat = os.path.join(BASE, rel)
    files = sorted(glob.glob(pat)) if "*" in rel else [pat]
    if not files:
        raise FileNotFoundError(pat)
    return files


def mono44(path):
    x, sr = sf.read(path, always_2d=True)
    x = x.mean(axis=1)
    if sr != SR:
        g = gcd(SR, sr)
        x = resample_poly(x, SR // g, sr // g)
    return x


def prese(x, soglia_db=-35, pausa_s=0.15):
    hop = int(SR * 0.005)
    env = np.sqrt(np.convolve(x ** 2, np.ones(hop) / hop, "same")[::hop]) + 1e-9
    on = 20 * np.log10(env / env.max()) > soglia_db
    seg, start, ultimo = [], None, None
    for i, v in enumerate(on):
        if not v:
            continue
        if start is None:
            start = i
        elif (i - ultimo) * 0.005 > pausa_s:
            seg.append((start, ultimo))
            start = i
        ultimo = i
    if start is not None:
        seg.append((start, ultimo))
    return [(a * hop, b * hop, float(env[a:b + 1].max())) for a, b in seg]


def ritaglia(x, quale, max_s):
    seg = prese(x)
    if quale == "forte":
        i = max(range(len(seg)), key=lambda k: seg[k][2])
    else:
        i = min(quale, len(seg) - 1)
    a = max(0, seg[i][0] - int(SR * 0.015))
    fine = seg[i + 1][0] - int(SR * 0.02) if i + 1 < len(seg) else len(x)
    b = min(fine, a + int(SR * max_s))
    y = x[a:b].copy()
    fi, fo = int(SR * 0.005), min(int(SR * 0.08), len(y) // 3)
    y[:fi] *= np.linspace(0, 1, fi)
    y[-fo:] *= np.linspace(1, 0, fo)
    return y


def costruisci(nome, strati):
    pezzi = []
    for rel, quale, t0, g, max_s in strati:
        files = carica(rel)
        if "*" in rel:          # varianti numerate: "quale" sceglie il file
            y = ritaglia(mono44(files[min(quale, len(files) - 1)]), "forte", max_s)
        else:
            y = ritaglia(mono44(files[0]), quale, max_s)
        pezzi.append((int(t0 * SR), y / (np.abs(y).max() + 1e-9) * g))
    n = max(s + len(y) for s, y in pezzi)
    mix = np.zeros(n)
    for s, y in pezzi:
        mix[s:s + len(y)] += y
    mix = mix / np.abs(mix).max() * 10 ** (-1 / 20)
    os.makedirs(OUT, exist_ok=True)
    sf.write(os.path.join(OUT, nome + ".wav"), mix.astype(np.float32), SR, subtype="PCM_16")
    return n / SR


if __name__ == "__main__":
    for nome, strati in EFFETTI.items():
        print(f"{nome:9s} {costruisci(nome, strati):.2f}s", flush=True)
    # anteprima: tutti in fila con 0.8 s di silenzio
    pausa = np.zeros(int(SR * 0.8))
    tutti = []
    for nome in EFFETTI:
        y, _ = sf.read(os.path.join(OUT, nome + ".wav"))
        tutti += [y, pausa]
    sf.write(os.path.join(QUI, "anteprima_tutti.wav"), np.concatenate(tutti).astype(np.float32),
             SR, subtype="PCM_16")
    print("anteprima:", os.path.join(QUI, "anteprima_tutti.wav"))
