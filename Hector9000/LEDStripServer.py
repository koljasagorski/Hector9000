#!/usr/bin/env python3
# -*- coding: utf8 -*-
##
#   LEDStripServer.py     Drives the WS2812 strip from MQTT messages.
#
#   Listens to the WebUI (theme, ambient mode, activity, drink selected),
#   to the controller (dosing progress, drink finished) and to cleaning /
#   valve / reboot commands. LEDEngine (utils/LEDAnimations.py) turns that
#   into frames; an output writes them to the hardware.
#
#   Outputs (HectorConfig "ws2812" -> "output"):
#     ws2812   strip on GPIO 18 via adafruit neopixel (needs root, see start.sh)
#     console  prints the frame as coloured blocks (development, no hardware)
#   WLED (ESP32 via MQTT) can be added later as another output.
#
#   Usage: python3 LEDStripServer.py [--console]
#

import sys
import threading
import time

import paho.mqtt.client as mqtt

from Hector9000.conf import HectorConfig as HC
from Hector9000.utils.LEDAnimations import LEDEngine

MQTT_SERVER = "localhost"
MQTT_PORT = 1883
FPS = 30

TOPICS = [
    "Hector9000/LEDStrip/#",
    "Hector9000/doseDrink/progress",
    "Hector9000/cleanMe",
    "Hector9000/dryMe",
    "Hector9000/openAllValves",
    "Hector9000/closeAllValves",
    "Hector9000/system/reboot",
]


def log(message):
    print("LEDStripServer: " + str(message), flush=True)


class WS2812Output:

    def __init__(self, count):
        import board      # only available on the Pi
        import neopixel
        self.pixels = neopixel.NeoPixel(board.D18, count, brightness=1.0,
                                        auto_write=False, pixel_order=neopixel.GRB)

    def show(self, frame):
        for i, color in enumerate(frame):
            self.pixels[i] = color
        self.pixels.show()


class ConsoleOutput:

    def __init__(self, count):
        self.last = None

    def show(self, frame):
        if frame == self.last:
            return
        self.last = frame
        blocks = "".join("\x1b[48;2;%d;%d;%dm  " % c for c in frame)
        sys.stdout.write("\r" + blocks + "\x1b[0m")
        sys.stdout.flush()


OUTPUTS = {"ws2812": WS2812Output, "console": ConsoleOutput}


def main():
    cfg = HC.config.get("ws2812", {})
    output_name = "console" if "--console" in sys.argv else cfg.get("output", "ws2812")
    engine = LEDEngine(
        count=cfg.get("count", 15),
        base=cfg.get("base", 5),
        brightness=cfg.get("brightness", 0.5),
        idle_after=cfg.get("idle_minutes", 5) * 60,
        column_reversed=cfg.get("column_reversed", False),
        now=time.monotonic(),
    )
    output = OUTPUTS[output_name](engine.count)
    lock = threading.Lock()

    def on_connect(client, userdata, flags, rc):
        log("connected to MQTT")
        for topic in TOPICS:
            client.subscribe(topic)

    def on_message(client, userdata, msg):
        try:
            with lock:
                engine.handle(msg.topic, msg.payload, time.monotonic())
        except Exception as e:  # never let one message kill the MQTT thread
            log("ignored message on %s: %s" % (msg.topic, e))

    client = mqtt.Client(client_id="HectorLED")
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect_async(MQTT_SERVER, MQTT_PORT, 60)
    client.loop_start()     # reconnects on its own if the broker restarts
    log("started, output=%s, %d pixels" % (output_name, engine.count))

    frame_time = 1.0 / FPS
    while True:
        start = time.monotonic()
        with lock:
            frame = engine.frame(start)
        try:
            output.show(frame)
        except Exception as e:  # keep running, e.g. on a transient SPI/PWM error
            log("output error: %s" % e)
            time.sleep(1)
        time.sleep(max(0.0, frame_time - (time.monotonic() - start)))


if __name__ == "__main__":
    main()
