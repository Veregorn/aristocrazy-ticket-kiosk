#!/usr/bin/env python3
"""
imprimir_muestras.py — Imprime UNA muestra de cada uno de los 3 tickets.

Pensado para sacar muestras para el cliente cuando AÚN NO está el relé montado:
para cada ticket graba el trabajo y, como HARDWARE_RELES está en False, te pide a
mano apagar/encender la impresora y pulsar el botón rojo. Reutiliza las promos, la
tipografía, las imágenes y la configuración de kiosko.py (única fuente de verdad).

Ejecutar:  sudo python3 ~/imprimir_muestras.py
"""
import kiosko


def main():
    promos = kiosko.PROMOS              # dict {nombre: líneas}
    print("Se imprimirán %d muestras (una de cada modelo)." % len(promos))
    print("Para cada una: pulsa ENTER, y cuando lo indique, apaga/enciende la "
          "impresora y pulsa el botón rojo.\n")
    for i, (name, lines) in enumerate(promos.items(), 1):
        print("=" * 50)
        print("MUESTRA %d/%d  —  %s" % (i, len(promos), name))
        input("Pulsa ENTER para grabar e imprimir esta muestra... ")
        try:
            kiosko.imprimir_promo(name, lines)
        except Exception as e:
            print("   ERROR:", e)
    print("\nFin de las muestras.")


if __name__ == "__main__":
    main()
