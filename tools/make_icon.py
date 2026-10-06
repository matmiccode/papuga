"""Generuje ikonę i logo programu do assets/ — osobno dla każdego wydania.

  * firma  — mikrofon na niebieskim kafelku: assets/
  * papuga — głowa ary w jej barwach:        assets/papuga/

Program szuka zasobów najpierw w katalogu swojego wydania, potem w assets/
(core/config.asset), więc wydanie bez własnego katalogu dostaje ikonę firmową.

Narzędzie budowy, nie część aplikacji — gotowe pliki leżą w repozytorium,
więc do uruchomienia programu Pillow nie jest potrzebne.

Uruchomienie:  .venv\\Scripts\\python.exe tools\\make_icon.py [--wydanie papuga]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"

#: Rysujemy w dużej skali i pomniejszamy — to najprostszy sposób na gładkie
#: krawędzie bez ręcznego antyaliasingu.
CANVAS = 1024

#: Rozmiary zapisywane do .ico. 16 i 32 px to pasek zadań i Eksplorator,
#: 256 px bierze podgląd w dużych ikonach.
SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]

TLO_GORA = (108, 170, 226)
TLO_DOL = (46, 106, 168)
BIALY = (255, 255, 255, 255)


def gradient_tla(rozmiar: int) -> Image.Image:
    """Pionowy gradient w kolorach akcentu aplikacji."""
    obraz = Image.new("RGB", (1, rozmiar))
    rysuj = ImageDraw.Draw(obraz)
    for y in range(rozmiar):
        t = y / max(rozmiar - 1, 1)
        rysuj.point(
            (0, y),
            fill=tuple(
                int(TLO_GORA[i] + (TLO_DOL[i] - TLO_GORA[i]) * t) for i in range(3)
            ),
        )
    return obraz.resize((rozmiar, rozmiar), Image.NEAREST)


def maska_zaokraglonego_kwadratu(rozmiar: int, promien: int) -> Image.Image:
    maska = Image.new("L", (rozmiar, rozmiar), 0)
    ImageDraw.Draw(maska).rounded_rectangle(
        (0, 0, rozmiar - 1, rozmiar - 1), radius=promien, fill=255
    )
    return maska


def narysuj_mikrofon(rysuj: ImageDraw.ImageDraw) -> None:
    """Mikrofon: kapsuła, pałąk, nóżka i podstawka."""
    srodek_x = CANVAS // 2

    # Kapsuła mikrofonu.
    szer = 250
    gora, dol = 210, 610
    rysuj.rounded_rectangle(
        (srodek_x - szer // 2, gora, srodek_x + szer // 2, dol),
        radius=szer // 2,
        fill=BIALY,
    )

    # Pałąk — łuk obejmujący kapsułę od dołu.
    promien = 205
    grubosc = 58
    rysuj.arc(
        (srodek_x - promien, 500 - promien, srodek_x + promien, 500 + promien),
        start=15,
        end=165,
        fill=BIALY,
        width=grubosc,
    )

    # Nóżka.
    rysuj.rounded_rectangle(
        (srodek_x - 29, 690, srodek_x + 29, 820), radius=29, fill=BIALY
    )

    # Podstawka.
    rysuj.rounded_rectangle(
        (srodek_x - 150, 800, srodek_x + 150, 858), radius=29, fill=BIALY
    )


#: Mikrofon rysowany jest w geometrii wygodnej do liczenia, a potem
#: powiększany względem środka. Przy 16 px liczy się każdy piksel glifu,
#: więc marginesu ma być tyle, ile trzeba, i ani trochę więcej.
SKALA_GLIFU = 1.18


def zbuduj_mikrofon() -> Image.Image:
    tlo = gradient_tla(CANVAS).convert("RGBA")
    tlo.putalpha(maska_zaokraglonego_kwadratu(CANVAS, 224))

    warstwa = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    narysuj_mikrofon(ImageDraw.Draw(warstwa))

    if SKALA_GLIFU != 1.0:
        powiekszony = int(CANVAS * SKALA_GLIFU)
        odsuniecie = (CANVAS - powiekszony) // 2
        skalowana = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
        skalowana.paste(
            warstwa.resize((powiekszony, powiekszony), Image.LANCZOS),
            (odsuniecie, odsuniecie),
        )
        warstwa = skalowana

    return Image.alpha_composite(tlo, warstwa)


# --- Papuga ---------------------------------------------------------------
# Ara czerwona z profilu. Barwy z natury, przygaszone o ton, żeby logo
# było rozpoznawalne, ale nie odciągało uwagi od okna.

CZERWONY = (206, 32, 47)
ZOLTY = (247, 186, 28)
ZIELONY = (44, 150, 92)
NIEBIESKI = (28, 100, 196)
GRANAT = (22, 62, 140)
KREMOWY = (246, 240, 230)
DZIOB = (232, 222, 204)
#: Kontur dzioba — bez niego jasny dziób ginie na jasnym pasku tytułu.
DZIOB_KONTUR = (170, 150, 120)
CZARNY = (28, 28, 32)


def _bezier(p0, p1, p2, p3, n: int = 60) -> list:
    punkty = []
    for i in range(n + 1):
        t = i / n
        u = 1 - t
        punkty.append((
            u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
            u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1],
        ))
    return punkty


def _sciezka(*odcinki) -> list:
    """Zamknięty kształt z kolejnych krzywych Béziera (p0, p1, p2, p3)."""
    punkty = []
    for odcinek in odcinki:
        punkty += _bezier(*odcinek)
    return punkty


def _maska(punkty) -> Image.Image:
    maska = Image.new("L", (CANVAS, CANVAS), 0)
    ImageDraw.Draw(maska).polygon(punkty, fill=255)
    return maska


def _ara() -> Image.Image:
    obraz = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))

    # Tułów i skrzydło: łza od głowy w dół, w pasach jak pióra ary —
    # łuki o wspólnym środku układają się w wachlarz.
    tulow = _sciezka(
        ((360, 330), (190, 430), (130, 690), (170, 1000)),
        ((170, 1000), (330, 930), (600, 760), (660, 540)),
        ((660, 540), (700, 400), (540, 280), (360, 330)),
    )
    pasy = Image.new("RGBA", (CANVAS, CANVAS), GRANAT + (255,))
    rysuj = ImageDraw.Draw(pasy)
    sx, sy = 900, 260
    for promien, kolor in ((790, NIEBIESKI), (690, ZIELONY), (625, ZOLTY), (545, CZERWONY)):
        rysuj.ellipse((sx - promien, sy - promien, sx + promien, sy + promien), fill=kolor)
    pasy.putalpha(_maska(tulow))
    obraz = Image.alpha_composite(obraz, pasy)

    rysuj = ImageDraw.Draw(obraz)
    rysuj.ellipse((270, 110, 710, 550), fill=CZERWONY)
    # Naga skóra wokół oka, wydłużona ku dziobowi.
    rysuj.polygon(_sciezka(
        ((520, 200), (610, 170), (700, 220), (700, 320)),
        ((700, 320), (700, 420), (640, 470), (580, 450)),
        ((580, 450), (500, 420), (470, 330), (480, 260)),
        ((480, 260), (485, 230), (500, 210), (520, 200)),
    ), fill=KREMOWY)
    # Żuchwa.
    rysuj.polygon(_sciezka(
        ((650, 430), (710, 470), (770, 520), (800, 560)),
        ((800, 560), (770, 650), (690, 660), (630, 570)),
        ((630, 570), (610, 510), (620, 460), (650, 430)),
    ), fill=CZARNY)
    # Górny dziób z hakiem.
    dziob = _sciezka(
        ((640, 240), (790, 190), (920, 300), (905, 520)),
        ((905, 520), (900, 630), (865, 705), (812, 730)),
        ((812, 730), (832, 650), (822, 570), (772, 525)),
        ((772, 525), (712, 478), (660, 460), (630, 430)),
        ((630, 430), (600, 370), (605, 290), (640, 240)),
    )
    rysuj.polygon(dziob, fill=DZIOB)
    rysuj.line(dziob + dziob[:1], fill=DZIOB_KONTUR, width=12, joint="curve")
    # Oko: żółta tęczówka, czarna źrenica.
    rysuj.ellipse((520, 262, 600, 342), fill=ZOLTY)
    rysuj.ellipse((538, 280, 582, 324), fill=CZARNY)
    return obraz


def _wysrodkuj(obraz: Image.Image, margines: float = 0.04) -> Image.Image:
    """Przycina do treści i centruje w kwadracie — ikona wypełnia cały kafelek."""
    wycinek = obraz.crop(obraz.getbbox())
    bok = int(max(wycinek.size) * (1 + 2 * margines))
    kwadrat = Image.new("RGBA", (bok, bok), (0, 0, 0, 0))
    kwadrat.paste(wycinek, ((bok - wycinek.width) // 2, (bok - wycinek.height) // 2), wycinek)
    return kwadrat.resize((CANVAS, CANVAS), Image.LANCZOS)


def zbuduj_ara() -> Image.Image:
    return _wysrodkuj(_ara())


IKONY = {
    "firma": (zbuduj_mikrofon, ASSETS),
    "papuga": (zbuduj_ara, ASSETS / "papuga"),
}


def zapisz(ikona: Image.Image, cel: Path) -> None:
    cel.mkdir(parents=True, exist_ok=True)

    # Każdy rozmiar skalujemy osobno z pełnej rozdzielczości — LANCZOS daje
    # ostrzejszy wynik niż pozwolenie bibliotece .ico na własne pomniejszanie.
    klatki = [ikona.resize((s, s), Image.LANCZOS) for s in SIZES]
    plik_ico = cel / "icon.ico"
    klatki[-1].save(plik_ico, format="ICO", sizes=[(s, s) for s in SIZES])
    print(f"zapisano {plik_ico}  ({plik_ico.stat().st_size / 1024:.1f} KB)")

    # Tkinter nie potrafi wyświetlić .ico w oknie — ekran powitalny i nagłówek
    # okna biorą PNG.
    for nazwa, bok in (("icon-96.png", 96), ("logo-40.png", 40)):
        ikona.resize((bok, bok), Image.LANCZOS).save(cel / nazwa)
        print(f"zapisano {cel / nazwa}")

    # Podglądy do oceny czytelności — nie trafiają do paczki.
    ikona.resize((256, 256), Image.LANCZOS).save(cel / "icon-preview.png")
    male = [16, 24, 32, 48, 64]
    pasek = Image.new("RGBA", (sum(male) + 20 * len(male), 160), (32, 34, 40, 255))
    jasny = Image.new("RGBA", (pasek.width, 80), (243, 243, 243, 255))
    pasek.paste(jasny, (0, 80))
    x = 10
    for s in male:
        mala = ikona.resize((s, s), Image.LANCZOS)
        pasek.paste(mala, (x, (80 - s) // 2), mala)
        pasek.paste(mala, (x, 80 + (80 - s) // 2), mala)
        x += s + 20
    pasek.save(cel / "icon-sizes.png")


# --- Pola wyboru -----------------------------------------------------------
# clam rysuje zaznaczenie krzyżykiem, który wygląda jak z lat 90. Własne
# obrazki: zaokrąglony kwadrat i ptaszek w kolorze akcentu z core/theme.

KONTROLKI = ASSETS / "ui"
BOK_POLA = 18
#: Przezroczysty margines po prawej — odstęp między polem a jego opisem.
ODSTEP_POLA = 8


def _hex(kolor: str) -> tuple:
    return tuple(int(kolor[i:i + 2], 16) for i in (1, 3, 5)) + (255,)


def zapisz_kontrolki() -> None:
    sys.path.insert(0, str(ROOT / "src"))
    from whisper_automat import theme

    skala = 8
    duzy = BOK_POLA * skala
    warianty = {
        "check-off.png": (theme.BG_INPUT, theme.BORDER_DROP, None),
        "check-off-hover.png": (theme.BG_HOVER, theme.FG_FAINT, None),
        "check-on.png": (theme.ACCENT, theme.ACCENT, "#ffffff"),
        "check-on-hover.png": (theme.ACCENT_HOVER, theme.ACCENT_HOVER, "#ffffff"),
        "check-off-disabled.png": (theme.BG_CARD, theme.BORDER, None),
        "check-on-disabled.png": (theme.BG_INPUT, theme.BORDER_DROP, theme.FG_FAINT),
    }
    KONTROLKI.mkdir(parents=True, exist_ok=True)
    for nazwa, (tlo, obwodka, ptaszek) in warianty.items():
        obraz = Image.new("RGBA", (duzy, duzy), (0, 0, 0, 0))
        rysuj = ImageDraw.Draw(obraz)
        rysuj.rounded_rectangle((skala, skala, duzy - skala, duzy - skala),
                                radius=4 * skala, fill=_hex(tlo),
                                outline=_hex(obwodka), width=skala)
        if ptaszek:
            rysuj.line([(0.27 * duzy, 0.52 * duzy), (0.43 * duzy, 0.68 * duzy),
                        (0.74 * duzy, 0.34 * duzy)],
                       fill=_hex(ptaszek), width=int(2.2 * skala), joint="curve")
        male = Image.new("RGBA", (BOK_POLA + ODSTEP_POLA, BOK_POLA), (0, 0, 0, 0))
        male.paste(obraz.resize((BOK_POLA, BOK_POLA), Image.LANCZOS), (0, 0))
        male.save(KONTROLKI / nazwa)
    print(f"zapisano kontrolki do {KONTROLKI}")


def zapisz_kawe(na_ciemnym: bool = False) -> None:
    """Filiżanka do przycisku „Postaw kawę”. Emoji ☕ Tk rysuje jako plamę.

    Dwa warianty: ciemna na żółte wypełnienie (kawa.png) i żółta na ciemne
    tło okna (kawa-zolta.png) — tej używa cichy przycisk w nagłówku.
    """
    sys.path.insert(0, str(ROOT / "src"))
    from whisper_automat import theme

    nazwa = "kawa-zolta.png" if na_ciemnym else "kawa.png"
    s = 8                      # rysujemy 8× większe i zmniejszamy — gładkie krawędzie
    szer, wys = 16, 16
    obraz = Image.new("RGBA", (szer * s, wys * s), (0, 0, 0, 0))
    rysuj = ImageDraw.Draw(obraz)
    kolor = _hex(theme.KAWA if na_ciemnym else theme.KAWA_FG)
    # Ucho po prawej, potem czasza filiżanki, która je zachodzi.
    rysuj.ellipse((9.0 * s, 7.0 * s, 14.6 * s, 12.4 * s), outline=kolor, width=int(1.5 * s))
    rysuj.rounded_rectangle((1.4 * s, 6.0 * s, 11.6 * s, 14.6 * s), radius=int(3.2 * s),
                            fill=kolor)
    rysuj.rectangle((1.4 * s, 6.0 * s, 11.6 * s, 8.6 * s), fill=kolor)
    # Dwie smugi pary.
    for x in (4.6, 8.0):
        rysuj.line([(x * s, 4.6 * s), ((x + 0.9) * s, 3.0 * s), (x * s, 1.4 * s)],
                   fill=kolor, width=int(1.3 * s), joint="curve")
    KONTROLKI.mkdir(parents=True, exist_ok=True)
    obraz.resize((szer, wys), Image.LANCZOS).save(KONTROLKI / nazwa)
    print(f"zapisano {KONTROLKI / nazwa}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--wydanie", choices=sorted(IKONY), default="firma")
    args = parser.parse_args()
    rysuj, cel = IKONY[args.wydanie]
    zapisz(rysuj(), cel)
    zapisz_kontrolki()
    zapisz_kawe()
    zapisz_kawe(na_ciemnym=True)


if __name__ == "__main__":
    main()
