#!/usr/bin/env python3
"""
kiosko_printer.py — Driver de la impresora SJ-TP01 en MODO AUTÓNOMO.

La impresora no usa ESC/POS: imprime imágenes (bitmap 384 px) envueltas en un
protocolo propio con marcas 0x7e. Además exige una secuencia especial para
imprimir contenido nuevo (confirmada por el manual):

    enviar datos por USB  ->  desconectar USB  ->  apagar/encender  ->  botón rojo

Aquí cubrimos las partes que controla la Pi:
  - render_ticket / encode_image : generar el bitmap y codificarlo.
  - usb_connect / usb_disconnect : conectar/"desenchufar" el USB por software (uhubctl).
  - send_raw                     : escribir los bytes al driver del kernel (/dev/usb/lp0).

El apagado/encendido (relé) y el botón rojo (cable soldado) se controlarán desde
el orquestador cuando esté el hardware; aquí quedan como funciones aparte.
"""
from __future__ import annotations
import os
import struct
import glob
import time
import subprocess
from PIL import Image, ImageDraw, ImageFont

# --- Protocolo / dispositivo ---
WIDTH = 384
BYTES_PER_ROW = WIDTH // 8           # 48
JOB_HEADER = bytes.fromhex(            # 88 bytes EXACTOS (¡ojo, no 92!)
    "7e015401000001000f270100050000000a000000000000000a00000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000"
)
assert len(JOB_HEADER) == 88, "JOB_HEADER debe medir 88 bytes"
END_MARK = bytes.fromhex("7eff0000")

# --- Dispositivo USB ---
VID = 0x28EA
PID = 0x028E
OUT_EP = 0x01

# --- USB (uhubctl) ---
USB_HUB = "1-1"     # hub integrado de la Pi 3B (0424:9514)
USB_PORT = "5"      # puerto donde está la impresora

# Pausa entre bloques al enviar (s). Más alto = envío más lento pero más fiable
# (da tiempo al buffer de la impresora y evita que se pierda algún byte = desfases).
SEND_CHUNK_DELAY = 0.02

# --- Fuente ---
# Fuente de marca (ValentineLL-Regular.otf) junto a este módulo. Si no se encuentra,
# se usa DejaVu Sans como respaldo para no bloquear. Como solo hay peso Regular, la
# "negrita" de los títulos se simula con un pequeño trazo (stroke).
_HERE = os.path.dirname(os.path.abspath(__file__))
FONT_PATH = os.path.join(_HERE, "ValentineLL-Regular.otf")
if not os.path.exists(FONT_PATH):
    FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

# Todos los textos van en MAYÚSCULAS (requisito del cliente).
UPPERCASE = True

# Altura fija (px) de TODOS los tickets, para que ocupen lo mismo en el mueble.
# Ajustable: si con las imágenes reales algún ticket no cabe, súbela.
TICKET_HEIGHT = 660


# ---------------------------------------------------------------------------
# Render del ticket
# ---------------------------------------------------------------------------
def _wrap(draw, text, font, max_w):
    """Parte 'text' en varias líneas para que cada una quepa en max_w píxeles."""
    words = text.split()
    if not words:
        return [""]
    out, cur = [], words[0]
    for w in words[1:]:
        if draw.textlength(cur + " " + w, font=font) <= max_w:
            cur += " " + w
        else:
            out.append(cur)
            cur = w
    out.append(cur)
    return out


def load_asset(path, target_width, autocrop=True):
    """
    Carga un logo/imagen (png/jpg), aplana transparencia, recorta el margen blanco,
    lo escala a target_width y lo pasa a monocromo (1bpp). El autorecorte hace que el
    logo/nombre llene su ancho aunque la imagen original traiga mucho blanco alrededor.
    """
    im = Image.open(path)
    if im.mode in ("RGBA", "LA", "P"):                 # aplanar transparencia sobre blanco
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        im = Image.alpha_composite(bg, im)
    im = im.convert("L")
    if autocrop:                                       # recortar el margen blanco
        mask = im.point(lambda x: 255 if x < 200 else 0)
        bbox = mask.getbbox()
        if bbox:
            im = im.crop(bbox)
    if target_width and im.width != target_width:
        h = round(im.height * target_width / im.width)
        im = im.resize((target_width, max(1, h)), Image.LANCZOS)
    return im.point(lambda x: 0 if x < 128 else 255, mode="1")   # umbral, sin tramado


def render_ticket(lines, header_images=None, footer_images=None, top_margin=22,
                  bottom_feed=130, header_gap=16, target_height=None):
    """
    lines: lista de dicts {"text","size","bold","align","gap_after"}.
    header_images: lista de imágenes PIL ya preparadas (logo/nombre) que se pegan
                   centradas ARRIBA, en orden, antes de los textos.
    target_height: altura total fija del ticket (px). Si se indica, TODOS los tickets
                   salen igual de altos: la cabecera (logo+nombre) queda anclada arriba,
                   el bloque de texto se centra en el espacio disponible, y abajo queda
                   un margen de avance/corte constante (bottom_feed). Por defecto usa
                   TICKET_HEIGHT. Si el contenido no cabe, la altura crece lo necesario.
    Devuelve imagen PIL modo '1'.
    """
    if target_height is None:
        target_height = TICKET_HEIGHT
    pad_x, spacing = 16, 8
    max_w = WIDTH - 2 * pad_x
    header_images = header_images or []
    footer_images = footer_images or []
    dummy = ImageDraw.Draw(Image.new("1", (WIDTH, 10), 1))

    # 1) medir textos (con wrap)
    rows = []
    text_h = 0
    for ln in lines:
        size = ln.get("size", 26)
        font = ImageFont.truetype(FONT_PATH, size)
        # negrita simulada con trazo (no hay peso bold en la fuente)
        sw = max(1, round(size / 20)) if ln.get("bold") else 0
        align = ln.get("align", "center")
        gap = ln.get("gap_after", 0)
        texto = ln["text"].upper() if UPPERCASE else ln["text"]
        subs = _wrap(dummy, texto, font, max_w)
        for i, sub in enumerate(subs):
            bbox = dummy.textbbox((0, 0), sub or "X", font=font, stroke_width=sw)
            h = bbox[3] - bbox[1]
            extra = gap if i == len(subs) - 1 else 0
            rows.append((sub, font, sw, align, bbox, h, extra))
            text_h += h + spacing + extra

    header_h = sum(im.height + header_gap for im in header_images)
    footer_h = sum(im.height + header_gap for im in footer_images)

    # Altura fija para que todos los tickets midan igual; si no cabe, crece.
    natural = top_margin + header_h + text_h + footer_h + bottom_feed
    total_h = max(natural, target_height)

    # 2) dibujar
    img = Image.new("1", (WIDTH, total_h), 1)
    draw = ImageDraw.Draw(img)

    # cabecera (anagrama/nombre) anclada arriba -> posición consistente en todos
    y = top_margin
    for im in header_images:
        img.paste(im, ((WIDTH - im.width) // 2, y))
        y += im.height + header_gap

    # el bloque de texto se centra entre la cabecera y el pie (imágenes de footer + feed)
    body_top = y
    body_bottom = total_h - bottom_feed - footer_h
    free = (body_bottom - body_top) - text_h
    y = body_top + max(0, free // 2)

    for sub, font, sw, align, bbox, h, extra in rows:
        w = bbox[2] - bbox[0]
        x = (WIDTH - w) // 2 if align == "center" else pad_x
        draw.text((x - bbox[0], y - bbox[1]), sub, font=font, fill=0,
                  stroke_width=sw, stroke_fill=0)
        y += h + spacing + extra

    # pie (logo) anclado abajo, justo encima del margen de avance/corte
    y = total_h - bottom_feed - footer_h
    for im in footer_images:
        y += header_gap
        img.paste(im, ((WIDTH - im.width) // 2, y))
        y += im.height
    return img


def encode_image(img: Image.Image) -> bytes:
    """Imagen PIL -> bytes del protocolo SJ-TP01."""
    if img.width != WIDTH:
        img = img.resize((WIDTH, round(img.height * WIDTH / img.width)))
    img = img.convert("1")
    h = img.height
    px = img.load()
    raster = bytearray()
    for y in range(h):
        for xb in range(BYTES_PER_ROW):
            byte = 0
            for bit in range(8):
                if px[xb * 8 + bit, y] == 0:        # 0 = negro -> bit 1
                    byte |= (1 << (7 - bit))
            raster.append(byte)
    img_header = (b"\x7e\x03\x08\x00"
                  + struct.pack("<H", WIDTH)
                  + struct.pack("<H", h)
                  + struct.pack("<I", len(raster)))
    return JOB_HEADER + img_header + bytes(raster) + END_MARK


# ---------------------------------------------------------------------------
# Control de USB (uhubctl) y envío por el driver del kernel
# ---------------------------------------------------------------------------
def _uhubctl(action: str):
    subprocess.run(["uhubctl", "-l", USB_HUB, "-p", USB_PORT, "-a", action],
                   check=False, capture_output=True)


def _find_lp():
    devs = sorted(glob.glob("/dev/usb/lp*"))
    return devs[0] if devs else None


def usb_connect(timeout=8.0):
    """Conecta el USB de la impresora y espera a que aparezca /dev/usb/lp*."""
    _uhubctl("on")
    t0 = time.time()
    while time.time() - t0 < timeout:
        dev = _find_lp()
        if dev:
            time.sleep(0.5)   # margen para que el driver termine de montar
            return dev
        time.sleep(0.3)
    raise RuntimeError("No apareció /dev/usb/lp* tras reconectar el USB.")


def usb_disconnect():
    """Desconecta el USB de la impresora (equivale a desenchufar el cable)."""
    _uhubctl("off")


def _chunk_sizes(total):
    """Trozos idénticos al software original: header(4+84), img(4+8), raster(512s), fin(4)."""
    JH, IH, END = 88, 12, 4
    rlen = total - JH - IH - END
    sizes = [4, 84, 4, 8]
    pos = JH + IH
    while pos < JH + IH + rlen:
        n = min(512, JH + IH + rlen - pos)
        sizes.append(n)
        pos += n
    sizes.append(END)
    return sizes


def send(data: bytes):
    """
    Graba el trabajo replicando la secuencia de Windows (la única que graba contenido
    nuevo): detach usblp -> set_config -> GET_PORT_STATUS -> SOFT_RESET -> envío
    troceado (4,84,4,8, bloques de 512, 4). Esta es la versión que imprime bien.
    El EPIPE (errno 32) de la última trama es benigno (la impresora ya tiene el trabajo
    y corta el USB) y se ignora. Requiere usb_connect() antes y usb_disconnect() después.
    """
    import time
    import usb.core
    import usb.util

    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        raise RuntimeError("Impresora no encontrada (¿USB conectado?).")
    try:
        if dev.is_kernel_driver_active(0):
            dev.detach_kernel_driver(0)
    except (NotImplementedError, usb.core.USBError):
        pass

    dev.set_configuration()
    usb.util.claim_interface(dev, 0)
    try:
        dev.ctrl_transfer(0xA1, 0x01, 0x0000, 0x0000, 1)     # GET_PORT_STATUS
    except usb.core.USBError:
        pass
    try:
        dev.ctrl_transfer(0x21, 0x02, 0x0000, 0x0000, None)  # SOFT_RESET
    except usb.core.USBError:
        pass

    pos = 0
    for sz in _chunk_sizes(len(data)):
        try:
            dev.write(OUT_EP, data[pos:pos + sz], timeout=10000)
        except usb.core.USBError as e:
            # EPIPE (errno 32) = la impresora ya tiene el trabajo y corta el USB.
            # Es benigno: damos el envío por bueno.
            if getattr(e, "errno", None) == 32:
                pos = len(data)
                break
            raise
        pos += sz
        time.sleep(SEND_CHUNK_DELAY)
    try:
        usb.util.dispose_resources(dev)
    except Exception:
        pass
    return pos
