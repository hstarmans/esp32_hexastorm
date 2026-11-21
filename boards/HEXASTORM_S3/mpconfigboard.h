#ifndef MICROPY_HW_BOARD_NAME
#define MICROPY_HW_BOARD_NAME               "Hexastorm ESP32S3"
#endif
#define MICROPY_HW_MCU_NAME                 "ESP32S3"

// Enable UART REPL for modules that have an external USB-UART or USB Serial/JTAG.
#define MICROPY_HW_ENABLE_UART_REPL         (1)

#define MICROPY_HW_I2C0_SCL                 (17)
#define MICROPY_HW_I2C0_SDA                 (18)

#define MICROPY_CAMERA_PIN_D0               (12)
#define MICROPY_CAMERA_PIN_D1               (14)
#define MICROPY_CAMERA_PIN_D2               (21)
#define MICROPY_CAMERA_PIN_D3               (13)
#define MICROPY_CAMERA_PIN_D4               (11)
#define MICROPY_CAMERA_PIN_D5               (10)
#define MICROPY_CAMERA_PIN_D6               (9)
#define MICROPY_CAMERA_PIN_D7               (8)
#define MICROPY_CAMERA_PIN_PCLK             (38)
#define MICROPY_CAMERA_PIN_VSYNC            (41)
#define MICROPY_CAMERA_PIN_HREF             (40)
#define MICROPY_CAMERA_PIN_XCLK             (39)
#define MICROPY_CAMERA_PIN_PWDN             (-1)
#define MICROPY_CAMERA_PIN_RESET            (42)
#define MICROPY_CAMERA_PIN_SIOD             (18)        // SDA
#define MICROPY_CAMERA_PIN_SIOC             (17)        // SCL
#define MICROPY_CAMERA_XCLK_FREQ            (10000000)  // 10 MHz (matches PlatformConfig.esp32_cfg)
#define MICROPY_CAMERA_FB_COUNT             (1)
#define MICROPY_CAMERA_JPEG_QUALITY         (85)
#define MICROPY_CAMERA_GRAB_MODE            (1)         // 0=WHEN_EMPTY, 1=LATEST
