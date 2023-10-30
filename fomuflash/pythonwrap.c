// Include MicroPython API.
#include "py/runtime.h"
#include "py/builtin.h"


#include "spi.h"
#include "fpga.h"
#include <fomu-flash.h>
#include "py/obj.h"
#include <stdio.h>
#include <string.h>

// Python call as fomuflash.memory_id
STATIC mp_obj_t print_memory_id() {
    struct ff_spi *spi; 
    struct ff_fpga *fpga;
    spi = spiAlloc();
    fpga = fpgaAlloc();
    initfomu(spi, fpga);
    memory_id(spi);
    releasefomu(spi, fpga);
    return mp_const_none; 
}
STATIC MP_DEFINE_CONST_FUN_OBJ_0(print_memory_id_obj, print_memory_id);


// Python call as fomuflash.reset_fpga
STATIC mp_obj_t reset_fpga() {
    struct ff_spi *spi;
    struct ff_fpga *fpga;
    spi = spiAlloc();
    fpga = fpgaAlloc();
    initfomu(spi, fpga);
    resetfpga(fpga);
    releasefomu(spi, fpga);
    return mp_const_none; 
}

STATIC MP_DEFINE_CONST_FUN_OBJ_0(reset_fpga_obj, reset_fpga);

// Python call as fomuflash.write_bin
STATIC mp_obj_t write_binary(mp_obj_t file_name) {
    // a typical binary is 100 kb
    const char *mode = "rb";
    mp_obj_t mode_obj = mp_obj_new_str(mode, strlen(mode));
    mp_obj_t args[2] = { file_name, mode_obj };
    mp_obj_t file = mp_builtin_open(2, args, (mp_map_t *)&mp_const_empty_map);
    mp_obj_t readinto_fn = mp_load_attr(file, MP_QSTR_readinto);
    uint32_t num_bytes = 256; // needs to be multiple of 256
    uint8_t *buf = malloc(num_bytes);
    mp_obj_t bytearray = mp_obj_new_bytearray_by_ref(num_bytes, buf);
    mp_int_t bytes_read = 10;
    mp_int_t address = 0;

    // setup fomu
    struct ff_spi *spi;
    struct ff_fpga *fpga;
    spi = spiAlloc();
    fpga = fpgaAlloc();
    initfomu(spi, fpga);

    while(bytes_read > 0){
        bytes_read = mp_obj_get_int(mp_call_function_1(readinto_fn, bytearray));
        if(bytes_read>0)
            spiWrite(spi, address, buf, num_bytes, 0);
        address += bytes_read;
    }

    // release fomu
    resetfpga(fpga);
    releasefomu(spi, fpga);
    return mp_const_none; 
}

STATIC MP_DEFINE_CONST_FUN_OBJ_1(write_bin_obj, write_binary);

// Define all properties of the module.
// Table entries are key/value pairs of the attribute name (a string)
// and the MicroPython object reference.
// All identifiers and strings are written as MP_QSTR_xxx and will be
// optimized to word-sized integers by the build system (interned strings).
STATIC const mp_rom_map_elem_t pythonwrap_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_fomuflash) },
    { MP_ROM_QSTR(MP_QSTR_memoryid), MP_ROM_PTR(&print_memory_id_obj) },
    { MP_ROM_QSTR(MP_QSTR_resetfpga), MP_ROM_PTR(&reset_fpga_obj) },
    { MP_ROM_QSTR(MP_QSTR_writebin), MP_ROM_PTR(&write_bin_obj) },
};
STATIC MP_DEFINE_CONST_DICT(pythonwrap_globals, pythonwrap_globals_table);

// Define module object.
const mp_obj_module_t pythonwrap_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&pythonwrap_globals,
};

// Register the module to make it available in Python.
MP_REGISTER_MODULE(MP_QSTR_fomuflash, pythonwrap_module);
