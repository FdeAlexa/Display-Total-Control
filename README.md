# FdA Display Total Control

A 7-inch graphical display for Raspberry Pi running moOde Audio Player.

The program displays, in real time:

- VU meter
- Spectrum analyzer
- Oscilloscope
- Current song information
- Audio format / bitrate information
- System information
- Clock

The project uses the Raspberry Pi ALSA Loopback, CAVA and the moOde REST API.

---

# Prerequisites

The following components must be configured before installing the project.

## 1. Raspberry Pi display

A working 7-inch display with a resolution of:

`
800 × 480
`

The display must already be configured and working with moOde.

---

## 2. Python

Python 3 must be installed.

Create the Python virtual environment:

```bash
python3 -m venv ~/display-env
```

Install the required Python modules:

```bash
~/display-env/bin/python -m pip install numpy psutil pygame
```

The project uses the virtual environment:

`
/home/pi/display-env
`

---

## 3. ALSA Loopback

ALSA Loopback must be enabled in moOde.

**Menu → Configure → Audio → ALSA Options → Loopback → ON**

---

## 4. ALSA configuration

Create the ALSA configuration file:

```bash
cd /home/pi
cat > ~/.asoundrc <<'EOF'
pcm.condiviso {
    type dsnoop
    ipc_key 1024
    slave {
        pcm "hw:Loopback,1,0"
        channels 2
        format S16_LE
        rate 44100
        period_time 0
        period_size 1024
        buffer_size 4096
    }
}

pcm.plug_condiviso {
    type plug
    slave.pcm "condiviso"
}
EOF
```

---

## 5. CAVA

Install CAVA:

```bash
sudo apt update
sudo apt install cava
```

Create the directory used by the project:

```bash
mkdir -p ~/cava
```

Copy the CAVA executable to the location used by the project:

```bash
cp /usr/bin/cava ~/cava/cava
```

The project expects CAVA at:

`
/home/pi/cava/cava
`

---

## 6. moOde Metadata

Metadata must be enabled in moOde.

**Menu → Configure → Audio → MPD Options → General → Metadata file → ON**

---

# Project installation

## 7. Create the project directory

```bash
mkdir -p ~/FdA_DISPLAY
cd ~/FdA_DISPLAY
```

---

## 8. Download the project files

Download the Python program:

```bash
curl -O https://raw.githubusercontent.com/FdeAlexa/Display-Total-Control/main/display_total_control.py
```

Download the fonts:

```bash
curl -O https://raw.githubusercontent.com/FdeAlexa/Display-Total-Control/main/DejaVuSansMono.ttf
```

```bash
curl -O https://raw.githubusercontent.com/FdeAlexa/Display-Total-Control/main/ProggyTiny.ttf
```

The C&C Red Alert font contains spaces and special characters in its filename. Use:

```bash
curl -o 'C&C Red Alert [INET].ttf' 'https://raw.githubusercontent.com/FdeAlexa/Display-Total-Control/main/'C%26C%20Red%20Alert%20%5BINET%5D.ttf'
```

Download the font documentation and licenses:

```bash
curl -O https://raw.githubusercontent.com/FdeAlexa/Display-Total-Control/main/'README%20%5BINET%5D.txt'
```

```bash
curl -O https://raw.githubusercontent.com/FdeAlexa/Display-Total-Control/main/LICENSE-DejaVu.txt
```

```bash
curl -O https://raw.githubusercontent.com/FdeAlexa/Display-Total-Control/main/LICENSE-ProggyTiny.txt
```

Download the CAVA configuration:

```bash
curl -O https://raw.githubusercontent.com/FdeAlexa/Display-Total-Control/main/configstereo
```

Download the startup script:

```bash
curl -O https://raw.githubusercontent.com/FdeAlexa/Display-Total-Control/main/start_display.sh
```

Download the systemd service:

```bash
curl -O https://raw.githubusercontent.com/FdeAlexa/Display-Total-Control/main/FdA_Display_Total_Control.service
```

---

## 9. Make the startup script executable

```bash
chmod +x ~/FdA_DISPLAY/start_display.sh
```

---

# First manual test

## 10. Start the display manually

Before installing the systemd service, start the program manually:

```bash
cd ~/FdA_DISPLAY
DISPLAY=:0 ~/display-env/bin/python display_total_control.py
```

Stop the program with:

`
Ctrl+C
`

---

# Systemd service

## 11. Install the service

Copy the service file:

```bash
sudo cp ~/FdA_DISPLAY/FdA_Display_Total_Control.service /etc/systemd/system/FdA_Display_Total_Control.service
```

Reload systemd:

```bash
sudo systemctl daemon-reload
```

Enable automatic startup:

```bash
sudo systemctl enable FdA_Display_Total_Control.service
```

Start the service:

```bash
sudo systemctl start FdA_Display_Total_Control.service
```

---

## 12. Reboot

Reboot the Raspberry Pi:

```bash
sudo reboot
```

The display application will start automatically after moOde and the graphical environment are ready.

---

# Troubleshooting

## Service status

```bash
systemctl status FdA_Display_Total_Control.service
```

## Service log

```bash
journalctl -u FdA_Display_Total_Control.service -n 50 --no-pager
```

## ALSA audio capture

```bash
arecord -D plug_condiviso -f S16_LE -c 2 -r 44100 -d 5 -t raw /tmp/test_audio.raw
```

## moOde REST API

```bash
curl -s "http://localhost/command/?cmd=get_currentsong"
```

---

# Fonts and licenses

This repository contains three fonts used by the application.

## C&C Red Alert [INET]

The original `README [INET].txt` supplied with the font is included unchanged with the font.

The conditions contained in that README apply to the distribution of the font.

## DejaVu Sans Mono

The corresponding license text is included as:

```text
LICENSE-DejaVu.txt
```

## ProggyTiny

The corresponding MIT license text is included as:

```text
LICENSE-ProggyTiny.txt
```

Please read the supplied license and README files before redistributing or modifying the fonts.

---

# Project structure

```text
FdA_Display_Total_Control/
├── display_total_control.py
├── C&C Red Alert [INET].ttf
├── DejaVuSansMono.ttf
├── ProggyTiny.ttf
├── README [INET].txt
├── LICENSE-DejaVu.txt
├── LICENSE-ProggyTiny.txt
├── configstereo
├── start_display.sh
├── FdA_Display_Total_Control.service
└── README.md
```

`FdAfifo` is not included in the repository. It is a named pipe used by CAVA at runtime.

---

# Credits

Developed for Raspberry Pi / moOde Audio Player systems.

Thanks to the developers and maintainers of:

- moOde Audio Player
- CAVA
- Python
- Pygame
- ALSA
