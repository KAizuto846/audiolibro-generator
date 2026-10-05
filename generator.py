#!/usr/bin/env python3
"""
Generador de audiolibros con Kokoro TTS.
Soporta TXT, EPUB, PDF.
"""
import os
import json
import re
import sys
import time
import threading
from pathlib import Path

# ── Extractor de texto según formato ──────────────────────────────

def extraer_texto(ruta, formato=None):
    """Extrae el texto completo de un libro y devuelve (titulo, lista_de_capitulos)."""
    ruta = Path(ruta)
    if formato is None:
        formato = ruta.suffix.lower().lstrip('.')
    
    if formato == 'txt':
        return _extraer_txt(ruta)
    elif formato == 'epub':
        return _extraer_epub(ruta)
    elif formato == 'pdf':
        return _extraer_pdf(ruta)
    else:
        raise ValueError(f"Formato no soportado: {formato}")

def _extraer_txt(ruta):
    with open(ruta, 'r', encoding='utf-8', errors='replace') as f:
        texto = f.read()
    titulo = ruta.stem
    capitulos = _dividir_en_capitulos(texto)
    return titulo, capitulos

def _extraer_epub(ruta):
    import ebooklib
    from ebooklib import epub
    from bs4 import BeautifulSoup
    book = epub.read_epub(ruta)
    titulo = book.get_metadata('DC', 'title')
    titulo = titulo[0][0] if titulo else ruta.stem
    
    capitulos = []
    for item in book.get_items():
        if item.get_type() == ebooklib.ITEM_DOCUMENT:
            soup = BeautifulSoup(item.get_content(), 'html.parser')
            texto = soup.get_text(separator='\n').strip()
            if len(texto) > 50:
                capitulos.append(texto)
    
    if not capitulos:
        capitulos = _dividir_en_capitulos('\n\n'.join(capitulos))
    
    return titulo, capitulos

def _extraer_pdf(ruta):
    from PyPDF2 import PdfReader
    reader = PdfReader(ruta)
    titulo = reader.metadata.title if reader.metadata and reader.metadata.title else ruta.stem
    texto_completo = ''
    for page in reader.pages:
        texto = page.extract_text() or ''
        texto_completo += texto + '\n\n'
    capitulos = _dividir_en_capitulos(texto_completo)
    return titulo, capitulos

def _dividir_en_capitulos(texto):
    """Divide el texto en capítulos. Busca patrones como 'Capítulo', 'Chapter', números romanos."""
    # Patrones comunes de capítulos
    patrones = [
        r'(?:^|\n)\s*(?:Cap[íi]tulo|Chapter|Lecci[óo]n|Parte|Tema)\s+[IVXLCDM\d]+[.:\s]',
        r'(?:^|\n)\s*(?:CAP[ÍI]TULO|CHAPTER|LECCI[ÓO]N|PARTE|TEMA)\s+[IVXLCDM\d]+[.:\s]',
        r'(?:^|\n)\s*\d+[.:]\s+(?:[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+[ ]?){2,}',
    ]
    
    for patron in patrones:
        matches = list(re.finditer(patron, texto))
        if len(matches) >= 2:
            capitulos = []
            for i, match in enumerate(matches):
                start = match.start()
                end = matches[i + 1].start() if i + 1 < len(matches) else len(texto)
                contenido = texto[start:end].strip()
                if len(contenido) > 100:
                    capitulos.append(contenido)
            if capitulos:
                return capitulos
    
    # Si no encuentra capítulos, divide por párrafos grandes (~3000 chars)
    parrafos = [p.strip() for p in texto.split('\n\n') if p.strip()]
    capitulos = []
    actual = ''
    for p in parrafos:
        if len(actual) + len(p) > 3000 and actual:
            capitulos.append(actual)
            actual = p
        else:
            actual = actual + '\n\n' + p if actual else p
    if actual:
        capitulos.append(actual)
    
    return capitulos if capitulos else [texto[:5000]]


# ── Generación TTS ────────────────────────────────────────────────

class ProgresoGeneracion:
    """Objeto compartido para trackear progreso entre hilos."""
    def __init__(self, total_chunks=0):
        self.total = total_chunks
        self.completado = 0
        self.actual = ''
        self.errores = []
        self.cancelar = False
        self.iniciado = time.time()
    
    def avanzar(self, nombre):
        self.completado += 1
        self.actual = nombre
    
    def tiempo_transcurrido(self):
        s = int(time.time() - self.iniciado)
        m, s = divmod(s, 60)
        h, m = divmod(m, 60)
        if h:
            return f"{h}h {m:02d}m {s:02d}s"
        return f"{m:02d}:{s:02d}"


def generar_audiolibro(book_id, capitulos, voces_por_capitulo=None,
                       voz_default='ef_dora', velocidad=1.0,
                        lang='e', callback_progreso=None):
    """
    Genera audio para cada capítulo usando Kokoro.
    Retorna lista de rutas de archivos generados.
    """
    from kokoro import KPipeline
    import soundfile as sf
    import torch
    import numpy as np
    
    pipeline = KPipeline(lang_code=lang, device='cpu')
    
    # Directorio de salida
    out_dir = Path(__file__).parent / 'libros' / book_id
    out_dir.mkdir(parents=True, exist_ok=True)
    
    archivos = []
    progreso = ProgresoGeneracion(total_chunks=len(capitulos))
    
    for idx, texto in enumerate(capitulos):
        if progreso.cancelar:
            break
        
        num_cap = idx + 1
        nombre_cap = f"Capítulo {num_cap}"
        voz = voces_por_capitulo.get(str(num_cap), voz_default) if voces_por_capitulo else voz_default
        
        progreso.actual = f"{nombre_cap} ({voz})"
        
        try:
            # Generar audio con Kokoro
            chunks_audio = []
            gen = pipeline(texto, voice=voz, speed=velocidad)
            for result in gen:
                if result.audio is not None:
                    chunks_audio.append(result.audio)
            
            if chunks_audio:
                audio = torch.cat(chunks_audio, dim=0)
                # Normalizar
                audio = audio / (audio.abs().max() + 1e-8)
                
                out_path = out_dir / f"cap_{num_cap:03d}.wav"
                sf.write(str(out_path), audio.numpy(), 24000)
                archivos.append(str(out_path))
            
        except Exception as e:
            progreso.errores.append(f"{nombre_cap}: {str(e)}")
        
        progreso.avanzar(nombre_cap)
        
        if callback_progreso:
            callback_progreso(progreso)
    
    # Guardar metadatos
    meta = {
        'titulo': book_id,
        'fecha': time.strftime('%Y-%m-%d %H:%M:%S'),
        'capitulos': len(archivos),
        'total_capitulos': len(capitulos),
        'voz_default': voz_default,
        'velocidad': velocidad,
        'idioma': lang,
        'archivos': archivos,
        'errores': progreso.errores,
        'tiempo_total_s': int(time.time() - progreso.iniciado)
    }
    with open(out_dir / 'metadata.json', 'w', encoding='utf-8') as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    
    meta['progreso'] = progreso
    return meta


def generar_hilo(book_id, capitulos, **kwargs):
    """Ejecuta generar_audiolibro en un hilo separado con callback."""
    resultado = {'meta': None, 'error': None}
    
    def _run():
        try:
            meta = generar_audiolibro(book_id, capitulos, **kwargs)
            resultado['meta'] = meta
        except Exception as e:
            resultado['error'] = str(e)
    
    hilo = threading.Thread(target=_run, daemon=True)
    hilo.start()
    return hilo, resultado


if __name__ == '__main__':
    # Test rápido
    test_texto = [
        "Capítulo 1: El comienzo. Esta es una prueba del generador de audiolibros con Kokoro TTS en español.",
        "Capítulo 2: El desarrollo. El audio se genera capítulo por capítulo usando inteligencia artificial.",
    ]
    meta = generar_audiolibro("prueba", test_texto, voz_default='ef_dora')
    print(f"Generados {len(meta['archivos'])} archivos en {meta['tiempo_total_s']}s")
    for a in meta['archivos']:
        print(f"  ✓ {a}")
