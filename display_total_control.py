#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# FdA_TOTAL_CONTROL_V17.py
#
#
# Contenitore Pygame 800x480 per:
#   - VumSpe   : pannelli superiori 320x240
#   - SCOPE    : pannelli inferiori 320x240
#   - Mus      : fascia centrale 160x480 (futura)
#   - Sin      : fascia centrale 160x480 (futura)
#   - TRACE    : storico VUM, fascia centrale 160x480 (futuro)
#
#
# Nota:
#   - SCOPE acquisisce direttamente il PCM da ALSA.
#   - La grafica è gestita interamente da Pygame.

import json
import math
import numpy as np
import os
import pygame
import psutil
import re
import select
import socket
import subprocess
import threading
import time
import urllib.request

# ============================================================
# CONFIGURAZIONE GENERALE
# ============================================================

RATE = 44100
CHANNELS = 2

# Dimensione blocco SCOPE già verificata nel MERGE.
READ_SAMPLES = 3072

# arecord: period-size già verificato nel MERGE.
ARECORD_PERIOD_SIZE = 1536

WIDTH = 800
HEIGHT = 480

PANEL_W = 320
PANEL_H = 240

LEFT_X = 0
RIGHT_X = 480
TOP_Y = 0

FPS = 60

# Aggiornamento dinamico visuale a circa 30 Hz.
DISPLAY_UPDATE_INTERVAL = 1.0 / 30.0
# Scroll MUS volutamente più lento del rendering generale.
MUS_SCROLL_INTERVAL = 0.03

MID_W = 160
MID_H = 120
OLED_W = 128
OLED_H = 64
MID_X = 320
MID_Y = 0
TIME_POS_X = MID_X
TIME_POS_Y = MID_Y + 2 * MID_H
TIME_FONT_PATH = "C&C Red Alert [INET].ttf"
TIME_ROW_IP = -1
TIME_ROW_HOST = 9
TIME_ROW_CLOCK = 26
TIME_ROW_DATE = 51

# ============================================================
# SYSINFO
# ============================================================

SYSINFO_REFRESH = 1.0

SYSINFO_ROW_TEMP = 0
SYSINFO_ROW_CPU = 11
SYSINFO_ROW_MEM = 22
SYSINFO_ROW_DISK = 33
SYSINFO_ROW_TIME = 44
SYSINFO_ROW_REL = 55

SYSINFO_BAR_X = 18
SYSINFO_BAR_W = 80
SYSINFO_BAR_H = 9
SYSINFO_BAR_BASE = 16

SYSINFO_TEMP_MIN = 30
SYSINFO_TEMP_MAX = 70

# ============================================================
# MUS / SCO
# ============================================================

MUS_REFRESH = 0.5
MUS_OVERHEAD = 0.0725
MUS_FFPROBE_TIMEOUT = 5

SCO_HISTORY_LEN = 128


# ============================================================
# STATO MOODE CONDIVISO
# ============================================================

def create_moode_state():
    """Crea lo stato cache di currentsong condiviso dal programma."""
    return {
        "timestamp": 0.0,
        "song": None,
        "stop_event": threading.Event(),
        "thread": None,
    }


def moode_api_worker(moode_state):
    """Aggiorna currentsong in background, senza bloccare il rendering."""
    while not moode_state["stop_event"].is_set():
        song = get_currentsong_api()
        if song:
            mpd_status = get_mpd_status()
            song["_mpd_audio"] = mpd_status.get("audio", "")
            song["_mpd_bitrate"] = mpd_status.get("bitrate", "")

        moode_state["song"] = song
        moode_state["timestamp"] = time.monotonic()
        moode_state["stop_event"].wait(PLAY_POLL_INTERVAL)


def start_moode_api_worker(moode_state):
    """Avvia il worker API di moOde."""
    moode_state["stop_event"].clear()
    moode_state["thread"] = threading.Thread(
        target=moode_api_worker,
        args=(moode_state,),
        daemon=True,
    )
    moode_state["thread"].start()


def stop_moode_api_worker(moode_state):
    """Ferma il worker API di moOde."""
    if moode_state is None:
        return
    moode_state["stop_event"].set()
    thread = moode_state.get("thread")
    if thread is not None and thread.is_alive():
        thread.join(timeout=1.0)
    moode_state["thread"] = None


def update_moode_state(moode_state, now):
    """Restituisce lo snapshot currentsong già aggiornato dal worker."""
    return moode_state["song"]


# ============================================================
# MUS / STREAM INFO
# ============================================================

def get_mpd_status():
    """Legge i dati audio correnti direttamente da MPD."""
    try:
        with socket.create_connection(("localhost", 6600), timeout=1) as sock:
            sock.recv(1024)
            sock.sendall(b"status\n")
            data = b""
            while b"\nOK\n" not in data:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk

        status = {}
        for line in data.decode("utf-8", errors="replace").splitlines():
            if ": " in line:
                key, value = line.split(": ", 1)
                status[key] = value
        return status
    except (OSError, UnicodeError):
        return {}


def get_currentsong_api():
    """Legge currentsong tramite l'API locale di moOde."""
    try:
        url = "http://localhost/command/?cmd=get_currentsong"
        with urllib.request.urlopen(url, timeout=3) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def is_song_playing(song):
    """Determina PLAY usando i dati di currentsong."""
    if not song:
        return False

    file_value = song.get("file", "")
    state = song.get("state", "")
    outrate = song.get("outrate", "")

    if file_value == "Spotify Active":
        return outrate != "Not playing"

    return state == "play" and outrate != "Not playing"


def mus_is_playing(song):
    """Determina PLAY usando currentsong, inclusa l'eccezione Spotify."""
    return is_song_playing(song)


def get_stream_info(url):
    """ffprobe una sola volta per ogni nuovo URL."""
    if not url or url == "Spotify Active":
        return None

    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "a:0",
        "-show_entries",
        "stream=codec_name,sample_rate,bits_per_raw_sample,channels,bit_rate",
        "-of", "json",
        url,
    ]

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=MUS_FFPROBE_TIMEOUT,
        )
        data = json.loads(result.stdout)
        if not data.get("streams"):
            return None

        stream = data["streams"][0]
        sample_rate = stream.get("sample_rate")
        try:
            sample_rate = int(sample_rate)
        except (TypeError, ValueError):
            sample_rate = 0

        bit_rate = stream.get("bit_rate")
        try:
            bit_rate = int(bit_rate)
        except (TypeError, ValueError):
            bit_rate = 0

        return {
            "codec": str(stream.get("codec_name", "N.A.")).upper(),
            "sample_rate": sample_rate,
            "bits": stream.get("bits_per_raw_sample", "N.A."),
            "channels": stream.get("channels", "N.A."),
            "bit_rate": bit_rate,
        }

    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None


def get_interface():
    """Restituisce l'interfaccia usata dalla default route."""
    try:
        with open("/proc/net/route") as f:
            for line in f:
                parts = line.split()
                if len(parts) > 1 and parts[1] == "00000000":
                    return parts[0]
    except OSError:
        pass
    return "eth0"


def get_rx_bytes(interface):
    """Byte ricevuti dall'interfaccia."""
    try:
        with open("/proc/net/dev") as f:
            for line in f:
                if line.strip().startswith(interface + ":"):
                    return int(line.split(":")[1].split()[0])
    except (OSError, ValueError, IndexError):
        pass
    return 0


def get_encoded_bits(song):
    """Ricava la profondità bit da encoded, solo come fallback."""
    encoded = song.get("encoded", "")
    match = re.search(r"\b(\d+)\s*/\s*[\d.]+\s*kHz", encoded, re.IGNORECASE)
    if match:
        return match.group(1)
    return "N.A."


def format_mus_in(song, stream, measured_kbps):
    """Formatta la riga IN del francobollo Mus."""
    if stream:
        codec = stream["codec"]
        if codec == "FLAC":
            codec = "FLA"
        bits = stream["bits"]
        if str(bits).upper() in ("N.A.", "N/A", "NONE", ""):
            audio = song.get("_mpd_audio", "")
            parts = audio.split(":")
            if len(parts) >= 2 and parts[1].isdigit():
                bits = parts[1]
        rate = stream["sample_rate"]
        channels = stream["channels"]
        if rate:
            rate_text = f"{rate/1000:.1f}".replace(".0", "") + "k"
        else:
            rate_text = "?k"
        # Il bitrate misurato è quello più utile per gli stream compressi.
        if measured_kbps > 0:
            return f"{codec} {bits}b {rate_text} {measured_kbps:.0f}k"
        declared = stream.get("bit_rate", 0)
        if declared:
            return f"{codec} {bits}b {rate_text} {declared/1000:.0f}k"
        return f"{codec} {bits}b {rate_text}"

    encoded = song.get("encoded", "")
    bitrate = song.get("bitrate", "")
    if encoded == "FLAC":
        encoded = "FLA"
    return f"{encoded} {bitrate}".strip()


def format_mus_out(song):
    """Formatta la riga OUT, mantenendo il formato del vecchio Musinfo."""
    value = song.get("outrate", "N.A.")
    if not value or value == "Not playing":
        return "N.A."
    return (
        value
        .replace("Hz", "")
        .replace(",", "")
        .replace("/", "b ")
        .replace(" k", "k")
    )


def create_mus_state():
    """Crea lo stato condiviso del monitor Mus."""
    return {
        "lock": threading.Lock(),
        "data": {
            "playing": False,
            "artist": "Artist N.A.",
            "album": "Album N.A.",
            "title": "Title N.A.",
            "in": "N.A.",
            "out": "N.A.",
            "url": "",
        },
        "url": None,
        "stream": None,
        "interface": get_interface(),
        "old_rx": 0,
        "old_time": time.perf_counter(),
        "avg_bits": 0.0,
        "readings": 0,
        "stop_event": threading.Event(),
        "thread": None,
    }


def start_mus_state(state, moode_state):
    """Avvia il worker Mus."""
    state["url"] = None
    state["avg_bits"] = 0.0
    state["readings"] = 0
    state["old_rx"] = get_rx_bytes(state["interface"])
    state["old_time"] = time.perf_counter()
    state["thread"] = threading.Thread(
        target=mus_worker,
        args=(state, moode_state),
        daemon=True,
    )
    state["thread"].start()


def stop_mus_state(state):
    """Ferma il worker Mus."""
    if state is None:
        return
    state["stop_event"].set()
    thread = state.get("thread")
    if thread is not None and thread.is_alive():
        thread.join(timeout=1.0)


def snapshot_mus_state(state):
    """Restituisce una copia consistente dello stato Mus."""
    with state["lock"]:
        return dict(state["data"])


def update_mus_measurement(state):
    """Aggiorna la misura RX e restituisce il bitrate medio in kbps."""
    now = time.perf_counter()
    rx = get_rx_bytes(state["interface"])

    elapsed = now - state["old_time"]
    if elapsed <= 0:
        elapsed = MUS_REFRESH

    delta = max(0, rx - state["old_rx"])
    bits_per_sec = delta * 8 / elapsed * (1.0 - MUS_OVERHEAD)

    state["avg_bits"] += bits_per_sec
    state["readings"] += 1
    state["old_rx"] = rx
    state["old_time"] = now

    return (state["avg_bits"] / state["readings"]) / 1000.0


def mus_worker(state, moode_state):
    """Polling Mus: stato moOde in cache e ffprobe solo su nuovo URL."""
    while not state["stop_event"].is_set():
        # La cache viene aggiornata dal main loop; Mus non effettua
        # una seconda interrogazione dell'API moOde.
        song = moode_state["song"]

        if song:
            playing = mus_is_playing(song)
            url = song.get("file", "")

            if url != state["url"]:
                state["url"] = url
                state["stream"] = None
                state["avg_bits"] = 0.0
                state["readings"] = 0
                state["old_rx"] = get_rx_bytes(state["interface"])
                state["old_time"] = time.perf_counter()

                if playing:
                    state["stream"] = get_stream_info(url)

            measured_kbps = (
                update_mus_measurement(state)
                if playing else 0.0
            )

            in_text = format_mus_in(
                song,
                state["stream"],
                measured_kbps,
            )
            out_text = format_mus_out(song)

            with state["lock"]:
                state["data"] = {
                    "playing": playing,
                    "artist": song.get("artist", "Artist N.A."),
                    "album": song.get("album", "Album N.A."),
                    "title": song.get("title", "Title N.A."),
                    "in": in_text,
                    "out": out_text,
                    "url": url,
                }

        state["stop_event"].wait(MUS_REFRESH)


# ============================================================
# GRAFICA SCO
# ============================================================

def build_sco_surface(
    font_small,
    spectrum,
    left_value,
    right_value,
    left_history,
    right_history,
):
    """Piccolo Scope OLED 128x64: storico a sinistra, nuovo valore a destra."""
    surface = pygame.Surface((OLED_W, OLED_H))
    surface.fill(BLACK)

    # ------------------------------------------------------------
    # STORICO
    #
    # I valori più vecchi sono a sinistra; il campione più recente
    # entra sempre dalla colonna più a destra.
    # La linea dello zero rimane una fascia nera di 2 px.
    # ------------------------------------------------------------
    zero_top = 31
    zero_bottom = 32
    sco_half_height = 30

    for x in range(SCO_HISTORY_LEN):
        lh = min(
            sco_half_height,
            int(left_history[x] / 100.0 * sco_half_height),
        )
        rh = min(
            sco_half_height,
            int(right_history[x] / 100.0 * sco_half_height),
        )

        pygame.draw.line(
            surface,
            WHITE,
            (x, zero_top - lh),
            (x, zero_top - 1),
        )

        pygame.draw.line(
            surface,
            WHITE,
            (x, zero_bottom + 1),
            (x, zero_bottom + rh),
        )

    pygame.draw.rect(
        surface,
        BLACK,
        (0, zero_top, OLED_W, 2),
    )

    return surface


def update_sco_history(left_history, right_history, left_value, right_value):
    """Fa scorrere lo storico a sinistra e inserisce il nuovo campione a destra."""
    left_history[:-1] = left_history[1:]
    right_history[:-1] = right_history[1:]
    left_history[-1] = float(left_value)
    right_history[-1] = float(right_value)


# ============================================================
# RENDER MUS
# ============================================================

def draw_scroll_text(surface, font, text, y, scroll_state):
    """Testo centrato oppure scroll continuo solo verso sinistra."""
    if not text:
        return {"offset": 0.0, "pause": 0}

    rendered = font.render(text, True, WHITE)
    text_width = rendered.get_width()

    if text_width <= OLED_W:
        surface.blit(
            rendered,
            ((OLED_W - text_width) // 2, y),
        )
        return {"offset": 0.0, "pause": 0}

    offset = float(scroll_state.get("offset", 0.0))
    pause = int(scroll_state.get("pause", 0))
    max_offset = float(text_width - OLED_W)

    if pause > 0:
        pause -= 1
    else:
        offset += 1.0
        if offset >= max_offset:
            offset = max_offset
            pause = 30

    surface.blit(rendered, (-int(offset), y))

    # Dopo la pausa finale ricomincia dall'inizio: nessun movimento verso destra.
    if pause == 1 and offset >= max_offset:
        return {"offset": 0.0, "pause": 30}

    return {"offset": offset, "pause": pause}


def build_mus_surface(font_small, font_scroll, state, scroll_state):
    """Costruisce il francobollo Mus 128x64."""
    surface = pygame.Surface((OLED_W, OLED_H))
    surface.fill(BLACK)

    if not state["playing"]:
        return surface

    scroll_state["artist"] = draw_scroll_text(
#        surface, font_small, state["artist"], 0, scroll_state["artist"]
        surface, font_scroll, state["artist"], 0, scroll_state["artist"]
    )
    scroll_state["album"] = draw_scroll_text(
#        surface, font_small, state["album"], 13, scroll_state["album"]
        surface, font_scroll, state["album"], 13, scroll_state["album"]
    )
    scroll_state["title"] = draw_scroll_text(
#        surface, font_small, state["title"], 26, scroll_state["title"]
        surface, font_scroll, state["title"], 26, scroll_state["title"]
    )

    surface.blit(
        font_small.render("IN", True, WHITE),
        (0, 41),
    )
    surface.blit(
        font_small.render(state["in"], True, WHITE),
        (14, 41),
    )

    surface.blit(
        font_small.render("OUT", True, WHITE),
        (0, 54),
    )
    surface.blit(
        font_small.render(state["out"], True, WHITE),
        (20, 54),
    )

    return surface


# ============================================================
# TIME
# ============================================================

def build_time_surface(font_std, font_time):
    """Disegna l'OLED virtuale 128x64 della schermata Time."""
    surface = pygame.Surface((OLED_W, OLED_H))
    surface.fill(BLACK)

    hostname = subprocess.check_output(["hostname"], text=True).strip()
    ip_addr = subprocess.check_output(["hostname", "-I"], text=True).split()[0]
    now = time.strftime("%H:%M:%S")
    day = time.strftime("%a %d %b %Y")

    texts = [
        (font_std, ip_addr, TIME_ROW_IP),
        (font_std, hostname, TIME_ROW_HOST),
        (font_time, now, TIME_ROW_CLOCK),
        (font_std, day, TIME_ROW_DATE),
    ]

    for font, value, y in texts:
        rendered = font.render(value, True, WHITE)
        x = int((OLED_W - rendered.get_width()) / 2)
        surface.blit(rendered, (x, y))

    return surface


def draw_time(screen, time_surface):
    """Centra l'OLED 128x64 all'interno di mid2 160x120."""
    x = TIME_POS_X + (MID_W - OLED_W) // 2
    y = TIME_POS_Y + (MID_H - OLED_H) // 2
    screen.blit(time_surface, (x, y))


# ============================================================
# SYSINFO
# ============================================================

def bytes2human(value):
    """Converte i byte in una rappresentazione compatta."""
    symbols = ("K", "M", "G", "T", "P", "E", "Z", "Y")

    for symbol in reversed(symbols):
        limit = 1 << ((symbols.index(symbol) + 1) * 10)

        if value >= limit:
            return f"{int(value / limit)}{symbol}"

    return f"{value}B"


def get_temperature():
    """Restituisce la temperatura CPU come valore numerico."""
    try:
        output = subprocess.check_output(
            ["vcgencmd", "measure_temp"],
            text=True,
        )

        value = output.split("=")[1].split("'")[0]
        return float(value)

    except (OSError, ValueError, IndexError):
        return None


def get_disk_usage():
    """Restituisce spazio usato e percentuale della root."""
    try:
        usage = psutil.disk_usage("/")
        return bytes2human(usage.used), usage.percent
    except OSError:
        return "--", 0


def get_moode_release():
    """Legge una sola volta la versione/release di moOde."""
    try:
        result = subprocess.run(
            ["moodeutl", "-l"],
            capture_output=True,
            text=True,
            check=True,
        )
        for line in result.stdout.splitlines():
            if "release" in line.lower():
                words = line.split()
                if len(words) >= 2:
                    return f"moOde: {words[-2]} {words[-1]}"
    except (OSError, subprocess.SubprocessError):
        pass
    return "moOde: unknown"


def get_sysinfo_data(moode_release):
    """Raccoglie i dati visualizzati dal Sysinfo OLED."""
    temperature = get_temperature()
    cpu = psutil.cpu_percent(interval=None)

    memory = psutil.virtual_memory()
    mem_used = bytes2human(memory.used)
    mem_percent = memory.percent

    disk_used, disk_percent = get_disk_usage()

    uptime_seconds = int(time.time() - psutil.boot_time())
    days, rem = divmod(uptime_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    uptime_text = f"{days}d {hours:02d}h {minutes:02d}m {seconds:02d}s"


    if temperature is None:
        temperature_text = "--C"
        temperature_value = SYSINFO_TEMP_MIN
    else:
        temperature_text = f"{temperature:.0f}C"
        temperature_value = temperature

    return {
        "temperature": temperature_text,
        "temperature_value": temperature_value,
        "cpu": cpu,
        "mem_used": mem_used,
        "mem_percent": mem_percent,
        "disk_used": disk_used,
        "disk_percent": disk_percent,
        "uptime": uptime_text,
        "release": moode_release
    }


def bar_width(value, vmin, vmax):
    """Calcola la fine della barra nel sistema di coordinate OLED."""
    value = max(vmin, min(value, vmax))
    percent = (value - vmin) * 100 / (vmax - vmin)

    return int(
        percent * SYSINFO_BAR_W / 100
        + SYSINFO_BAR_BASE
    )


def build_sysinfo_surface(font_small, moode_release):
    """
    Costruisce l'OLED virtuale 128x64 di Sysinfo.

    È indipendente dal PLAY: può quindi essere visualizzato
    continuamente anche quando l'audio è fermo.
    """
    surface = pygame.Surface((OLED_W, OLED_H))
    surface.fill(BLACK)

    data = get_sysinfo_data(moode_release)

    rows = [
        (
            SYSINFO_ROW_TEMP,
            "Te:",
            bar_width(
                data["temperature_value"],
                SYSINFO_TEMP_MIN,
                SYSINFO_TEMP_MAX,
            ),
            data["temperature"],
        ),
        (
            SYSINFO_ROW_CPU,
            "Ld:",
            bar_width(
                data["cpu"],
                0,
                100,
            ),
            f"{data['cpu']:.0f}%",
        ),
        (
            SYSINFO_ROW_MEM,
            "Me:",
            bar_width(
                data["mem_percent"],
                0,
                100,
            ),
            data["mem_used"],
        ),
        (
            SYSINFO_ROW_DISK,
            "SD:",
            bar_width(
                data["disk_percent"],
                0,
                100,
            ),
            data["disk_used"],
        ),
    ]

    for row_y, label, width, value in rows:
        surface.blit(
            font_small.render(label, True, WHITE),
            (0, row_y - 2),
        )

        pygame.draw.rect(
            surface,
            WHITE,
            (
                SYSINFO_BAR_X,
                row_y,
                SYSINFO_BAR_W,
                SYSINFO_BAR_H,
            ),
            1,
        )

        pygame.draw.rect(
            surface,
            WHITE,
            (
                SYSINFO_BAR_X,
                row_y,
                max(
                    0,
                    min(
                        SYSINFO_BAR_W,
                        width - SYSINFO_BAR_X,
                    ),
                ),
                SYSINFO_BAR_H,
            ),
        )

        rendered_value = font_small.render(
            value,
            True,
            WHITE,
        )

        surface.blit(
            rendered_value,
            (
                OLED_W - rendered_value.get_width(),
                row_y - 2,
            ),
        )

    surface.blit(
        font_small.render(
            "TimeUP:",
            True,
            WHITE,
        ),
        (0, SYSINFO_ROW_TIME - 2),
    )

    surface.blit(
        font_small.render(
            data["uptime"],
            True,
            WHITE,
        ),
        (47, SYSINFO_ROW_TIME - 2),
    )

    surface.blit(
        font_small.render(
            data["release"],
            True,
            WHITE,
        ),
        (0, SYSINFO_ROW_REL - 2),
    )

    return surface


def draw_mid_oled(screen, oled_surface, slot):
    """Centra un OLED 128x64 nel relativo slot 160x120."""
    x = MID_X + (MID_W - OLED_W) // 2
    y = MID_Y + slot * MID_H + (MID_H - OLED_H) // 2

    screen.blit(
        oled_surface,
        (x, y),
    )


# ============================================================
# STATO PLAY / NO PLAY
# ============================================================

PLAY_POLL_INTERVAL = 0.50
PLAY_CONFIRM_COUNT = 2

# Attesa dopo la conferma di PLAY prima di toccare ALSA.
# Partiamo volutamente da 3 secondi per lasciare a moOde
# il tempo di completare l'apertura del percorso audio.
PLAY_AUDIO_DELAY = 3.0



# ============================================================
# COLORI
# ============================================================

BLACK = "Black"
WHITE = "White"

GRAY15 = "Gray15"
GRAY50 = "Gray50"
GRAY80 = "Gray80"

TRACE_LEFT = "RoyalBlue1"
TRACE_RIGHT = "MediumOrchid2"


GLOW_GREEN = "Yellow"
GLOW_RED = "Orange"


# ============================================================
# CONFIGURAZIONE VUM / SPE
# ============================================================

CAVA_PATH = "/home/pi/cava/cava"
CAVA_FIFO = "/home/pi/FdA_DISPLAY/FdAfifo"
CAVA_CONFIG = "/home/pi/FdA_DISPLAY/configstereo"


# Peak Level
PEAK_LEVEL_POS = (160, 3)

PEAK_CONTOUR_BBOX = (-50, 9, 420, 125)
PEAK_CONTOUR_START = 42
PEAK_CONTOUR_END = 140
PEAK_CONTOUR_WIDTH = 8

PEAK_BACKGROUND_BBOX = (-50, 10, 420, 125)
PEAK_BACKGROUND_START = 43
PEAK_BACKGROUND_END = 139
PEAK_BACKGROUND_WIDTH = 6

PEAK_DYNAMIC_BBOX = (-50, 11, 420, 125)
PEAK_DYNAMIC_LEFT = 138.0
PEAK_DYNAMIC_RIGHT = 44.0
PEAK_DYNAMIC_WIDTH = 4

# Delimitatore VU Meter / CHANNEL
VU_DELIMITER_BBOX = (-50, 44, 420, 125)
VU_DELIMITER_START = 46
VU_DELIMITER_END = 136
VU_DELIMITER_WIDTH = 2

# Geometria VU Meter
VUM_ARC_CX = 160
VUM_ARC_CY = 620
VUM_ARC_RADIUS = 600
VUM_ARC_START = -105
VUM_ARC_END = -75

VUM_PEAK_DECAY = 0.6

# Spettro
SPECTRUM_BAR_SCALE = 1.28
SPECTRUM_BAR_TOP = 85
SPECTRUM_BAR_BASE = 213


# ============================================================
# STATO
# ============================================================

lvp = 0.0
rvp = 0.0
Valop = [0.0] * 64


# ============================================================
# STATO PLAY / NO PLAY
# ============================================================

def create_play_state():
    """Crea lo stato del controllo PLAY/NO PLAY."""
    return {
        "playing": False,
        "candidate": False,
        "count": 0,
        "status": {
            "playing": False,
            "state": "",
            "outrate": "",
        },
    }


def update_play_state(play_state, song):
    """Aggiorna PLAY/NO PLAY con conferma su più letture."""
    if not song:
        play_state["status"] = {
            "playing": False,
            "state": "",
            "outrate": "",
        }
    else:
        state = song.get("state", "")
        outrate = song.get("outrate", "")

        play_state["status"] = {
            "playing": is_song_playing(song),
            "state": state,
            "outrate": outrate,
        }

    candidate = play_state["status"]["playing"]

    if candidate == play_state["candidate"]:
        play_state["count"] += 1
    else:
        play_state["candidate"] = candidate
        play_state["count"] = 1

    if (
        play_state["count"] >= PLAY_CONFIRM_COUNT
        and candidate != play_state["playing"]
    ):
        play_state["playing"] = candidate

    return play_state["playing"]


# ============================================================
# ACQUISIZIONE AUDIO SCOPE
# ============================================================

def start_audio_capture():
    """Avvia arecord sul secondo loopback, LoopB."""
    print("Avvia arecord sul loopback")
    cmd = [
        "arecord",
#        "-D", "plughw:LoopB,1",
#        "-D", "plughw:Loopback,1,0",
        "-D", "plug_condiviso",
        "-f", "S16_LE",
        "-c", str(CHANNELS),
        "-r", str(RATE),
        "--period-size=" + str(ARECORD_PERIOD_SIZE),
        "-t", "raw",
    ]

    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        bufsize=0,
    )


def read_audio_block(proc):
    """Legge un blocco completo di campioni stereo."""
    if proc is None or proc.stdout is None:
        return None

    raw = proc.stdout.read(READ_SAMPLES * CHANNELS * 2)

    if not raw or len(raw) < CHANNELS * 2:
        return None

    frame_bytes = CHANNELS * 2
    nbytes = len(raw) - (len(raw) % frame_bytes)

    samples = np.frombuffer(
        raw[:nbytes],
        dtype="<i2",
    ).reshape(-1, CHANNELS)

    if len(samples) == 0:
        return None

    return samples
 
 
def create_audio_state():
    """Crea lo stato del worker PCM dello SCOPE."""
    return {
        "proc": None,
        "thread": None,
        "stop_event": threading.Event(),
        "lock": threading.Lock(),
        "samples": None,
    }


def audio_worker(state):
    """Acquisisce PCM in background senza bloccare il rendering."""
    while not state["stop_event"].is_set():
        proc = state["proc"]

        if proc is None:
            time.sleep(0.01)
            continue

        samples = read_audio_block(proc)

        if samples is None:
            if state["stop_event"].is_set():
                break
            continue

        with state["lock"]:
            state["samples"] = samples


def start_audio_worker(state, proc):
    """Associa arecord allo worker PCM."""
    state["proc"] = proc
    state["samples"] = None
    state["stop_event"].clear()
    state["thread"] = threading.Thread(
        target=audio_worker,
        args=(state,),
        daemon=True,
    )
    state["thread"].start()


def stop_audio_worker(state):
    """Ferma il worker PCM prima di chiudere arecord."""
    if state is None:
        return

    state["stop_event"].set()

    proc = state.get("proc")
    if proc is not None:
        try:
            proc.stdout.close()
        except (OSError, AttributeError):
            pass

    thread = state.get("thread")
    if thread is not None and thread.is_alive():
        thread.join(timeout=1.0)

    state["thread"] = None
    state["proc"] = None
    with state["lock"]:
        state["samples"] = None


def get_audio_samples(state):
    """Restituisce l'ultimo blocco PCM disponibile."""
    if state is None:
        return None

    with state["lock"]:
        samples = state["samples"]

    return samples


# ============================================================
# DISPLAY / PYGAME
# ============================================================

def init_display():
    """Inizializza solo display e font, senza audio Pygame."""
    pygame.display.init()
    pygame.font.init()
    screen = pygame.display.set_mode((WIDTH, HEIGHT))
    pygame.display.set_caption("FdA DISPLAY")

    font = pygame.font.Font(None, 24)
    clock = pygame.time.Clock()

    return screen, font, clock


# ============================================================
# LAYOUT SCOPE
# ============================================================

def get_layout():
    """Definisce la geometria dei due pannelli SCOPE inferiori."""
    top0 = 1
    top1 = HEIGHT // 2 + 1

    bot0 = HEIGHT // 2 - 1
    bot1 = HEIGHT - 1

    mid_top = (top0 + top1) // 2
    mid_bot = (bot0 + bot1) // 2

    lef0 = 1
    lef1 = WIDTH - PANEL_W + 1

    rig0 = PANEL_W - 1
    rig1 = WIDTH - 1

    return {
        "top0": top0,
        "top1": top1,
        "bot0": bot0,
        "bot1": bot1,
        "mid_top": mid_top,
        "mid_bot": mid_bot,
        "lef0": lef0,
        "lef1": lef1,
        "rig0": rig0,
        "rig1": rig1,
    }


# ============================================================
# GRAFICA SCOPE
# ============================================================

def draw_grid(screen, layout):
    """Disegna la griglia dei due pannelli SCOPE."""
    top1 = layout["top1"]
    bot1 = layout["bot1"]
    mid_bot = layout["mid_bot"]

    lef0 = layout["lef0"]
    lef1 = layout["lef1"]
    rig0 = layout["rig0"]
    rig1 = layout["rig1"]

    for i in range(11):
        x = int(i / 10 * (rig0 - 1))

        pygame.draw.line(
            screen,
            GRAY15,
            (x, top1),
            (x, bot1),
        )

        pygame.draw.line(
            screen,
            GRAY15,
            (x + lef1, top1),
            (x + lef1, bot1),
        )

    # Asse zero LEFT
    pygame.draw.line(
        screen,
        GRAY50,
        (lef0, mid_bot),
        (rig0, mid_bot),
    )

    # Asse zero RIGHT
    pygame.draw.line(
        screen,
        GRAY50,
        (lef1, mid_bot),
        (rig1, mid_bot),
    )


def draw_trace(screen, samples, layout):
    """Disegna l'ultimo blocco reale LEFT / RIGHT."""
    left = samples[:, 0]
    right = samples[:, 1]
    n = len(samples)

    lef0 = layout["lef0"]
    lef1 = layout["lef1"]
    rig0 = layout["rig0"]
    rig1 = layout["rig1"]

    mid_bot = layout["mid_bot"]
    top1 = layout["top1"]
    bot1 = layout["bot1"]

    x_left = np.linspace(lef0, rig0, n).astype(np.int32)
    x_right = np.linspace(lef1, rig1, n).astype(np.int32)

    scale = ((bot1 - top1) // 2) - 5

    y_left = (
        mid_bot
        - left.astype(np.float64) / 32768.0 * scale
    ).astype(np.int32)

    y_right = (
        mid_bot
        - right.astype(np.float64) / 32768.0 * scale
    ).astype(np.int32)

    pts_left = list(zip(x_left, y_left))
    pts_right = list(zip(x_right, y_right))

    if len(pts_left) > 1:
        pygame.draw.lines(
            screen,
            TRACE_LEFT,
            False,
            pts_left,
            2,
        )

        pygame.draw.lines(
            screen,
            TRACE_RIGHT,
            False,
            pts_right,
            2,
        )

    return left, right


def draw_trace_info(screen, font, left, right, layout):
    """Visualizza i peak dei due canali SCOPE."""
    peak_l = int(np.max(np.abs(left)))
    peak_r = int(np.max(np.abs(right)))

    lef0 = layout["lef0"]
    rig1 = layout["rig1"]
    bot1 = layout["bot1"]

    screen.blit(
        font.render(
            f"peak={peak_l:5d}",
            True,
            TRACE_LEFT,
        ),
        (lef0 + 2, bot1 - 15),
    )

    screen.blit(
        font.render(
            f"peak={peak_r:5d}",
            True,
            TRACE_RIGHT,
        ),
        (rig1 - 98, bot1 - 15),
    )


# ============================================================
# ACQUISIZIONE VUM / SPE
# ============================================================

# VUM e storico SCOPE usano direttamente il peak PCM
# ricavato dal blocco acquisito da arecord.
# Il FIFO PeppyMeter non è più necessario.

# ============================================================
# ACQUISIZIONE CAVA
# ============================================================

def read_cava_fifo():
    """Legge due record CAVA e utilizza soltanto il secondo."""
    try:
        with open(CAVA_FIFO, "r") as f:
            f.readline()
            line = f.readline()
    except OSError:
        return [0] * 64

    values = line.strip().split(";")

    if values and values[-1] == "":
        values.pop()

    if len(values) != 64:
        return [0] * 64

    if not all(value.isdigit() for value in values):
        return [0] * 64

    return [int(value) for value in values]


# ============================================================
# GRAFICA VUM
# ============================================================

def draw_vu_arc_led(
    surface,
    value,
    cx,
    cy,
    radius,
    start_angle,
    end_angle,
):
    """Disegna i 40 segmenti LED del VUM."""
    db_min = -20
    db_max = 3

    db = (
        (value / 100.0) * (db_max - db_min)
        + db_min
    )

    segments = 40
    led_len = 18
    led_width = 4

    step = (end_angle - start_angle) / segments
    active = int(
        (db - db_min)
        / (db_max - db_min)
        * segments
    )

    for i in range(segments):
        angle = start_angle + i * step
        rad = math.radians(angle)

        r_mid = radius - led_len / 2

        x = cx + r_mid * math.cos(rad)
        y = cy + r_mid * math.sin(rad)

        dx = math.cos(rad)
        dy = math.sin(rad)

        px = -dy
        py = dx

        x1 = x + (led_len / 2) * dx + (led_width / 2) * px
        y1 = y + (led_len / 2) * dy + (led_width / 2) * py
        x2 = x + (led_len / 2) * dx - (led_width / 2) * px
        y2 = y + (led_len / 2) * dy - (led_width / 2) * py
        x3 = x - (led_len / 2) * dx - (led_width / 2) * px
        y3 = y - (led_len / 2) * dy - (led_width / 2) * py
        x4 = x - (led_len / 2) * dx + (led_width / 2) * px
        y4 = y - (led_len / 2) * dy + (led_width / 2) * py

        segment_db = (
            db_min
            + (i / segments) * (db_max - db_min)
        )


        if segment_db < -5:
            # Verde → giallo
            t = (segment_db + 20) / 15
            red = int(255 * t)
            color = (
                max(0, min(255, red)),
                255,
                0
            )
            glow = GLOW_GREEN

        else:
            # Giallo → rosso
            t = (segment_db + 5) / 8
            green = int(255 * (1 - t))
            color = (
                255,
                max(0, min(255, green)),
                0
            )
            glow = GLOW_RED
             
        points = [
            (int(x1), int(y1)),
            (int(x2), int(y2)),
            (int(x3), int(y3)),
            (int(x4), int(y4)),
        ]

        if i <= active:
            pygame.draw.polygon(
                surface,
                color,
                points,
            )

            pygame.draw.ellipse(
                surface,
                glow,
                (int(x - 2), int(y - 4), 4, 8),
            )
        else:
            pygame.draw.polygon(
                surface,
                BLACK,
                points,
            )

            pygame.draw.line(
                surface,
                color,
                points[0],
                points[1],
                1,
            )
            pygame.draw.line(
                surface,
                color,
                points[1],
                points[2],
                1,
            )
            pygame.draw.line(
                surface,
                color,
                points[2],
                points[3],
                1,
            )
            pygame.draw.line(
                surface,
                color,
                points[3],
                points[0],
                1,
            )


# ============================================================
# GRAFICA SPE
# ============================================================

def draw_spectrum_background(surface, font_small, font_channel):
    """Disegna scala, sfondo e griglia dello spettro."""
    gain_labels = [
        (6, 82, "+3"),
        (8, 101, "+1"),
        (11, 113, "0"),
        (9, 125, "-1"),
        (7, 144, "-3"),
        (7, 160, "-5"),
        (3, 181, "-10"),
        (1, 208, "-30"),
    ]

    for x, y, text in gain_labels:
        surface.blit(
            font_small.render(text, True, GRAY80),
            (x, y),
        )

    frequency_labels = [
        (29, 215, "20"),
        (41, 225, "31.5"),
        (65, 215, "50"),
        (83, 225, "80"),
        (100, 215, "125"),
        (116, 225, "200"),
        (135, 215, "315"),
        (152, 225, "500"),
        (170, 215, "800"),
        (186, 225, "1.25k"),
        (209, 215, "2k"),
        (222, 225, "3.15k"),
        (245, 215, "5k"),
        (263, 225, "8k"),
        (275, 215, "12.5k"),
        (296, 225, "20k"),
    ]

    for x, y, text in frequency_labels:
        surface.blit(
            font_small.render(text, True, GRAY80),
            (x, y),
        )

    # Sfondo colorato
    height = 128

    for i in range(height):
        ratio = i / (height - 1)

        if ratio < 0.5:
            red = int(ratio * 2 * 255)
            green = 255
        else:
            red = 255
            green = int(
                (1 - (ratio - 0.5) * 2) * 255
            )

        pygame.draw.line(
            surface,
            (red, green, 0),
            (19, 214 - i),
            (309, 214 - i),
        )

    # Griglia orizzontale
    for y in [107, 119, 131, 150, 166, 187]:
        pygame.draw.line(
            surface,
            BLACK,
            (20, y),
            (319, y),
        )

    # Griglia verticale
    for x in range(21, 320, 9):
        pygame.draw.line(
            surface,
            BLACK,
            (x, 85),
            (x, 213),
            2,
        )
        pygame.draw.line(
            surface,
            BLACK,
            (x + 6, 85),
            (x + 6, 213),
            2,
        )

    pygame.draw.line(
        surface,
        BLACK,
        (19, 85),
        (19, 213),
    )


def build_vumspe_background(channel, font_small, font_channel):
    """Costruisce lo sfondo statico di un pannello VumSpe."""
    surface = pygame.Surface((PANEL_W, PANEL_H))
    surface.fill(BLACK)

    draw_spectrum_background(
        surface,
        font_small,
        font_channel,
    )

    # Arco di contorno Peak.
    pygame.draw.arc(
        surface,
        GRAY50,
        PEAK_CONTOUR_BBOX,
        math.radians(PEAK_CONTOUR_START),
        math.radians(PEAK_CONTOUR_END),
        PEAK_CONTOUR_WIDTH,
    )

    # Arco di background Peak.
    pygame.draw.arc(
        surface,
        BLACK,
        PEAK_BACKGROUND_BBOX,
        math.radians(PEAK_BACKGROUND_START),
        math.radians(PEAK_BACKGROUND_END),
        PEAK_BACKGROUND_WIDTH,
    )

    # PEAK LEVEL.
    peak_text = font_small.render(
        "PEAK LEVEL",
        True,
        GRAY80,
    )
    peak_rect = peak_text.get_rect(
        center=PEAK_LEVEL_POS
    )
    surface.blit(peak_text, peak_rect)

    # Nome canale orizzontale.
    channel_text = font_channel.render(
        channel,
        True,
        GRAY80,
    )
    channel_rect = channel_text.get_rect(
        center=(160, 68)
    )
    surface.blit(channel_text, channel_rect)

    # Arco di delimitazione VUM / CHANNEL.
    pygame.draw.arc(
        surface,
        GRAY80,
        VU_DELIMITER_BBOX,
        math.radians(VU_DELIMITER_START),
        math.radians(VU_DELIMITER_END),
        VU_DELIMITER_WIDTH,
    )

    return surface


def draw_vumspe_panel(
    surface,
    background,
    value,
    peak,
    spectrum,
    offset,
):
    """Aggiorna VUM + SPE di un pannello 320x240."""
    surface.blit(background, (0, 0))

    draw_vu_arc_led(
        surface,
        value,
        VUM_ARC_CX,
        VUM_ARC_CY,
        VUM_ARC_RADIUS,
        VUM_ARC_START,
        VUM_ARC_END,
    )

    # Peak:
    #   0%   = 138° (sinistra)
    #   100% = 44°  (destra)
    peak = max(0.0, min(100.0, float(peak)))

    peak_angle = PEAK_DYNAMIC_LEFT - peak * (
        PEAK_DYNAMIC_LEFT - PEAK_DYNAMIC_RIGHT
    ) / 100.0

    if peak > 0.0:
        pygame.draw.arc(
            surface,
            GRAY80,
            PEAK_DYNAMIC_BBOX,
            math.radians(peak_angle),
            math.radians(PEAK_DYNAMIC_LEFT),
            PEAK_DYNAMIC_WIDTH,
        )

    # Spettro + peak hold.
    for local_index in range(32):
        if offset == 0:
            # CAVA LEFT = High -> Low
            # Display LEFT = Low -> High
            barcount = 31 - local_index
        else:
            # CAVA RIGHT = Low -> High
            barcount = 32 + local_index

        value = int(
            int(spectrum[barcount])
            * SPECTRUM_BAR_SCALE
        )

        x = 23 + local_index * 9

        pygame.draw.rect(
            surface,
            BLACK,
            (
                x,
                SPECTRUM_BAR_TOP,
                4,
                max(
                    0,
                    SPECTRUM_BAR_BASE
                    - value
                    - SPECTRUM_BAR_TOP,
                ),
            ),
        )

        if value > Valop[barcount]:
            Valop[barcount] = value
        else:
            Valop[barcount] = max(
                0,
                Valop[barcount] - 1,
            )

        if Valop[barcount] > 0:
            pygame.draw.rect(
                surface,
                GRAY80,
                (
                    x - 1,
                    SPECTRUM_BAR_BASE
                    - int(Valop[barcount] + 1),
                    6,
                    1,
                ),
            )


# ============================================================
# CAVA
# ============================================================

def cava_running():
    """True se esiste già un processo CAVA."""
    return subprocess.run(
        ["pgrep", "-x", "cava"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0


def start_cava():
    """Avvia CAVA se non è già in esecuzione."""
    print("Start CAVA")
    if cava_running():
        print("CAVA 1")
        return None

    if not os.path.exists(CAVA_CONFIG):
        print("CAVA 2")
        return None

    if not os.path.exists(CAVA_PATH):
        print("CAVA 3")
        return None

    try:
        print("CAVA Opening")
        proc = subprocess.Popen(
            [CAVA_PATH, "-p", CAVA_CONFIG],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        time.sleep(1.0)
        return proc

    except OSError:
        print("CAVA Error")
        return None


# ============================================================
# STATO LOOP PRINCIPALE
# ============================================================

""" Raggruppa in un unico dict, sullo stesso modello di
    create_audio_state/create_mus_state/create_moode_state, tutte le
    variabili di controllo playback e di rendering VUM/SCOPE che nella
    V15 vivevano come variabili locali sciolte dentro main(). """

def create_render_state():
    """Valori VUM/SCOPE azzerati a ogni avvio o stop del playback."""
    return {
        "lvp": 0.0,
        "rvp": 0.0,
        "left_value": 0.0,
        "right_value": 0.0,
        "vum_left_value": 0.0,
        "vum_right_value": 0.0,
        "sco_left_history": [0.0] * SCO_HISTORY_LEN,
        "sco_right_history": [0.0] * SCO_HISTORY_LEN,
        "last_display_update": 0.0,
        "last_mus_scroll_update": 0.0,
        "current_spectrum": [0] * 64,
        "display_update": False,
    }


def create_loop_state():
    """Stato mutabile del loop principale: controllo playback + rendering."""
    state = {
        "mus_state": None,
        "audio_proc": None,
        "cava_proc": None,
        "playing": False,
        "play_requested_at": None,
        "last_play_poll": 0.0,
    }
    state.update(create_render_state())
    return state


def reset_render_state(loop_state):
    """Azzera i soli valori VUM/SCOPE, lasciando intatto lo stato di controllo."""
    loop_state.update(create_render_state())


def terminate_process(proc, timeout=1.0):
    """Termina un sottoprocesso, forzando il kill se non risponde in tempo."""
    if proc is None:
        return

    proc.terminate()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()


def stop_playback(mus_state, audio_state, audio_proc, cava_proc):
    """Ferma MUS, acquisizione PCM e CAVA quando lo stato passa a NO PLAY.

    Restituisce (mus_state, audio_proc, cava_proc) azzerati a None, da
    riassegnare nel chiamante.
    """
    if mus_state is not None:
        stop_mus_state(mus_state)

    if audio_proc is not None:
        stop_audio_worker(audio_state)
        terminate_process(audio_proc)

    if cava_proc is not None:
        terminate_process(cava_proc)

    return None, None, None


def start_playback_audio(audio_state):
    """Avvia arecord + worker PCM, poi CAVA (ordine verificato nel MERGE)."""
    audio_proc = start_audio_capture()
    start_audio_worker(audio_state, audio_proc)

    cava_proc = start_cava()

    return audio_proc, cava_proc


def poll_moode_playback(loop_state, moode_state, play_state, audio_state, now):
    """Aggiorna PLAY/NO PLAY leggendo lo stato moOde, al più ogni PLAY_POLL_INTERVAL.

    Alle transizioni avvia/ferma MUS e, in STOP, chiude subito
    l'acquisizione audio e CAVA.
    """
    if now - loop_state["last_play_poll"] < PLAY_POLL_INTERVAL:
        return

    previous_playing = loop_state["playing"]
    song_snapshot = update_moode_state(moode_state, now)
    loop_state["playing"] = update_play_state(play_state, song_snapshot)
    loop_state["last_play_poll"] = now

    # PLAY confermato -> parte il tempo di attesa.
    if loop_state["playing"] and not previous_playing:
        loop_state["play_requested_at"] = now

        # Avvia il worker MUS solo con PLAY confermato.
        loop_state["mus_state"] = create_mus_state()
        start_mus_state(loop_state["mus_state"], moode_state)

    # STOP/PAUSE confermato -> eventuale acquisizione viene chiusa subito.
    elif not loop_state["playing"] and previous_playing:
        loop_state["play_requested_at"] = None

        (
            loop_state["mus_state"],
            loop_state["audio_proc"],
            loop_state["cava_proc"],
        ) = stop_playback(
            loop_state["mus_state"],
            audio_state,
            loop_state["audio_proc"],
            loop_state["cava_proc"],
        )
        reset_render_state(loop_state)


def maybe_start_delayed_audio(loop_state, audio_state, now):
    """Avvia arecord/CAVA quando PLAY_AUDIO_DELAY è trascorso dalla conferma PLAY."""
    ready = (
        loop_state["playing"]
        and loop_state["audio_proc"] is None
        and loop_state["play_requested_at"] is not None
        and now - loop_state["play_requested_at"] >= PLAY_AUDIO_DELAY
    )
    if not ready:
        return

    loop_state["audio_proc"], loop_state["cava_proc"] = start_playback_audio(
        audio_state
    )
    loop_state["play_requested_at"] = None
    reset_render_state(loop_state)


def update_audio_frame(loop_state, audio_state, now):
    """Legge il PCM disponibile e aggiorna VUM/SCOPE/spettro/storico SCOPE.

    Il ricalcolo di VUM/spettro/storico avviene al più a circa 30 Hz
    (DISPLAY_UPDATE_INTERVAL); il peak PCM viene invece letto a ogni frame.
    Restituisce il blocco di campioni PCM corrente (o None).
    """
    audio_active = loop_state["playing"] and loop_state["audio_proc"] is not None

    if not audio_active:
        loop_state["vum_left_value"] = 0.0
        loop_state["vum_right_value"] = 0.0
        loop_state["lvp"] = 0.0
        loop_state["rvp"] = 0.0
        loop_state["current_spectrum"] = [0] * 64
        loop_state["display_update"] = False
        return None

    samples = get_audio_samples(audio_state)

    # V11: peak PCM usato sia dai VUM sia dallo storico SCOPE.
    # Il FIFO PeppyMeter non viene più letto.
    if samples is not None and len(samples) > 0:
        left_peak = float(np.max(np.abs(samples[:, 0])))
        right_peak = float(np.max(np.abs(samples[:, 1])))
        loop_state["left_value"] = min(100.0, left_peak * 100.0 / 32768.0)
        loop_state["right_value"] = min(100.0, right_peak * 100.0 / 32768.0)
    else:
        loop_state["left_value"] = 0.0
        loop_state["right_value"] = 0.0

    # Dimezzamento della frequenza di aggiornamento dinamico.
    display_update = (
        now - loop_state["last_display_update"] >= DISPLAY_UPDATE_INTERVAL
    )
    loop_state["display_update"] = display_update

    if display_update:
        loop_state["vum_left_value"] = loop_state["left_value"]
        loop_state["vum_right_value"] = loop_state["right_value"]

        loop_state["lvp"] = max(
            loop_state["lvp"] - VUM_PEAK_DECAY,
            loop_state["vum_left_value"],
        )
        loop_state["rvp"] = max(
            loop_state["rvp"] - VUM_PEAK_DECAY,
            loop_state["vum_right_value"],
        )

        # CAVA viene interrogato anch'esso solo a circa 30 Hz.
        loop_state["current_spectrum"] = read_cava_fifo()
        loop_state["last_display_update"] = now

        # Lo storico SCOPE usa gli stessi peak PCM dei VUM.
        update_sco_history(
            loop_state["sco_left_history"],
            loop_state["sco_right_history"],
            loop_state["left_value"],
            loop_state["right_value"],
        )

    return samples


def update_sco_mus_surfaces(
    ui_cache, loop_state, font_small, font_scroll, mus_scroll, now
):
    """Ricostruisce le surface MUS (scroll testo) e SCO (scope/spettro).

    MUS scorre a MUS_SCROLL_INTERVAL; SCO segue la stessa cadenza di
    update_audio_frame (DISPLAY_UPDATE_INTERVAL).
    """
    mus_state = loop_state["mus_state"]
    active = (
        loop_state["playing"]
        and loop_state["audio_proc"] is not None
        and mus_state is not None
    )
    if not active:
        return

    mus_scroll_update = (
        now - loop_state["last_mus_scroll_update"] >= MUS_SCROLL_INTERVAL
    )

    if mus_scroll_update or ui_cache["mus_surface"] is None:
        mus_snapshot = snapshot_mus_state(mus_state)
        ui_cache["mus_surface"] = build_mus_surface(
            font_small, font_scroll, mus_snapshot, mus_scroll,
        )
        loop_state["last_mus_scroll_update"] = now

    if loop_state["display_update"] or ui_cache["sco_surface"] is None:
        ui_cache["sco_surface"] = build_sco_surface(
            font_small,
            loop_state["current_spectrum"],
            loop_state["left_value"],
            loop_state["right_value"],
            loop_state["sco_left_history"],
            loop_state["sco_right_history"],
        )


def update_time_and_sysinfo_surfaces(
    ui_cache, time_font_std, time_font_large, font_small, moode_release, now
):
    """TIME cambia al più una volta al secondo; SYSINFO ogni SYSINFO_REFRESH."""
    if ui_cache["time_surface"] is None or now - ui_cache["last_time_update"] >= 1.0:
        ui_cache["time_surface"] = build_time_surface(time_font_std, time_font_large)
        ui_cache["last_time_update"] = now

    if (
        ui_cache["sysinfo_surface"] is None
        or now - ui_cache["last_sysinfo_update"] >= SYSINFO_REFRESH
    ):
        ui_cache["sysinfo_surface"] = build_sysinfo_surface(font_small, moode_release)
        ui_cache["last_sysinfo_update"] = now


def render_frame(
    screen,
    ui_cache,
    loop_state,
    vum_left,
    vum_right,
    bg_left,
    bg_right,
    layout,
    samples,
    font,
):
    """Compone e mostra il frame corrente."""
    screen.fill(BLACK)

    # In NO PLAY restano visibili esclusivamente Time e Sysinfo.
    mus_active = (
        loop_state["playing"]
        and loop_state["audio_proc"] is not None
        and loop_state["mus_state"] is not None
    )
    if mus_active:
        draw_mid_oled(screen, ui_cache["sco_surface"], 0)
        draw_mid_oled(screen, ui_cache["mus_surface"], 1)

    # Time sempre presente in mid2.
    draw_time(screen, ui_cache["time_surface"])

    # Sysinfo sempre presente in mid3, anche NO PLAY.
    draw_mid_oled(screen, ui_cache["sysinfo_surface"], 3)

    audio_active = loop_state["playing"] and loop_state["audio_proc"] is not None
    if audio_active:
        if loop_state["display_update"]:
            draw_vumspe_panel(
                vum_left, bg_left,
                loop_state["vum_left_value"], loop_state["lvp"],
                loop_state["current_spectrum"], 0,
            )
            draw_vumspe_panel(
                vum_right, bg_right,
                loop_state["vum_right_value"], loop_state["rvp"],
                loop_state["current_spectrum"], 32,
            )

        screen.blit(vum_left, (LEFT_X, TOP_Y))
        screen.blit(vum_right, (RIGHT_X, TOP_Y))

        if samples is not None:
            draw_grid(screen, layout)

            left, right = draw_trace(screen, samples, layout)

            draw_trace_info(screen, font, left, right, layout)

    # Durante NO PLAY, o durante l'attesa dei 3 s, il frame resta nero
    # a parte Time e Sysinfo, già disegnati sopra.
    pygame.display.flip()


# ============================================================
# EVENTI
# ============================================================

def handle_events():
    """Gestisce chiusura finestra ed ESC."""
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            return False

        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                return False

    return True


# ============================================================
# MAIN
# ============================================================

def main():

    screen, font, clock = init_display()
    layout = get_layout()

    # --------------------------------------------------------
    # FONT VUM/SPE
    # --------------------------------------------------------
    font_dir = os.path.dirname(os.path.abspath(__file__))
    font_path = os.path.join(font_dir, "C&C Red Alert [INET].ttf")

    font_small = pygame.font.Font(font_path, 13)
    font_scroll = pygame.font.Font(
        os.path.join(font_dir, "DejaVuSansMono.ttf"), 12,
    )
    font_channel = pygame.font.Font(font_path, 26)

    bg_left = build_vumspe_background("LEFT CHANNEL", font_small, font_channel)
    bg_right = build_vumspe_background("RIGHT CHANNEL", font_small, font_channel)

    vum_left = pygame.Surface((PANEL_W, PANEL_H))
    vum_right = pygame.Surface((PANEL_W, PANEL_H))

    time_font_std = pygame.font.Font(font_path, 13)
    time_font_large = pygame.font.Font(
        os.path.join(font_dir, "ProggyTiny.ttf"), 32,
    )

    moode_release = get_moode_release()

    # Cache delle surface OLED (TIME/SYSINFO/MUS/SCO), ricostruite solo
    # quando serve dai rispettivi update_* più sotto.
    ui_cache = {
        "time_surface": None,
        "last_time_update": 0.0,
        "sysinfo_surface": None,
        "last_sysinfo_update": 0.0,
        "mus_surface": None,
        "sco_surface": None,
    }

    # --------------------------------------------------------
    # MUS / SCO
    # --------------------------------------------------------
    mus_scroll = {
        "artist": {"offset": 0.0, "pause": 30},
        "album": {"offset": 0.0, "pause": 30},
        "title": {"offset": 0.0, "pause": 30},
    }

    # --------------------------------------------------------
    # STATO MOODE CONDIVISO + API IN BACKGROUND
    # --------------------------------------------------------
    moode_state = create_moode_state()
    start_moode_api_worker(moode_state)

    # --------------------------------------------------------
    # PLAY STATE / STATO LOOP (controllo playback + rendering)
    # --------------------------------------------------------
    play_state = create_play_state()
    audio_state = create_audio_state()
    loop_state = create_loop_state()

    try:
        running = True

        while running:
            running = handle_events()

            now = time.monotonic()

            poll_moode_playback(
                loop_state, moode_state, play_state, audio_state, now,
            )
            maybe_start_delayed_audio(loop_state, audio_state, now)

            samples = update_audio_frame(loop_state, audio_state, now)

            update_sco_mus_surfaces(
                ui_cache, loop_state, font_small, font_scroll, mus_scroll, now,
            )
            update_time_and_sysinfo_surfaces(
                ui_cache,
                time_font_std,
                time_font_large,
                font_small,
                moode_release,
                now,
            )

            render_frame(
                screen,
                ui_cache,
                loop_state,
                vum_left,
                vum_right,
                bg_left,
                bg_right,
                layout,
                samples,
                font,
            )

            clock.tick(FPS)

    finally:
        stop_moode_api_worker(moode_state)

        (
            loop_state["mus_state"],
            loop_state["audio_proc"],
            loop_state["cava_proc"],
        ) = stop_playback(
            loop_state["mus_state"],
            audio_state,
            loop_state["audio_proc"],
            loop_state["cava_proc"],
        )

        pygame.quit()


if __name__ == "__main__":
    main()