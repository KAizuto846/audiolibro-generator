# Audiolibro Generator

Genera tus propios audiolibros en tu máquina: convierte libros en **TXT, EPUB o PDF** a audio con **Kokoro TTS** (síntesis de voz local, gratuita y sin nube).

## Qué hace

- Interfaz web (Flask) para subir un libro y elegir voz.
- Convierte el texto del libro en voz humana con **Kokoro TTS**.
- Generación por hilos con **progreso en tiempo real** en la página.
- Voz masculina y femenina (`ef_dora`, `em_alex`, `em_santa`).
- Los audiolibros generados quedan en `libros/` listos para escuchar.

## Requisitos

- Python 3.11+
- Dependencias en `requirements.txt` (Flask, Kokoro, torch, ebooklib, pypdf2, beautifulsoup4, lxml, soundfile)

## Uso

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python app.py
```

Abre http://localhost:5050, sube tu libro y genera tu audiolibro.

## Archivos

- `app.py` — servidor Flask (subida, progreso, descarga).
- `generator.py` — extracción de texto (TXT/EPUB/PDF) y generación de audio con Kokoro.
- `templates/` y `static/` — interfaz web.

## Proyectos de ejemplo

- "El cisne negro" (EPUB) y "El Bosque Encantado" (TXT) han sido convertidos con este mismo programa.