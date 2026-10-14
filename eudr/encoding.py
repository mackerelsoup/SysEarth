"""Bit layout of the SysEarth EUDR cube.

One uint16 per 30 m pixel replaces the Hansen stack (treecover2000, lossyear,
gain, datamask) plus the ancillary baseline, plantation and protected-area
layers. Every field is either lossless (lossyear, flags) or quantised so that
the thresholds compliance regimes actually use stay exact (canopy bins).

    bits 0-4   loss year: 0 = no loss, 1..25 = 2001..2025 (Hansen lossyear, lossless)
    bits 5-7   canopy class (treecover2000 quantised, see CANOPY_EDGES)
    bit  8     forest at 2020 according to the external baseline
    bit  9     tree height >= 5 m at 2020
    bit  10    tree-crop plantation (oil palm / rubber / other commodity trees)
    bit  11    strict protected area (burned with all_touched -> conservative)
    bit  12    restricted-use area (all_touched -> conservative)
    bit  13    not land (datamask != 1: water or no data)
    bits 14-15 spare

The year-2000 canopy percentage is binned at the thresholds used by the
Hansen/GFW tooling, so "canopy > T" is exact for T in CANOPY_THRESHOLDS.
"""
import numpy as np

LOSS_SHIFT, LOSS_MASK = 0, 0x1F
CANOPY_SHIFT, CANOPY_MASK = 5, 0x7
FOREST2020 = 1 << 8
HEIGHT5M = 1 << 9
PLANTATION = 1 << 10
STRICT_PA = 1 << 11
RESTRICTED = 1 << 12
NOT_LAND = 1 << 13

FLAGS = {
    "forest2020": FOREST2020,
    "height5m": HEIGHT5M,
    "plantation": PLANTATION,
    "strict_pa": STRICT_PA,
    "restricted": RESTRICTED,
    "not_land": NOT_LAND,
}

# class k holds canopy values in (CANOPY_EDGES[k-1], CANOPY_EDGES[k]]; class 0 is exactly 0.
CANOPY_EDGES = np.array([0, 10, 15, 20, 25, 30, 50, 100])
CANOPY_THRESHOLDS = (0, 10, 15, 20, 25, 30, 50)
# representative value per class, used when reconstructing a canopy %
CANOPY_MID = np.array([0, 5, 13, 18, 23, 28, 40, 75], dtype=np.float32)

HANSEN_YEAR0 = 2000  # lossyear 1 == 2001


def canopy_class(treecover):
    """treecover2000 (0..100) -> 3-bit class."""
    return np.searchsorted(CANOPY_EDGES, treecover, side="left").astype(np.uint16)


def canopy_class_above(threshold):
    """Smallest class whose every member has canopy > threshold."""
    if threshold not in CANOPY_THRESHOLDS:
        raise ValueError(f"canopy threshold {threshold} not representable; use one of {CANOPY_THRESHOLDS}")
    return int(np.searchsorted(CANOPY_EDGES, threshold, side="left")) + 1


def encode(lossyear, treecover, datamask, forest2020=None, height5m=None,
           plantation=None, strict_pa=None, restricted=None):
    """Pack aligned arrays (same shape) into the uint16 cube."""
    if lossyear.max() > LOSS_MASK:
        raise ValueError("lossyear does not fit in 5 bits")
    cube = lossyear.astype(np.uint16) & LOSS_MASK
    cube |= canopy_class(treecover) << CANOPY_SHIFT
    for arr, bit in ((forest2020, FOREST2020), (height5m, HEIGHT5M), (plantation, PLANTATION),
                     (strict_pa, STRICT_PA), (restricted, RESTRICTED)):
        if arr is not None:
            cube |= np.where(arr.astype(bool), bit, 0).astype(np.uint16)
    cube |= np.where(datamask != 1, NOT_LAND, 0).astype(np.uint16)
    return cube


def lossyear(cube):
    return (cube >> LOSS_SHIFT) & LOSS_MASK


def canopy(cube):
    return (cube >> CANOPY_SHIFT) & CANOPY_MASK


def flag(cube, name):
    return (cube & FLAGS[name]) != 0


# ---- per-regime decision byte (uint8), the most compact single-purpose form ----
D_FOREST = 1 << 0        # pixel counts as forest at the cut-off date
D_DEFOREST = 1 << 1      # forest at cut-off and lost after it
D_ANYLOSS = 1 << 2       # any loss after the cut-off (legacy rule, no baseline)
D_STRICT_PA = 1 << 3
D_RESTRICTED = 1 << 4
D_NOT_LAND = 1 << 5


def decision_byte(cube, rule):
    """Collapse the cube into a uint8 for one fixed rule (see eudr.rules.Rule)."""
    forest = rule.forest_mask(cube)
    after = rule.lost_after_cutoff(cube)
    out = np.zeros(cube.shape, np.uint8)
    out |= np.where(forest, D_FOREST, 0).astype(np.uint8)
    out |= np.where(forest & after, D_DEFOREST, 0).astype(np.uint8)
    out |= np.where(after, D_ANYLOSS, 0).astype(np.uint8)
    out |= np.where(flag(cube, "strict_pa"), D_STRICT_PA, 0).astype(np.uint8)
    out |= np.where(flag(cube, "restricted"), D_RESTRICTED, 0).astype(np.uint8)
    out |= np.where(flag(cube, "not_land"), D_NOT_LAND, 0).astype(np.uint8)
    return out


def overview(cube, block=16):
    """Conservative block summary: OR of flag bits, max of loss year and canopy class.

    If the overview says a block has no post-cut-off loss or no protected area,
    that is guaranteed true for every pixel in it, so queries can skip the
    full-resolution read. Positive answers are only 'maybe' and get refined.
    """
    h, w = cube.shape
    H, W = -(-h // block), -(-w // block)
    pad = np.zeros((H * block, W * block), np.uint16)
    pad[:h, :w] = cube
    b = pad.reshape(H, block, W, block).transpose(0, 2, 1, 3).reshape(H, W, block * block)
    flags = np.bitwise_or.reduce(b & ~np.uint16(LOSS_MASK | (CANOPY_MASK << CANOPY_SHIFT)), axis=2)
    ly = lossyear(b).max(axis=2)
    cc = canopy(b).max(axis=2)
    return (flags | ly | (cc << CANOPY_SHIFT)).astype(np.uint16)
