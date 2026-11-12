# 🧠 El cerebro de Trumi

Trumi es chiquita: en su placa no entra una inteligencia artificial. Por eso su **cerebro**
vive en la **Raspberry Pi** de la casa. Cuando Sofi le habla, Trumi graba lo que escucha y
se lo manda al cerebro, que:

1. 👂 **Escucha:** convierte la voz en texto (con **Whisper**)
2. 💭 **Piensa:** decide que contestar (con un modelo de IA en **Ollama**, con la
   personalidad de Trumi y cuidado para chicos)
3. 🗣️ **Habla:** convierte la respuesta en voz (con **Piper**, voz argentina "Daniela",
   un poquito mas aguda para que suene como una nena)
4. Le devuelve a Trumi la respuesta hablada y escrita

**Todo pasa adentro de la casa:** nada sale a internet.

El codigo de Trumi (la placa) esta en su propio repo: [trumi](https://github.com/javifanelli/trumi).

---

## 🧑‍🔧 Instalar (en la Raspberry Pi)

Hace falta tener **Ollama** andando con el modelo `qwen2.5:1.5b` (`ollama pull qwen2.5:1.5b`).

1. Bajar este repo a la Raspberry:

   ```bash
   cd ~
   git clone https://github.com/javifanelli/cerebro.git
   ```

   Para actualizarlo despues: `cd ~/cerebro && git pull` y reiniciar el servicio
   (`sudo systemctl restart trumi-cerebro`).
2. Crear un entorno de Python e instalar lo necesario:

   ```bash
   cd ~/cerebro
   sudo apt install -y python3-venv
   python3 -m venv venv
   venv/bin/pip install -r requirements.txt
   ```

3. Bajar la voz (dos archivos) a la carpeta `voces/`:

   ```bash
   mkdir -p voces && cd voces
   wget https://huggingface.co/rhasspy/piper-voices/resolve/main/es/es_AR/daniela/high/es_AR-daniela-high.onnx
   wget https://huggingface.co/rhasspy/piper-voices/resolve/main/es/es_AR/daniela/high/es_AR-daniela-high.onnx.json
   cd ..
   ```

4. Copiar `config.ejemplo.json` como `config.json` y poner una **clave** inventada (la misma
   va a ir despues en Trumi). `config.json` no se sube al repo.
5. Probarlo a mano:

   ```bash
   venv/bin/python trumi_cerebro.py
   ```

   La primera vez baja solo el modelo de Whisper (unos 150 MB) y tarda un rato. Cuando dice
   **"¡El cerebro de Trumi esta listo!"**, ya escucha.

6. Para que arranque solo al prender la Raspberry: editar `trumi-cerebro.service` (cambiar
   `USUARIO` por tu usuario y revisar la carpeta) y:

   ```bash
   sudo cp trumi-cerebro.service /etc/systemd/system/
   sudo systemctl enable --now trumi-cerebro
   journalctl -u trumi-cerebro -f        # para ver que esta haciendo
   ```

## 🧪 Probar desde la compu (antes de conectar a Trumi)

`probar.py` usa solo Python, sin instalar nada:

```bash
# Solo la voz (dice el texto tal cual)
python probar.py --servidor http://IP-DE-LA-RASPBERRY:8765 --clave CLAVE --decir "¡Hola! Soy Trumi"

# El modelo y la voz (pregunta escrita)
python probar.py --servidor http://IP-DE-LA-RASPBERRY:8765 --clave CLAVE --texto "Hola Trumi, ¿como estas?"

# Todo: oido, modelo y voz (pregunta grabada en un .wav de 16 bits)
python probar.py --servidor http://IP-DE-LA-RASPBERRY:8765 --clave CLAVE pregunta.wav
```

Muestra lo que entendio, lo que contesta y cuanto tardo, y guarda la respuesta hablada en
`respuesta.wav`.

## 🎛️ Ajustes (`config.json`)

| Ajuste | Que hace |
|---|---|
| `clave` | Palabra secreta: Trumi la manda en cada pedido (asi nadie mas en la red lo usa) |
| `ollama_modelo` | El modelo de IA (`qwen2.5:1.5b`; con mas paciencia, `qwen2.5:3b` es mas inteligente) |
| `whisper_modelo` | `tiny` (rapidisimo, entiende menos), `base` (equilibrado) o `small` (entiende mejor, mas lento) |
| `tono` | Que tan aguda es la voz: `1.0` natural, `1.15` de nena, `1.3` muy finita |
| `lentitud` | Velocidad al hablar: `1.0` normal, `1.2` mas despacito |
| `olvidar_despues_de_s` | Despues de cuantos segundos sin charlar se olvida de lo que venian hablando |
| `guardar_charlas` | Si anota las charlas en `charlas.log` (para que los grandes puedan leerlas) |

La personalidad de Trumi (como habla, que no tiene que decir) esta en `PERSONALIDAD`, en
`trumi_cerebro.py`.

## 🔌 Como le habla Trumi (HTTP)

| Pedido | Manda | Recibe |
|---|---|---|
| `GET /estado` | — | JSON con el modelo, el Whisper y la voz |
| `POST /charlar` | Audio crudo: 16 bits, mono, 16 kHz | La respuesta en el mismo formato |
| `POST /preguntar` | JSON `{"texto": "..."}` | La respuesta hablada |
| `POST /decir` | JSON `{"texto": "..."}` | Ese texto hablado (sin pensar) |

Cada pedido lleva el encabezado `X-Clave`. Las respuestas de audio traen `X-Escuche` (lo que
entendio), `X-Respuesta` (lo que contesta) y `X-Pantalla` (lo mismo sin tildes, para la
pantalla de Trumi), en UTF-8 "percent-encoded".
