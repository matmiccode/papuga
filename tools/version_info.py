"""Generuje plik z metadanymi dla PyInstallera.

Bez tego właściwości pliku .exe są puste: brak nazwy produktu, wersji,
firmy i praw autorskich. Przy rozsyłaniu programu po firmie pusty wydawca
wygląda podejrzanie i dokłada się do ostrzeżenia SmartScreen.

Dane pochodzą z whisper_automat/__init__.py — jedno miejsce, żeby wersja
w kodzie, w pliku .exe i w instalatorze nie rozjechały się między sobą.
"""

from __future__ import annotations

from pathlib import Path

SZABLON = """\
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({major}, {minor}, {patch}, 0),
    prodvers=({major}, {minor}, {patch}, 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        # Klucz MUSI odpowiadac wpisowi w Translation ponizej, inaczej Windows
        # szuka bloku, ktorego nie ma, i pokazuje puste wlasciwosci pliku.
        # 0415 = polski (1045), 04b0 = strona kodowa Unicode (1200).
        '041504b0',
        [StringStruct('CompanyName', {company!r}),
         StringStruct('FileDescription', {description!r}),
         StringStruct('FileVersion', {version!r}),
         StringStruct('InternalName', {plik!r}),
         StringStruct('LegalCopyright', {copyright!r}),
         StringStruct('OriginalFilename', {plik_exe!r}),
         StringStruct('ProductName', {product!r}),
         StringStruct('ProductVersion', {version!r})])
    ]),
    # 1045 = polski, 1200 = Unicode
    VarFileInfo([VarStruct('Translation', [1045, 1200])])
  ]
)
"""


def zbuduj(metadane: dict, cel: Path, wydanie) -> Path:
    """Zapisuje plik z metadanymi i zwraca jego ścieżkę."""
    czesci = (metadane["version"].split(".") + ["0", "0", "0"])[:3]
    major, minor, patch = (int(c) if c.isdigit() else 0 for c in czesci)

    tresc = SZABLON.format(
        major=major,
        minor=minor,
        patch=patch,
        version=metadane["version"],
        company=metadane.get("publisher") or metadane["company"],
        product=wydanie.nazwa,
        description=(wydanie.pelna_nazwa if wydanie.haslo
                     else f"{wydanie.nazwa} — transkrypcja audio i wideo"),
        plik=wydanie.plik,
        plik_exe=f"{wydanie.plik}.exe",
        copyright=metadane["copyright"],
    )
    cel.parent.mkdir(parents=True, exist_ok=True)
    cel.write_text(tresc, encoding="utf-8")
    return cel
