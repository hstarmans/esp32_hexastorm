from tmc.stepperdriver import TMC_2209
from tmc.uart import TMC_UART
from tmc.reg import GSTAT
from hexastorm.config import PlatformConfig


def init_stepper():
    esp32_cfg = PlatformConfig(test=False).esp32_cfg
    tmc_cfg = esp32_cfg["tmc2209"]
    for mtr_id in tmc_cfg["mtr_ids"].items():
        tmc = TMC_2209(
            pin_en=esp32_cfg["stepper_cs"],
            mtr_id=mtr_id,
            uart_dct=tmc_cfg["uart"],
        )
        # apply settings in correct order
        for key, value in tmc_cfg["settings"]:
            setattr(tmc, key, value)


def test_communication_pause(pauses=None):
    """
    Try several communication_pause values and return the first that works.
    """
    works = []
    esp32_cfg = PlatformConfig(test=False).esp32_cfg
    tmc_cfg = esp32_cfg["tmc2209"]
    if pauses is None:
        pauses = [0.0005, 0.001, 0.002, 0.005, 0.01]
    for pause in pauses:
        for id in tmc_cfg["uart_ids"].items():
            uart = TMC_UART(
                uart_dct=tmc_cfg["uart"],
                mtr_id=id,
                communication_pause=pause,
            )
            try:
                for _ in range(100):
                    uart.read_u32(GSTAT)
                print(f"Communication_pause {pause} works")
                works.append(pause)
            except Exception as e:
                print(f"Communication_pause {pause} failed: {e}")
    print(f"Working communication pauses: {works}")
