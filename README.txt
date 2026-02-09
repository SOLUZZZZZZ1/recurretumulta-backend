RTM backend multi-documento (MVP)
- Archivo: analyze_expediente.py
- Añade endpoint: POST /analyze/expediente (files[] hasta 5)
- Para activarlo: importar e incluir router en app.py:
    from analyze_expediente import router as analyze_expediente_router
    app.include_router(analyze_expediente_router)
