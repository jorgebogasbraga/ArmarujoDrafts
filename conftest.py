"""
Test-wide setup.

Config is read at import time, so the league profile has to be chosen before
any project module loads. Tests run against the Arma profile, which is the one
their expected sheet cells and division layout come from.
"""

import os

os.environ.setdefault("LEAGUE_DIR", "leagues/arma")
