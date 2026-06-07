"""Camera discovery and role-based selection helpers."""

from __future__ import annotations

from dataclasses import dataclass
import sys

import cv2
import numpy as np


@dataclass(frozen=True)
class CameraCandidate:
    index: int
    width: int
    height: int
    opened_with_frame: bool

    def label(self) -> str:
        status = "video ok" if self.opened_with_frame else "opened"
        size = f"{self.width}x{self.height}" if self.width and self.height else "unknown size"
        return f"camera {self.index} ({size}, {status})"


def open_camera(index: int):
    cap = cv2.VideoCapture(index, cv2.CAP_AVFOUNDATION)
    if not cap.isOpened():
        cap.release()
        cap = cv2.VideoCapture(index)
    return cap


def discover_cameras(max_index: int = 8) -> list[CameraCandidate]:
    candidates = []
    for index in range(max_index):
        cap = open_camera(index)
        if not cap.isOpened():
            cap.release()
            continue
        ok, frame = cap.read()
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        if ok and frame is not None:
            height, width = frame.shape[:2]
        candidates.append(CameraCandidate(index, width, height, bool(ok)))
        cap.release()
    return candidates


def print_camera_list(candidates: list[CameraCandidate]) -> None:
    if not candidates:
        print("No cameras were detected.")
        return
    print("Available cameras:")
    for candidate in candidates:
        print(f"  {candidate.index}: {candidate.label()}")


def show_camera_previews(candidates: list[CameraCandidate], role: str) -> None:
    tiles = []
    for candidate in candidates:
        cap = open_camera(candidate.index)
        ok, frame = cap.read() if cap.isOpened() else (False, None)
        cap.release()
        if not ok or frame is None:
            frame = np.full((270, 480, 3), (35, 35, 35), dtype=np.uint8)
            cv2.putText(
                frame,
                "Preview unavailable",
                (95, 145),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (180, 180, 180),
                2,
            )
        else:
            frame = cv2.resize(frame, (480, 270))
        cv2.rectangle(frame, (0, 0), (480, 42), (0, 0, 0), -1)
        cv2.putText(
            frame,
            f"CAMERA {candidate.index}",
            (14, 29),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.75,
            (80, 240, 255),
            2,
        )
        tiles.append(frame)

    columns = min(2, len(tiles))
    rows = []
    for start in range(0, len(tiles), columns):
        row = tiles[start:start + columns]
        while len(row) < columns:
            row.append(np.zeros_like(tiles[0]))
        rows.append(np.hstack(row))
    preview = np.vstack(rows)
    window_name = f"Choose {role.title()} Camera - enter index in Terminal"
    cv2.imshow(window_name, preview)
    cv2.waitKey(1)


def resolve_camera(camera_arg: str | None, role: str, max_index: int = 8) -> int:
    if camera_arg is not None and camera_arg.lower() != "select":
        return int(camera_arg)

    candidates = discover_cameras(max_index)
    if not candidates:
        raise RuntimeError("no camera could be opened")

    print_camera_list(candidates)
    if len(candidates) == 1:
        chosen = candidates[0].index
        print(f"[CAMERA] Only one camera is available; using camera {chosen} for {role}.")
        return chosen

    if not sys.stdin.isatty():
        chosen = candidates[0].index
        print(f"[CAMERA] Non-interactive shell; using camera {chosen} for {role}.")
        return chosen

    preview_open = False
    try:
        show_camera_previews(candidates, role)
        preview_open = True
    except cv2.error:
        pass

    valid = {candidate.index for candidate in candidates}
    try:
        while True:
            answer = input(f"Choose the {role} camera index: ").strip()
            if not answer:
                continue
            try:
                chosen = int(answer)
            except ValueError:
                print("Please enter one of the listed camera numbers.")
                continue
            if chosen in valid:
                print(f"[CAMERA] Using camera {chosen} for {role}.")
                return chosen
            print("That camera number was not detected in this session.")
    finally:
        if preview_open:
            cv2.destroyWindow(
                f"Choose {role.title()} Camera - enter index in Terminal"
            )
