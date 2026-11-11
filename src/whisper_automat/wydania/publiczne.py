"""Wydanie publiczne: marka MATCODE w jednym miejscu.

Nazwa, identyfikatory, repozytorium z aktualizacjami, wydawca, przycisk
wsparcia, barwy piór na pasku postępu i grafiki (assets/matcode/).
Paczka wydania firmowego nie zawiera ani tego modułu, ani assets/matcode/
(tools/build_exe.py); tools/wydaj.py sprawdza ją potem `sprawdz-firmowe.js`.
"""

#: Klucz publiczny Ed25519 autora do sprawdzania podpisu wydań.
#: Prywatny: %USERPROFILE%\.matcode\papuga-klucz-wydan.txt (tools/klucz_wydan.py).
KLUCZ_WYDAN = "5889fe93bba0dc4ee0df9a4be7c5f3ad908cce307f7d664f0046773f335656b4"

WYDANIE = dict(
    kod="papuga",
    nazwa="Papuga",
    plik="Papuga",
    app_id="MATCODE.Papuga",
    inno_id="{7CDF7F7F-B74F-4415-970D-792FE960F44A}",
    repo="matmiccode/papuga",
    wydawca="MATCODE",
    # Skrzynka kontaktowa do uzupełnienia, gdy powstanie.
    kontakt_email="",
    wsparcie_url="https://buycoffee.to/matcode",
    wsparcie_tekst="Postaw kawę autorowi",
    wsparcie_ikona="ui/kawa-zolta.png",
    zasoby="matcode",
    #: Pasy piór z logo Papugi (tools/make_icon.py), od skrzydła do głowy.
    kolory_postepu=("#1c64c4", "#2c965c", "#f7ba1c", "#ce202f"),
    # Nagłówek pliku podpisu, który znają wszystkie zainstalowane wersje.
    naglowek_podpisu="papuga-podpis 1",
    klucz_publiczny=KLUCZ_WYDAN,
    haslo="transkrypcje offline",
    opis=("Nagrywa spotkania, zamienia nagrania w tekst i rozpoznaje, "
          "kto mówi. Działa na Twoim komputerze, bez internetu i bez chmury."),
    nagrywanie=True,
)

EN = {
    "Postaw kawę autorowi": "Buy the author a coffee",
}
