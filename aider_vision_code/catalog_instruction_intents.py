import re
import unicodedata


def plain(text):
    return ''.join(c for c in unicodedata.normalize('NFD', str(text).lower())
                   if not unicodedata.combining(c))


def requests_no_scroll(text):
    text=plain(text)
    return bool(re.search(
        r'\b(?:sin|evit\w*|elimin\w*|quit\w*|sac\w*|no\s+(?:sea\s+)?necesari\w*|no\s+(?:quier\w*|mostr\w*))'
        r'[^.\n]{0,70}\b(?:scroll|desplazamiento|barra\w*\s+vertical\w*)\b'
        r'|\b(?:todo|contenido)\b[^.\n]{0,40}\bvisible\b[^.\n]{0,40}\bsin\b[^.\n]{0,30}\b(?:scroll|desplazamiento)\b',
        text))


def requests_preserve_sizes(text):
    text=plain(text)
    return bool(re.search(
        r'\b(?:conserv|manten)\w*\b[^.\n]{0,70}\b(?:tamano\w*|dimension\w*|escala)\b'
        r'|\bno\s+cambi\w*\b[^.\n]{0,70}\b(?:tamano\w*|dimension\w*|escala)\b'
        r'|\b(?:no|sin)\s+escal\w*\b'
        r'|\b(?:no|sin)\s+(?:redu|achic|disminu)\w*\b[^.\n]{0,70}'
        r'\b(?:tamano\w*|dimension\w*|element\w*|control\w*|texto\w*)\b',
        text))


def requests_relayout(text):
    text=plain(text)
    verb=r'\b(?:reorganiz|redistrib|reacomod|reubic|reorden|distribu)\w*\b'
    target=r'\b(?:contenido|seccion\w*|panel\w*|control\w*|campo\w*|componente\w*|elemento\w*)\b'
    axis=r'\b(?:column\w*|fila\w*|ancho|horizontal\w*|vertical\w*|estructura|layout)\b'
    return bool(
        re.search(verb+r'[^.\n]{0,100}'+target+r'[^.\n]{0,100}'+axis,text)
        or re.search(verb+r'[^.\n]{0,100}'+axis+r'[^.\n]{0,100}'+target,text)
        or re.search(target+r'[^.\n]{0,100}'+verb+r'[^.\n]{0,100}'+axis,text)
        or re.search(target+r'[^.\n]{0,100}'+axis+r'[^.\n]{0,100}'+verb,text))


def requests_compaction(text):
    text=plain(text)
    return bool(re.search(
        r'\b(?:redu|achic|compact)\w*\b[^.\n]{0,70}'
        r'\b(?:espacio\w*|margen\w*|separacion\w*|padding)\b',text))


def permits_width_growth(text):
    text=plain(text)
    return bool(re.search(
        r'\b(?:podes?|permit\w*|autoriz\w*)\b[^.\n]{0,70}'
        r'\b(?:aument|ampli|ensancha)\w*\b[^.\n]{0,45}\bancho\b'
        r'|\b(?:aument|ampli|ensancha)\w*\b[^.\n]{0,45}\bancho\b[^.\n]{0,70}'
        r'\b(?:permit\w*|autoriz\w*)\b',text))
