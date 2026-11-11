"""Dane wydań — każde wydanie w osobnym module.

Wspólny kod nie zna nazw, marek ani tekstów żadnego wydania: bierze je
z `Wydanie` (core/wydanie.py), a ten — z modułów tego pakietu. Paczka
danego wydania zawiera tylko jego moduł (tools/build_exe.py pomija
pozostałe), więc to, czego wydanie mieć nie może, w ogóle do niego nie
trafia — nie jest tylko ukryte.

Moduł wydania ma słownik `WYDANIE` (pola `Wydanie`) i może mieć `EN`
z tłumaczeniami własnych tekstów (dopisywanymi do `teksty.EN`).
"""
