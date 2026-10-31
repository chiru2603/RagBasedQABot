import re


def chunks(text, size=1800, overlap=200):
    """Heading-aware character windows; bounded even for long unbroken text."""
    if size < 100 or not 0 <= overlap < size:
        raise ValueError('Invalid chunk size or overlap')
    heading = 'Document'
    body = []
    sections = []
    for line in text.splitlines():
        if re.match(r'^#{1,6}\s', line):
            if body:
                sections.append((heading, '\n'.join(body)))
            heading = line.lstrip('#').strip()
            body = []
        else:
            body.append(line)
    sections.append((heading, '\n'.join(body)))
    for heading, body in sections:
        body = body.strip()
        for start in range(0, len(body), size - overlap):
            piece = body[start:start + size].strip()
            if piece:
                yield heading, piece
            if start + size >= len(body):
                break


def read_document(path):
    if path.suffix.lower() == '.pdf':
        from pypdf import PdfReader
        pages = [p.extract_text() or '' for p in PdfReader(path).pages]
        if not any(p.strip() for p in pages):
            raise ValueError(f'{path.name}: no extractable text; OCR is not included')
        return '\n'.join(f'# Page {i}\n{p}' for i, p in enumerate(pages, 1))
    return path.read_text(encoding='utf-8')
