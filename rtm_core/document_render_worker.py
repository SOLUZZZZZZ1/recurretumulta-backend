"""Isolated renderer: document bytes on stdin, metadata or PNG on stdout.

No storage clients, application configuration, URLs, JavaScript or temporary
document files. Called by document_viewer, never exposed directly as a route.
"""
import io
import json
import math
import sys
import warnings

MAX_BYTES = 12 * 1024 * 1024
MAX_PAGES = 200
MAX_PIXELS = 4_000_000


class RenderRejected(ValueError):
    pass


def _limits(width, height):
    if not all(math.isfinite(x) and 0 < x <= 100_000 for x in (width, height)):
        raise RenderRejected()


def _scale(width, height, target):
    _limits(width, height)
    return min(target / width, math.sqrt(MAX_PIXELS / (width * height)))


def inspect_or_render(data, mime, page_number=None, target_width=1400):
    from PIL import Image, ImageOps
    if not 1 <= len(data) <= MAX_BYTES or not 400 <= target_width <= 2000:
        raise RenderRejected()
    if mime == 'application/pdf':
        if not data.startswith(b'%PDF-'):
            raise RenderRejected()
        import pypdfium2 as pdfium
        with pdfium.PdfDocument(data) as document:
            count = len(document)
            if not 1 <= count <= MAX_PAGES:
                raise RenderRejected()
            number = 1 if page_number is None else page_number
            if not 1 <= number <= count:
                raise RenderRejected()
            page = document[number - 1]
            try:
                width, height = page.get_size()
                _limits(width, height)
                if page_number is None:
                    return {'pages': count, 'width': width, 'height': height}
                # PDFium runs in this process alone, not concurrent FastAPI threads.
                # Standard wheel has no V8/XFA. No document actions are executed.
                bitmap = page.render(scale=_scale(width, height, target_width),
                                     may_draw_forms=False)
                try:
                    image = bitmap.to_pil()
                    try:
                        output = io.BytesIO()
                        image.save(output, format='PNG')
                        return output.getvalue()
                    finally:
                        image.close()
                finally:
                    bitmap.close()
            finally:
                page.close()
    expected = {'image/jpeg': 'JPEG', 'image/png': 'PNG', 'image/webp': 'WEBP'}.get(mime)
    if not expected:
        raise RenderRejected()
    Image.MAX_IMAGE_PIXELS = 24_000_000
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as original:
            if original.format != expected or getattr(original, 'n_frames', 1) != 1:
                raise RenderRejected()
            with ImageOps.exif_transpose(original) as image:
                _limits(*image.size)
                if page_number is None:
                    return {'pages': 1, 'width': image.width, 'height': image.height}
                if page_number != 1:
                    raise RenderRejected()
                scale = min(1, _scale(image.width, image.height, target_width))
                with image.convert('RGB') as rgb:
                    rgb.thumbnail((max(1, int(image.width * scale)), max(1, int(image.height * scale))))
                    # Re-encode pixels only; never forward original metadata or attachments.
                    rgb.info.clear()
                    output = io.BytesIO()
                    rgb.save(output, format='PNG')
                    return output.getvalue()


def main():
    try:
        try:
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (384 * 1024 * 1024,) * 2)
            resource.setrlimit(resource.RLIMIT_CPU, (8, 8))
        except ImportError:
            pass  # Windows tests: parent timeout + pixel/input bounds still apply.
        mode, mime = sys.argv[1:3]
        if mode not in {'inspect', 'page'}:
            raise RenderRejected()
        page = int(sys.argv[3]) if mode == 'page' else None
        width = int(sys.argv[4]) if mode == 'page' else 1400
        result = inspect_or_render(sys.stdin.buffer.read(MAX_BYTES + 1), mime, page, width)
        if mode == 'inspect':
            sys.stdout.buffer.write(json.dumps(result).encode('ascii'))
        else:
            sys.stdout.buffer.write(result)
    except Exception:
        # Never print exception text or a traceback derived from private input.
        sys.stderr.write('DOCUMENT_RENDER_REJECTED')
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
