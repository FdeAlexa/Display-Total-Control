#!/bin/bash

sleep 15
while ! DISPLAY=:0 xwininfo -root -tree 2>/dev/null | grep -q '"localhost" "Chromium-browser"'; do
    sleep 5
done
sleep 15
export DISPLAY=:0
exec runuser -u pi -- \
    /home/pi/display-env/bin/python /home/pi/FdA_DISPLAY/display_total_control.py
	