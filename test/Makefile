SIM       ?= verilator
TOPLEVEL_LANG = verilog

VERILOG_SOURCES = $(PWD)/rtl/axi_lite_regs.v
TOPLEVEL        = axi_lite_regs
MODULE         ?= test_regs

# Make cocotemu importable
export PYTHONPATH := $(PWD)/../src:$(PYTHONPATH)

# Verilator extra args: enable tracing, suppress width warnings
EXTRA_ARGS += --trace --trace-structs -Wno-WIDTH

include $(shell cocotb-config --makefiles)/Makefile.sim

clean::
	rm -r __pycache__ results.xml
