"""Compliance rules expressed as parameters over the cube.

A regime is a Rule: the cut-off year, the canopy threshold that makes a pixel
'forest', which extra evidence the forest mask needs, and how many deforested
pixels trigger an indication. Switching regime (EUDR 2020, RSPO 2005, the
legacy 'any Hansen loss 2021-2023' check) changes only these numbers, never
the stored data.
"""
from dataclasses import dataclass, replace

import numpy as np

from . import encoding as E


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

REGIMES = {"eudr": EUDR, "eudr_hansen_only": EUDR_HANSEN_ONLY, "legacy": LEGACY, "rspo": RSPO}


def plot_decision(rule, cube_px, strict_hit, restricted_hit):
    """Plot-level outputs from the cube pixels inside the plot."""
    n_def = int(rule.deforested(cube_px).sum())
    return {
        "n_px": int(cube_px.size),
        "deforested_px": n_def,
        "c2_indication": n_def >= rule.min_px,
        "d1_strict_pa": bool(strict_hit),
        "d2_restricted": bool(restricted_hit),
    }
