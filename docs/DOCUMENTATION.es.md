# Kiosko de tickets promocionales — Aristocrazy

**Proyecto de ingeniería a medida (hardware + software)**
Última actualización: 9 de junio de 2026
Estado: **funcionando entero por software; pendiente automatización física (relé + botón rojo)**

---

## 1. Objetivo del proyecto

Crear un kiosko interactivo para un **stand de feria** del cliente final (Aristocrazy).
El visitante pulsa un **botón grande (arcade)** y la máquina imprime al instante un ticket
promocional, cada uno con la marca del cliente (nombre + logo) y un mensaje distinto. Inicialmente
los tickets salían **al azar**; por petición del cliente ahora siguen una **secuencia fija en bucle:
4 merchan, 1 piercing, 4 merchan, 1 ta-tu** (ver sección 11). Todo debe funcionar **de forma
autónoma, sin operario**, fiable y desatendido durante la feria.

Las tres promociones:

1. **Piercing** — "¡ENHORABUENA! VIVE UNA PIERCING EXPERIENCE / ELIGE TU PIERCING CON NUESTRO EQUIPO".
2. **Ta-tu** — "¡ENHORABUENA! VIVE UNA TA-TU EXPERIENCE / UN COMPROMISO CONTIGO PARA SIEMPRE / CONSULTA CON NUESTRO EQUIPO".
3. **Merchan** — "¡ENHORABUENA! YA PUEDES HACERTE CON UNA EXCLUSIVA PIEZA DE NUESTRO MERCHAN".

Todos rematan con "* Sólo válido hoy." y llevan arriba el nombre **ARISTOCRAZY** y su logo.

---

## 2. El reto principal

La impresora del cliente (**Quanzhou Shuojiang SJ-TP01**, impresora térmica de tickets de turno)
trae un software propietario de Windows que **solo permite un diseño fijo y obliga a pulsar un
botón físico** para imprimir. No sirve para el caso de uso (varios diseños, aleatorios, automáticos).

El gran reto técnico: **la impresora no usa ESC/POS** (el estándar habitual de las térmicas) sino
un **protocolo propietario no documentado**. Hubo que hacer **ingeniería inversa** del mismo
capturando el tráfico USB del software original para poder enviarle nuestros propios diseños desde
una **Raspberry Pi**.

---

## 3. Arquitectura final

Todo-en-uno sobre una **Raspberry Pi 3 Model B** (sin PC):

```
[ Botón arcade ] --GPIO--> [ Raspberry Pi 3B ] --USB--> [ Impresora SJ-TP01 ]
                                   |                            |
                          (control relé / botón)        (su propia fuente DC)
```

- **Entrada:** botón arcade conectado a GPIO17 (resistencia pull-up interna, antirrebote por software).
- **Cerebro:** Raspberry Pi 3B con Raspberry Pi OS. Programa en Python que, al pulsar el botón,
  elige promo aleatoria (con pesos configurables), **genera el ticket como imagen** (384 px de ancho,
  con nombre + logo + textos), lo **codifica al protocolo propietario** y lo envía por USB.
- **Salida:** impresora SJ-TP01 por USB, alimentada con su propia fuente.

### Cómo "piensa" la impresora (clave del proyecto)

- **No imprime texto, imprime imágenes.** El diseño se rasteriza a un **bitmap monocromo de 384 px
  de ancho** (1 bit por píxel, 1 = negro, MSB primero).
- **Protocolo propietario con marcas `0x7e`:**
  - `7e 01` + cabecera de trabajo (88 bytes fijos).
  - `7e 03 08 00` + ancho (LE16) + alto (LE16) + longitud del raster (LE32).
  - el raster (ancho/8 × alto bytes).
  - `7e ff 00 00` (fin de trabajo).
- **Secuencia USB para "grabar" un trabajo nuevo** (la única que funciona, copiada del software original):
  `detach usblp → SET_CONFIGURATION → GET_PORT_STATUS (0xA1/0x01) → SOFT_RESET de clase Printer (0x21/0x02) → envío de los datos`.
- **Modo de impresión autónomo:** para imprimir contenido nuevo hay que, **tras grabar**:
  desconectar el USB → apagar/encender la impresora → pulsar el botón rojo. Solo así, arrancando
  **sin host USB**, entra en modo autónomo e imprime. Es un requisito del firmware, documentado en
  su manual.

---

## 4. Recorrido del proyecto (pasos seguidos)

1. **Validación del hardware base.** Confirmar que la Raspberry Pi 3B servía; preparar la tarjeta
   con Raspberry Pi OS; conectar y probar el **botón arcade** por GPIO (script de prueba).
2. **Conexión de la impresora.** Reconocida por el kernel como `/dev/usb/lp0` (clase USB "Printer",
   VID `0x28EA` / PID `0x028E`, endpoint de salida `0x01`).
3. **Descubrimiento de que no es ESC/POS.** Ni texto plano ni comandos ESC/POS (avance, corte)
   producían efecto, aunque el autotest interno de la impresora sí imprimía.
4. **Ingeniería inversa del protocolo.** Captura del tráfico USB del software original con
   **Wireshark + USBPcap** en Windows. Análisis de la captura (parser propio de `.pcapng` + USBPcap)
   para extraer los bytes, decodificar la estructura `0x7e` y **reconstruir la imagen** enviada
   (se leía "Prueba 1234", confirmando ancho 384, polaridad y orden de bits).
5. **Driver propio.** Módulo Python que **rinde** el ticket como imagen (texto con ajuste de línea,
   logo y nombre desde archivos), lo **codifica** al protocolo y lo **envía** por USB. Verificado con
   un viaje de ida y vuelta (generar → codificar → decodificar → ver imagen) antes de tocar la impresora.
6. **Resolución del "modo autónomo".** Largo proceso de diagnóstico hasta entender (gracias al manual)
   que la impresora solo graba/imprime contenido nuevo con la secuencia desconectar-USB → apagar/encender →
   botón. Se logró **desconectar el USB por software** con `uhubctl` (la Pi 3B soporta apagado de puerto
   individual, "ppps").
7. **Orquestación completa.** Programa final que une botón arcade → selección aleatoria → generación →
   grabado → desconexión USB, dejando preparados (manuales por ahora) el apagado/encendido y el botón rojo.
8. **Pulido.** Corrección de un bug crítico (cabecera de 92 bytes en vez de 88 que invalidaba todos los
   envíos), manejo del "pipe error" benigno, fiabilidad de envíos repetidos, y ajuste de densidad de
   tinta en un ticket que se cortaba.

---

## 5. Dificultades encontradas y cómo se resolvieron

- **Protocolo no documentado.** → Ingeniería inversa por captura USB y análisis byte a byte.
- **`/dev/usb/lp0` aceptaba datos pero no imprimía.** → Era protocolo propietario, no ESC/POS.
- **No se grababa contenido nuevo (reimprimía siempre el de Windows).** → Causa raíz: nuestra
  constante de cabecera tenía **92 bytes en vez de 88** (4 ceros de más), desplazaba la cabecera de
  imagen y la impresora descartaba el trabajo. Corregido → grabado correcto.
- **Comportamiento de "una sola impresión por encendido" / ventana de ~2 s.** → No era ahorro de
  energía (descartado: `runtime_status = active`) ni sondeo del host. Es el **diseño del firmware**:
  exige reinicio sin USB para imprimir. Documentado en su manual.
- **Imagen corrupta / banda cortada intermitente.** → En parte por cambios que rompieron lo que
  funcionaba (se revirtió al envío troceado en bloques de 512 que sí imprime bien); en el ticket de
  "merchan", además, un bloque de texto en negrita muy denso provocaba bajada de tinta → se aligeró.
- **"Pipe error" (errno 32) tras grabar.** → Es benigno (la impresora corta el USB al recibir el
  trabajo). Se trata como éxito y se garantiza la desconexión USB siempre.

### Dificultades en la puesta en marcha con relés (montaje físico)

- **Cortaba la corriente DC con el relé pero la impresora no se reiniciaba (luces atenuadas).** →
  La impresora se alimenta **también por los 5 V del USB (VBUS)**, y `uhubctl` en la Pi 3B **no corta
  ese VBUS** (solo desactiva la enumeración). Al cortar solo la DC, el USB la mantenía viva.
- **Cortar el +5V del USB (cable solo-datos) tampoco valía.** → Sin VBUS la impresora **no enumera**
  (la Pi no la ve), así que no se le puede enviar el trabajo. Necesita el VBUS para recibir.
- **Solución final (sin tercer relé):** el **interruptor de encendido** de la impresora corta la
  alimentación *aguas abajo* de donde entra el USB (por eso apagarlo a mano sí la reinicia del todo).
  Se cableó el **relé 1 en paralelo a ese interruptor** (basculante de 2 patillas, dejado en OFF). Así
  el relé apaga la impresora por completo, se reinicia y entra en modo autónomo. USB original siempre
  conectado; DC directa.
- **Cortaba la corriente antes de que la impresora grabara el trabajo.** → Pausa `T_COMMIT` tras enviar
  (con la DC aún activa) antes de reiniciar.
- **Logs no visibles bajo systemd.** → `PYTHONUNBUFFERED=1` en el servicio.
- **El botón arcade aporreado imprimía varios tickets.** → `gpiozero` **encola** las pulsaciones y las
  reproduce al terminar; se añadió **enfriamiento por tiempo** (`COOLDOWN`) además del cerrojo, que
  descarta las encoladas: una ráfaga = un solo ticket.

---

## 6. Aprendizajes

- Muchas impresoras térmicas baratas **no son ESC/POS**; conviene verificarlo pronto (un volcado de
  texto a `/dev/usb/lp0` y/o el autotest).
- La **captura de USB (Wireshark + USBPcap)** es la herramienta decisiva para reproducir un protocolo
  propietario; reconstruir la imagen a partir del raster valida toda la decodificación de golpe.
- En la Raspberry Pi 3B se puede **cortar la alimentación de un puerto USB por software** (`uhubctl`,
  soporte "ppps"), lo que evita hardware adicional para "desenchufar" el USB.
- Verificar con contenido **distinto y reconocible** (no idéntico al ya almacenado) es imprescindible
  para no confundir "grabar de verdad" con "reimprimir lo anterior".
- Un detalle de **4 bytes** en una cabecera puede invalidar todo: medir y aseverar tamaños (`assert`).

---

## 7. Hardware (lista de materiales y costes reales)

### Material comprado (pedido junio 2026)

| Componente | Uso | Coste |
|---|---|---|
| ELEGOO 120 cables DuPont (M-H / M-M / H-H) | Conexiones de control y cableado | 9,99 € |
| SEENGREAT módulo relé 1 canal 3.3 V optoacoplado **×2** | Relé 1 = corriente impresora · Relé 2 = pulsar botón rojo | 19,80 € |
| KUOQIY 7 pares conector jack DC 5,5×2,1 mm (macho + hembra, bornas de tornillo) | Intercalar el relé 1 en la corriente, sin soldar | 7,99 € |
| GUUZI pulsador 22 mm momentáneo 1NO/1NC (5 uds, colores) | Botón del visitante | 16,99 € |
| **Total material comprado** | | **54,77 €** |

### Accesorios de la Raspberry Pi

| Componente | Uso | Coste |
|---|---|---|
| Carcasa transparente con ventilador (Pi 3B) | Protección y refrigeración de la Pi | ~9 € * |
| Interruptor de perilla (en línea, alimentación) | Encender/apagar el conjunto cómodamente | ~5 € * |

\* Coste aproximado; ajustar con el precio real de compra.

### Ya disponible (reutilizado)

| Componente | Notas |
|---|---|
| Raspberry Pi 3 Model B + fuente + microSD | Equipo propio |
| Impresora SJ-TP01 + su fuente DC | Provista por el cliente |
| Soldador, estaño y cinta/termorretráctil | Ya en taller |
| Tipografía ValentineLL-Regular | Aportada por el cliente |

### Totales de material

- Pedido de junio 2026: **54,77 €**
- Accesorios de la Pi (aprox.): **~14 €**
- **Material total aprox.: ~69 €** (sin contabilizar la Pi y la impresora, reutilizadas)

> Notas técnicas del montaje:
> - Los relés SEENGREAT son **disparo por nivel bajo (active-low)** y tienen un **interruptor de nivel
>   que debe ponerse en `3V3`**. Se usan sus bornas **COM + NO**.
> - El **único punto de soldadura** son los 2 cables al botón rojo de la impresora.

---

## 8. Valoración del proyecto (orientativa, para tarificar al cliente)

Este es un desarrollo **a medida** que incluye ingeniería inversa de un protocolo propietario,
desarrollo de un driver, generación de tickets, integración hardware y puesta a punto — no es un
"montaje" estándar. Estimación orientativa de esfuerzo:

| Partida | Horas aprox. |
|---|---|
| Ingeniería inversa del protocolo (capturas, análisis, decodificación) | 8–12 h |
| Desarrollo del driver e impresión de imágenes | 6–8 h |
| Orquestación, lógica de promos y fiabilidad | 5–7 h |
| Integración hardware (relé, botón) y puesta a punto | 4–6 h |
| Documentación y entrega | 2–3 h |
| **Total** | **~25–36 h** |

A una tarifa de ingeniería freelance de referencia de **40–60 €/h**, el **valor del trabajo** se
situaría aproximadamente entre **1.000 € y 2.200 €**, más materiales. A esto puede sumarse el valor
de **reutilización** (la solución sirve para futuras ferias/campañas cambiando textos e imágenes) y
un posible **margen** sobre el material.

> Esto es una estimación orientativa para ayudar a fijar el precio; la decisión final de tarifa es
> del propio profesional, en función del mercado, el acuerdo con el cliente y el alcance acordado.

---

## 9. Estado actual y pendiente

**Funcionando hoy (todo por software):**
- Protocolo descifrado e implementado; los 3 tickets se generan, son aleatorios e imprimen bien.
- Botón arcade, desconexión de USB por software (`uhubctl`), cerrojo anti-pulsaciones.

**Pendiente (último tramo — automatización física):**
- Montar los **dos módulos relé** (1 canal cada uno): relé 1 = corriente de la impresora (vía jacks de
  tornillo), relé 2 = pulsar el botón rojo (2 cables soldados al botón). Interruptor de cada relé en `3V3`.
- En `kiosko.py`, poner **`HARDWARE_RELES = True`** (la lógica ya está implementada, en modo active-low).
- **Afinar los tiempos** `T_POWER_OFF` / `T_BOOT` / `T_PRESS` midiendo el arranque real de la impresora,
  para bajar el ciclo a ~5–7 s por ticket.
- Con la automatización, el cerrojo cubrirá el ciclo completo (el visitante podrá pulsar sin efecto
  hasta que salga el ticket).

**Conexiones finales (montaje real, 2 relés)** — ver diagrama en
`recursos/diagrama_montaje_final.svg`:

| Señal | GPIO | Pin físico | Notas |
|---|---|---|---|
| Botón arcade (entrada) | GPIO17 | 11 | a un terminal; el otro a GND. Sin polaridad |
| Relé 1 — IN | GPIO23 | 16 | COM·NO **en paralelo al interruptor de encendido** de la impresora (déjalo en OFF) |
| Relé 2 — IN | GPIO25 | 22 | COM·NO a los 2 cables soldados del **botón rojo** |
| VCC de los dos relés | 3V3 | 1 | relé de 3 V; interruptor de cada módulo en `3V3` |
| GND común | GND | 6/9/14… | compartido por relés y arcade |
| USB de la impresora | (puerto USB) | puerto 5 del hub `1-1` | **cable original con sus 5 V**, siempre conectado |
| Fuente DC de la impresora | — | — | **directa** a la impresora (sin relé) |

> Clave del montaje: el relé 1 corta donde corta el interruptor manual (aguas abajo de donde el USB
> inyecta los 5 V), por eso apaga la impresora del todo y la reinicia. No hace falta tercer relé.

---

## 10. Contenido de esta carpeta

- `DOCUMENTACION.md` — este documento (mantener actualizado en cada avance).
- `Tipografias_y_tamanos_tickets_v1_DejaVu.docx` — versión 1 (diseño inicial, tipografía DejaVu).
- `Tipografias_y_tamanos_tickets_v2_Valentine.docx` — versión 2 **vigente** (ValentineLL + mayúsculas + altura uniforme).
- `ValentineLL-Regular.otf` — tipografía de marca (también copiada en `codigo/`).
- `codigo/` — `kiosko.py` (orquestador) y `kiosko_printer.py` (driver: render + protocolo + USB).
- `guias/` — guía de montaje, guía de captura del protocolo, diseño de la automatización.
- `recursos/` — capturas USB (`.pcapng`), bytes del protocolo, imágenes del cliente, previews de los
  tickets y **`diagrama_montaje_final.svg`** (conexiones reales del montaje, 2 relés).

### Cómo ejecutar (estado actual)

En la Raspberry Pi (con sus dependencias del sistema: `python3-gpiozero`, `python3-lgpio`,
`python3-pil`, `python3-usb`, y `uhubctl`):

```bash
sudo python3 ~/kiosko.py
```

Las imágenes del cliente van en `~/assets/` (`aristocrazy_nombre_nuevo.jpeg` y `aristocrazy_logo.png`).

### Arranque automático al encender (para la feria)

Para que el personal NO técnico solo tenga que enchufar y todo funcione, el script se lanza
solo al arrancar la Pi mediante un **servicio de systemd** (`codigo/kiosko.service`), que además
lo **reinicia automáticamente** si se cerrara por cualquier motivo.

Instalación (una sola vez, en la Pi):

```bash
sudo cp ~/kiosko.service /etc/systemd/system/kiosko.service
sudo systemctl daemon-reload
sudo systemctl enable kiosko.service     # que arranque en cada encendido
sudo systemctl start kiosko.service      # arrancarlo ya, para probar
sudo systemctl status kiosko.service     # ver que está "active (running)"
journalctl -u kiosko.service -f          # ver los mensajes en vivo
```

Comandos útiles de mantenimiento:

```bash
sudo systemctl restart kiosko.service    # tras actualizar el código
sudo systemctl stop kiosko.service       # pararlo (p. ej. para depurar a mano)
sudo systemctl disable kiosko.service    # que NO arranque solo
```

Notas:
- El servicio corre como **root** (necesario para `uhubctl`, GPIO y USB) y desde `/home/rjbarco`.
- Espera 8 s al arrancar para que el USB/impresora terminen de enumerarse.
- Requiere que el código esté en `~/` y `HARDWARE_RELES = True` (con el hardware ya cableado).
- El día del evento: el personal solo enchufa la Pi (y la impresora); a los ~10 s el kiosko queda
  listo y el botón arcade imprime. No hay que tocar terminal ni teclado.

---

## 11. Historial de cambios

- **9 jun 2026** — Diseño de tickets: tipografía cambiada a **ValentineLL-Regular** (aportada por el
  cliente, peso Regular; la "negrita" de títulos se simula con trazo). **Todos los textos en
  MAYÚSCULAS** por requisito del cliente. La fuente vive junto al código (`ValentineLL-Regular.otf`)
  y debe copiarse también a la Raspberry Pi junto a `kiosko_printer.py`.
- **9 jun 2026** — Confirmado el conector de corriente: **jack DC 5,5×2,1 mm, centro = positivo (+)**.
  El relé se intercala en el hilo del centro (+).
- **9 jun 2026** — Pulsador del visitante elegido: GUUZI 22 mm, momentáneo, 1NO 1NC, 10A/440V (la
  alta tensión nominal es solo capacidad máxima; se usa a 3,3 V sin problema). Se usa el contacto NO+COM.
- **9 jun 2026** — Material pedido (54,77 €): 2× relé SEENGREAT 1 canal 3.3 V (active-low, switch a
  `3V3`), pack ELEGOO 120 cables DuPont, conectores jack DC 5,5×2,1 mm de tornillo (macho+hembra),
  y pulsador GUUZI 22 mm momentáneo. Soldador y consumibles ya disponibles. Ver sección 7.
- **9 jun 2026** — Código preparado para los relés (funciones `power_cycle_printer()` /
  `press_red_button()` implementadas en `kiosko.py`, modo **active-low**, tras el flag `HARDWARE_RELES`).
  Añadido `RELE_ACTIVE_LOW` por si algún módulo va con polaridad invertida.
- **9 jun 2026** — Relé cambiado al genérico **HL-51 (1 Relay Module, relé 03VDC-C, 3V)** por
  disponibilidad/envío. También active-low. **Alimentar VCC desde 3,3 V** (relé de 3 V, no 5 V).
  Sin optoacoplador en la entrada, pero el contacto del relé sigue aislado del lado de control.
- **9 jun 2026** — Recibidas e integradas las **imágenes reales** del cliente: nombre
  (`aristocrazy_nombre_nuevo.jpeg`) y logo (`aristocrazy_logo.png`). Añadido **autorecorte** en
  `load_asset` para que llenen bien su ancho. Nuevo script **`imprimir_muestras.py`** para sacar una
  muestra de cada ticket (flujo manual, sin relé). `kiosko.py` refactorizado para ser importable.
- **9 jun 2026** — Detectada corrupción **intermitente** en un ticket (desfase horizontal del raster =
  byte perdido en la transferencia USB; no depende del contenido). Mitigación: pausa entre bloques
  configurable **`SEND_CHUNK_DELAY`** (subida a 0,02 s) para enviar más despacio y fiable. Si persiste,
  subirla más o cambiar a un cable USB más corto/mejor (sin cambiar de puerto, que rompería el `USB_PORT=5`).
  **Resuelto:** con `SEND_CHUNK_DELAY = 0.02` los 3 tickets salen limpios de forma repetida y fiable.
- **9 jun 2026** — Ajustes de diseño pedidos por el cliente: **anagrama (nombre) más grande**
  (`NAME_WIDTH` 320→360), **logo movido al pie** del ticket (`footer_images` en `render_ticket`;
  en `kiosko.py` el nombre va en `HEADER` y el logo en `FOOTER`), y **¡ENHORABUENA! +2 px** (38→40).
- **9 jun 2026** — Añadido **arranque automático** (`codigo/kiosko.service`, systemd con
  `Restart=always`): el personal solo enchufa y el kiosko arranca y se autorrecupera. Ver sección 10.
- **9 jun 2026** — Puesta en marcha con relés: el ciclo automático cortaba la corriente demasiado
  pronto tras el envío y la impresora no llegaba a **grabar** el trabajo (no imprimía ni a mano).
  Solución: pausa **`T_COMMIT`** (2,5 s) tras enviar antes de apagar/encender. Tiempos a 3 s (apagado)
  y 5 s (arranque). También `PYTHONUNBUFFERED=1` en el servicio para ver logs en vivo.
- **9 jun 2026** — Hallazgo clave en la puesta en marcha: la impresora **se alimenta también por los
  5 V del USB (VBUS)** y `uhubctl` en la Pi 3B **no corta ese VBUS** (solo desactiva la enumeración).
  Por eso, al cortar solo la DC con el relé, la impresora se quedaba viva por el USB, no se reiniciaba
  y no entraba en modo autónomo → no imprimía. **Solución:** **tercer relé** en el hilo +5V del cable
  USB (GPIO24); el código (`usb_on`/`usb_off`) ahora corta el VBUS por hardware en vez de `uhubctl`.
  Secuencia: VBUS on → grabar → VBUS off → (DC sigue, graba) → apagar/encender DC → botón → imprime.
- **9 jun 2026** — ¡FUNCIONA! Solución final **sin 3.er relé**: el relé 1 se cableó **en paralelo al
  interruptor de encendido de la impresora** (basculante de 2 patillas, dejado en OFF), que corta la
  alimentación **aguas abajo** de la inyección del USB → reinicio completo aunque el USB siga con 5 V.
  USB original (con VBUS) siempre conectado; `HAS_USB_RELAY = False`.
- **9 jun 2026** — Tiempos afinados al mínimo: T_COMMIT 1,5 / T_POWER_OFF 1,2 / T_BOOT 3,5 / T_PRESS
  0,25 (~8 s por ticket). Guía de ajuste en los comentarios de `kiosko.py`. `SEND_CHUNK_DELAY` intacto.
- **9 jun 2026** — `T_BOOT` bajado a 2,5 s (~7 s/ticket). Cerrojo cambiado a `threading.Lock`
  (acquire no bloqueante): a prueba de aporreo del botón — las pulsaciones durante una impresión
  se ignoran sin romper nada ni encolarse.
- **9 jun 2026** — Detectado que `gpiozero` **encola** las pulsaciones y las reproduce al terminar
  cada impresión (varias ráfagas → varios tickets). Solución: **enfriamiento por tiempo** (`COOLDOWN`
  2 s) además del cerrojo, que descarta las pulsaciones encoladas. Ráfaga de pulsaciones → 1 ticket.
- **15 jun 2026** — Instalado en el mueble y validado por el cliente. **Cambio a secuencia fija**
  (en vez de aleatoria) por petición del cliente: ciclo de 10 → **4 merchan, 1 piercing, 4 merchan,
  1 ta-tu**, en bucle (`SEQUENCE` en `kiosko.py`). El índice se **persiste** en `kiosko_secuencia.txt`,
  así la secuencia **continúa tras un reinicio/apagado** (no se reinicia a merchan). El índice avanza
  **solo si el ticket se imprime sin error**. Para reiniciar la secuencia a mano: borrar ese archivo.
  Actualizado también `imprimir_muestras.py`.
- **15 jun 2026 (incidencia en feria, resuelta)** — Tras sustituir `kiosko.py` en remoto, el kiosko
  dejó de imprimir. Diagnóstico: el fichero quedó **vacío** (la transferencia falló) → el script
  arrancaba y salía al instante → el servicio entraba en **bucle de reinicios** (`Restart=always`).
  Señales: `systemctl status` "parece running" pero `journalctl` muestra "Scheduled restart job,
  restart counter is at N" subiendo; `wc -l kiosko.py` = 0. Solución: volver a copiar el `kiosko.py`
  completo (283 líneas) y reiniciar. **Recomendación:** tras copiar un fichero a la Pi, verificar
  `wc -l` antes de reiniciar; y mantener una copia de seguridad en la Pi (ver abajo).
- Pendiente: —

> Nota: el documento `Tipografias_y_tamanos_tickets.docx` se generó con la fuente anterior (DejaVu);
> conviene regenerarlo cuando se cierren tipografía y tamaños definitivos con el cliente.

---

*Documento vivo: actualizar la sección 9 (estado), la 8 (valoración) y el historial (11) a medida que avance el proyecto.*
