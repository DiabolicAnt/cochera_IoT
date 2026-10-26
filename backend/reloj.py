"""Desfase del reloj de la laptop respecto a NTP, para que L = t_recepcion - t_envio sea comparable.

El gateway pone t_envio con su hora NTP; el backend pone t_recepcion con el reloj de Windows, que en las
pruebas iba hasta 1.5 s adelantado. Se consulta un servidor SNTP y se corrige t_recepcion con el desfase.
"""
import socket
import struct
import time

EPOCA_NTP = 2208988800  # Segundos entre 1900 (NTP) y 1970 (Unix).


def desfase_ms(servidor="pool.ntp.org", timeout=2.0):
    """Hora NTP menos hora local, en ms (negativo si la laptop va adelantada). None si no hay respuesta."""
    paquete = b"\x1b" + 47 * b"\0"
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        t1 = time.time()
        s.sendto(paquete, (servidor, 123))
        respuesta, _ = s.recvfrom(48)
        t4 = time.time()
    def marca(offset):
        segundos, fraccion = struct.unpack("!II", respuesta[offset:offset + 8])
        return segundos - EPOCA_NTP + fraccion / 2**32
    t2, t3 = marca(32), marca(40)
    return round(((t2 - t1) + (t3 - t4)) / 2 * 1000, 1)
