# audio_android.py
# Звук звонка на Android: AudioRecord (микрофон) -> VOICE-кадры,
# VOICE-кадры -> AudioTrack. Без внешних библиотек — только pyjnius из python-for-android.
#
# Воспроизведение идёт в отдельном потоке через очередь: AudioTrack.write()
# в режиме STREAM блокируется, и в UI-потоке это подвешивало бы интерфейс.
import base64
import queue
import threading

from jnius import autoclass

AudioFormat = autoclass('android.media.AudioFormat')
AudioRecord = autoclass('android.media.AudioRecord')
AudioTrack = autoclass('android.media.AudioTrack')
AudioManager = autoclass('android.media.AudioManager')
# AudioSource — вложенный класс, в pyjnius он указывается через «$».
AudioSource = autoclass('android.media.MediaRecorder$AudioSource')

CHANNEL_IN = AudioFormat.CHANNEL_IN_MONO
CHANNEL_OUT = AudioFormat.CHANNEL_OUT_MONO
ENCODING = AudioFormat.ENCODING_PCM_16BIT

MAX_QUEUE = 25        # ~1 с звука (кадры по 40 мс); при переполнении старые кадры отбрасываем


class AndroidAudio:
    """Кадры 16-бит mono 16 кГц. Сервер не разбирает звук — только пересылает."""

    def __init__(self, net, rate, block):
        self.net = net
        self.rate = rate
        self.block = block            # сэмплов на кадр (40 мс)
        self.playing = False
        self.recording = False
        self.track = None
        self.record = None
        self.queue = queue.Queue(maxsize=MAX_QUEUE)

    # ---------------------------------------------------------------- старт
    def start(self):
        self._start_playback()
        try:
            self._start_recording()
        except Exception:
            self.stop()               # не оставляем открытый AudioTrack
            raise
        return self.playing

    def _start_playback(self):
        min_size = AudioTrack.getMinBufferSize(self.rate, CHANNEL_OUT, ENCODING)
        size = max(min_size, self.block * 2 * 8)
        track = AudioTrack(AudioManager.STREAM_VOICE_CALL, self.rate, CHANNEL_OUT,
                           ENCODING, size, AudioTrack.MODE_STREAM)
        track.play()
        self.track = track
        self.playing = True
        threading.Thread(target=self._play_loop, args=(track,), daemon=True).start()

    def _start_recording(self):
        min_size = AudioRecord.getMinBufferSize(self.rate, CHANNEL_IN, ENCODING)
        size = max(min_size, self.block * 2 * 4)
        record = AudioRecord(AudioSource.VOICE_COMMUNICATION,
                             self.rate, CHANNEL_IN, ENCODING, size)
        if record.getState() != AudioRecord.STATE_INITIALIZED:
            record.release()
            raise RuntimeError('микрофон недоступен (нет разрешения?)')
        record.startRecording()
        self.record = record
        self.recording = True
        threading.Thread(target=self._rec_loop, args=(record,), daemon=True).start()

    # ---------------------------------------------------------------- микрофон
    def _rec_loop(self, record):
        buf = bytearray(self.block * 2)
        while self.recording:
            try:
                n = record.read(buf, 0, len(buf))
            except Exception:
                break
            if n and n > 0:
                self.net.send('VOICE ' + base64.b64encode(bytes(buf[:n])).decode())
            elif n is not None and n < 0:      # ошибка AudioRecord
                break
        try:
            record.stop()
            record.release()
        except Exception:
            pass

    # ---------------------------------------------------------------- приём
    def play(self, payload):
        """Вызывается из UI-потока: только декодируем и кладём в очередь."""
        if not self.playing or not payload:
            return
        try:
            data = base64.b64decode(payload)
        except (ValueError, TypeError):
            return
        if not data:
            return
        try:
            self.queue.put_nowait(data)
        except queue.Full:
            try:                       # отбрасываем самый старый кадр, чтобы не копить задержку
                self.queue.get_nowait()
                self.queue.put_nowait(data)
            except (queue.Empty, queue.Full):
                pass

    def _play_loop(self, track):
        while self.playing:
            try:
                data = self.queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if data is None or not self.playing:
                break
            try:
                track.write(data, 0, len(data))
            except Exception:
                break
        try:
            track.pause()
            track.flush()
            track.stop()
            track.release()
        except Exception:
            pass

    def stop(self):
        self.recording = False
        self.playing = False
        try:
            self.queue.put_nowait(None)      # разбудить поток воспроизведения
        except queue.Full:
            pass
        self.track = None
