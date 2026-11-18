# Build the C port: make -C impl/c  ->  impl/c/build/f1
# No external libraries; only the C standard library and POSIX clock_gettime.

CC      ?= cc
CFLAGS  ?= -O2 -std=c11 -Wall -Wextra -pedantic
# clock_gettime is POSIX, not ISO C: ask the headers to declare it
CPPFLAGS += -D_POSIX_C_SOURCE=200809L
# sqrt() is in libm: Linux needs it named, macOS links it implicitly
LDLIBS  += -lm

BUILD := build
SRC   := f1.c csv.c
HDR   := csv.h pairmap.h

$(BUILD)/f1: $(SRC) $(HDR) | $(BUILD)
	$(CC) $(CPPFLAGS) $(CFLAGS) -o $@ $(SRC) $(LDFLAGS) $(LDLIBS)

$(BUILD):
	mkdir -p $(BUILD)

clean:
	rm -rf $(BUILD)

.PHONY: clean
