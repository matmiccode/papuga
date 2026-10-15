"""Testy sondowania i ekstrakcji: tag i kanały własnego nagrania przez ffprobe,
wybór kanału w ffmpeg. Pomijane, gdy nie ma ffmpeg (setup.bat go instaluje)."""

import shutil
import sys
import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from whisper_automat.core import media, nagrywanie as N  # noqa: E402
from whisper_automat.core.probe import find_ffmpeg  # noqa: E402

FFMPEG, FFPROBE = find_ffmpeg()


@unittest.skipUnless(FFMPEG and FFPROBE, "brak ffmpeg")
class WlasneNagranieTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="media-"))
        self.plik = self.tmp / "Spotkanie 2026-10-15 10-00.flac"
        z = N.ZapisFlac(self.plik, kanaly=2, tagi=N.tagi_nagrania(self.plik))
        t = np.arange(N.RATE * 2) / N.RATE
        self.m = (np.sin(2 * np.pi * 220 * t) * 10000).astype(np.int16)
        self.s = (np.sin(2 * np.pi * 440 * t) * 6000).astype(np.int16)
        for i in range(0, self.m.size, N.BLOK):
            z.zapisz(N.przeplot(self.m[i:i + N.BLOK], self.s[i:i + N.BLOK]))
        z.zamknij()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def wczytaj(self, plik: Path):
        with wave.open(str(plik)) as w:
            return w.getnchannels(), np.frombuffer(w.readframes(w.getnframes()), np.int16)

    def test_probe_widzi_tag_i_kanaly(self):
        info = media.probe_media(self.plik)
        self.assertEqual(info.channels, 2)
        self.assertEqual(info.tags.get("whisper_automat"), N.TAG_WARTOSC)
        self.assertEqual(info.tags.get("title"), self.plik.stem)
        self.assertAlmostEqual(info.duration, 2.0, places=1)

    def test_kanaly(self):
        mono = media.extract_audio(self.plik, self.tmp / "mono.wav")
        prawy = media.extract_audio(self.plik, self.tmp / "prawy.wav", kanal="prawy")
        lewy = media.extract_audio(self.plik, self.tmp / "lewy.wav", kanal="lewy")
        stereo = media.extract_audio(self.plik, self.tmp / "stereo.wav", kanal="stereo")
        self.assertEqual(self.wczytaj(mono)[0], 1)
        self.assertEqual(self.wczytaj(stereo)[0], 2)
        _, p = self.wczytaj(prawy)
        _, l = self.wczytaj(lewy)
        # Prawy to sam ton 440 Hz (6000), lewy sam 220 Hz (10000) — po poziomie.
        self.assertAlmostEqual(float(np.abs(p).max()), 6000, delta=200)
        self.assertAlmostEqual(float(np.abs(l).max()), 10000, delta=200)
        d = self.wczytaj(stereo)[1]
        np.testing.assert_allclose(d[0::2][:1000], self.m[:1000], atol=2)
        np.testing.assert_allclose(d[1::2][:1000], self.s[:1000], atol=2)

    def test_nieznany_kanal(self):
        with self.assertRaises(media.MediaError):
            media.extract_audio(self.plik, self.tmp / "x.wav", kanal="srodek")


if __name__ == "__main__":
    unittest.main()
