STEPPERS_MOD_DIR := $(USERMOD_DIR)

# Add our source files to the respective variables.
SRC_USERMOD += $(STEPPERS_MOD_DIR)/steppersmodule.c
SRC_USERMOD_CXX += $(STEPPERS_MOD_DIR)/steppers.cpp

# Add our module directory to the include path.
CFLAGS_USERMOD += -I$(STEPPERS_MOD_DIR)
CXXFLAGS_USERMOD += -I$(STEPPERS_MOD_DIR) -std=c++11

# We use C++ features so have to link against the standard library.
LDFLAGS_USERMOD += -lstdc++
