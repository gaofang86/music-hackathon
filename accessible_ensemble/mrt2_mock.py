#!/usr/bin/env python3
"""Visual mock for conductor-to-MRT2 parameter mappings."""

from __future__ import annotations

import argparse
import threading
import time

import cv2
import numpy as np
from pythonosc import dispatcher, osc_server


class MockState:
    def __init__(self):
        self.values = {
            "temperature": 1.05,
            "top_k": 50,
            "cfg_musiccoca": 1.6,
            "cfg_notes": 2.4,
            "cfg_drums": 3.0,
            "style": 0,
            "section": 0,
            "volume": -60.0,
            "bypass": 0,
        }
        self.action = "IDLE"
        self.last_message = time.monotonic()
        self._lock = threading.Lock()

    def set(self, name, value):
        with self._lock:
            self.values[name] = value
            self.last_message = time.monotonic()

    def set_action(self, name):
        with self._lock:
            self.action = name.upper()
            self.last_message = time.monotonic()

    def snapshot(self):
        with self._lock:
            return dict(self.values), self.action, self.last_message


def start_server(state: MockState, port: int):
    osc_dispatcher = dispatcher.Dispatcher()
    for name in (
        "temperature",
        "top_k",
        "cfg_musiccoca",
        "cfg_notes",
        "cfg_drums",
        "style",
        "section",
        "volume",
        "bypass",
    ):
        osc_dispatcher.map(
            f"/mrt2/{name}",
            lambda _address, value, key=name: state.set(key, value),
        )
    for action in ("prepare", "start", "hold", "stop_queued"):
        osc_dispatcher.map(
            f"/mrt2/action/{action}",
            lambda _address, *_args, key=action: state.set_action(key),
        )
    osc_dispatcher.map(
        "/mrt2/volume_ramp",
        lambda _address, target, duration: (
            state.set("volume", target),
            state.set_action(f"ramp {duration:.2f}s"),
        ),
    )
    server = osc_server.ThreadingOSCUDPServer(("127.0.0.1", port), osc_dispatcher)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def draw_text(image, text, position, scale=0.7, color=(230, 230, 230)):
    cv2.putText(image, text, position, cv2.FONT_HERSHEY_SIMPLEX, scale, color, 2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=9100)
    args = parser.parse_args()
    state = MockState()
    server = start_server(state, args.port)
    print(f"[MRT2 MOCK] Listening on 127.0.0.1:{args.port}")
    try:
        while True:
            values, action, last_message = state.snapshot()
            image = np.full((520, 700, 3), (24, 26, 30), dtype=np.uint8)
            draw_text(image, "MRT2 PARAMETER MOCK", (30, 45), 1.0, (100, 220, 255))
            draw_text(image, f"ACTION: {action}", (30, 92), 0.8, (100, 255, 140))
            y = 145
            for name, value in values.items():
                draw_text(image, f"{name:<18} {value}", (45, y), 0.65)
                y += 38
            age = time.monotonic() - last_message
            color = (80, 240, 110) if age < 1.0 else (100, 100, 180)
            draw_text(image, f"Last OSC: {age:.1f}s ago", (430, 92), 0.55, color)
            draw_text(image, "Q quit", (30, 500), 0.5, (160, 160, 165))
            cv2.imshow("MRT2 Bridge Mock", image)
            if cv2.waitKey(30) & 0xFF == ord("q"):
                break
    finally:
        cv2.destroyAllWindows()
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
