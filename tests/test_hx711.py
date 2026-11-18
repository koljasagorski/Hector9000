"""HX711 scale: read_np_arr8 must work with numpy 2.x (the old numpy.uint8(ragged list) raised
"inhomogeneous shape" and crashed hector-server at start). RPi.GPIO is replaced by a stub."""
import sys
import types

import pytest

numpy = pytest.importorskip("numpy")


@pytest.fixture
def hx711(monkeypatch):
    rpi = types.ModuleType("RPi")
    gpio = types.ModuleType("RPi.GPIO")
    rpi.GPIO = gpio
    monkeypatch.setitem(sys.modules, "RPi", rpi)
    monkeypatch.setitem(sys.modules, "RPi.GPIO", gpio)
    monkeypatch.delitem(sys.modules, "Hector9000.conf.hx711", raising=False)
    from Hector9000.conf import hx711 as mod
    return mod


def make(mod, raw):
    hx = mod.HX711.__new__(mod.HX711)          # no GPIO access
    hx.read = lambda: raw
    return hx


def test_read_np_arr8_accepts_the_ragged_list_read_returns(hx711):
    # read() returns three one-element arrays (packbits) and a plain int, as on the real device
    raw = [numpy.array([0x56], dtype=numpy.uint8), numpy.array([0x34], dtype=numpy.uint8),
           numpy.array([0x12 ^ 0x80], dtype=numpy.uint8), 0]
    arr = make(hx711, raw).read_np_arr8()
    assert arr.dtype == numpy.uint8 and arr.shape == (4,)
    assert list(arr) == [0x56, 0x34, 0x12 ^ 0x80, 0]


def test_read_long_is_one_32_bit_value(hx711):
    raw = [numpy.array([1], dtype=numpy.uint8), numpy.array([0], dtype=numpy.uint8),
           numpy.array([0], dtype=numpy.uint8), 0]
    value = make(hx711, raw).read_long()
    assert int(numpy.ravel(value)[0]) == 1
