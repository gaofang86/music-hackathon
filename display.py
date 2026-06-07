"""
display.py — Unified fullscreen demo visualization for Accessible MRT2.

Replaces three separate OpenCV windows with one clean 1280x720 display.

Usage:
    python display.py [--musician-camera INT] [--conductor-camera INT]
"""

import argparse
import threading
import random
import math
from dataclasses import dataclass, field

import numpy as np
import cv2
from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import ThreadingOSCUDPServer

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WIDTH, HEIGHT = 1280, 720

CAM_W, CAM_H = 400, 225
TOP_STRIP_H = 225          # rows 0–224
PARTICLE_Y0 = 225          # particle field top
PARTICLE_Y1 = 600          # particle field bottom  (h = 375)
PARTICLE_H = PARTICLE_Y1 - PARTICLE_Y0  # 375
BAR_Y0 = 600               # bottom bar top
BAR_H = 120                # rows 600–719

MAX_PARTICLES = 300

STYLE_NAMES = {
    0: "Warm Acoustic",
    1: "Minimal Pulse",
    2: "Bright Electronic",
    3: "Dark Cinematic",
    4: "Percussive Experimental",
}

# BGR dark background colors per style
STYLE_BG = {
    0: np.array([80,  40,  20], dtype=np.float32),   # Warm Acoustic
    1: np.array([25,  15,  15], dtype=np.float32),   # Minimal Pulse
    2: np.array([80,  60,   0], dtype=np.float32),   # Bright Electronic
    3: np.array([30,   5,  10], dtype=np.float32),   # Dark Cinematic
    4: np.array([10,  15,  30], dtype=np.float32),   # Percussive Experimental
}

# Bright style accent colors (BGR) for particles
STYLE_COLOR = {
    0: np.array([80,  160, 255], dtype=np.float32),  # warm orange
    1: np.array([200, 200, 200], dtype=np.float32),  # cold white
    2: np.array([40,  230, 255], dtype=np.float32),  # electric yellow
    3: np.array([180,  60,  60], dtype=np.float32),  # dark red/purple
    4: np.array([255, 120,  40], dtype=np.float32),  # cyan-ish
}

# ---------------------------------------------------------------------------
# Shared live state
# ---------------------------------------------------------------------------

@dataclass
class LiveState:
    bpm: float = 120.0
    beat: int = 0
    state: str = "WAITING"
    tracking: bool = False
    energy: float = 0.5
    style_index: int = 0
    energy_floor: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock)


STATE = LiveState()
_prev_beat = -1  # used to detect beat changes in the render loop

# ---------------------------------------------------------------------------
# OSC handlers
# ---------------------------------------------------------------------------

def _h_bpm(address, *args):
    with STATE._lock:
        STATE.bpm = float(args[0]) if args else STATE.bpm

def _h_beat(address, *args):
    with STATE._lock:
        STATE.beat = int(args[0]) if args else STATE.beat

def _h_state(address, *args):
    with STATE._lock:
        STATE.state = str(args[0]) if args else STATE.state

def _h_tracking(address, *args):
    with STATE._lock:
        STATE.tracking = bool(args[0]) if args else STATE.tracking

def _h_energy(address, *args):
    with STATE._lock:
        STATE.energy = float(args[0]) if args else STATE.energy

def _h_style(address, *args):
    with STATE._lock:
        STATE.style_index = int(args[0]) if args else STATE.style_index

def _h_energy_floor(address, *args):
    with STATE._lock:
        STATE.energy_floor = float(args[0]) if args else STATE.energy_floor


def start_osc_servers():
    """Start OSC listener threads (daemon)."""
    def make_server(port, handlers):
        d = Dispatcher()
        for addr, fn in handlers:
            d.map(addr, fn)
        server = ThreadingOSCUDPServer(("0.0.0.0", port), d)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()

    make_server(9002, [
        ("/feedback/bpm",      _h_bpm),
        ("/feedback/beat",     _h_beat),
        ("/feedback/state",    _h_state),
        ("/feedback/tracking", _h_tracking),
    ])
    make_server(9003, [
        ("/conductor/energy", _h_energy),
        ("/conductor/style",  _h_style),
    ])
    make_server(9004, [
        ("/performer/energy_floor", _h_energy_floor),
    ])


# ---------------------------------------------------------------------------
# Particle system
# ---------------------------------------------------------------------------

class ParticleSystem:
    def __init__(self):
        n = MAX_PARTICLES
        self.px    = np.zeros(n, dtype=np.float32)
        self.py    = np.zeros(n, dtype=np.float32)
        self.vx    = np.zeros(n, dtype=np.float32)
        self.vy    = np.zeros(n, dtype=np.float32)
        self.life  = np.zeros(n, dtype=np.float32)
        self.decay = np.zeros(n, dtype=np.float32)
        self.size  = np.zeros(n, dtype=np.int32)
        self.r     = np.zeros(n, dtype=np.uint8)
        self.g     = np.zeros(n, dtype=np.uint8)
        self.b     = np.zeros(n, dtype=np.uint8)
        self.active = np.zeros(n, dtype=bool)

    # Return indices of up to `count` inactive slots
    def _free_slots(self, count):
        free = np.where(~self.active)[0]
        if len(free) == 0:
            return np.array([], dtype=np.int32)
        return free[:count]

    def beat_burst(self, energy: float, style_index: int):
        count = 20 + int(energy * 50)
        slots = self._free_slots(count)
        if len(slots) == 0:
            return
        n = len(slots)

        cx = WIDTH / 2.0
        cy = PARTICLE_Y0 + PARTICLE_H / 2.0

        # Random origin within 200px radius of center
        angles_orig = np.random.uniform(0, 2 * math.pi, n)
        radii_orig  = np.random.uniform(0, 200, n)
        self.px[slots] = cx + radii_orig * np.cos(angles_orig)
        self.py[slots] = cy + radii_orig * np.sin(angles_orig)

        # Radial outward velocity
        angles_vel = np.random.uniform(0, 2 * math.pi, n)
        speed = 1.5 + energy * 5
        self.vx[slots] = speed * np.cos(angles_vel)
        self.vy[slots] = speed * np.sin(angles_vel)

        self.life[slots]  = 1.0
        self.decay[slots] = (1.0 / 90.0) + np.random.rand(n) * (1.0 / 60.0)

        # Color: bright style color ± 40, clamped
        base = STYLE_COLOR[style_index % len(STYLE_COLOR)]
        noise = np.random.randint(-40, 41, (n, 3))
        colors = np.clip(base + noise, 0, 255).astype(np.uint8)
        self.b[slots] = colors[:, 0]
        self.g[slots] = colors[:, 1]
        self.r[slots] = colors[:, 2]

        self.size[slots] = 3 + int(energy * 5)
        self.active[slots] = True

    def ambient_spawn(self, active_state: bool, style_index: int):
        count = 2 if active_state else 1
        slots = self._free_slots(count)
        if len(slots) == 0:
            return
        n = len(slots)

        self.px[slots] = np.random.uniform(0, WIDTH, n)
        self.py[slots] = float(PARTICLE_Y1)  # bottom of particle field

        self.vy[slots] = -(0.5 + np.random.rand(n) * 1.5)
        self.vx[slots] = np.random.rand(n) * 0.4 - 0.2

        self.life[slots]  = 1.0
        self.decay[slots] = 1.0 / 150.0

        base = STYLE_COLOR[style_index % len(STYLE_COLOR)] * 0.4
        colors = np.clip(base, 0, 255).astype(np.uint8)
        self.b[slots] = colors[0]
        self.g[slots] = colors[1]
        self.r[slots] = colors[2]

        self.size[slots] = np.random.randint(2, 4, n)
        self.active[slots] = True

    def update(self):
        a = self.active
        if not np.any(a):
            return
        self.px[a] += self.vx[a]
        self.py[a] += self.vy[a]
        self.vx[a] *= 0.98
        self.vy[a] *= 0.98
        self.life[a] -= self.decay[a]

        # Deactivate out-of-bounds or dead
        oob = (
            (self.px < 0) | (self.px >= WIDTH) |
            (self.py < PARTICLE_Y0) | (self.py >= PARTICLE_Y1) |
            (self.life <= 0)
        )
        self.active[a & oob] = False

    def draw(self, canvas: np.ndarray):
        """Draw particles into the particle field rows of canvas."""
        # Particle layer (same size as particle region)
        layer = np.zeros((PARTICLE_H, WIDTH, 3), dtype=np.uint8)

        indices = np.where(self.active)[0]
        for i in indices:
            alpha = float(self.life[i])
            x = int(self.px[i])
            y = int(self.py[i]) - PARTICLE_Y0  # local coords
            if 0 <= y < PARTICLE_H and 0 <= x < WIDTH:
                color = (
                    int(self.b[i] * alpha),
                    int(self.g[i] * alpha),
                    int(self.r[i] * alpha),
                )
                cv2.circle(layer, (x, y), int(self.size[i]), color, -1)

        # Blend with existing particle bg in canvas
        bg_slice = canvas[PARTICLE_Y0:PARTICLE_Y1, :, :]
        blended = cv2.addWeighted(layer, 0.85, bg_slice, 1.0, 0)
        canvas[PARTICLE_Y0:PARTICLE_Y1, :, :] = blended


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------

def lerp_color(current: np.ndarray, target: np.ndarray, t: float) -> np.ndarray:
    return current + (target - current) * t


def draw_camera_strip(canvas: np.ndarray, musician_frame, conductor_frame,
                      tracking: bool):
    """Draw top strip with camera thumbnails."""
    # Background for top strip
    canvas[0:TOP_STRIP_H, :] = (20, 20, 25)

    border_color = (0, 200, 80) if tracking else (80, 80, 80)

    # --- Musician camera ---
    mx0 = 40
    _draw_thumb(canvas, musician_frame, mx0, 0, CAM_W, CAM_H,
                "MUSICIAN", border_color)

    # --- Conductor camera ---
    cx0 = 840
    _draw_thumb(canvas, conductor_frame, cx0, 0, CAM_W, CAM_H,
                "CONDUCTOR", border_color)


def _draw_thumb(canvas, frame, x0, y0, w, h, label, border_color):
    """Paste a camera thumbnail (or NO SIGNAL) at (x0, y0) with label."""
    if frame is not None:
        thumb = cv2.resize(frame, (w, h))
        canvas[y0:y0+h, x0:x0+w] = thumb
    else:
        canvas[y0:y0+h, x0:x0+w] = 0
        # NO SIGNAL text
        tx = x0 + w // 2 - 70
        ty = y0 + h // 2 + 10
        cv2.putText(canvas, "NO SIGNAL", (tx, ty),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (120, 120, 120), 2)

    # Border
    cv2.rectangle(canvas, (x0, y0), (x0 + w, y0 + h), border_color, 2)

    # Label below camera, inside top strip
    label_y = y0 + h + 18
    if label_y < TOP_STRIP_H:
        cv2.putText(canvas, label, (x0 + 10, label_y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)


def draw_bottom_bar(canvas: np.ndarray, state: LiveState):
    """Draw the bottom status bar."""
    bar = canvas[BAR_Y0:BAR_Y0 + BAR_H, :]
    bar[:] = (20, 15, 15)

    bpm   = state.bpm
    beat  = state.beat % 4
    st    = state.state
    energy = state.energy
    style_index = state.style_index
    ef    = state.energy_floor

    # -- Row 1 (y=40 from bar top) --
    r1y = 40

    # BPM
    cv2.putText(bar, f"BPM: {bpm:.1f}", (20, r1y),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 1)

    # Beat dots: 4 circles, evenly spaced starting at x=200
    dot_x_start = 200
    dot_spacing = 35
    dot_r = 12
    for i in range(4):
        cx = dot_x_start + i * dot_spacing
        color = (80, 220, 0) if i == beat else (60, 60, 60)
        cv2.circle(bar, (cx, r1y - dot_r // 2), dot_r, color, -1)

    # State label
    state_colors = {
        "ACTIVE":    (80, 220, 0),
        "HOLD":      (0, 220, 220),
        "WAITING":   (150, 150, 150),
        "EMERGENCY": (0, 0, 220),
    }
    sc = state_colors.get(st, (150, 150, 150))
    cv2.putText(bar, st, (370, r1y),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, sc, 2)

    # -- Row 2 (y=75) --
    r2y = 75

    # Energy bar
    cv2.putText(bar, "ENERGY", (20, r2y),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (180, 180, 180), 1)

    bar_x0 = 110
    bar_bw = 200
    bar_bh = 14
    bar_by = r2y - bar_bh + 2
    cv2.rectangle(bar, (bar_x0, bar_by),
                  (bar_x0 + bar_bw, bar_by + bar_bh), (50, 50, 50), -1)

    filled = int(energy * bar_bw)
    # Color: green → yellow → red
    if energy < 0.5:
        t = energy / 0.5
        ec = (int(0 + t * 0), int(200), int(220 * (1 - t)))
    else:
        t = (energy - 0.5) / 0.5
        ec = (int(t * 0), int(200 * (1 - t)), int(t * 220))
    # Actually use green→yellow→red properly in BGR:
    if energy < 0.5:
        t = energy * 2.0          # 0..1
        ec = (0, 200, int(255 * t))          # green→yellow (increase R)
    else:
        t = (energy - 0.5) * 2.0  # 0..1
        ec = (0, int(200 * (1 - t)), 255)    # yellow→red (decrease G)

    if filled > 0:
        cv2.rectangle(bar, (bar_x0, bar_by),
                      (bar_x0 + filled, bar_by + bar_bh), ec, -1)

    # Style name
    style_name = STYLE_NAMES.get(style_index % len(STYLE_NAMES), "Unknown")
    cv2.putText(bar, f"STYLE: {style_name}", (330, r2y),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (220, 200, 0), 1)

    # Musician pushing indicator
    if ef > 0.1:
        cv2.putText(bar, "^ musician pushing", (700, r2y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 140, 255), 1)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def open_camera(index):
    cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        print(f"[display] Camera {index} not available — showing NO SIGNAL.")
        return None
    return cap


def read_frame(cap):
    if cap is None:
        return None
    ret, frame = cap.read()
    return frame if ret else None


def main():
    parser = argparse.ArgumentParser(description="Accessible MRT2 — unified display")
    parser.add_argument("--musician-camera",  type=int, default=0)
    parser.add_argument("--conductor-camera", type=int, default=1)
    args = parser.parse_args()

    print("[display] Starting OSC servers …")
    start_osc_servers()

    print(f"[display] Opening cameras (musician={args.musician_camera}, "
          f"conductor={args.conductor_camera}) …")
    musician_cap  = open_camera(args.musician_camera)
    conductor_cap = open_camera(args.conductor_camera)

    particles = ParticleSystem()
    current_bg = STYLE_BG[0].copy()

    global _prev_beat
    _prev_beat = -1

    cv2.namedWindow("Accessible MRT2 — Live", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("Accessible MRT2 — Live", WIDTH, HEIGHT)

    print("[display] Running. Press 'q' to quit.")

    while True:
        # Snapshot state under lock
        with STATE._lock:
            bpm         = STATE.bpm
            beat        = STATE.beat
            app_state   = STATE.state
            tracking    = STATE.tracking
            energy      = STATE.energy
            style_index = STATE.style_index
            ef          = STATE.energy_floor

        # Detect beat change → burst
        beat_changed = (beat != _prev_beat) and (_prev_beat != -1)
        _prev_beat = beat

        # Build canvas
        canvas = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)

        # -- Background lerp for particle field --
        target_bg = STYLE_BG.get(style_index % len(STYLE_BG), STYLE_BG[0])
        current_bg = lerp_color(current_bg, target_bg, 0.02)
        bg_color = current_bg.astype(np.uint8)
        canvas[PARTICLE_Y0:PARTICLE_Y1, :] = bg_color  # fill particle bg

        # -- Particles --
        if beat_changed:
            particles.beat_burst(energy, style_index)
        particles.ambient_spawn(app_state == "ACTIVE", style_index)
        particles.update()
        particles.draw(canvas)

        # -- Camera thumbnails --
        musician_frame  = read_frame(musician_cap)
        conductor_frame = read_frame(conductor_cap)
        draw_camera_strip(canvas, musician_frame, conductor_frame, tracking)

        # -- Bottom bar --
        # Temporarily write state snapshot into a throwaway object for draw fn
        snap = LiveState(bpm=bpm, beat=beat, state=app_state,
                         tracking=tracking, energy=energy,
                         style_index=style_index, energy_floor=ef)
        draw_bottom_bar(canvas, snap)

        # -- Window title / separator lines --
        cv2.line(canvas, (0, TOP_STRIP_H), (WIDTH, TOP_STRIP_H), (40, 40, 40), 1)
        cv2.line(canvas, (0, PARTICLE_Y1), (WIDTH, PARTICLE_Y1), (40, 40, 40), 1)

        cv2.imshow("Accessible MRT2 — Live", canvas)

        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            break

    # Cleanup
    if musician_cap:
        musician_cap.release()
    if conductor_cap:
        conductor_cap.release()
    cv2.destroyAllWindows()
    print("[display] Bye.")


if __name__ == "__main__":
    main()
