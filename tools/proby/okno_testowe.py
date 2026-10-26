"""Okno do prób nagrywania obrazu.

    python okno_testowe.py <sekundy> [ruch|stoi|blyski]

ruch   — licznik zmienia się co 50 ms (ciągły strumień klatek),
stoi   — nic się nie zmienia,
blyski — co 2 s (od 2. sekundy) okno na 200 ms robi się białe i gra krótki
         ton 1 kHz; czasy błysków (perf_counter) idą na stdout — do pomiaru
         zgrania obrazu z dźwiękiem.
"""
import sys
import time
import tkinter as tk

sekundy = float(sys.argv[1])
tryb = sys.argv[2] if len(sys.argv) > 2 else "ruch"

root = tk.Tk()
root.title("Okno testowe spotkania")
root.geometry("640x400+100+100")
root.configure(bg="#202020")
napis = tk.Label(root, text="", font=("Segoe UI", 40), fg="#e0e0e0", bg="#202020")
napis.pack(expand=True, fill="both")
t0 = time.perf_counter()


def ton():
    import winsound
    winsound.Beep(1000, 150)


def ruch():
    napis.configure(text=f"{time.perf_counter() - t0:7.2f}")
    root.after(50, ruch)


def blysk(n=1):
    if 2.0 * n > sekundy - 1:
        return
    opoznienie = int((t0 + 2.0 * n - time.perf_counter()) * 1000)

    def teraz():
        root.configure(bg="white")
        napis.configure(bg="white")
        root.update_idletasks()
        print(f"blysk {time.perf_counter():.4f}", flush=True)
        import threading
        threading.Thread(target=ton, daemon=True).start()
        root.after(200, lambda: (root.configure(bg="#202020"), napis.configure(bg="#202020")))
        blysk(n + 1)

    root.after(max(0, opoznienie), teraz)


if tryb == "ruch":
    ruch()
elif tryb == "blyski":
    napis.configure(text="")
    blysk()
else:
    napis.configure(text="stoi")
root.after(int(sekundy * 1000), root.destroy)
root.mainloop()
