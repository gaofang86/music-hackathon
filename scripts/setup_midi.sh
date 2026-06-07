#!/bin/bash
# Opens Audio MIDI Setup for the Reaper + MRT2 AU configuration.
#
# Option A (automatic): ensemble.py creates the GestureInstrument and
# MusicianClock virtual ports. No manual setup is normally needed.
#
# Option B (manual IAC bus): if you want a persistent IAC bus that survives
#   across app restarts, enable the IAC Driver in Audio MIDI Setup.

echo "Opening Audio MIDI Setup..."
osascript -e 'tell application "Audio MIDI Setup" to activate'

echo ""
echo "========================================================="
echo "  Manual IAC setup (only needed if virtual port fails)"
echo "========================================================="
echo "1. In Audio MIDI Setup, open the MIDI Studio window"
echo "   (Window > Show MIDI Studio, or Cmd+2)"
echo "2. Double-click 'IAC Driver'"
echo "3. Check 'Device is online'"
echo "4. Click '+' to add a bus named 'GestureInstrument'"
echo "5. Click Apply"
echo ""
echo "Then in Reaper, on the track containing Google: MRT2:"
echo "  Input: MIDI > GestureInstrument (or IAC Driver Bus 1) > All Channels"
echo "  Enable record monitoring."
echo ""
echo "Or just run:  python ensemble.py"
echo "(The ensemble opens GestureInstrument and MusicianClock automatically.)"
