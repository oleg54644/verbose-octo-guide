# build_apk.py — подготовка и запуск сборки APK (внутри Linux/WSL).
#
# ВАЖНО: это сборка Android-версии. EXE для Windows собирает build_pc.py (build_pc.bat).
#
# На Windows: скрипт проверит окружение и объяснит, чего не хватает, — APK
# на Windows не собирается: python-for-android/NDK требуют Linux.
# Внутри Ubuntu (WSL):   python3 build_apk.py
#
# Что делает:
#   1) кладёт Arial-Regular.ttf рядом с клиентом (в APK он попадает как шрифт с кириллицей);
#   2) рисует icon.png, если его нет (Pillow);
#   3) запускает `buildozer android debug` и показывает путь к готовому APK.
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FONT_NAME = 'Arial-Regular.ttf'
ICON = os.path.join(HERE, 'icon.png')
APK_DIR = os.path.join(HERE, 'bin')

WINDOWS_FONTS = [
    r'C:\Windows\Fonts\Arial-Regular.ttf',
    r'C:\Windows\Fonts\arial.ttf',
]
LINUX_FONTS = [
    '/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf',
    '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
]


def ensure_font():
    target = os.path.join(HERE, FONT_NAME)
    if os.path.exists(target):
        print(f'шрифт уже есть: {FONT_NAME}')
        return target
    for path in (WINDOWS_FONTS if sys.platform == 'win32' else []) + LINUX_FONTS:
        if os.path.exists(path):
            shutil.copyfile(path, target)
            print(f'шрифт скопирован: {path} -> {FONT_NAME}')
            return target
    print(f'⚠ {FONT_NAME} не найден: на Android возьмётся системный Roboto')
    return None


def ensure_icon():
    if os.path.exists(ICON):
        return
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print('⚠ icon.png нет, а Pillow не установлен (pip install pillow)')
        return
    img = Image.new('RGBA', (512, 512), (255, 255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([56, 76, 456, 436], radius=56, fill=(11, 110, 153, 255))
    draw.ellipse([150, 170, 362, 382], fill=(255, 255, 255, 255))
    draw.ellipse([206, 226, 306, 326], fill=(11, 110, 153, 255))
    img.save(ICON)
    print('icon.png создан')


def build():
    if sys.platform == 'win32':
        print(
            'Сборка APK на Windows невозможна: python-for-android и Android NDK\n'
            'работают только на Linux. Варианты:\n'
            '  1) WSL:  wsl --install -d Ubuntu, затем в Ubuntu:\n'
            '       sudo apt update && sudo apt install -y git zip unzip openjdk-17-jdk \\\n'
            '            python3-pip autoconf libtool pkg-config zlib1g-dev libncurses-dev \\\n'
            '            libtinfo-dev libffi-dev libssl-dev\n'
            '       pip3 install buildozer cython==0.29.36 pillow\n'
            '       cp /mnt/c/Users/<вы>/CodeGPT/list/*.py .   # или соберите прямо в проекте\n'
            '       buildozer android debug\n'
            '  2) Ubuntu в виртуалке/на сервере, тот же buildozer.spec.\n'
            '  3) Android Studio + Chaquopy (нативный проект с теми же Python-файлами).'
        )
        return 1

    if shutil.which('buildozer') is None:
        print('buildozer не установлен: pip install buildozer cython==0.29.36')
        return 1
    print('Запуск: buildozer android debug (первый раз — 20-40 минут и ~5 ГБ загрузок)')
    code = subprocess.call(['buildozer', 'android', 'debug'], cwd=HERE)
    if code != 0:
        print(f'buildozer завершился с кодом {code}')
        return code
    if os.path.isdir(APK_DIR):
        apks = [f for f in os.listdir(APK_DIR) if f.endswith('.apk')]
        for apk in apks:
            print('Готово:', os.path.join(APK_DIR, apk))
    return 0


def main():
    ensure_font()
    ensure_icon()
    sys.exit(build())


if __name__ == '__main__':
    main()
