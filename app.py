#!/usr/bin/env python3
"""
Audiolibro Generator - Interfaz Web
Servidor Flask para generar audiolibros con Kokoro TTS.
"""
import os
import sys
import json
import time
import threading
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_file

# Asegurar que el directorio del proyecto esté en el path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from generator import extraer_texto, generar_audiolibro, generar_hilo

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max upload
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(__file__), 'textos')
app.config['LIBROS_FOLDER'] = os.path.join(os.path.dirname(__file__), 'libros')
app.config['PROGRESO_FOLDER'] = os.path.join(os.path.dirname(__file__), 'progreso')

# Estado global de generación
jobs = {}  # job_id -> {hilo, resultado, progreso, estado}


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/libros')
def listar_libros():
    """Lista libros disponibles (textos fuente y ya generados)."""
    textos = []
    libros_dir = Path(app.config['LIBROS_FOLDER'])
    textos_dir = Path(app.config['UPLOAD_FOLDER'])
    
    # Libros fuente
    if textos_dir.exists():
        for f in sorted(textos_dir.iterdir()):
            if f.suffix.lower() in ('.txt', '.epub', '.pdf'):
                tamano = f.stat().st_size
                textos.append({
                    'nombre': f.name,
                    'ruta': str(f),
                    'formato': f.suffix[1:].lower(),
                    'tamano': _formatear_tamano(tamano),
                    'tamano_bytes': tamano
                })
    
    # Libros generados
    generados = []
    if libros_dir.exists():
        for d in sorted(libros_dir.iterdir()):
            if d.is_dir():
                meta_path = d / 'metadata.json'
                info = {'id': d.name}
                if meta_path.exists():
                    with open(meta_path) as f:
                        meta = json.load(f)
                    info['meta'] = meta
                    info['capitulos'] = sorted([
                        str(p) for p in d.glob('cap_*.wav')
                    ])
                    info['tamano_total'] = sum(
                        p.stat().st_size for p in d.glob('*.wav')
                    )
                generados.append(info)
    
    return jsonify({'textos': textos, 'generados': generados})


@app.route('/api/upload', methods=['POST'])
def subir_libro():
    """Sube un archivo de libro."""
    if 'file' not in request.files:
        return jsonify({'error': 'No se envió ningún archivo'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'Nombre de archivo vacío'}), 400
    
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ('.txt', '.epub', '.pdf'):
        return jsonify({'error': 'Formato no soportado. Usa TXT, EPUB o PDF'}), 400
    
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    ruta = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
    file.save(ruta)
    
    return jsonify({'ok': True, 'archivo': file.filename})


@app.route('/api/preview', methods=['POST'])
def previsualizar():
    """Previsualiza un libro: extrae capítulos y los muestra."""
    data = request.json
    archivo = data.get('archivo')
    if not archivo:
        return jsonify({'error': 'Falta nombre de archivo'}), 400
    
    ruta = os.path.join(app.config['UPLOAD_FOLDER'], archivo)
    if not os.path.exists(ruta):
        return jsonify({'error': 'Archivo no encontrado'}), 404
    
    try:
        titulo, capitulos = extraer_texto(ruta)
        # Resumen de capítulos
        resumen = []
        for i, cap in enumerate(capitulos, 1):
            preview = cap[:200].replace('\n', ' ').strip()
            resumen.append({
                'num': i,
                'preview': preview + ('...' if len(cap) > 200 else ''),
                'chars': len(cap),
                'estimado_seg': _estimar_duracion(len(cap))
            })
        return jsonify({
            'ok': True,
            'titulo': titulo,
            'total_capitulos': len(capitulos),
            'capitulos': resumen
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/generar', methods=['POST'])
def iniciar_generacion():
    """Inicia la generación del audiolibro en segundo plano."""
    data = request.json
    archivo = data.get('archivo')
    voz = data.get('voz', 'ef_dora')
    velocidad = data.get('velocidad', 1.0)
    capitulos_seleccion = data.get('capitulos')  # lista de nums o None = todos
    
    if not archivo:
        return jsonify({'error': 'Falta archivo'}), 400
    
    ruta = os.path.join(app.config['UPLOAD_FOLDER'], archivo)
    if not os.path.exists(ruta):
        return jsonify({'error': 'Archivo no encontrado'}), 404
    
    try:
        titulo, capitulos = extraer_texto(ruta)
    except Exception as e:
        return jsonify({'error': f'Error al leer: {str(e)}'}), 500
    
    # Filtrar capítulos si se especificó
    if capitulos_seleccion:
        indices = [int(i) - 1 for i in capitulos_seleccion if 1 <= int(i) <= len(capitulos)]
        capitulos_filtrados = [capitulos[i] for i in indices]
    else:
        capitulos_filtrados = capitulos
        indices = list(range(len(capitulos)))
    
    if not capitulos_filtrados:
        return jsonify({'error': 'No hay capítulos para generar'}), 400
    
    # ID único para el job
    book_id = f"{titulo.replace(' ', '_')}_{int(time.time())}"
    job_id = book_id
    
    # Callback de progreso
    progreso = {'porcentaje': 0, 'actual': '', 'completados': 0, 'total': len(capitulos_filtrados)}
    
    def callback(prog):
        progreso['completados'] = prog.completado
        progreso['total'] = prog.total
        progreso['actual'] = prog.actual
        progreso['porcentaje'] = int(prog.completado / max(prog.total, 1) * 100)
        progreso['tiempo'] = prog.tiempo_transcurrido()
    
    hilo, resultado = generar_hilo(
        book_id=titulo,
        capitulos=capitulos_filtrados,
        voz_default=voz,
        velocidad=velocidad,
        callback_progreso=callback
    )
    
    jobs[job_id] = {
        'hilo': hilo,
        'resultado': resultado,
        'progreso': progreso,
        'estado': 'generando'
    }
    
    return jsonify({'ok': True, 'job_id': job_id})


@app.route('/api/progreso/<job_id>')
def ver_progreso(job_id):
    """Consulta el progreso de un job."""
    job = jobs.get(job_id)
    if not job:
        return jsonify({'error': 'Job no encontrado'}), 404
    
    hilo = job['hilo']
    resultado = job['resultado']
    progreso = job['progreso']
    
    if not hilo.is_alive():
        if resultado['error']:
            job['estado'] = 'error'
            progreso['error'] = resultado['error']
        elif resultado['meta']:
            job['estado'] = 'completado'
            meta = resultado['meta']
            progreso['meta'] = {
                'titulo': meta['titulo'],
                'archivos': meta.get('archivos', []),
                'capitulos': meta['capitulos'],
                'tiempo_total_s': meta.get('tiempo_total_s', 0),
                'errores': meta.get('errores', [])
            }
    
    progreso['estado'] = job['estado']
    return jsonify(progreso)


@app.route('/api/audio/<path:ruta_audio>')
def servir_audio(ruta_audio):
    """Sirve un archivo de audio para reproducción."""
    ruta = os.path.join(app.config['LIBROS_FOLDER'], ruta_audio)
    if not os.path.exists(ruta):
        return jsonify({'error': 'Audio no encontrado'}), 404
    return send_file(ruta, mimetype='audio/wav')


@app.route('/api/eliminar/<book_id>', methods=['DELETE'])
def eliminar_libro(book_id):
    """Elimina un libro generado."""
    ruta = os.path.join(app.config['LIBROS_FOLDER'], book_id)
    if not os.path.exists(ruta):
        return jsonify({'error': 'No encontrado'}), 404
    
    import shutil
    shutil.rmtree(ruta)
    return jsonify({'ok': True})


def _formatear_tamano(bytes):
    for unidad in ['B', 'KB', 'MB', 'GB']:
        if bytes < 1024:
            return f"{bytes:.1f} {unidad}"
        bytes /= 1024
    return f"{bytes:.1f} TB"


def _estimar_duracion(chars):
    """Estima duración en segundos (español ~12 chars/seg)."""
    return round(chars / 12)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5050))
    print(f"🎧 Audiolibro Generator corriendo en http://localhost:{port}")
    print(f"   Voces disponibles: ef_dora (fem), em_alex (masc), em_santa (masc)")
    app.run(host='0.0.0.0', port=port, debug=True, threaded=True)
