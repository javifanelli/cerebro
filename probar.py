#!/usr/bin/env python3
# ─────────────────────────────────────────────────────────────────────────────
#  Probar el cerebro de Trumi desde la compu (sin Trumi)
# ─────────────────────────────────────────────────────────────────────────────
#
#  Preguntar con texto (prueba el modelo y la voz):
#      python probar.py --servidor http://IP-DE-LA-RASPBERRY:8765 --clave CLAVE --texto "Hola Trumi, ¿como estas?"
#
#  Preguntar con audio (prueba todo: oido, modelo y voz). El archivo puede ser
#  cualquier .wav de 16 bits (mono o estereo, cualquier frecuencia):
#      python probar.py --servidor http://IP-DE-LA-RASPBERRY:8765 --clave CLAVE pregunta.wav
#
#  La respuesta hablada queda en respuesta.wav (y se muestra el texto).
#  Solo usa la libreria estandar de Python (no hace falta instalar nada).
#
import argparse
import array
import json
import sys
import time
import urllib.parse
import urllib.request
import wave

TASA = 16000


def leer_wav_16k_mono(ruta):
    """Lee un .wav de 16 bits y lo devuelve como audio crudo de 16 kHz, mono"""
    with wave.open(ruta, 'rb') as w:
        if w.getsampwidth() != 2:
            sys.exit('El .wav tiene que ser de 16 bits')
        canales, tasa = w.getnchannels(), w.getframerate()
        muestras = array.array('h', w.readframes(w.getnframes()))
    if sys.byteorder == 'big':
        muestras.byteswap()
    if canales > 1:                                   # a mono: promedio de los canales
        muestras = array.array('h', (sum(muestras[i:i + canales]) // canales
                                     for i in range(0, len(muestras), canales)))
    if tasa != TASA:                                  # a 16 kHz (interpolacion lineal)
        largo = int(len(muestras) * TASA / tasa)
        nuevo = array.array('h')
        for i in range(largo):
            pos = i * tasa / TASA
            j = int(pos)
            f = pos - j
            a = muestras[j]
            b = muestras[j + 1] if j + 1 < len(muestras) else a
            nuevo.append(int(a + (b - a) * f))
        muestras = nuevo
    return muestras.tobytes()


def guardar_wav(ruta, pcm):
    with wave.open(ruta, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(TASA)
        w.writeframes(pcm)


def main():
    p = argparse.ArgumentParser(description='Probar el cerebro de Trumi')
    p.add_argument('wav', nargs='?', help='archivo .wav con la pregunta')
    p.add_argument('--texto', help='pregunta escrita (en vez de un .wav)')
    p.add_argument('--decir', help='texto para que lo diga tal cual (prueba solo la voz)')
    p.add_argument('--servidor', default='http://localhost:8765')
    p.add_argument('--clave', default='')
    p.add_argument('--salida', default='respuesta.wav')
    a = p.parse_args()

    if a.decir:
        ruta, cuerpo, tipo = '/decir', json.dumps({'texto': a.decir}).encode('utf-8'), 'application/json'
    elif a.texto:
        ruta, cuerpo, tipo = '/preguntar', json.dumps({'texto': a.texto}).encode('utf-8'), 'application/json'
    elif a.wav:
        ruta, cuerpo, tipo = '/charlar', leer_wav_16k_mono(a.wav), 'application/octet-stream'
    else:
        p.error('Falta un .wav, --texto o --decir')

    pedido = urllib.request.Request(a.servidor.rstrip('/') + ruta, data=cuerpo,
                                    headers={'Content-Type': tipo, 'X-Clave': a.clave})
    t0 = time.time()
    with urllib.request.urlopen(pedido, timeout=180) as r:
        audio = r.read()
        encabezados = r.headers
    demora = time.time() - t0

    leer = lambda nombre: urllib.parse.unquote(encabezados.get(nombre, ''))
    print('Entendio:   ', leer('X-Escuche'))
    print('Contesta:   ', leer('X-Respuesta'))
    print('En pantalla:', leer('X-Pantalla'))
    print('Tardo %.1f s; la respuesta dura %.1f s' % (demora, len(audio) / 2 / TASA))
    guardar_wav(a.salida, audio)
    print('La respuesta hablada esta en', a.salida)


if __name__ == '__main__':
    main()
