"""Largest-Triangle-Three-Buckets downsampling for long time series."""

from __future__ import annotations

from collections.abc import Sequence

DEFAULT_THRESHOLD = 2000


def lttb(
    points: Sequence[tuple[float, float]], threshold: int = DEFAULT_THRESHOLD
) -> list[tuple[float, float]]:
    """Keep ``threshold`` points that preserve the series' visual shape (first and last kept)."""
    n = len(points)
    if threshold >= n or threshold < 3:
        return list(points)
    out = [points[0]]
    every = (n - 2) / (threshold - 2)
    a = 0
    for i in range(threshold - 2):
        start = int(i * every) + 1
        end = int((i + 1) * every) + 1
        nxt_start, nxt_end = end, min(int((i + 2) * every) + 1, n)
        nxt = points[nxt_start:nxt_end] or [points[-1]]
        avg_x = sum(p[0] for p in nxt) / len(nxt)
        avg_y = sum(p[1] for p in nxt) / len(nxt)
        ax, ay = points[a]
        best, best_area = start, -1.0
        for j in range(start, min(end, n - 1)):
            bx, by = points[j]
            area = abs((ax - avg_x) * (by - ay) - (ax - bx) * (avg_y - ay))
            if area > best_area:
                best, best_area = j, area
        out.append(points[best])
        a = best
    out.append(points[-1])
    return out
