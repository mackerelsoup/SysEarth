"""Compliance rules expressed as parameters over the cube.

A regime is a Rule: the cut-off year, the canopy threshold that makes a pixel
'forest', which extra evidence the forest mask needs, and how many deforested
pixels trigger an indication (optionally counting only cleared patches of a
minimum size). Switching regime (EUDR 2020, RSPO 2005, the
legacy 'any Hansen loss 2021-2023' check) changes only these numbers, never
the stored data.
"""
from dataclasses import dataclass, replace

import numpy as np
from scipy import ndimage

from . import encoding as E

_EIGHT = np.ones((3, 3), dtype=bool)


def large_patches(mask, min_px):
    """Keep the 8-connected patches of a 2-D mask with at least min_px pixels.

    A patch touching the window edge may continue outside it, so it is kept
    whatever its size: the filter can over-count but never drops a patch that
    is large in the full landscape.
    """
    lab, n = ndimage.label(mask, structure=_EIGHT)
    if n == 0:
        return mask
    sizes = np.bincount(lab.ravel(), minlength=n + 1)
    keep = sizes >= min_px
    edge = np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))
    keep[edge] = True
    keep[0] = False
    return keep[lab]


@dataclass(frozen=True)
class Rule:
    name: str
    cutoff_year: int = 2020          # loss strictly after this year counts
    loss_end_year: int = 2025        # last loss year considered (data runs to 2025)
    canopy_threshold: int = 10       # forest needs treecover2000 > this
    use_baseline: bool = True        # require the external 2020 forest map
    use_height: bool = False         # require tree height >= 5 m flag
    exclude_plantation: bool = True  # tree-crop plantations are not forest
    require_forest: bool = True      # False -> any loss counts (legacy rule)
    min_px: int = 1                  # deforested pixels needed for an indication
    min_patch_px: int = 0            # >0: only 8-connected cleared patches this big count
    pixel_rule: str = "centre"       # 'centre' or 'all_touched'

    # loss codes are years minus 2000
    @property
    def loss_lo(self):
        return self.cutoff_year + 1 - E.HANSEN_YEAR0

    @property
    def loss_hi(self):
        return self.loss_end_year - E.HANSEN_YEAR0

    # ---- masks on the cube ----
    def lost_after_cutoff(self, cube):
        ly = E.lossyear(cube)
        return (ly >= self.loss_lo) & (ly <= self.loss_hi)

    def forest_mask(self, cube):
        if not self.require_forest:
            return ~E.flag(cube, "not_land")
        ly = E.lossyear(cube)
        m = E.canopy(cube) >= E.canopy_class_above(self.canopy_threshold)
        m &= (ly == 0) | (ly >= self.loss_lo)        # still standing at the cut-off
        m &= ~E.flag(cube, "not_land")
        if self.use_baseline:
            m &= E.flag(cube, "forest2020")
        if self.use_height:
            m &= E.flag(cube, "height5m")
        if self.exclude_plantation:
            m &= ~E.flag(cube, "plantation")
        return m

    def deforested(self, cube):
        return self.forest_mask(cube) & self.lost_after_cutoff(cube)

    @property
    def window_pad(self):
        # a patch that touches the plot and reaches min_patch_px beyond its bbox
        # already has min_patch_px pixels, so this pad makes patch sizing exact
        return max(1, self.min_patch_px)

    def count_deforested(self, defor, in_plot):
        """Deforested pixels inside the plot, from a 2-D mask over the read window.

        With min_patch_px, a patch is sized over the whole window, so a clearing
        that straddles the plot boundary counts at its full size.
        """
        if self.min_patch_px > 0:
            defor = large_patches(defor, self.min_patch_px)
        return int((defor & in_plot).sum())

    # ---- conservative test on the block overview (no false negatives) ----
    def overview_maybe_deforested(self, ov):
        m = E.lossyear(ov) >= self.loss_lo        # block max loss year
        if self.require_forest:
            m &= E.canopy(ov) >= E.canopy_class_above(self.canopy_threshold)
            if self.use_baseline:
                m &= E.flag(ov, "forest2020")
            if self.use_height:
                m &= E.flag(ov, "height5m")
            # plantation exclusion is a negative condition: cannot be used on an OR summary
        return m


EUDR = Rule("EUDR (cut-off 31 Dec 2020, FAO forest, plantations excluded)")
EUDR_HANSEN_ONLY = replace(EUDR, name="EUDR, Hansen only (no baseline, no plantation mask)",
                           use_baseline=False, exclude_plantation=False)
LEGACY = Rule("Legacy report rule (any Hansen loss 2021-2023)", loss_end_year=2023,
              require_forest=False, use_baseline=False, exclude_plantation=False)
# The 2020 baseline and the plantation map post-date a 2005 cut-off, so they
# would hide forest cleared 2006-2020; this regime falls back to Hansen 2000.
RSPO = replace(EUDR, name="RSPO-style (cut-off Nov 2005 -> loss from 2006, canopy > 30%)",
               cutoff_year=2005, canopy_threshold=30, use_baseline=False, exclude_plantation=False)

# A Hansen pixel (0.00025 deg) is ~0.077 ha at 8.5 N, so 0.5 ha is 7 pixels.
EUDR_PATCH_05HA = replace(EUDR, name="EUDR, only cleared patches >= 0.5 ha (7 px) count", min_patch_px=7)

REGIMES = {"eudr": EUDR, "eudr_patch05ha": EUDR_PATCH_05HA, "eudr_hansen_only": EUDR_HANSEN_ONLY,
           "legacy": LEGACY, "rspo": RSPO}
