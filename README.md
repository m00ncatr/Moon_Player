# Мой плеер — Android (Kivy)

## Проверка на компьютере
    pip install "kivy[base]" mutagen certifi
    python main.py
(на ПК звук идёт через Kivy SoundLoader, скорость не работает — это только Android)

## Сборка APK (Linux или Windows через WSL2 + Ubuntu)
    sudo apt update
    sudo apt install -y git zip unzip openjdk-17-jdk python3-pip autoconf libtool pkg-config \
        zlib1g-dev libncurses5-dev libncursesw5-dev libtinfo5 cmake libffi-dev libssl-dev
    pip3 install --user --upgrade buildozer cython==0.29.36
    cd android          # папка с main.py и buildozer.spec (лучше внутри ~/, не в /mnt/c)
    buildozer android debug
Готовый файл появится в `bin/*.apk`. Первая сборка долгая (20-40 минут), дальше быстрее.

## Установка на телефон
Скопируй .apk на телефон и открой (разреши установку из неизвестных источников),
либо по USB:  `buildozer android deploy run`.

## Логи, если приложение вылетает
    adb logcat | grep -i python
