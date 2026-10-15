"""Urządzenia audio Windows: co jest czym i które jest domyślne.

PortAudio (core/nagrywanie.py) zna tylko nazwy urządzeń i „domyślne
urządzenie multimedialne”. Laik po nazwie „Głośniki (3 — Realtek(R) Audio)”
albo „Zestaw mikrofonów (Technologia Intel® Smart Sound…)” nie pozna, czy to
słuchawki, głośniki laptopa, monitor po HDMI czy mikrofon wbudowany. Windows
to wie: każdy punkt końcowy ma typ obudowy (PKEY_AudioEndpoint_FormFactor:
głośniki, słuchawki, zestaw słuchawkowy, mikrofon, monitor…), magistralę
(USB, Bluetooth, wbudowane — z nazwy enumeratora urządzenia) i dwa osobne
urządzenia domyślne: multimedialne (muzyka, filmy; tego używa PortAudio)
i **komunikacyjne** (rozmowy; tego domyślnie używa Teams — często inne!).

Pobieramy to przez COM (IMMDeviceEnumerator) z samego ctypes, bez comtypes.
Nazwa (PKEY_Device_FriendlyName) jest tą samą, którą PortAudio pokazuje
w WASAPI, więc obie listy łączy się po nazwie. Poza Windows i przy każdym
błędzie COM `punkty()` oddaje pustą listę — program działa wtedy jak dotąd,
po samych nazwach.

Wywołanie trwa kilka milisekund, można je robić z dowolnego wątku (każde
samo inicjuje COM w swoim wątku).
"""

from __future__ import annotations

import ctypes
import os
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

from ..teksty import t

# -- rodzaje -----------------------------------------------------------------

GLOSNIKI, SLUCHAWKI, ZESTAW, MIKROFON, MONITOR, LINIOWE, CYFROWE, INNE = (
    "glosniki", "sluchawki", "zestaw", "mikrofon", "monitor", "liniowe", "cyfrowe", "inne")
USB, BLUETOOTH, WBUDOWANE = "usb", "bluetooth", "wbudowane"

#: EndpointFormFactor z mmdeviceapi.h.
_FORM_FACTOR = {
    0: INNE,       # RemoteNetworkDevice
    1: GLOSNIKI,   # Speakers
    2: LINIOWE,    # LineLevel
    3: SLUCHAWKI,  # Headphones
    4: MIKROFON,   # Microphone
    5: ZESTAW,     # Headset
    6: ZESTAW,     # Handset
    7: CYFROWE,    # UnknownDigitalPassthrough
    8: CYFROWE,    # SPDIF
    9: MONITOR,    # DigitalAudioDisplayDevice (HDMI / DisplayPort)
    10: INNE,      # UnknownFormFactor
}


@dataclass(frozen=True)
class PunktAudio:
    """Jedno aktywne urządzenie audio widziane przez Windows."""

    nazwa: str
    #: True = mikrofon (nagrywanie), False = odtwarzanie.
    wejscie: bool
    rodzaj: str = INNE
    #: USB, Bluetooth, wbudowane albo pusto (nieznane, np. wirtualne).
    zlacze: str = ""
    #: Domyślne urządzenie multimedialne — to, które PortAudio ma za domyślne.
    domyslne: bool = False
    #: Domyślne urządzenie komunikacyjne — tego domyślnie używa Teams.
    komunikacyjne: bool = False
    id: str = ""

    @property
    def zewnetrzne(self) -> bool:
        """Słuchawki, zestaw albo cokolwiek po USB/Bluetooth — podłączone
        świadomie, więc pewnie do rozmowy."""
        return self.rodzaj in (SLUCHAWKI, ZESTAW) or self.zlacze in (USB, BLUETOOTH)


def rodzaj_z(form_factor: Optional[int], enumerator: str) -> Tuple[str, str]:
    """(rodzaj, złącze) z surowych danych Windows."""
    rodzaj = _FORM_FACTOR.get(form_factor if form_factor is not None else -1, INNE)
    e = (enumerator or "").upper()
    if e.startswith("USB"):
        zlacze = USB
    elif e.startswith("BTH"):
        zlacze = BLUETOOTH
    elif e in ("HDAUDIO", "INTELAUDIO", "ACPI", "PCI") or e.endswith("AUDIO"):
        zlacze = WBUDOWANE
    else:
        zlacze = ""
    return rodzaj, zlacze


def opis_rodzaju(p: PunktAudio, krotko: bool = False) -> str:
    """Krótko po ludzku: „słuchawki USB”, „mikrofon wbudowany”, „monitor (HDMI)”.

    `krotko` — do linii „Mikrofon: …” w karcie nagrywania, gdzie słowo
    „mikrofon” już stoi: „wbudowany”, „zestaw słuchawkowy USB”. Pusty wynik
    = nie wiadomo, co to; pokaż samą nazwę.
    """
    zl = {USB: t("USB"), BLUETOOTH: t("Bluetooth")}.get(p.zlacze, "")
    if p.rodzaj == MONITOR:
        return t("monitor / telewizor (HDMI)")
    if p.rodzaj == ZESTAW:
        baza = (t("zestaw słuchawkowy") if krotko or not p.wejscie
                else t("mikrofon zestawu słuchawkowego"))
    elif p.rodzaj == SLUCHAWKI:
        baza = t("słuchawki") if krotko or not p.wejscie else t("mikrofon słuchawek")
    elif p.rodzaj == GLOSNIKI:
        if p.zlacze == WBUDOWANE:
            return t("głośniki komputera")
        baza = t("głośniki")
    elif p.rodzaj == MIKROFON:
        if p.zlacze == WBUDOWANE:
            return t("wbudowany") if krotko else t("mikrofon wbudowany")
        if krotko:
            return zl
        baza = t("mikrofon")
    elif p.rodzaj == LINIOWE:
        return t("wejście liniowe") if p.wejscie else t("wyjście liniowe")
    elif p.rodzaj == CYFROWE:
        return t("wyjście cyfrowe")
    else:
        return zl
    return f"{baza} {zl}".strip()


def znajdz(punkty: Iterable[PunktAudio], nazwa: str, wejscie: bool) -> Optional[PunktAudio]:
    """Punkt po nazwie; pusta nazwa = domyślne (multimedialne), jak w PortAudio."""
    lista = [p for p in punkty if p.wejscie == wejscie]
    if nazwa:
        for p in lista:
            if p.nazwa == nazwa:
                return p
        return None
    for p in lista:
        if p.domyslne:
            return p
    return None


def propozycja_zestawu(punkty: Iterable[PunktAudio], mikrofon: str, glosniki: str
                       ) -> Tuple[Optional[PunktAudio], Optional[PunktAudio]]:
    """Podłączony zestaw (słuchawki, USB, Bluetooth), którego nagranie by
    nie użyło: (mikrofon zestawu albo None, odtwarzanie zestawu albo None).

    `mikrofon`/`glosniki` to nazwy z ustawień (puste = domyślne). Gdy jest
    kilka kandydatów, wygrywa urządzenie komunikacyjne (to, na którym
    rozmawia Teams), potem domyślne, potem pierwsze z listy.
    """
    punkty = list(punkty)

    def kandydat(wejscie: bool, wybrane: str) -> Optional[PunktAudio]:
        uzyte = znajdz(punkty, wybrane, wejscie)
        zew = [p for p in punkty if p.wejscie == wejscie and p.zewnetrzne
               and p.rodzaj != MONITOR]
        if not zew or (uzyte is not None and uzyte.zewnetrzne):
            return None
        zew.sort(key=lambda p: (not p.komunikacyjne, not p.domyslne))
        return zew[0]

    return kandydat(True, mikrofon), kandydat(False, glosniki)


# -- COM ---------------------------------------------------------------------

_ole32 = None


def _guid(tekst: str):
    u = uuid.UUID(tekst)
    g = _GUID()
    g.Data1, g.Data2, g.Data3 = u.time_low, u.time_mid, u.time_hi_version
    g.Data4[:] = u.bytes[8:]
    return g


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]


class _PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", _GUID), ("pid", ctypes.c_ulong)]


class _PROPVARIANT(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort),
                ("r3", ctypes.c_ushort), ("val", ctypes.c_void_p), ("val2", ctypes.c_void_p)]


_VT_LPWSTR, _VT_UI4 = 31, 19
_STGM_READ = 0
_DEVICE_STATE_ACTIVE = 1
_CLSCTX_ALL = 23
_RPC_E_CHANGED_MODE = -2147417850


def _metoda(obj, indeks: int, *argtypes):
    """Metoda COM z tablicy wirtualnej po numerze (0–2 to IUnknown)."""
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    return ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)(vtbl[indeks])


def _zwolnij(obj) -> None:
    if obj:
        _metoda(obj, 2)(obj)


@contextmanager
def _com():
    """COM w bieżącym wątku; gdy już jest (np. wątek Tk z OLE), nie rusza."""
    global _ole32
    if _ole32 is None:
        _ole32 = ctypes.oledll.ole32
    nasze = True
    try:
        _ole32.CoInitializeEx(None, 2)  # COINIT_APARTMENTTHREADED
    except OSError as exc:
        if getattr(exc, "winerror", None) != _RPC_E_CHANGED_MODE:
            raise
        nasze = False
    try:
        yield _ole32
    finally:
        if nasze:
            _ole32.CoUninitialize()


_CLSID_ENUM = "BCDE0395-E52F-467C-8E3D-C4579291692E"
_IID_ENUM = "A95664D2-9614-4F35-A746-DE8DB63617E6"
_PKEY_DEVICE = "a45c254e-df1c-4efd-8020-67d146a850e0"
_PKEY_ENDPOINT = "1da5d803-d492-4edd-8c23-e0c0ffee7f0e"


def _wartosc(ole, store, klucz: _PROPERTYKEY):
    pv = _PROPVARIANT()
    _metoda(store, 5, ctypes.POINTER(_PROPERTYKEY), ctypes.POINTER(_PROPVARIANT))(
        store, ctypes.byref(klucz), ctypes.byref(pv))
    try:
        if pv.vt == _VT_LPWSTR and pv.val:
            return ctypes.wstring_at(pv.val)
        if pv.vt == _VT_UI4:
            return int(pv.val or 0) & 0xFFFFFFFF
        return None
    finally:
        ole.PropVariantClear(ctypes.byref(pv))


def _opisz(ole, dev, wejscie: bool, klucze) -> PunktAudio:
    k_nazwa, k_enum, k_form = klucze
    ident = ctypes.c_wchar_p()
    _metoda(dev, 5, ctypes.POINTER(ctypes.c_wchar_p))(dev, ctypes.byref(ident))
    id_tekst = ident.value or ""
    ole.CoTaskMemFree(ident)
    store = ctypes.c_void_p()
    _metoda(dev, 4, ctypes.c_ulong, ctypes.POINTER(ctypes.c_void_p))(
        dev, _STGM_READ, ctypes.byref(store))
    try:
        nazwa = _wartosc(ole, store, k_nazwa) or ""
        enumerator = _wartosc(ole, store, k_enum) or ""
        form = _wartosc(ole, store, k_form)
    finally:
        _zwolnij(store)
    rodzaj, zlacze = rodzaj_z(form if isinstance(form, int) else None, str(enumerator))
    return PunktAudio(str(nazwa), wejscie, rodzaj, zlacze, id=id_tekst)


def punkty() -> List[PunktAudio]:
    """Aktywne urządzenia audio Windows; [] poza Windows lub przy błędzie."""
    if os.name != "nt":
        return []
    try:
        with _com() as ole:
            return _punkty(ole)
    except Exception:
        return []


def _punkty(ole) -> List[PunktAudio]:
    klucze = (_PROPERTYKEY(_guid(_PKEY_DEVICE), 14),   # PKEY_Device_FriendlyName
              _PROPERTYKEY(_guid(_PKEY_DEVICE), 24),   # PKEY_Device_EnumeratorName
              _PROPERTYKEY(_guid(_PKEY_ENDPOINT), 0))  # PKEY_AudioEndpoint_FormFactor
    clsid, iid = _guid(_CLSID_ENUM), _guid(_IID_ENUM)
    en = ctypes.c_void_p()
    ole.CoCreateInstance(ctypes.byref(clsid), None, _CLSCTX_ALL, ctypes.byref(iid),
                         ctypes.byref(en))
    wynik: List[PunktAudio] = []
    try:
        for flow, wejscie in ((0, False), (1, True)):  # eRender, eCapture
            domyslne = {}
            for rola in (1, 2):  # eMultimedia, eCommunications
                dev = ctypes.c_void_p()
                try:
                    _metoda(en, 4, ctypes.c_uint, ctypes.c_uint,
                            ctypes.POINTER(ctypes.c_void_p))(en, flow, rola, ctypes.byref(dev))
                except OSError:
                    continue  # brak urządzeń tego rodzaju
                if dev:
                    domyslne[rola] = _opisz(ole, dev, wejscie, klucze).id
                    _zwolnij(dev)
            kolekcja = ctypes.c_void_p()
            _metoda(en, 3, ctypes.c_uint, ctypes.c_ulong, ctypes.POINTER(ctypes.c_void_p))(
                en, flow, _DEVICE_STATE_ACTIVE, ctypes.byref(kolekcja))
            try:
                n = ctypes.c_uint()
                _metoda(kolekcja, 3, ctypes.POINTER(ctypes.c_uint))(kolekcja, ctypes.byref(n))
                for i in range(n.value):
                    dev = ctypes.c_void_p()
                    _metoda(kolekcja, 4, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))(
                        kolekcja, i, ctypes.byref(dev))
                    try:
                        p = _opisz(ole, dev, wejscie, klucze)
                    finally:
                        _zwolnij(dev)
                    wynik.append(PunktAudio(
                        p.nazwa, p.wejscie, p.rodzaj, p.zlacze,
                        domyslne=(domyslne.get(1) == p.id),
                        komunikacyjne=(domyslne.get(2) == p.id), id=p.id))
            finally:
                _zwolnij(kolekcja)
    finally:
        _zwolnij(en)
    return wynik
