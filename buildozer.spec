[app]
title = Мой плеер
package.name = myplayer
package.domain = org.myplayer
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,json
source.exclude_dirs = tools,bin,.buildozer,__pycache__
version = 1.0
# Версии зафиксированы: новый python-for-android (master) собирает на Python 3.14,
# с которым Kivy 2.3.0 не компилируется. Здесь взята связка с Python 3.11.
requirements = python3==3.11.5,hostpython3==3.11.5,kivy==2.3.0,pyjnius,android,certifi,mutagen,openssl
p4a.branch = v2024.01.21
orientation = portrait
fullscreen = 0
icon.filename = %(source.dir)s/assets/icon.png

# доступ к музыке на телефоне, интернет для скачивания, не засыпать при игре
android.permissions = INTERNET,READ_EXTERNAL_STORAGE,READ_MEDIA_AUDIO,WAKE_LOCK
android.api = 33
android.minapi = 24
android.ndk = 25b
android.archs = arm64-v8a
android.accept_sdk_license = True
android.allow_backup = True

[buildozer]
log_level = 2
warn_on_root = 1
