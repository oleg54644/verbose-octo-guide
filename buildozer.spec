# buildozer.spec — конфиг python-for-android для сборки APK (точка входа — main.py).
# Собирать нужно на Linux (Ubuntu 24.04/22.04): p4a не работает на Windows.
# Проще всего — кнопка в GitHub Actions (.github/workflows/build-apk.yml).
# Локально:
#   sudo apt install -y git zip unzip openjdk-17-jdk python3-pip autoconf libtool \
#                        pkg-config zlib1g-dev libncurses-dev libtinfo-dev libffi-dev libssl-dev
#   pip install buildozer==1.5.0 cython==0.29.36
#   buildozer android debug
# Результат: bin/messenger-1.0-arm64-v8a_armeabi-v7a-debug.apk
[app]

title = Мессенджер
package.name = messenger
package.domain = org.chat

source.dir = .
source.include_exts = py,png,jpg,kv,atlas,ttf
# В APK попадают только файлы клиента, сервер и десктоп-версии — нет
source.include_patterns = main.py,audio_android.py,Arial-Regular.ttf,icon.png
source.exclude_dirs = dist,build,bin,.buildozer,.git,__pycache__,.kivy,.github

version = 1.0

# kivy и pyjnius ставятся вместе с p4a; android и requests не нужны
requirements = python3,kivy==2.3.0,pyjnius

orientation = portrait
fullscreen = 0

android.permissions = INTERNET, RECORD_AUDIO, MODIFY_AUDIO_SETTINGS, WAKE_LOCK

android.api = 33
android.minapi = 24
android.ndk = 25b
android.archs = arm64-v8a, armeabi-v7a

# Фиксируем проверенную версию python-for-android (Python 3.11.5, совместим с kivy 2.3.0).
# На свежем master kivy не собирается (ошибки vertex.c / cgl.c).
p4a.branch = v2024.01.21

android.accept_sdk_license = True
# хранилище настроек (chat_settings.json: сервер, имя, устройство)
android.allow_backup = True
android.presplash_color = #ffffff
# иконка приложения (создаётся build_apk.py или берётся из icon.png)
icon.filename = %(source.dir)s/icon.png

[buildozer]
log_level = 2
build_dir = ./.buildozer
warn_on_root = 0
