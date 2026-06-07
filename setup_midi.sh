#!/bin/bash
# Creates a virtual MIDI bus so MRT2 Jam can receive from GestureInstrument.
#
# Option A (automatic): gesture_midi.py already creates a virtual port via
#   rtmidi.MidiOut().open_virtual_port("GestureInstrument")
#   Just run the Python script — no extra setup needed.
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
echo "Then in MRT2 Jam:"
echo "  Settings > MIDI Input > select 'GestureInstrument' or 'IAC Driver Bus 1'"
echo ""
echo "Or just run:  python gesture_midi.py"
echo "(The script opens its own virtual port automatically.)"
