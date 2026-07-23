import sys

if sys.implementation.name == "cpython":
    from .base import BaseLaserhead

    laserhead = BaseLaserhead()
else:
    from .micropython import Laserhead

    laserhead = Laserhead()
