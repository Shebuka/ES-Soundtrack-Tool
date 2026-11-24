# pyside-deploy configuration for es-soundtrack-tool.
# Validate against the installed pyside-deploy version before a release build.
[app]
input_file=src/es_ost/gui/app.py
name=es-soundtrack-tool
title=ES Soundtrack Tool
version=0.1.0

[python]
packages=es_ost

# The root .env is embedded via resource_data.qrc -> rc_data.py, not here.
# data/ is the frozen dataset; assets/ holds the static ffmpeg binary.
[nuitka]
extra_args=--include-data-dir=data=data --include-data-dir=assets=assets

[pyinstaller]
extra_args=--add-data data:data --add-data assets:assets
