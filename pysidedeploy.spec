[app]

# title of the application, shown in the bundle and the window manager
title = ES Soundtrack Tool

# project root directory. empty means the parent directory of input_file
project_dir = .

# source file entry point. the build directives are read from main.py
input_file = main.py

# directory where the executable output is generated
exec_directory = dist

# path to the project file relative to project_dir
project_file =

# application icon. scripts/select_icon.py writes the format this platform
# needs (icns, ico or png)
icon = assets/icons/soundtrack.icns

[python]

# packages pyside6-deploy installs for the build. the version also appears in
# the release workflow and in the build extra, so all three stay in step
packages = Nuitka==4.1.1

[qt]

# paths to required qml files, comma separated. this app has no qml
qml_files =

# excluded qml plugin binaries
excluded_qml_plugins =

# qt modules used, comma separated. listing them avoids a recursive scan of
# the checkout for python files; multimedia is the preview player
modules = Concurrent,Core,DBus,Gui,Multimedia,Network,Widgets

# qt plugins used by the application. the tool detects them when this is empty
# and writes them back; pinned to the media service the player needs, because
# detection also drags in the virtual keyboard (and quick with it)
plugins = multimedia

[nuitka]

# macos usage descriptions. the app needs no protected resources
macos.permissions =

# a standalone folder is faster to start than onefile and keeps the bundled
# data and assets directories visible next to the executable
mode = standalone

# --assume-yes-for-downloads lets the build fetch ccache and the dependency
# walker on a clean runner. translations are not used by this app
extra_args = --quiet --assume-yes-for-downloads --noinclude-qt-translations
