#!/usr/bin/env python3
"""Compatibility launcher for the performer-led ensemble.

The old standalone MusicianClock implementation was removed so that starting
this file cannot create a second clock or let keyboard autocorrelation override
the performer's head-controlled BPM.
"""

from accessible_ensemble.performer import main


if __name__ == "__main__":
    main()
