import sys

if sys.implementation.name == "cpython":
    from .base import BaseLaserhead

    LASERHEAD = BaseLaserhead()
else:
    from .micropython import Laserhead

    LASERHEAD = Laserhead()
