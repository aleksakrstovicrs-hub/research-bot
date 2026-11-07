"""Zajednička brava za težak posao (Chromium + mnogo zahteva).

Tvoj bot i portal za klijente rade kao posebni procesi; ova brava (fajl) obezbeđuje
da u jednom trenutku radi samo jedna pretraga, da server ne ostane bez memorije.
"""
import asyncio
import contextlib
import fcntl

from . import config


@contextlib.asynccontextmanager
async def heavy_job():
    path = config.DATA_DIR / "tezak_posao.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "w")
    try:
        await asyncio.to_thread(fcntl.flock, f, fcntl.LOCK_EX)  # čeka dok drugi proces ne završi
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(f, fcntl.LOCK_UN)
        f.close()
