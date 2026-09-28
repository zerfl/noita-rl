import statistics

import psutil


def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def summary(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "p50": round(pct(values, 0.5), 4),
        "p95": round(pct(values, 0.95), 4),
        "mean": round(statistics.fmean(values), 4),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
    }


def memory(pid: int) -> dict:
    m = psutil.Process(pid).memory_info()
    mb = 1024 * 1024
    return {
        "working_set_mb": round(m.wset / mb, 1),
        "peak_working_set_mb": round(m.peak_wset / mb, 1),
        "private_mb": round(m.private / mb, 1),
    }
