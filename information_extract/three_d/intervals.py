"""Interval clipping shared by finite-band reconstruction and historical audits."""
import numpy as np


def coverage(intervals, length):
    end = total = 0.
    for a, b in sorted(intervals):
        a = max(0., a); b = min(length, b)
        if b > max(end, a):
            total += b - max(end, a)
        end = max(end, b)
    return total / length


def merged_intervals(intervals, length):
    result = []
    for a, b in sorted(intervals):
        a = max(0., a); b = min(length, b)
        if b - a <= 1e-6:
            continue
        if result and a <= result[-1][1] + 1e-6:
            result[-1][1] = max(result[-1][1], b)
        else:
            result.append([a, b])
    return np.array(result)
