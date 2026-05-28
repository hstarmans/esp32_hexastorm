import sys
from tmc.stepperdriver import *

pin_en = 16  # enable pin
mtr_ids = [0, 1, 2, 3]  # uart id

settings = [
    ("direction_inverted", False),
    ("vsense", True),
    ("current", 400),
    ("iscale_analog", False),
    ("interpolation", True),
    ("spread_cycle", False),
    ("microstep_resolution", 16),
    ("internal_rsense", True),
    ("motor_enabled", False),
]

uart_dct = dict(
    id=2,
    ctor=dict(
        baudrate=115200,
        tx=15,
        rx=7,
    ),
    init=dict(
        bits=8,
        parity=None,
        stop=1,
    ),
)

for mtr_id in mtr_ids:
    try:
        tmc = TMC_2209(pin_en=pin_en, mtr_id=mtr_id, uart_dct=uart_dct)
        for key, value in settings:
            setattr(tmc, key, value)
        print(f"success mtr_ids {mtr_id}")
    except Exception as e:
        sys.print_exception(e)
        print(f"fail mtr_ids {mtr_id}")
