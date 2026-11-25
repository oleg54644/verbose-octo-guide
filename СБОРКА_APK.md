# СБОРКА APK — начните отсюда

Этот архив — всё, что нужно, чтобы получить **APK-файл приложения «Мессенджер»**
без установки Android Studio и SDK на свой компьютер.

## Быстрый путь (GitHub, ~20–40 минут)

1. Создайте новый репозиторий на GitHub (можно приватный).
2. Загрузите в него **всё содержимое этого архива**. Важно сохранить папку
   `.github/workflows/build-apk.yml` — GitHub ищет сборку именно там.
   (Через сайт: *Add file → Upload files* — перетащите файлы и папку `.github`.)
3. Откройте вкладку **Actions** → слева **Build APK** → **Run workflow**.
4. Через 15–40 минут (первая сборка дольше — скачиваются Android SDK и NDK)
   откройте завершённый запуск и скачайте артефакт **messenger-debug-apk** —
   внутри `messenger-1.0-arm64-v8a_armeabi-v7a-debug.apk`.
5. Скопируйте APK на телефон и установите (разрешите установку из неизвестных источников).

Если сборка упала — в том же запуске будет артефакт **build-log** с полным логом.

## Что делать после установки APK

Телефон не содержит сервера: приложению нужен компьютер в той же Wi-Fi-сети,
где запущен сервер (`server_windows` в основном архиве):

```powershell
cd <папка с server.py>
$env:CHAT_BIND="0.0.0.0"; Start-Process python -ArgumentList "server.py" -WindowStyle Hidden
```

В приложении: **IP компьютера** (из `ipconfig`, раздел беспроводной сети, `IPv4-адрес`),
порт `55`, имя, при желании логин и пароль (даёт постоянный ID).

## Где что лежит

| Файл | Роль |
|---|---|
| `main.py` | само приложение (экраны, чаты, группы, личные сообщения, звонки) |
| `audio_android.py` | звук звонка на Android (`AudioRecord`/`AudioTrack`) |
| `buildozer.spec` | конфиг сборки: имя пакета, разрешения, API level, архитектуры |
| `Arial-Regular.ttf` | шрифт с кириллицей (штатный шрифт Kivy её не рисует) |
| `icon.png` | иконка приложения |
| `.github/workflows/build-apk.yml` | сам workflow сборки APK на GitHub Actions |
| `build_apk.py` | необязательно: подготовка и сборка локально (нужен Linux/WSL) |

## Локальная сборка (если есть Linux или WSL)

```bash
sudo apt install -y git zip unzip openjdk-17-jdk python3-pip autoconf libtool \
     pkg-config zlib1g-dev libncurses-dev libtinfo-dev libffi-dev libssl-dev
pip3 install buildozer "cython<3"
python3 build_apk.py          # или: buildozer android debug
```

Готовый файл: `bin/messenger-1.0-arm64-v8a_armeabi-v7a-debug.apk`.

> На Windows APK не собирается: python-for-android и Android NDK требуют Linux.
> Поэтому и нужен GitHub Actions (или WSL).

## Проверить приложение без телефона

Скопируйте в этот же архив `server.py` и запустите:

```bash
pip install kivy
python main.py      # то же приложение, но в окне на компьютере
python server.py              # и сервер рядом
```
