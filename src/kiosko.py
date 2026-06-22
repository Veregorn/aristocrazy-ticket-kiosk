#!/usr/bin/env python3
"""
kiosko.py — Orquestador del kiosko de tickets promocionales.

Al pulsar el BOTÓN ARCADE (GPIO17):
  1. Elige una de las 3 promos al azar (con pesos configurables).
  2. Genera la imagen del ticket y la codifica.
  3. Reconecta el USB, envía el trabajo y vuelve a desconectar el USB.
  4. (Por ahora MANUAL) apaga/enciende la impresora y pulsa el botón rojo.

Cuando esté el hardware (relé de corriente + botón rojo soldado), solo habrá que
rellenar power_cycle_printer() y press_red_button(); el resto no cambia.

Ejecutar:  sudo ~/kiosko-venv/bin/python ~/kiosko.py
"""
import os
import random
import time
import signal
import threading

from gpiozero import Button
import kiosko_printer as printer

# ===========================================================================
# IMÁGENES DE CABECERA (nombre del cliente + logo)
# Pon los dos archivos en la Pi en estas rutas. Se pegan arriba de cada ticket
# en este orden: primero el NOMBRE, luego el LOGO. Si falta alguno, se omite.
# ===========================================================================
# Carpeta "assets" junto a este script (funciona aunque se ejecute con sudo)
ASSET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
NAME_IMG = os.path.join(ASSET_DIR, "aristocrazy_nombre_nuevo.jpeg")   # palabra "ARISTOCRAZY"
LOGO_IMG = os.path.join(ASSET_DIR, "aristocrazy_logo.png")            # el símbolo

NAME_WIDTH = 360    # ancho al que se escala el nombre/anagrama (px, máx 384)
LOGO_WIDTH = 130    # ancho al que se escala el logo


def _load_one(path, w):
    if not os.path.exists(path):
        print("Aviso: falta la imagen %s — se omite." % path)
        return None
    try:
        return printer.load_asset(path, w)
    except Exception as e:
        print("Aviso: no se pudo cargar %s (%s)" % (path, e))
        return None


def load_assets():
    """Devuelve (anagrama/nombre, logo); cualquiera puede ser None si falta el archivo."""
    return _load_one(NAME_IMG, NAME_WIDTH), _load_one(LOGO_IMG, LOGO_WIDTH)


# ===========================================================================
# CONFIGURACIÓN — los 3 modelos de ticket (por nombre) y sus textos
# ===========================================================================
PROMOS = {
    "piercing": [
        {"text": "¡ENHORABUENA!", "size": 40, "bold": True, "gap_after": 18},
        {"text": "VIVE UNA PIERCING EXPERIENCE", "size": 26, "bold": True, "gap_after": 14},
        {"text": "ELIGE TU PIERCING CON NUESTRO EQUIPO", "size": 22, "gap_after": 16},
        {"text": "* Sólo válido hoy.", "size": 18},
    ],
    "tatu": [
        {"text": "¡ENHORABUENA!", "size": 40, "bold": True, "gap_after": 18},
        {"text": "VIVE UNA TA-TU EXPERIENCE", "size": 26, "bold": True, "gap_after": 14},
        {"text": "UN COMPROMISO CONTIGO PARA SIEMPRE", "size": 22, "gap_after": 10},
        {"text": "CONSULTA CON NUESTRO EQUIPO", "size": 22, "gap_after": 16},
        {"text": "* Sólo válido hoy.", "size": 18},
    ],
    "merchan": [
        {"text": "¡ENHORABUENA!", "size": 40, "bold": True, "gap_after": 18},
        {"text": "YA PUEDES HACERTE CON UNA EXCLUSIVA PIEZA DE NUESTRO MERCHAN",
         "size": 24, "bold": False, "gap_after": 16},
        {"text": "* Sólo válido hoy.", "size": 18},
    ],
}

# Secuencia FIJA pedida por el cliente (se repite en bucle):
# 4 merchan, 1 piercing, 4 merchan, 1 ta-tu, y vuelta a empezar.
SEQUENCE = ["merchan", "merchan", "merchan", "merchan", "piercing",
            "merchan", "merchan", "merchan", "merchan", "tatu"]

# Archivo donde se guarda la posición de la secuencia (sobrevive a reinicios/apagados).
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kiosko_secuencia.txt")

ARCADE_PIN = 17     # botón arcade (pin físico 11)

# ===========================================================================
# RELÉS (SEENGREAT — disparo por nivel BAJO / active-low)
# Mantén HARDWARE_RELES = False hasta que estén cableados el relé de corriente y
# el del botón rojo. Mientras tanto, los dos pasos se piden a mano por consola.
# Cuando montes el hardware, pon True (y el interruptor de cada relé en 3V3).
# ===========================================================================
HARDWARE_RELES = True    # PRODUCCIÓN: relés cableados. (False = pedir los pasos a mano)
RELE_ACTIVE_LOW = True   # estos módulos suelen activarse a nivel BAJO. Si al probar
                         # el relé va invertido (activa cuando debería soltar), ponlo en False.
RELE_POWER_PIN = 23   # relé 1: corriente de la impresora (pin físico 16)
RELE_BOTON_PIN = 25   # relé 2: pulsar el botón rojo      (pin físico 22)
RELE_USB_PIN   = 24   # relé 3: corta el +5V (VBUS) del cable USB (pin físico 18)
# ¿Hay un tercer relé para el VBUS? Si NO (cable USB "solo datos", con el +5V cortado),
# déjalo en False: no se toca el USB y el relé de corriente basta para reiniciar la impresora.
HAS_USB_RELAY = False

# Tiempos (s) — afinados al mínimo. Si algún paso falla, sube SOLO ese:
#   ticket en blanco / no graba  -> sube T_COMMIT
#   no se reinicia del todo       -> sube T_POWER_OFF
#   el botón no imprime (muy pronto) -> sube T_BOOT
T_COMMIT = 1.5        # espera TRAS enviar (que la impresora grabe) ANTES de cortar
T_POWER_OFF = 1.2     # tiempo apagada durante el ciclo (reset completo)
T_BOOT = 2.5          # espera tras encender, hasta que la impresora esté lista
T_PRESS = 0.25        # duración del "pulsado" del botón rojo

_rele_power = None     # se inicializan abajo si HARDWARE_RELES = True
_rele_boton = None
_rele_usb = None       # relé 3 (corta el VBUS del USB)

# ===========================================================================
# Cerrojo: solo se procesa UNA impresión a la vez. Las pulsaciones durante un
# trabajo en curso se ignoran (el visitante puede aporrear el botón sin efecto).
_lock = threading.Lock()
# Enfriamiento: tras imprimir, se ignoran las pulsaciones de los próximos segundos
# (descarta las que gpiozero deja ENCOLADAS durante la impresión = anti-aporreo).
COOLDOWN = 2.0
_last_done = 0.0


def _load_index():
    """Lee la posición guardada de la secuencia (0 si no existe o falla)."""
    try:
        with open(STATE_FILE) as f:
            return int(f.read().strip()) % len(SEQUENCE)
    except Exception:
        return 0


def _save_index(i):
    try:
        with open(STATE_FILE, "w") as f:
            f.write(str(i))
    except Exception as e:
        print("   Aviso: no se pudo guardar el estado de la secuencia:", e)


_seq_index = _load_index()   # posición actual en SEQUENCE (persistida)


def current_promo():
    """Devuelve (nombre, líneas) del modelo que toca AHORA en la secuencia."""
    name = SEQUENCE[_seq_index % len(SEQUENCE)]
    return name, PROMOS[name]


def advance_sequence():
    """Avanza a la siguiente posición y la guarda. Solo tras imprimir bien."""
    global _seq_index
    _seq_index = (_seq_index + 1) % len(SEQUENCE)
    _save_index(_seq_index)


def usb_on():
    """Asegura el USB conectado y espera a que la impresora enumere.
    Con relé 3: enciende el VBUS. Sin relé (cable solo-datos): solo espera al dispositivo."""
    if not HARDWARE_RELES:
        printer.usb_connect()          # modo manual (uhubctl), legado
        return
    if HAS_USB_RELAY:
        _rele_usb.on()                 # restaura los 5V del USB
    import usb.core
    for _ in range(20):
        if usb.core.find(idVendor=printer.VID, idProduct=printer.PID) is not None:
            time.sleep(0.5)
            return
        time.sleep(0.3)
    print("   Aviso: la impresora no apareció en USB")


def usb_off():
    """Desconecta el USB. Con relé 3: corta el VBUS. Sin relé (cable solo-datos): no hace falta
    (el +5V ya está cortado físicamente; el relé de corriente DC reinicia la impresora)."""
    if not HARDWARE_RELES:
        printer.usb_disconnect()
        return
    if HAS_USB_RELAY:
        _rele_usb.off()                # corta los 5V del USB


def power_cycle_printer():
    """Apaga y enciende la impresora. Con hardware usa el relé 1; si no, lo pide a mano."""
    if not HARDWARE_RELES:
        print("   >>> APAGA y ENCIENDE la impresora <<<")
        return
    _rele_power.off()          # abre el relé -> corta la corriente
    time.sleep(T_POWER_OFF)
    _rele_power.on()           # cierra el relé -> restaura la corriente
    time.sleep(T_BOOT)         # espera a que arranque (en modo autónomo, USB ya desconectado)


def press_red_button():
    """Pulsa el botón rojo. Con hardware da un pulso por el relé 2; si no, lo pide a mano."""
    if not HARDWARE_RELES:
        print("   >>> PULSA el botón rojo <<<")
        return
    _rele_boton.on()           # cierra el contacto = botón pulsado
    time.sleep(T_PRESS)
    _rele_boton.off()          # suelta


def imprimir_promo(name, lines):
    """Genera, graba y dispara la impresión de UN modelo de ticket."""
    print("   Modelo: %s" % name)
    data = printer.encode_image(printer.render_ticket(
        lines, header_images=HEADER, footer_images=FOOTER))
    usb_on()                           # VBUS conectado -> impresora en modo recepción
    try:
        n = printer.send(data)
        print("   Grabado en la impresora (%d bytes)" % n)
    finally:
        usb_off()                      # SIEMPRE cortar el VBUS, pase lo que pase
        print("   USB (VBUS) desconectado.")
    time.sleep(T_COMMIT)               # con la DC aún activa, la impresora GRABA el trabajo
    print("   Reiniciando impresora y disparando botón...")
    power_cycle_printer()
    press_red_button()
    print("   Ticket disparado.")


def do_print():
    """Handler del botón arcade: imprime el modelo que toca en la SECUENCIA fija.
    Doble guarda anti-aporreo: enfriamiento por tiempo (descarta pulsaciones encoladas
    tras una impresión) + cerrojo (no procesa dos a la vez).
    El índice de la secuencia avanza SOLO si la impresión se realiza sin error."""
    global _last_done
    if time.time() - _last_done < COOLDOWN:
        return                          # pulsación encolada/repetida tras imprimir -> ignorar
    if not _lock.acquire(blocking=False):
        return                          # ya hay un trabajo en marcha -> ignorar
    try:
        name, lines = current_promo()
        print("\n[%.0f] Pulsación arcade — secuencia %d/%d -> %s"
              % (time.time(), _seq_index + 1, len(SEQUENCE), name))
        imprimir_promo(name, lines)
        advance_sequence()              # avanza SOLO si no hubo excepción
    except Exception as e:
        print("   ERROR:", e)
    finally:
        _last_done = time.time()        # marca de fin: las encoladas que lleguen ahora se descartan
        _lock.release()


NAME_IMG_OBJ, LOGO_IMG_OBJ = load_assets()   # carga anagrama + logo una vez al arrancar
HEADER = [NAME_IMG_OBJ] if NAME_IMG_OBJ else []   # anagrama (nombre) ARRIBA
FOOTER = [LOGO_IMG_OBJ] if LOGO_IMG_OBJ else []   # logo AL FINAL del ticket

if HARDWARE_RELES:
    from gpiozero import OutputDevice
    # active_high según RELE_ACTIVE_LOW: con active-low, .on() pone el pin en BAJO y activa el relé.
    _ah = not RELE_ACTIVE_LOW
    _rele_power = OutputDevice(RELE_POWER_PIN, active_high=_ah, initial_value=True)   # impresora ENCENDIDA al arrancar
    _rele_boton = OutputDevice(RELE_BOTON_PIN, active_high=_ah, initial_value=False)  # botón en reposo
    if HAS_USB_RELAY:
        _rele_usb = OutputDevice(RELE_USB_PIN, active_high=_ah, initial_value=True)   # VBUS USB conectado al arrancar
    print("Relés activos: corriente=GPIO%d, botón=GPIO%d, USB=%s (active_low=%s)"
          % (RELE_POWER_PIN, RELE_BOTON_PIN,
             ("GPIO%d" % RELE_USB_PIN) if HAS_USB_RELAY else "cable solo-datos",
             RELE_ACTIVE_LOW))

def main():
    boton = Button(ARCADE_PIN, pull_up=True, bounce_time=0.1)
    boton.when_pressed = do_print
    print("=" * 50)
    print("  Kiosko de tickets — LISTO")
    print("  Pulsa el botón arcade para imprimir. Ctrl+C para salir.")
    print("=" * 50)
    signal.pause()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nSaliendo.")
