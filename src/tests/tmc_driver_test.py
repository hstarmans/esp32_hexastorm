import sys

from tmc.stepperdriver import TMC_2209

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

uart_dct = {
    "id": 2,
    "ctor": {
        "baudrate": 115200,
        "tx": 15,
        "rx": 7,
    },
    "init": {
        "bits": 8,
        "parity": None,
        "stop": 1,
    },
}

for mtr_id in mtr_ids:
    try:
        tmc = TMC_2209(pin_en=pin_en, mtr_id=mtr_id, uart_dct=uart_dct)
        for key, value in settings:
            setattr(tmc, key, value)
        print(f"success mtr_ids {mtr_id}")
    except Exception as e:  # noqa: BLE001
        print_exc = getattr(sys, "print_exception", None)
        if print_exc is not None:
            print_exc(e)
        else:
            print(e)
        print(f"fail mtr_ids {mtr_id}")
