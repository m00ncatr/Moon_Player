[app]
title = Мой плеер
package.name = myplayer
package.domain = org.myplayer
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,json
source.exclude_dirs = tools,bin,.buildozer,__pycache__
version = 1.0
requirements = python3==3.11.11,hostpython3==3.11.11,kivy==2.3.1,pyjnius,android,certifi,mutagen,openssl,requests,urllib3,idna,chardet,filetype,six
orientation = portrait
fullscreen = 0
icon.filename = %(source.dir)s/assets/icon.png

# доступ к музыке на телефоне, интернет для скачивания, не засыпать при игре
android.permissions = INTERNET,READ_EXTERNAL_STORAGE,READ_MEDIA_AUDIO,WAKE_LOCK
android.api = 33
android.minapi = 24
android.archs = arm64-v8a, armeabi-v7a
android.accept_sdk_license = True
android.allow_backup = True

[buildozer]
log_level = 2
warn_on_root = 1
