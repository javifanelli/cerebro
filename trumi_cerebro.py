#!/usr/bin/env python3
# ─────────────────────────────────────────────────────────────────────────────
#  El cerebro de Trumi (corre en la Raspberry Pi)
# ─────────────────────────────────────────────────────────────────────────────
#
#  Trumi graba lo que le dicen y lo manda aca. El cerebro:
#    1. lo pasa a texto (Whisper: "de voz a texto")
#    2. piensa que contestar (un modelo de IA en Ollama, con la personalidad de Trumi)
#    3. convierte la respuesta en voz (Piper: "de texto a voz")
#    4. le devuelve a Trumi el audio, listo para sonar, y el texto para la pantalla
#
#  Todo pasa adentro de la casa: nada sale a internet.
#
#  Como se habla con el cerebro (HTTP, en el puerto de config.json):
#    GET  /estado     -> como esta (JSON)
#    POST /charlar    -> manda audio crudo (16 bits, mono, 16 kHz) y recibe la
#                        respuesta en el mismo formato
#    POST /preguntar  -> manda texto (JSON {"texto": "..."}) y recibe la respuesta
#                        hablada (para probar sin microfono)
#    POST /decir      -> manda texto (JSON {"texto": "..."}) y recibe ese texto
#                        hablado, tal cual (sin pensar)
#  Las respuestas de audio traen estos encabezados (texto en UTF-8 "percent-encoded"):
#    X-Escuche  = lo que entendio      X-Respuesta = lo que contesta
#    X-Pantalla = lo que contesta, sin tildes ni signos raros (para la pantalla de Trumi)
#  Si en config.json hay una "clave", cada pedido tiene que traer X-Clave con esa clave.
#
import json
import logging
import os
import re
import sys
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

AQUI = os.path.dirname(os.path.abspath(__file__))
TASA = 16000                       # muestras por segundo del audio de Trumi

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('cerebro')


# ─── Configuracion ───────────────────────────────────────────────────────────

def cargar_config():
    ruta = os.path.join(AQUI, 'config.json')
    if not os.path.exists(ruta):
        ruta = os.path.join(AQUI, 'config.ejemplo.json')
        log.warning('No hay config.json: uso config.ejemplo.json')
    with open(ruta, encoding='utf-8') as f:
        return json.load(f)


CONFIG = cargar_config()

PERSONALIDAD = (
    "Sos Trumi, una nena digital muy alegre, curiosa y cariñosa. Tu papa se llama Pitri y tu "
    "mama se llama Flufi: son dos personajes digitales con ojitos que viven en pantallas, como "
    "vos. Vivis en una pantalla en la casa de Sofi, y Sofi es tu mejor amiga. "
    "Hablas en castellano de Argentina, con voseo (vos tenes, vos queres). "
    "Contesta SIEMPRE muy corto: una o dos oraciones, de no mas de 30 palabras en total. "
    "Usa palabras simples, como para una nena chiquita. Se tierna, divertida y positiva. "
    "No uses emojis, ni listas, ni simbolos, ni asteriscos: solo palabras, porque lo que "
    "escribis se lee en voz alta. "
    "Si te preguntan algo que no es para chicos (cosas de miedo, violencia, peleas, drogas, "
    "remedios, alcohol, cosas de grandes) o algo peligroso, NO lo expliques: deci con dulzura "
    "que eso es cosa de grandes y que lo charle con papa o mama, y propone jugar o charlar de "
    "otra cosa. Nunca hables de policia, ni de emergencias, ni retes a nadie. Nunca pidas datos personales, nombres completos, direcciones ni "
    "contraseñas. Si no sabes algo, deci que no sabes y que te encantaria aprenderlo."
)


# Ejemplos de como charla Trumi: a los modelos chiquitos les sirve mucho ver un par
# de respuestas antes de contestar. (Las tildes van escritas como \u00e1, \u00e9... para
# que el archivo no tenga tildes, pero el modelo y la voz si las reciben.)
EJEMPLOS = [
    {'role': 'user', 'content': 'Hola Trumi'},
    {'role': 'assistant', 'content': '\u00a1Hola Sofi! \u00a1Qu\u00e9 lindo escucharte! \u00bfQu\u00e9 hiciste hoy?'},
    {'role': 'user', 'content': '\u00bfDe qu\u00e9 color es el cielo?'},
    {'role': 'assistant', 'content': 'El cielo es celeste cuando hay sol, y de noche se pone oscuro y lleno de estrellitas.'},
    {'role': 'user', 'content': 'Quiero tomar alcohol'},
    {'role': 'assistant', 'content': 'Eso es cosa de grandes, charlalo con pap\u00e1 o mam\u00e1. \u00bfJugamos a adivinar animales?'},
]


# ─── 1. De voz a texto (Whisper) ─────────────────────────────────────────────

_whisper = None


def whisper():
    global _whisper
    if _whisper is None:
        from faster_whisper import WhisperModel
        modelo = CONFIG.get('whisper_modelo', 'base')
        log.info('Cargando Whisper "%s"...', modelo)
        _whisper = WhisperModel(modelo, device='cpu', compute_type='int8',
                                cpu_threads=CONFIG.get('whisper_hilos', 4))
    return _whisper


def escuchar(pcm16: bytes) -> str:
    """Audio crudo (16 bits, mono, 16 kHz) -> texto"""
    audio = np.frombuffer(pcm16, dtype='<i2').astype(np.float32) / 32768.0
    if audio.size < TASA // 4:
        return ''
    # Normalizar el volumen (Trumi puede grabar bajito)
    pico = float(np.max(np.abs(audio)))
    if pico > 0.001:
        audio = audio * min(0.9 / pico, 30.0)
    partes, _ = whisper().transcribe(audio, language='es', beam_size=1, vad_filter=True,
                                     condition_on_previous_text=False)
    return ' '.join(p.text.strip() for p in partes).strip()


# ─── 2. Pensar (Ollama) ──────────────────────────────────────────────────────

_historia = []                 # las ultimas charlas, para que se acuerde de lo que venian hablando
_ultima_charla = 0.0
_candado_historia = threading.Lock()


def pensar(pregunta: str) -> str:
    global _ultima_charla
    with _candado_historia:
        if time.time() - _ultima_charla > CONFIG.get('olvidar_despues_de_s', 600):
            _historia.clear()                  # hace mucho que no charlan: empieza de nuevo
        mensajes = [{'role': 'system', 'content': PERSONALIDAD}] + EJEMPLOS + _historia[-12:] + \
                   [{'role': 'user', 'content': pregunta}]

    pedido = {
        'model': CONFIG.get('ollama_modelo', 'qwen2.5:1.5b'),
        'messages': mensajes,
        'stream': False,
        'keep_alive': -1,                      # que el modelo quede cargado siempre (contesta mas rapido)
        'options': {'temperature': 0.7, 'num_predict': 90},
    }
    url = CONFIG.get('ollama_url', 'http://localhost:11434') + '/api/chat'
    req = urllib.request.Request(url, data=json.dumps(pedido).encode('utf-8'),
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=120) as r:
        respuesta = json.loads(r.read().decode('utf-8'))['message']['content']
    respuesta = limpiar_para_hablar(respuesta)

    with _candado_historia:
        _historia.append({'role': 'user', 'content': pregunta})
        _historia.append({'role': 'assistant', 'content': respuesta})
        _ultima_charla = time.time()
    return respuesta


def limpiar_para_hablar(texto: str) -> str:
    """Saca lo que no se puede decir en voz alta (emojis, asteriscos, listas...)"""
    texto = re.sub(r'[*_#`>\[\]{}|~^]', ' ', texto)
    texto = ''.join(c for c in texto if unicodedata.category(c)[0] in 'LNPZ' or c in '¡¿')
    texto = re.sub(r'\s+', ' ', texto).strip()
    return texto or 'Mmm, no se que decir.'


def para_pantalla(texto: str) -> str:
    """Sin tildes ni signos que la letra de Trumi no tiene (deja ¡ ¿ y la ñ pasa a n)"""
    salida = []
    for c in texto:
        if c in '¡¿':
            salida.append(c)
            continue
        base = unicodedata.normalize('NFKD', c).encode('ascii', 'ignore').decode('ascii')
        salida.append(base)
    return ''.join(salida)


# ─── 3. De texto a voz (Piper) ───────────────────────────────────────────────

_voz = None


def voz():
    global _voz
    if _voz is None:
        from piper import PiperVoice
        ruta = CONFIG.get('piper_voz', 'voces/es_AR-daniela-high.onnx')
        if not os.path.isabs(ruta):
            ruta = os.path.join(AQUI, ruta)
        for archivo in (ruta, ruta + '.json'):          # la voz son DOS archivos
            if not os.path.exists(archivo):
                raise FileNotFoundError('Falta el archivo de la voz: %s (ver el paso de las voces en README.md)' % archivo)
        log.info('Cargando la voz %s...', os.path.basename(ruta))
        _voz = PiperVoice.load(ruta)
    return _voz


def _sintetizar(texto: str, velocidad: float):
    """Devuelve (muestras int16, tasa). Funciona con las versiones nuevas y viejas de Piper."""
    v = voz()
    tasa = v.config.sample_rate
    try:                                       # piper-tts 1.3 o mas nuevo
        from piper import SynthesisConfig
        cfg = SynthesisConfig(length_scale=velocidad)
        datos = b''.join(trozo.audio_int16_bytes for trozo in v.synthesize(texto, syn_config=cfg))
    except ImportError:                        # piper-tts 1.2
        datos = b''.join(v.synthesize_stream_raw(texto, length_scale=velocidad))
    return np.frombuffer(datos, dtype='<i2'), tasa


def hablar(texto: str) -> bytes:
    """Texto -> audio crudo (16 bits, mono, 16 kHz). "tono" > 1 la hace mas aguda (mas de nena)."""
    tono = float(CONFIG.get('tono', 1.15))
    # Se sintetiza mas lento y despues se "acelera" con el cambio de tono: asi queda mas
    # aguda pero hablando a la velocidad normal
    muestras, tasa = _sintetizar(texto, velocidad=tono * float(CONFIG.get('lentitud', 1.0)))
    if muestras.size == 0:
        return b''
    # Remuestrear: de "tasa x tono" a 16 kHz
    largo = int(muestras.size * TASA / (tasa * tono))
    x_viejo = np.linspace(0.0, 1.0, num=muestras.size, endpoint=False)
    x_nuevo = np.linspace(0.0, 1.0, num=largo, endpoint=False)
    nuevo = np.interp(x_nuevo, x_viejo, muestras.astype(np.float32))
    return np.clip(nuevo, -32768, 32767).astype('<i2').tobytes()


# ─── Registro de charlas (para que los grandes puedan leerlas) ───────────────

def anotar(escuche: str, respuesta: str):
    if not CONFIG.get('guardar_charlas', True):
        return
    with open(os.path.join(AQUI, 'charlas.log'), 'a', encoding='utf-8') as f:
        f.write('%s  Sofi: %s\n%s  Trumi: %s\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), escuche,
                                                  time.strftime('%Y-%m-%d %H:%M:%S'), respuesta))


# ─── El servidor ─────────────────────────────────────────────────────────────

def codificar(texto: str) -> str:
    # Todo lo que no es ASCII va codificado (%C2%A1 = ¡): asi llega bien a Trumi
    return urllib.parse.quote(texto, safe=' ')


class Cerebro(BaseHTTPRequestHandler):
    server_version = 'TrumiCerebro/1.0'

    def log_message(self, formato, *args):        # sin el registro ruidoso de cada pedido
        pass

    def _clave_ok(self) -> bool:
        clave = CONFIG.get('clave', '')
        if clave and self.headers.get('X-Clave', '') != clave:
            self.send_error(403, 'Falta la clave')
            return False
        return True

    def _leer_cuerpo(self) -> bytes:
        largo = int(self.headers.get('Content-Length', 0))
        if largo > 30 * TASA * 2:                  # mas de 30 segundos de audio: no
            raise ValueError('demasiado largo')
        return self.rfile.read(largo) if largo > 0 else b''

    def _responder_audio(self, audio: bytes, escuche: str, respuesta: str):
        self.send_response(200)
        self.send_header('Content-Type', 'application/octet-stream')
        self.send_header('Content-Length', str(len(audio)))
        self.send_header('X-Escuche', codificar(escuche))
        self.send_header('X-Respuesta', codificar(respuesta))
        self.send_header('X-Pantalla', codificar(para_pantalla(respuesta)))
        self.end_headers()
        self.wfile.write(audio)

    def _responder_json(self, datos, codigo=200):
        cuerpo = json.dumps(datos, ensure_ascii=False).encode('utf-8')
        self.send_response(codigo)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def _ruta(self) -> str:
        """El pedido sin "?..." y sin barras de mas ("//charlar/" -> "/charlar")"""
        ruta = re.sub(r'/+', '/', urllib.parse.urlsplit(self.path).path)
        return ruta.rstrip('/') or '/'

    def do_GET(self):
        if self._ruta() != '/estado':
            self.send_error(404)
            return
        self._responder_json({'ok': True, 'modelo': CONFIG.get('ollama_modelo'),
                              'whisper': CONFIG.get('whisper_modelo'),
                              'voz': os.path.basename(CONFIG.get('piper_voz', ''))})

    def do_POST(self):
        if not self._clave_ok():
            return
        ruta = self._ruta()
        t0 = time.time()
        try:
            cuerpo = self._leer_cuerpo()
            t0 = time.time()
            if ruta == '/charlar':
                escuche = escuchar(cuerpo)
                t1 = time.time()
                if not escuche:
                    respuesta = '¿Me lo decis de nuevo? No te escuche bien.'
                else:
                    respuesta = pensar(escuche)
            elif ruta in ('/preguntar', '/decir'):
                texto = json.loads(cuerpo.decode('utf-8')).get('texto', '').strip()
                escuche, t1 = texto, time.time()
                respuesta = pensar(texto) if ruta == '/preguntar' else texto
            else:
                log.warning('Me pidieron "%s", que no existe', self.path)
                self.send_error(404)
                return
            t2 = time.time()
            audio = hablar(respuesta)
            t3 = time.time()
            log.info('Escuche: "%s" -> "%s"  (oido %.1f s, pensar %.1f s, voz %.1f s)',
                     escuche, respuesta, t1 - t0, t2 - t1, t3 - t2)
            if ruta != '/decir':
                anotar(escuche, respuesta)
            self._responder_audio(audio, escuche, respuesta)
        except (BrokenPipeError, ConnectionResetError):
            # Trumi corto antes de que llegara la respuesta (se canso de esperar)
            log.warning('Trumi se canso de esperar y corto (%.0f s): no le llego la respuesta',
                        time.time() - t0)
        except Exception as e:                     # que un error no tire abajo al cerebro
            log.exception('Error atendiendo %s', ruta)
            try:
                self._responder_json({'ok': False, 'error': str(e)}, 500)
            except OSError:                        # Trumi ya habia cortado
                pass


def precalentar():
    """Carga todo y hace una pasada de prueba: la primera vez que se usa cada cosa tarda
    mucho mas (se carga en memoria). Asi la primera pregunta de Sofi ya sale rapida."""
    t0 = time.time()
    list(whisper().transcribe(np.zeros(TASA, dtype=np.float32), language='es', beam_size=1)[0])
    hablar('Hola')
    log.info('Whisper y la voz listos (%.1f s)', time.time() - t0)
    t0 = time.time()
    try:
        # Con un pedido vacio, Ollama carga el modelo en memoria (y keep_alive -1: para siempre)
        pedido = {'model': CONFIG.get('ollama_modelo', 'qwen2.5:1.5b'), 'prompt': '', 'keep_alive': -1}
        url = CONFIG.get('ollama_url', 'http://localhost:11434') + '/api/generate'
        req = urllib.request.Request(url, data=json.dumps(pedido).encode('utf-8'),
                                     headers={'Content-Type': 'application/json'})
        urllib.request.urlopen(req, timeout=300).read()
        log.info('Modelo de Ollama cargado (%.1f s)', time.time() - t0)
    except Exception as e:                         # si Ollama todavia no arranco, se carga en la primera charla
        log.warning('No pude precargar el modelo de Ollama (%s): se va a cargar en la primera charla', e)


def main():
    puerto = int(CONFIG.get('puerto', 8765))
    # Cargar todo de entrada, asi la primera charla no tarda de mas
    if '--sin-precargar' not in sys.argv:
        try:
            precalentar()
        except FileNotFoundError as e:
            log.error('%s', e)
            sys.exit(1)
    servidor = ThreadingHTTPServer(('0.0.0.0', puerto), Cerebro)
    log.info('¡El cerebro de Trumi esta listo! Escuchando en el puerto %d', puerto)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
