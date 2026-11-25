# main.py  (точка входа python-for-android: имя файла менять нельзя)
# Мессенджер в стиле Telegram: Android (Kivy) и Windows (EXE).
# Сервер — server.py, протокол общий у телефона и ПК.
#
# Что есть в интерфейсе:
#   • вход по имени или по логину (имя|логин|пароль) — логин даёт постоянный ID;
#   • список чатов как в Telegram: общий чат, группы, личные диалоги, непрочитанные;
#   • группы: создание, каталог, вступление, участники, добавление по ID;
#   • личные сообщения по ID собеседника, поиск людей на сервере;
#   • звонки: входящий/исходящий/в разговоре;
#   • пузыри сообщений: свои справа, чужие слева, имя отправителя и время.
#
# Android: разрешения INTERNET, RECORD_AUDIO (микрофон в звонке).
# Проверка на ПК:  python main.py   (или messenger_pc.py)
# EXE на ПК:       build_pc.bat  -> dist\Messenger.exe
# APK:             buildozer android debug  (или GitHub Actions)
import base64
import hashlib
import json
import os
import random
import socket
import sys
import threading
import time

from kivy.clock import Clock
from kivy.core.text import LabelBase
from kivy.core.window import Window
from kivy.lang import Builder
from kivy.metrics import dp, sp
from kivy.storage.jsonstore import JsonStore
from kivy.utils import platform
from kivy.app import App
from kivy.properties import BooleanProperty, ColorProperty, DictProperty
from kivy.properties import ListProperty, NumericProperty, ObjectProperty
from kivy.properties import StringProperty
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget

IS_PC = platform in ('win', 'linux', 'macosx')
if platform == 'android':
    Window.softinput_mode = 'below_target'   # клавиатура не перекрывает поле ввода
Window.clearcolor = (0.95, 0.95, 0.94, 1)
# Диагностика протокола: MESSENGER_DEBUG=1 пишет все строки сервера в client_protocol.log
DEBUG_PROTO = os.environ.get('MESSENGER_DEBUG') == '1'


def app_dir():
    """Папка рядом с EXE (или с исходниками): там лежат настройки, шрифт, иконка."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def find_asset(name):
    """Ищет файл рядом с приложением, во временной папке PyInstaller и в исходниках."""
    hits = [os.path.join(app_dir(), name),
            os.path.join(getattr(sys, '_MEIPASS', ''), name),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), name)]
    for path in hits:
        if path and os.path.exists(path):
            return path
    return None


def storage_dir():
    """Папка для chat_settings.json: рядом с EXE (если там можно писать),
    иначе — системная папка настроек пользователя (например, Program Files)."""
    base = app_dir() if IS_PC else '.'   # на Android настройки лежат в user_data_dir (см. build)
    probe = os.path.join(base, '.write_test')
    try:
        with open(probe, 'w', encoding='utf-8') as f:
            f.write('ok')
        os.remove(probe)
        return base
    except OSError:
        pass
    home = os.path.expanduser('~')
    folder = os.path.join(home, '.messenger')
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError:
        folder = home
    return folder


# Кириллица: штатный шрифт Kivy её не содержит.
# Arial-Regular.ttf лежит рядом с клиентом и упаковывается в APK/EXE.
TEXT_FONT = None
for _path in (find_asset('Arial-Regular.ttf'),
              r'C:\Windows\Fonts\arial.ttf',           # проверка на ПК
              '/system/fonts/Roboto-Regular.ttf'):    # запасной путь на Android
    if _path and os.path.exists(_path):
        LabelBase.register('ChatText', fn_regular=_path)
        TEXT_FONT = 'ChatText'
        break
FONT = TEXT_FONT or 'Roboto'

SAMPLE_RATE = 16000
FRAME_MS = 40
BLOCK_SIZE = SAMPLE_RATE * FRAME_MS // 1000
HISTORY_KEEP = 300          # сколько сообщений чата держим в памяти клиента

try:                        # звук на ПК (на Android используется AudioRecord)
    import sounddevice
    HAVE_SOUNDDEVICE = (platform != 'android')
except Exception:
    sounddevice = None
    HAVE_SOUNDDEVICE = False

# ------------------------------------------------------------ цвета (как в Telegram)
BG = (0.95, 0.95, 0.94, 1)            # фон чата
HDR = (0.32, 0.43, 0.59, 1)           # шапка
HDR_D = (0.27, 0.37, 0.52, 1)         # нажатие в шапке
BUBBLE_ME = (0.93, 0.99, 0.88, 1)     # свой пузырь
BUBBLE_OTHER = (1, 1, 1, 1)           # чужой пузырь
BUBBLE_SYS = (0.85, 0.88, 0.91, 1)    # служебное сообщение
MUTED = (0.45, 0.47, 0.51, 1)
TXT = (0.08, 0.09, 0.1, 1)
ACCENT = (0.2, 0.55, 0.87, 1)         # кнопки
UNREAD = (0.2, 0.6, 0.85, 1)
DANGER = (0.75, 0.25, 0.2, 1)
ONLINE = (0.35, 0.78, 0.45, 1)

KV = r'''
#:import dp kivy.metrics.dp

<IconButton>:

<BubbleText>:
    text_size: self.width - dp(4), None
    size_hint_y: None
    height: self.texture_size[1] + dp(4)
    font_size: dp(15)
    font_name: app.text_font
    color: (0.08, 0.09, 0.1, 1)
    shorten: False
    halign: 'left'
    valign: 'top'
    max_lines: 0
'''

# ---------------------------------------------------------------- сеть
class ChatNet:
    """Соединение с сервером; строки из сокета приходят в UI-поток через Clock."""

    def __init__(self, on_line):
        self.sock = None
        self.on_line = on_line        # callable(str)
        self.running = False
        self.send_lock = threading.Lock()   # send() зовут UI-поток и поток микрофона
        self.rest = b''

    def connect(self, host, port, hello, buffer=b''):
        self.sock = socket.create_connection((host, port), timeout=8)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock.sendall((hello + '\n').encode('utf-8'))
        self.sock.settimeout(8)
        data = buffer
        while b'\n' not in data:
            chunk = self.sock.recv(1024)
            if not chunk:
                raise ConnectionError('сервер закрыл соединение при подключении')
            data += chunk
        self.sock.settimeout(None)
        first, rest = data.split(b'\n', 1)
        reply = first.decode('utf-8', errors='replace').strip()
        if not reply.startswith('OK'):
            raise ConnectionError(reply[4:].strip() if reply.startswith('ERR ') else reply)
        self.rest = rest
        return reply[2:].strip()

    def start(self):
        """Чтение строк — только когда экран уже создан (иначе потеряем историю)."""
        self.running = True
        threading.Thread(target=self._reader, args=(self.rest,), daemon=True).start()

    def _reader(self, buf):
        reason = ''
        try:
            while self.running:
                while b'\n' not in buf:
                    chunk = self.sock.recv(4096)
                    if not chunk:
                        reason = 'сервер закрыл соединение'
                        return
                    buf += chunk
                while b'\n' in buf:
                    line, buf = buf.split(b'\n', 1)
                    text = line.decode('utf-8', errors='replace')
                    Clock.schedule_once(lambda dt, t=text: self.on_line(t), 0)
        except OSError as e:
            if self.running:
                reason = str(e)
        finally:
            if reason and self.running:
                self.running = False
                Clock.schedule_once(lambda dt: self.on_line('DISCONNECT ' + reason), 0)
            self.running = False

    def send(self, text):
        sock = self.sock
        if sock is None:
            return False
        try:
            with self.send_lock:
                sock.sendall((text + '\n').encode('utf-8'))
            return True
        except OSError:
            return False

    def close(self):
        self.running = False
        if self.sock is None:
            return
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass
        self.sock = None


# ---------------------------------------------------------------- звук
class AudioEngine:
    """Микрофон -> VOICE-кадры, VOICE-кадры с сервера -> динамик."""

    def __init__(self, net):
        self.net = net
        self.active = False
        self.impl = None

    def backend(self):
        if platform == 'android':
            try:
                from audio_android import AndroidAudio
                return AndroidAudio
            except Exception as e:
                print('Нет AndroidAudio:', e)
        if HAVE_SOUNDDEVICE:
            return DesktopAudio
        return None

    def start(self):
        if self.active:
            return True
        cls = self.backend()
        if cls is None:
            return False
        try:
            self.impl = cls(self.net, SAMPLE_RATE, BLOCK_SIZE)
            self.active = bool(self.impl.start())
        except Exception as e:
            print('Звук недоступен:', e)
            self.active = False
        return self.active

    def play(self, payload):
        if self.active and self.impl is not None:
            try:
                self.impl.play(payload)
            except Exception:
                pass

    def stop(self):
        if self.impl is not None:
            try:
                self.impl.stop()
            except Exception:
                pass
        self.impl = None
        self.active = False


class DesktopAudio:
    """Для проверки на ПК: sounddevice (нужен pip install sounddevice)."""

    def __init__(self, net, rate, block):
        self.net = net
        self.rate = rate
        self.block = block
        self.inp = self.out = None
        self.raw = b''
        self.lock = threading.Lock()

    def start(self):
        self.out = sounddevice.RawOutputStream(samplerate=self.rate, channels=1,
                                               dtype='int16')
        self.inp = sounddevice.RawInputStream(samplerate=self.rate, channels=1,
                                              dtype='int16', blocksize=self.block,
                                              callback=self._mic)
        self.out.start()
        self.inp.start()
        return True

    def _mic(self, indata, frames, time_info, status):
        self.net.send('VOICE ' + base64.b64encode(bytes(indata)).decode())

    def play(self, payload):
        try:
            data = base64.b64decode(payload)
        except (ValueError, TypeError):
            return
        with self.lock:
            self.raw += data
            ready = len(self.raw) - len(self.raw) % self.block
            chunk, self.raw = self.raw[:ready], self.raw[ready:]
        if chunk:
            self.out.write(chunk)

    def stop(self):
        for stream in (self.inp, self.out):
            if stream is not None:
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass
        self.inp = self.out = None

# ---------------------------------------------------------------- виджеты
def button(text, **kw):
    """Кнопка с кириллицей и цветом Kivy 2.3 (background_color работает)."""
    kw.setdefault('font_name', FONT)
    kw.setdefault('background_normal', '')
    kw.setdefault('background_down', '')
    kw.setdefault('background_color', ACCENT)
    return Button(text=text, **kw)


class Avatar(Widget):
    """Кружок с первой буквой имени (как аватарка в Telegram)."""

    text = StringProperty('?')
    circle_color = ColorProperty(HDR)
    text_color = ColorProperty((1, 1, 1, 1))
    bg_color = ColorProperty(HDR)
    font_size = NumericProperty(dp(20))
    label = ObjectProperty(None, allownone=True)

    def __init__(self, **kw):
        super().__init__(**kw)
        self.label = Label(text=self.text, font_name=FONT, bold=True,
                           font_size=self.font_size, color=self.text_color,
                           halign='center', valign='middle')
        self.add_widget(self.label)
        self.bind(text=lambda *a: setattr(self.label, 'text', self.text),
                  pos=self._layout, size=self._layout,
                  text_color=lambda *a: setattr(self.label, 'color', self.text_color))
        Clock.schedule_once(lambda dt: self._layout(), 0)

    def _layout(self, *args):
        self.label.pos = self.pos
        self.label.size = self.size
        self.label.text_size = self.size
        self.label.font_size = max(dp(12), self.height * 0.42)


class _Bg:
    """Фон виджета (прямоугольник/круг) для canvas.before."""

    def __init__(self, width=0, height=0):
        self.width = width
        self.height = height

    def follow(self, widget, rgba):
        from kivy.graphics import Color, Rectangle, RoundedRectangle
        if self.height:
            shape = RoundedRectangle(pos=widget.pos, size=widget.size,
                                     radius=[self.height / 2])
        else:
            shape = Rectangle(pos=widget.pos, size=widget.size)
        with widget.canvas.before:
            Color(*rgba)
            widget.canvas.before.add(shape)
        widget.bind(pos=lambda *a: setattr(shape, 'pos', widget.pos),
                    size=lambda *a: setattr(shape, 'size', widget.size))
        return shape


class Bubble(BoxLayout):
    """Пузырь сообщения."""

    bg = ColorProperty(BUBBLE_OTHER)

    def __init__(self, **kw):
        super().__init__(**kw)
        from kivy.graphics import Color, RoundedRectangle
        with self.canvas.before:
            self._color = Color(*self.bg)
            self._rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(10)])
        self.bind(pos=lambda *a: setattr(self._rect, 'pos', self.pos),
                  size=lambda *a: setattr(self._rect, 'size', self.size),
                  bg=lambda *a: setattr(self._color, 'rgba', self.bg))


class BubbleText(Label):
    pass


class IconButton(ButtonBehavior, BoxLayout):
    """Кнопка в шапке: текст + подсветка фона при нажатии (фон — в Python)."""

    text = StringProperty('')
    text_color = ColorProperty((1, 1, 1, 1))
    font_size = NumericProperty(dp(15))
    on_action = ObjectProperty(None, allownone=True)
    label = ObjectProperty(None, allownone=True)

    def __init__(self, **kw):
        action = kw.pop('on_action', None)
        super().__init__(**kw)
        if action is not None:
            self.on_action = action
        self.label = Label(text=self.text, font_name=FONT, font_size=self.font_size,
                           color=self.text_color, halign='center', valign='middle',
                           shorten=True, shorten_from='right', size_hint=(1, 1))
        self.add_widget(self.label)
        from kivy.graphics import Color, Rectangle
        with self.canvas.before:
            self._color = Color(0, 0, 0, 0)
            self._rect = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=lambda *a: setattr(self._rect, 'pos', self.pos),
                  size=self._sync_size, state=self._sync_bg,
                  text=lambda *a: setattr(self.label, 'text', self.text),
                  font_size=lambda *a: setattr(self.label, 'font_size', self.font_size),
                  text_color=lambda *a: setattr(self.label, 'color', self.text_color))
        self._sync_size()

    # Касания обрабатываем сами: так нажатие срабатывает всегда,
    # независимо от того, как ведёт себя ButtonBehavior в текущей версии Kivy.
    def on_touch_down(self, touch):
        if not self.disabled and self.collide_point(*touch.pos):
            touch.grab(self)
            self.state = 'down'
            return True
        return super().on_touch_down(touch)

    def on_touch_up(self, touch):
        if touch.grab_current is self:
            touch.ungrab(self)
            self.state = 'normal'
            if self.collide_point(*touch.pos) and callable(self.on_action):
                self.on_action(self)
            return True
        return super().on_touch_up(touch)

    def _sync_size(self, *args):
        self._rect.size = self.size
        self.label.font_size = self.font_size
        self.label.pos = self.pos
        self.label.size = self.size
        self.label.text_size = self.size

    def _sync_bg(self, *args):
        self._color.rgba = HDR_D if self.state == 'down' else (0, 0, 0, 0)


class Row(ButtonBehavior, BoxLayout):
    """Строка списка чатов или человека: аватар, название, подпись, справа — бейдж.
    Всё строится в Python: виджеты обычные, без правил KV."""

    title = StringProperty('')
    sub = StringProperty('')
    # Внимание: имя right занято в Widget (x + width) — своё свойство так называть нельзя
    badge = StringProperty('')
    avatar = StringProperty('?')
    avatar_bg = ColorProperty(HDR)
    fg = ColorProperty(TXT)
    on_action = ObjectProperty(None, allownone=True)

    def __init__(self, **kw):
        action = kw.pop('on_action', None)
        super().__init__(**kw)
        if action is not None:
            self.on_action = action
        from kivy.graphics import Color, Rectangle
        self.orientation = 'horizontal'
        self.padding = (dp(10), dp(6))
        self.spacing = dp(10)
        with self.canvas.before:
            self._bg_color = Color(1, 1, 1, 1)
            self._bg_rect = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=lambda *a: setattr(self._bg_rect, 'pos', self.pos),
                  size=lambda *a: setattr(self._bg_rect, 'size', self.size),
                  state=self._sync_bg)

        self.av = Avatar(size_hint=(None, None), size=(dp(46), dp(46)), text=self.avatar,
                         circle_color=self.avatar_bg, font_size=dp(20))
        self.av.pos_hint = {'center_y': 0.5}
        self.add_widget(self.av)

        texts = BoxLayout(orientation='vertical', spacing=dp(1))
        self.title_label = Label(text=self.title, bold=True, font_name=FONT,
                                 font_size=dp(16), color=self.fg, halign='left',
                                 valign='middle', shorten=True, shorten_from='right')
        self.sub_label = Label(text=self.sub, font_name=FONT, font_size=dp(13),
                               color=MUTED, halign='left', valign='middle',
                               shorten=True, shorten_from='right')
        for lab in (self.title_label, self.sub_label):
            lab.bind(size=lambda *a, l=lab: setattr(l, 'text_size', (l.width, None)))
        texts.add_widget(self.title_label)
        texts.add_widget(self.sub_label)
        self.add_widget(texts)

        self.right_label = Label(text=self.badge, font_name=FONT, font_size=dp(13),
                                 color=UNREAD, bold=True, size_hint_x=None, width=dp(38),
                                 halign='right', valign='top')
        self.right_label.pos_hint = {'top': 0.85}
        self.add_widget(self.right_label)
        self.bind(title=lambda *a: setattr(self.title_label, 'text', self.title),
                  sub=lambda *a: setattr(self.sub_label, 'text', self.sub),
                  badge=lambda *a: setattr(self.right_label, 'text', self.badge),
                  avatar=lambda *a: setattr(self.av, 'text', self.avatar),
                  avatar_bg=lambda *a: setattr(self.av, 'circle_color', self.avatar_bg))
        self.av.text = self.avatar
        self.av.circle_color = self.avatar_bg

    # Свои касания: нажатие на строку всегда открывает чат
    def on_touch_down(self, touch):
        if not self.disabled and self.collide_point(*touch.pos):
            touch.grab(self)
            self.state = 'down'
            return True
        return super().on_touch_down(touch)

    def on_touch_up(self, touch):
        if touch.grab_current is self:
            touch.ungrab(self)
            self.state = 'normal'
            if self.collide_point(*touch.pos) and callable(self.on_action):
                self.on_action(self)
            return True
        return super().on_touch_up(touch)

    def _sync_bg(self, *args):
        self._bg_color.rgba = (0.88, 0.89, 0.9, 1) if self.state == 'down' else (1, 1, 1, 1)


class MessagesView(ScrollView):
    """Лента сообщений: прокручиваемый список пузырей."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.box = BoxLayout(orientation='vertical', size_hint_y=None,
                             padding=(dp(6), dp(8)), spacing=dp(4))
        self.box.bind(minimum_height=self.box.setter('height'))
        self.add_widget(self.box)

    def clear(self):
        self.box.clear_widgets()

    def _scroll_bottom(self):
        Clock.schedule_once(lambda dt: setattr(self, 'scroll_y', 0), 0.02)

    def _bubble(self, bg, sender, text, when, me):
        b = Bubble(bg=bg, orientation='vertical', size_hint_y=None, size_hint_x=None,
                   padding=(dp(8), dp(5), dp(8), dp(5)), width=dp(300))
        self.box.add_widget(b)
        if sender:
            head = BubbleText(text=f'{sender}  {when}', size_hint_y=None)
            head.font_size = dp(12)
            head.color = ACCENT if me else (0.75, 0.4, 0.2, 1)
            head.bold = True
            b.add_widget(head)
        body = BubbleText(text=text or ' ')
        b.add_widget(body)
        b.height = body.texture_size[1] + dp(12) + (dp(16) if sender else 0)
        Clock.schedule_once(lambda dt: self._fit(b), 0)
        self._scroll_bottom()
        return b

    def _fit(self, bubble):
        """Ширина пузыря по содержимому, все пузыри — слева (как в общем чате)."""
        max_w = self.width - dp(40)
        need = dp(60)
        for child in bubble.children:
            if isinstance(child, Label):
                child.text_size = (None, None)
                own = child.texture_size[0]
                child.text_size = (min(max_w - dp(16), max(dp(80), own)), None)
                need = max(need, own + dp(18))
        bubble.width = min(max_w, need)
        bubble.height = sum(c.height for c in bubble.children) + dp(12)

    def on_size(self, *args):
        for bubble in self.box.children:
            self._fit(bubble)

    def add(self, text, kind='other', sender='', when=''):
        """kind: me | other | pm | sys | info | err | call."""
        if kind == 'me':
            return self._bubble(BUBBLE_ME, '', text, when, True)
        if kind == 'pm':
            return self._bubble((1, 0.95, 1, 1), 'ЛС · ' + sender, text, when, False)
        if kind in ('sys', 'info', 'err', 'call'):
            b = self._bubble(BUBBLE_SYS, '', text, '', False)
            for child in b.children:
                if isinstance(child, Label):
                    child.color = {'sys': MUTED, 'info': (0.5, 0.38, 0, 1),
                                   'err': DANGER, 'call': (0.04, 0.43, 0.6, 1)}.get(kind, MUTED)
                    child.font_size = dp(13)
                    break
            return b
        return self._bubble(BUBBLE_OTHER, sender, text, when, False)


class ChatView(BoxLayout):
    """Экран одного чата: шапка, сообщения, поле ввода — как в Telegram."""

    def __init__(self, app, chat, **kw):
        super().__init__(orientation='vertical', **kw)
        self.app = app
        self.chat = chat

        # --- шапка ---
        header = BoxLayout(size_hint_y=None, height=dp(56), spacing=dp(2),
                           padding=(dp(2), 0))
        _Bg().follow(header, HDR)

        back = IconButton(text='←', size_hint_x=None, width=dp(40), font_size=sp(16),
                          on_action=lambda *a: app.close_chat())
        header.add_widget(back)
        info_box = BoxLayout(orientation='vertical', spacing=0)
        self.title = Label(text=chat.title, bold=True, font_name=FONT,
                           font_size=sp(14), color=(1, 1, 1, 1), halign='left',
                           valign='bottom', shorten=True)
        self.sub = Label(text=chat.sub, font_name=FONT, font_size=sp(11),
                         color=(0.86, 0.9, 0.95, 1), halign='left', valign='top',
                         shorten=True)
        self.title.bind(size=lambda *a: setattr(self.title, 'text_size',
                                                (self.title.width, dp(30))))
        self.sub.bind(size=lambda *a: setattr(self.sub, 'text_size',
                                              (self.sub.width, dp(20))))
        info_box.add_widget(self.title)
        info_box.add_widget(self.sub)
        header.add_widget(info_box)
        if chat.kind == 'dm':
            header.add_widget(IconButton(text='Позвонить', size_hint_x=None, width=dp(84),
                                         font_size=sp(11),
                                         on_action=lambda *a: app.start_call(chat.key)))
        self.add_widget(header)

        # --- сообщения ---
        self.msgs = MessagesView()
        _Bg().follow(self.msgs, BG)
        self.add_widget(self.msgs)

        # --- полоса звонка ---
        self.call_bar = BoxLayout(size_hint_y=None, height=0, spacing=dp(4),
                                  padding=(dp(8), dp(2)))
        _Bg().follow(self.call_bar, (0.83, 0.91, 0.97, 1))
        self.call_label = Label(text='', font_name=FONT, font_size=sp(12), halign='left',
                                valign='middle', color=(0.04, 0.33, 0.5, 1),
                                shorten=True, shorten_from='right')
        self.call_label.bind(size=lambda *a: setattr(self.call_label, 'text_size',
                                                     (self.call_label.width, None)))
        self.call_bar.add_widget(self.call_label)
        self.accept_btn = button('Принять', size_hint_x=None, width=dp(84),
                                 font_size=dp(13), background_color=(0.2, 0.65, 0.3, 1),
                                 on_release=lambda *a: self.app.send('/answer'))
        self.decline_btn = button('Сброс', size_hint_x=None, width=dp(76),
                                  font_size=dp(13), background_color=DANGER,
                                  on_release=lambda *a: self.app.send(
                                      '/answer' if False else '/decline'))
        self.call_bar.add_widget(self.accept_btn)
        self.call_bar.add_widget(self.decline_btn)
        self.add_widget(self.call_bar)
        self.show_call_bar(False)

        # --- ввод ---
        bottom = BoxLayout(size_hint_y=None, height=dp(58), spacing=dp(4),
                           padding=(dp(6), dp(4)))
        field = TextInput(multiline=False, font_size=sp(15),
                          hint_text='Сообщение', font_name=FONT,
                          background_normal='', background_active='',
                          background_color=(0.96, 0.97, 0.98, 1),
                          foreground_color=TXT, cursor_color=ACCENT,
                          padding=(dp(10), dp(12)))
        self.entry = field
        self.entry.bind(on_text_validate=self.send_entry)
        bottom.add_widget(self.entry)
        # На ПК Enter отправляет сообщение, Shift+Enter — перенос строки
        self.entry.multiline = bool(IS_PC)
        if IS_PC:
            self.entry.bind(on_text_validate=lambda *a: self.send_entry())
        self.send_btn = button('Отправить', size_hint_x=None, width=dp(110),
                               font_size=sp(14), bold=True,
                               on_release=self.send_entry)
        bottom.add_widget(self.send_btn)
        self.add_widget(bottom)
        self.set_placeholder()
        if IS_PC:
            Clock.schedule_once(lambda dt: self.entry.focus and None, 0.3)

    def _open_info(self, *args):
        self.app.show_chat_menu(self.chat)

    def set_placeholder(self):
        self.entry.hint_text = {
            'general': 'Сообщение в общий чат',
            'group': 'Сообщение в группу',
            'dm': 'Личное сообщение',
        }.get(self.chat.kind, 'Сообщение')

    def send_entry(self, *args):
        text = self.entry.text.strip()
        if not text:
            return
        self.entry.text = ''
        self.app.send_chat_message(self.chat, text)

    def add(self, text, kind='other', sender='', when=''):
        return self.msgs.add(text, kind, sender, when)

    def show_call_bar(self, visible):
        self.call_bar.height = dp(44) if visible else 0
        self.call_bar.opacity = 1 if visible else 0
        self.call_bar.disabled = not visible
        incoming = self.app.call_state == 'incoming'
        self.accept_btn.opacity = 1 if incoming else 0
        self.accept_btn.disabled = not incoming
        self.accept_btn.width = dp(84) if incoming else 0
        self.decline_btn.width = dp(90) if visible else 0
        self.decline_btn.disabled = not visible
        self.decline_btn.text = 'Сброс' if self.app.call_state in ('active', 'outgoing') \
            else 'Отклонить'


# ---------------------------------------------------------------- данные чатов
class ChatInfo:
    """Один чат в списке: общий, группа или личный диалог."""

    def __init__(self, kind, key, title, **kw):
        self.kind = kind            # 'general' | 'group' | 'dm'
        self.key = key              # 'general' | id группы | id собеседника
        self.title = title
        self.sub = kw.get('sub', '')
        self.members = kw.get('members', 0)
        self.member = kw.get('member', False)
        self.online = kw.get('online', False)
        self.unread = 0
        # История чата в памяти клиента: при возврате в чат лента не пустая,
        # а затем обновляется с сервера.
        self.history = []           # [(when, sender, text, mine), ...]
        self.cache_token = ''       # какой запрос истории её заполнил
        self.cache_time = 0.0

    def remember(self, when, sender, text, mine):
        self.history.append((when, sender, text, mine))
        if len(self.history) > HISTORY_KEEP:
            del self.history[:len(self.history) - HISTORY_KEEP]
        self.cache_time = time.time()

    def touch(self):
        self.cache_time = time.time()

    @property
    def avatar(self):
        if self.kind == 'general':
            return '#'
        return (self.title or '?')[:1].upper()

    @property
    def avatar_color(self):
        if self.kind == 'group':
            return (0.95, 0.55, 0.2, 1)
        if self.kind == 'dm':
            return (0.3, 0.62, 0.45, 1)
        return HDR

    @property
    def sub_text(self):
        if self.kind == 'group':
            known = self.member
            return f'{self.members} участников' + ('' if known else ' · вы не в группе')
        if self.kind == 'dm':
            return 'в сети' if self.online else 'не в сети'
        return self.sub or 'общий чат — всех видно всем'


class ChatList(ScrollView):
    """Список чатов: строки пересобираются при каждом изменении."""

    def __init__(self, app, on_open, **kw):
        super().__init__(**kw)
        self.app = app
        self.on_open = on_open
        self.box = BoxLayout(orientation='vertical', size_hint_y=None, spacing=0)
        self.box.bind(minimum_height=self.box.setter('height'))
        self.add_widget(self.box)

    def render(self, chats):
        self.box.clear_widgets()
        if not chats:
            self.box.add_widget(Label(text='Пока нет чатов.\nНажмите «Группа», чтобы создать.',
                                      font_name=FONT, font_size=dp(14), color=MUTED,
                                      size_hint_y=None, height=dp(90), halign='center'))
            return
        for chat in chats:
            row = Row(title=chat.title, sub=chat.sub_text, avatar=chat.avatar,
                      avatar_bg=chat.avatar_color,
                      badge=str(chat.unread) if chat.unread else '',
                      size_hint_y=None, height=dp(66))
            if chat.unread:
                row.title = chat.title
            row.on_action = (lambda *a, c=chat: self.on_open(c))
            self.box.add_widget(row)


class ChatsScreen(BoxLayout):
    """Главный экран: шапка + список чатов, кнопки «Группа» и «Личный чат»."""

    def __init__(self, app, **kw):
        super().__init__(orientation='vertical', **kw)
        self.app = app

        header = BoxLayout(size_hint_y=None, height=dp(60), spacing=dp(2),
                           padding=(dp(10), dp(2)))
        _Bg().follow(header, HDR)
        title_box = BoxLayout(orientation='vertical')
        t = Label(text='Чаты', bold=True, font_name=FONT, font_size=dp(20),
                  color=(1, 1, 1, 1), halign='left', valign='bottom')
        self.me = Label(text=self.app.me_line, font_name=FONT, font_size=dp(12),
                        color=(0.86, 0.9, 0.95, 1), halign='left', valign='top',
                        shorten=True)
        t.bind(size=lambda *a: setattr(t, 'text_size', (t.width, dp(32))))
        self.me.bind(size=lambda *a: setattr(self.me, 'text_size', (self.me.width, dp(20))))
        title_box.add_widget(t)
        title_box.add_widget(self.me)
        title_box.bind(on_touch_down=lambda w, t: (w.collide_point(*t.pos)
                                                  and self._copy_id() or False))
        header.add_widget(title_box)
        header.add_widget(IconButton(text='Поиск', size_hint_x=None, width=dp(64),
                                     on_action=lambda *a: app.show_search()))
        header.add_widget(IconButton(text='Меню', size_hint_x=None, width=dp(60),
                                     on_action=lambda *a: app.show_menu()))
        self.add_widget(header)

        self.search = TextInput(multiline=False, font_size=dp(15), font_name=FONT,
                                hint_text='Поиск по чатам и группам',
                                size_hint_y=None, height=dp(42),
                                background_normal='', background_active='',
                                background_color=(0.88, 0.89, 0.91, 1),
                                foreground_color=TXT, cursor_color=ACCENT,
                                padding=(dp(12), dp(10)))
        self.search.bind(text=self._filter)
        self.add_widget(self.search)

        self.chat_list = ChatList(app, app.open_chat)
        self.add_widget(self.chat_list)

        actions = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(6),
                            padding=(dp(6), dp(4)))
        actions.add_widget(button('Группа +', font_size=dp(14), bold=True,
                                  on_release=lambda *a: self.app.create_group_dialog()))
        actions.add_widget(button('Личный чат', font_size=dp(14), bold=True,
                                  background_color=(0.3, 0.62, 0.45, 1),
                                  on_release=lambda *a: self.app.show_search()))
        actions.add_widget(button('Общий чат', font_size=dp(14), bold=True,
                                  background_color=HDR,
                                  on_release=lambda *a: self.app.open_general()))
        self.add_widget(actions)

    def _filter(self, widget, text):
        self.chat_list.render(self.app.filtered_chats(text))

    def _copy_id(self, *args):
        """На ПК строка с ID в шапке — кнопка выхода; на телефоне — копирование ID."""
        if IS_PC:
            self.app.leave()
            return
        if self.app.mine_id:
            self.app.copy_id(self.app.mine_id)

    def refresh(self):
        self.me.text = self.app.me_line
        self.chat_list.render(self.app.filtered_chats(self.search.text))


# ---------------------------------------------------------------- прочие экраны
class LoginScreen(BoxLayout):
    """Вход: имя, сервер; по желанию — логин и пароль (постоянный ID)."""

    def __init__(self, app, **kw):
        super().__init__(orientation='vertical', padding=dp(16), spacing=dp(6), **kw)
        self.app = app
        from kivy.graphics import Color as GColor, Rectangle as GRect
        with self.canvas.before:
            GColor(*BG)
            rect = GRect(pos=self.pos, size=self.size)
        self.bind(pos=lambda *a: setattr(rect, 'pos', self.pos),
                  size=lambda *a: setattr(rect, 'size', self.size))
        saved = app.store.get('server') if app.store.exists('server') else {}
        scroll = ScrollView()
        form = BoxLayout(orientation='vertical', size_hint_y=None, spacing=dp(4))
        form.bind(minimum_height=form.setter('height'))

        form.add_widget(Label(text='Мессенджер', font_size=dp(28), bold=True,
                              size_hint_y=None, height=dp(50), color=HDR,
                              font_name=FONT, halign='center'))
        form.add_widget(Label(text='Группы, личные сообщения, ID и звонки',
                              font_size=dp(13), size_hint_y=None, height=dp(24),
                              color=MUTED, font_name=FONT, halign='center'))
        self.name = self._field(form, 'Ваше имя', saved.get('name', ''))
        self.host = self._field(form, 'IP сервера', saved.get('host', ''))
        self.port = self._field(form, 'Порт', saved.get('port', '55'), numeric=True)
        form.add_widget(self._caption('Логин и пароль — необязательно, '
                                      'даёт постоянный ID'))
        self.login = self._field(form, 'Логин (латиница)', saved.get('login', ''))
        self.password = self._field(form, 'Пароль', saved.get('password', ''),
                                    password=True)
        scroll.add_widget(form)
        self.add_widget(scroll)

        self.status = Label(text='', font_size=dp(13), color=DANGER,
                            size_hint_y=None, height=dp(34), markup=False,
                            font_name=FONT, text_size=(None, None))
        self.add_widget(self.status)
        self.connect_btn = button('Подключиться', size_hint_y=None,
                                  height=dp(52), font_size=dp(17), bold=True,
                                  on_release=lambda *a: self.on_connect())
        self.add_widget(self.connect_btn)
        self.identity = ''

    def _caption(self, text):
        return Label(text=text, size_hint_y=None, height=dp(30), font_size=dp(12),
                     color=MUTED, font_name=FONT)

    def _field(self, box, caption, value, numeric=False, password=False):
        field = TextInput(text=str(value), multiline=False, size_hint_y=None,
                          height=dp(44), font_size=dp(16), font_name=FONT,
                          password=password, foreground_color=TXT,
                          hint_text=caption, hint_text_color=(0.45, 0.47, 0.51, 1),
                          background_normal='', background_active='',
                          background_color=(0.96, 0.97, 0.98, 1), cursor_color=ACCENT,
                          padding=(dp(10), dp(10)))
        if numeric:
            field.input_filter = 'int'
        box.add_widget(field)
        return field

    def set_status(self, text):
        self.status.text = text

    def on_connect(self):
        self.app.do_connect(self.host.text.strip(), self.port.text.strip(),
                            self.name.text.strip(), self.login.text.strip(),
                            self.password.text)
        self.keep_password(self.password.text)

    def keep_password(self, password):
        """Пароль хранится локально рядом с настройками — удобно и безопасно для этого приложения."""
        if password and self.app.store.exists('server'):
            saved = dict(self.app.store.get('server'))
            saved['password'] = password
            self.app.store.put('server', **saved)
# ---------------------------------------------------------------- приложение
class SettingsStore(JsonStore):
    """Настройки клиента. Если папка только для чтения — пишем в ~/.messenger."""

    @staticmethod
    def save(store, key, **values):
        try:
            store.put(key, **values)
            return True
        except (OSError, ValueError) as e:
            print('Настройки не сохранились в', store.filename, e)
        fallback = os.path.join(os.path.expanduser('~'), '.messenger', 'chat_settings.json')
        try:
            os.makedirs(os.path.dirname(fallback), exist_ok=True)
            store.filename = fallback
            store.put(key, **values)
            return True
        except (OSError, ValueError) as e:
            print('Настройки не сохранились:', e)
            return False


class ChatApp(App):
    call_state = None          # None | 'outgoing' | 'incoming' | 'active'
    call_peer = None
    nickname = ''             # имя в чате ('name' у App занят)
    mine_id = ''              # мой ID на сервере
    text_font = StringProperty(FONT)

    def build(self):
        self.title = 'Мессенджер'
        self._setup_desktop()
        # На ПК настройки лежат рядом с EXE (а не в профиле пользователя)
        if IS_PC:
            settings = os.path.join(storage_dir(), 'chat_settings.json')
        else:
            settings = os.path.join(self.user_data_dir, 'chat_settings.json')
        self.store = JsonStore(settings)
        self.users = []
        self.net = None
        self.audio = AudioEngine(self)
        self.server_line = ''
        self.groups = {}
        self.contacts = {}         # id -> имя (кого нашли через поиск)
        self.unread = {}           # ключ чата -> число непрочитанных
        self.chats = {}            # ключ -> ChatInfo
        self.current = None        # открытый чат (ключ)
        self.last_from = {}        # ключ чата -> последнее сообщение (подпись)
        self.pending_pop = {}      # (чат, текст) -> сколько раз уже показано локально
        self._hist_done = set()    # чаты, чья история уже начала рисоваться
        self._hist_active = None   # чат, для которого сейчас идёт история
        self._hist_buffer = []     # сообщения, пришедшие из истории
        self._hist_target = None
        self._ready = False
        self.root_box = BoxLayout(orientation='vertical')
        self.login = LoginScreen(self)
        self.chats_screen = None
        self.chat_view = None
        self.root_box.add_widget(self.login)
        self.root = self.root_box
        return self.root_box

    # ---------- ПК-режим ----------
    def gui_check(self):
        """Заполнить поля из переменных окружения — для автопроверки Exe/интерфейса.
        MESSENGER_HOST, MESSENGER_PORT, MESSENGER_NAME, MESSENGER_LOGIN, MESSENGER_PASS;
        при MESSENGER_AUTOCONNECT=1 после старта окна подключается сам."""
        env = os.environ
        if not (env.get('MESSENGER_HOST') or env.get('MESSENGER_AUTOCONNECT')):
            return
        self.login.host.text = env.get('MESSENGER_HOST', '127.0.0.1')
        self.login.port.text = env.get('MESSENGER_PORT', '55')
        self.login.name.text = env.get('MESSENGER_NAME', 'Проверка')
        self.login.login.text = env.get('MESSENGER_LOGIN', '')
        self.login.password.text = env.get('MESSENGER_PASS', '')
        if env.get('MESSENGER_AUTOCONNECT') == '1':
            Clock.schedule_once(lambda dt: self.login.on_connect(), 1.0)

    def _setup_desktop(self):
        """Окно, иконка, клавиши и правый клик — только на компьютере."""
        if not IS_PC:
            return
        from kivy.config import Config
        Config.set('kivy', 'exit_on_escape', '0')
        Config.set('graphics', 'width', '980')
        Config.set('graphics', 'height', '740')
        Config.set('input', 'mouse', 'mouse,disable_multitouch')
        try:
            from kivy.core.window import Window
            Window.minimum_width, Window.minimum_height = 760, 560
            Window.softinput_mode = ''
            icon = find_asset('icon.png')
            if icon:
                Window.set_icon(icon)
            if platform == 'win':
                self._name_window('Мессенджер')
        except Exception as e:
            print('Окно:', e)
        Clock.schedule_once(lambda dt: self._bind_desktop_keys(), 0.2)
        Clock.schedule_once(lambda dt: self.gui_check(), 0.5)

    @staticmethod
    def _name_window(title):
        """Заголовок окна в панели задач Windows (у него имя приложения от SDL)."""
        try:
            import ctypes
            handle = ctypes.windll.kernel32.GetConsoleWindow()
            ctypes.windll.user32.SetWindowTextW(
                ctypes.windll.user32.FindWindowW(None, 'Messenger'), title)
            if handle:
                ctypes.windll.user32.SetWindowTextW(handle, title)
        except Exception:
            pass

    def _bind_desktop_keys(self):
        from kivy.core.window import Window
        Window.bind(on_key_down=self._on_key_down,
                    on_touch_down=self._on_pc_touch)

    def _on_key_down(self, window, key, scancode, codepoint, modifiers):
        modifiers = modifiers or []
        if key == 27 and self.chat_view is not None:          # Esc — назад к чатам
            self.close_chat()
            return True
        if key == 13 and 'ctrl' in modifiers:                 # Ctrl+Enter — меню чата
            if self.chat_view is not None:
                self.show_chat_menu(self.chat_view.chat)
            return True
        if key == 70 and 'ctrl' in modifiers:                 # Ctrl+F — поиск людей
            self.show_search()
            return True
        return False

    def _on_pc_touch(self, window, touch):
        """Правый клик по шапке чата — меню (как контекстное меню)."""
        if not IS_PC or touch.button != 'right':
            return False
        if self.chat_view is not None and touch.y > window.height - dp(120):
            self.show_chat_menu(self.chat_view.chat)
            return True
        return False

    # ---------- подключение ----------
    @property
    def me_line(self):
        if not self.nickname:
            return ''
        return f'{self.nickname} · ID {self.mine_id}' + ('  ·  нажмите, чтобы выйти'
                                                           if IS_PC else '')

    def device_id(self):
        if not self.store.exists('device'):
            self.store.put('device', id=''.join(random.choice('0123456789')
                                                for _ in range(10)))
        return self.store.get('device')['id']

    def do_connect(self, host, port, name, login='', password=''):
        if not host:
            self.login.set_status('Укажите IP сервера')
            return
        if not port.isdigit():
            self.login.set_status('Порт должен быть числом')
            return
        if not login and not name:
            self.login.set_status('Введите имя')
            return
        name = name or login
        if login:
            hello = f'{name}|{login}|{password}'
        else:
            hello = f'{name}||{self.device_id()}'
        self.login.connect_btn.disabled = True
        self.login.set_status('Подключение…')

        net = ChatNet(self.on_line)

        def worker():          # сеть — в фоне, чтобы интерфейс не замирал
            try:
                reply = net.connect(host, int(port), hello)
            except (OSError, ConnectionError) as e:
                err = str(e)
                Clock.schedule_once(lambda dt: self._connect_failed(net, err), 0)
                return
            Clock.schedule_once(
                lambda dt: self._connected(net, reply, host, port, name, login,
                                           password), 0)

        threading.Thread(target=worker, daemon=True).start()

    def _connect_failed(self, net, err):
        net.close()
        self.login.connect_btn.disabled = False
        self.login.set_status(f'Не удалось: {err}')
        print('Подключение не удалось:', err, flush=True)

    def _connected(self, net, reply, host, port, name, login, password):
        parts = reply.split()
        self.nickname = parts[0] if parts else name
        self.mine_id = parts[1] if len(parts) > 1 else ''
        self.net = net
        self.server_line = f'{host}:{port}'
        self.gui_check()
        SettingsStore.save(self.store, 'server', host=host, port=str(port),
                           name=name, login=login, password=password if login else '')
        self.login.connect_btn.disabled = False
        self.login.password.text = ''

        self._build_general()
        self.load_chat_list()
        self.root_box.clear_widgets()
        self.chats_screen = ChatsScreen(self)
        self.root_box.add_widget(self.chats_screen)
        self.refresh_chats()
        self._ready = True
        self.request_mic_permission()
        self.open_chat(self.chat('general'))
        print('Подключён как', self.nickname, self.mine_id, flush=True)
        net.start()            # экран готов принимать строки (история и т.д.)

    def request_mic_permission(self):
        """На Android спрашиваем разрешение на микрофон заранее."""
        if platform != 'android':
            return
        try:
            from android.permissions import request_permissions, Permission
            request_permissions([Permission.RECORD_AUDIO])
        except Exception as e:
            print('Разрешение микрофона:', e)

    def leave(self):
        self.hangup_local()
        if self.net is not None:
            self.net.close()
            self.net = None
        if platform == 'android':
            self.stop()
            return
        self._ready = False
        self.chats.clear()
        self.chat_view = None
        self.chats_screen = None
        self.root_box.clear_widgets()
        self.root_box.add_widget(self.login)
        self.login.set_status('Отключено')

    # ---------- чаты ----------
    def _build_general(self):
        self.chats = {}
        self.chats['general'] = ChatInfo('general', 'general', 'Общий чат')
        self._hist_active = None
        self._hist_buffer = []
        self.apply_group_catalog()

    def chat(self, key):
        return self.chats.get(key)

    def apply_group_catalog(self):
        """Группы из каталога сервера; личные диалоги и общий не трогаем."""
        for key in [k for k, c in self.chats.items() if c.kind == 'group']:
            if key not in self.groups:
                self.chats.pop(key, None)
        for gid, info in self.groups.items():
            chat = self.chats.get(gid)
            if chat is None:
                chat = ChatInfo('group', gid, info['title'], members=info['members'])
                self.chats[gid] = chat
            chat.title = info['title']
            chat.members = info['members']
            chat.member = info['member']

    def ensure_chat(self, key, kind, title, **kw):
        chat = self.chats.get(key)
        if chat is None:
            chat = ChatInfo(kind, key, title, **kw)
            self.chats[key] = chat
        elif title:
            chat.title = title
        return chat

    def sorted_chats(self):
        order = {'general': 0, 'group': 1, 'dm': 2}
        items = list(self.chats.values())
        items.sort(key=lambda c: (0 if c.unread else 1, order[c.kind],
                                  self.last_from.get(c.key, '') == '' and 1 or 0,
                                  c.title.lower()))
        return items

    def filtered_chats(self, text):
        text = (text or '').strip().lower()
        items = self.sorted_chats()
        if text:
            items = [c for c in items if text in c.title.lower() or text in c.key]
        return items

    def refresh_chats(self):
        self.save_chat_list()
        if self.chats_screen is not None:
            self.chats_screen.refresh()

    # ---------- сохранение чатов и история ----------
    def save_chat_list(self):
        """Список чатов пишется в настройки: после перезапуска всё на месте."""
        if not self._ready or self.net is None:
            return
        items = [{'kind': c.kind, 'key': c.key, 'title': c.title,
                  'last': self.last_from.get(c.key, '')}
                 for c in self.chats.values()]
        try:
            self.store.put('chats', who=self.mine_id, items=json.dumps(items,
                                                                     ensure_ascii=False))
        except (OSError, ValueError) as e:
            print('Список чатов не сохранился:', e)

    def load_chat_list(self):
        """Восстанавливает чаты из предыдущего запуска (до ответа сервера)."""
        if not self.store.exists('chats'):
            return
        saved = self.store.get('chats')
        if saved.get('who') != self.mine_id:
            return
        try:
            items = json.loads(saved.get('items', '[]'))
        except ValueError:
            return
        for item in items:
            kind, key = item.get('kind'), item.get('key')
            title = item.get('title') or key
            if kind == 'general':
                continue
            chat = self.chats.get(key)
            if chat is None:
                chat = ChatInfo(kind, key, title)
                self.chats[key] = chat
            if item.get('last'):
                self.last_from[key] = item['last']

    def request_history(self, chat):
        """Запрашивает историю чата у сервера — она доступна в любой момент."""
        if self.net is None or not self.net.running:
            return
        if chat.kind == 'general':
            self.net.send('CHAT general')
        elif chat.kind == 'group':
            self.net.send('CHAT group:' + chat.key)
        elif chat.key.isdigit():
            self.net.send('CHAT dm:' + chat.key)

    def open_general(self):
        self.open_chat(self.chat('general'))

    def open_chat(self, chat):
        if chat is None or not self._ready:
            return
        self.current = chat.key
        self.unread.pop(chat.key, None)
        self.chat_view = ChatView(self, chat)
        self.root_box.clear_widgets()
        self.root_box.add_widget(self.chat_view)
        # 1) сразу показываем сохранённую историю — чат никогда не выглядит пустым
        self.render_history(chat)
        self.refresh_chats()
        # 2) и обновляем её с сервера
        self.request_history(chat)
        self.update_call_ui()

    def render_history(self, chat):
        """Рисует историю чата из памяти клиента (без запроса к серверу)."""
        if self.chat_view is None or self.current != chat.key:
            return
        self.chat_view.msgs.clear()
        for when, sender, text, mine in chat.history:
            self.chat_view.add(text, 'me' if mine else 'other', '' if mine else sender, when)

    def remember_chat(self, key, when, sender, text, mine):
        """Кладёт сообщение в память чата — оно останется после ухода и возврата."""
        chat = self.chats.get(key)
        if chat is not None:
            chat.remember(when, sender, text, mine)

    def close_chat(self):
        self.current = None
        self.chat_view = None
        if self.call_state in ('outgoing', 'incoming'):
            self.net.send('/hangup' if self.call_state == 'outgoing' else '/decline')
        self.root_box.clear_widgets()
        if self.chats_screen is None:
            self.chats_screen = ChatsScreen(self)
        self.root_box.add_widget(self.chats_screen)
        self.refresh_chats()

    def bump_unread(self, key):
        if self.current == key:
            return 0
        self.unread[key] = self.unread.get(key, 0) + 1
        return self.unread[key]

    # ---------- отправка ----------
    def send_chat_message(self, chat, text):
        if self.net is None or not self.net.running:
            self.log('Нет соединения', 'err')
            return
        if text.startswith('/'):
            if text.lower() in ('/quit', '/exit'):
                self.leave()
                return
            if text.split(' ', 1)[0].lower() in ('/hangup', '/decline'):
                self.hangup_local()
            self.log('→ ' + text, 'info')
            self.net.send(text)
            return
        if chat.kind == 'dm' and not chat.key.isdigit():
            self.log('Личному собеседнику нужно сначала присвоить ID: '
                     'откройте «Личный чат» и выберите человека', 'err')
            return
        if chat.kind == 'group' and not chat.member:
            self.log('Вы не в группе — сначала вступите (Меню группы)', 'err')
            return
        if chat.kind == 'dm':
            self.net.send(f'PM {chat.key} {text}')
        elif chat.kind == 'group':
            self.net.send(f'G {chat.key} {text}')
        else:
            self.net.send(text)
        self.add_local(chat.key, text, 'me', self.nickname)
        self.remember_chat(chat.key, time.strftime('%H:%M'), self.nickname, text, True)
        self.pending_pop[(chat.key, text)] = self.pending_pop.get((chat.key, text), 0) + 1
        self.last_from[chat.key] = 'Вы: ' + text
        self.refresh_chats()

    def send(self, text):
        """Совместимость: команды и служебные строки (звонки)."""
        if self.net is None or not self.net.running:
            self.log('Нет соединения', 'err')
            return
        if text.startswith('VOICE '):
            self.net.send(text)
            return
        if text.lower() in ('/quit', '/exit'):
            self.leave()
            return
        if text.startswith('/'):
            if text.split(' ', 1)[0].lower() in ('/hangup', '/decline'):
                self.hangup_local()
            self.net.send(text)
            return
        self.send_chat_message(self.chat('general'), text)

    def add_local(self, key, text, kind, sender='', when=''):
        when = when or time.strftime('%H:%M')
        if self.chat_view is not None and self.current == key:
            self.chat_view.add(text, kind, sender, when)
            self.remember_chat(key, when, sender, text, kind == 'me')
        else:
            self.remember_chat(key, when, sender, text, kind == 'me')

    def log(self, text, kind='other', key=None):
        """Служебные строки: в чат key (по умолчанию — текущий)."""
        target = key or self.current
        if self.chat_view is not None and target == self.current:
            self.chat_view.add(text, kind)

    # ---------- история чата ----------
    def _history_start(self, arg):
        """Сервер начал выдавать историю чата — собираем её заново в буфер."""
        kind, _, key = (arg or '').strip().partition(':')
        chat_key = 'general' if kind == 'general' else key
        self._hist_active = chat_key
        self._hist_buffer = []
        self._hist_target = chat_key

    def _hist_seen(self, key):
        """Для этого чата сейчас идёт загрузка истории."""
        return self._hist_active == key

    def _history_take(self, chat_key, when, sender, text, mine):
        """Сообщение из истории: в буфер (если идёт загрузка) или прямо в ленту."""
        if self._hist_active == chat_key:
            self._hist_buffer.append((when, sender, text, mine))
            return True
        self.remember_chat(chat_key, when, sender, text, mine)
        return False

    def _history_finish(self):
        """Загрузка истории закончена: заменяем память чата и перерисовываем ленту."""
        chat_key = self._hist_active
        self._hist_active = None
        if not chat_key or not self._hist_buffer:
            self._hist_buffer = []
            return
        chat = self.chats.get(chat_key)
        if chat is not None:
            chat.history = list(self._hist_buffer)
            chat.touch()
        if self.chat_view is not None and self.current == chat_key:
            self.render_history(chat)
        self._hist_buffer = []

    def _pop_own(self, key, text):
        """Сообщение уже показано локально при отправке — второй раз не рисуем."""
        count = self.pending_pop.get((key, text), 0)
        if count <= 0:
            return False
        if count == 1:
            self.pending_pop.pop((key, text), None)
        else:
            self.pending_pop[(key, text)] = count - 1
        return True

    def route_system(self, key, text, kind='sys', when=''):
        """Системное событие относится к конкретному чату."""
        when = when or time.strftime('%H:%M')
        if self.chat_view is not None and self.current == key:
            self.chat_view.add(text, kind, '', when)
        elif kind == 'call':
            self.log(text, kind)

    def on_line(self, text):
        if DEBUG_PROTO:
            try:
                with open(os.path.join(storage_dir(), 'client_protocol.log'), 'a',
                          encoding='utf-8') as f:
                    f.write(f'{time.strftime("%H:%M:%S")} {text[:160]}\n')
            except OSError:
                pass
        if text.startswith('DISCONNECT '):
            self.log('Соединение потеряно: ' + text[11:], 'err')
            self.hangup_local()
            return
        kind, _, rest = text.partition(' ')

        if kind == 'ACK':
            # Своё сообщение уже показано локально. Если сервер никому его не доставил,
            # оставляем локальную копию (в общем чате она есть у всех, поэтому
            # в ленте одного клиента видно одно сообщение).
            return
        if kind == 'USERS':
            self.users = [n for n in rest.split(',') if n]
            if self.nickname and self.nickname not in self.users:
                for n in self.users:
                    if n.startswith(self.nickname + '_'):
                        self.nickname = n
                        break
            general = self.chat('general')
            if general is not None:
                general.sub = f'{len(self.users)} участников'
            if self.chat_view is not None and self.current == 'general':
                self.chat_view.sub.text = general.sub
            self.refresh_chats()
            return
        if kind == 'LIST':
            self.groups = {}
            for item in rest.split(','):
                parts = item.split('|')
                if len(parts) < 4 or not parts[0]:
                    continue
                self.groups[parts[0]] = {'id': parts[0], 'title': parts[1],
                                         'members': int(parts[2] or 0),
                                         'member': parts[3] == '1'}
            self.apply_group_catalog()
            self.refresh_chats()
            return
        if kind == 'CHATS':
            # сервер вернул все чаты с историей — добавляем недостающие
            if rest and rest != '-':
                for item in rest.split(','):
                    parts = item.split('|')
                    if len(parts) < 3:
                        continue
                    ckind, ckey, ctitle = parts[0], parts[1], parts[2].replace('_', ' ')
                    if ckind == 'general' or not ckey:
                        continue
                    if ckind == 'group' and ckey not in self.chats \
                            and ckey in self.groups:
                        self.chats[ckey] = ChatInfo('group', ckey, self.groups[ckey]['title'],
                                                   members=self.groups[ckey]['members'],
                                                   member=self.groups[ckey]['member'])
                    elif ckind == 'dm':
                        self.ensure_chat(ckey, 'dm', ctitle)
            self.refresh_chats()
            return
        if kind == 'HISTEND':
            self._history_finish()
            self.refresh_chats()
            return
        if kind == 'CHAT':
            # сервер начал выдавать историю запрошенного чата (arg: general / group:12 / dm:34)
            self._history_start(rest)
            return
        if kind == 'GROUP+':
            gid, _, title = rest.partition(' ')
            self.groups[gid] = self.groups.get(gid, {'id': gid, 'title': title,
                                                     'members': 0, 'member': True})
            self.groups[gid]['member'] = True
            self.apply_group_catalog()
            self.refresh_chats()
            return
        if kind == 'GROUP-':
            gid = rest.strip()
            if gid in self.groups:
                self.groups[gid]['member'] = False
            self.apply_group_catalog()
            if self.current == gid:
                self.close_chat()
            self.refresh_chats()
            return
        if kind == 'GMEM':
            gid, _, text = rest.partition(' ')
            self.route_system(gid, text)
            return
        if kind == 'GMSG' or kind == 'GHIST':
            when, _, tail = rest.partition(' ')
            gid, _, tail = tail.partition(' ')
            title, _, tail = tail.partition(' ')
            sender, _, body = tail.partition(' ')
            chat = self.ensure_chat(gid, 'group', title.replace('_', ' '))
            chat.title = title.replace('_', ' ')
            self.last_from[gid] = f'{sender}: {body}'
            mine = sender == self.nickname
            if kind == 'GHIST':
                self._history_take(gid, when, sender, body, mine)
                return
            self.remember_chat(gid, when, sender, body, mine)
            if self.current == gid and self.chat_view is not None:
                if not self._pop_own(gid, body):
                    self.chat_view.add(body, 'me' if mine else 'other', sender, when)
                self.refresh_chats()
            elif self.current != gid and kind == 'GMSG':
                if self.bump_unread(gid):
                    self.refresh_chats()
            return
        if kind == 'DMSG' or kind == 'DHIST':
            when, _, tail = rest.partition(' ')
            peer, _, tail = tail.partition(' ')
            sender_id, _, tail = tail.partition(' ')
            sender_name, _, body = tail.partition(' ')
            chat = self.ensure_chat(peer, 'dm', sender_name, online=False)
            chat.online = sender_name in self.users
            self.last_from[peer] = f'{sender_name}: {body}'
            mine = sender_id == self.mine_id
            if kind == 'DHIST':
                self._history_take(peer, when, sender_name, body, mine)
                if not mine:
                    self.bump_unread(peer)
                return
            self.remember_chat(peer, when, sender_name, body, mine)
            if self.current == peer and self.chat_view is not None:
                if not (mine and self._pop_own(peer, body)):
                    self.chat_view.add(body, 'me' if mine else 'other', sender_name, when)
                self.refresh_chats()
            elif self.current != peer and not mine:
                self.bump_unread(peer)
                self.refresh_chats()
            return
        if kind == 'SEARCH':
            self.show_search_results(rest)
            return
        if kind == 'MEMBERS':
            gid, _, tail = rest.partition(' ')
            people = []
            for item in tail.split(','):
                parts = item.split('|')
                if len(parts) >= 3:
                    people.append({'id': parts[0], 'name': parts[1].replace('_', ' '),
                                   'online': parts[2] == '1'})
            self.show_members_popup(gid, people)
            return
        if kind == 'ERR':
            self.log(rest, 'err')
            if 'нет входящего' in rest.lower():
                if self.call_state == 'incoming':
                    self.reset_call()
            return
        if kind == 'INFO':
            when, _, tail = rest.partition(' ')
            who, _, what = tail.partition(' ')
            self.route_system(self.current, what or tail, 'info', when)
            return
        if kind == 'VOICE':
            _, _, payload = rest.partition(' ')
            self.audio.play(payload)
            return
        if kind in ('CALL', 'CALLRING', 'CALLA', 'CALLR', 'CALLB'):
            when, _, tail = rest.partition(' ')
            who, _, what = tail.partition(' ')
            if kind == 'CALL':
                self.call_state = 'incoming'
                self.call_peer = who
                self.log(f'Входящий звонок от «{who}»', 'call')
                self.update_call_ui()
            elif kind == 'CALLRING':
                self.call_state = 'outgoing'
                self.call_peer = who
                self.log(f'Звоним «{who}»…', 'call')
                self.update_call_ui()
            elif kind == 'CALLA':
                self.connected_call(who)
            elif kind == 'CALLR':
                self.log(f'«{who}» {what}', 'call')
                self.hangup_local()
            elif kind == 'CALLB':
                self.log(f'Звонок с «{who}» завершён', 'call')
                self.hangup_local()
            return
        if kind in ('MSG', 'HIST'):
            when, _, tail = rest.partition(' ')
            sender, _, body = tail.partition(' ')
            self.last_from['general'] = f'{sender}: {body}'
            mine = sender == self.nickname
            if kind == 'HIST':
                self._history_take('general', when, sender, body, mine)
                return
            self.remember_chat('general', when, sender, body, mine)
            if self.current == 'general' and self.chat_view is not None:
                self.chat_view.add(body, 'me' if mine else 'other', sender, when)
                self.refresh_chats()
            elif not mine:
                self.bump_unread('general')
                self.refresh_chats()
            return
        if kind in ('JOIN', 'LEFT'):
            when, _, tail = rest.partition(' ')
            sender, _, body = tail.partition(' ')
            self.route_system('general', f'{sender} {body}')
            return
        if kind == 'NICK':
            when, _, tail = rest.partition(' ')
            old, _, new = tail.partition(' ')
            if old == self.nickname:
                self.nickname = new
                self.refresh_chats()
            self.route_system('general', f'{old} теперь {new}')
            return
        self.log(text, 'sys')

    # ---------- звонки ----------
    def start_call(self, target_id):
        if not target_id:
            self.log('У собеседника пока нет ID', 'err')
            return
        if self.call_state:
            self.log('Вы уже в звонке', 'info')
            return
        self.call_state = 'outgoing'
        self.call_peer = self.chats.get(target_id).title if self.chat(target_id) else target_id
        self.update_call_ui()
        self.net.send('/call ' + target_id)

    def connected_call(self, peer):
        self.call_state = 'active'
        self.call_peer = peer
        if not self.audio.active:
            started = self.audio.start()
            self.log('Звонок принят — говорите' if started else
                     'Звонок принят (без звука: нет микрофона/динамика)',
                     'call')
        else:
            self.log('Звонок принят — говорите', 'call')
        self.update_call_ui()

    def hangup_local(self):
        was = self.call_state
        self.audio.stop()
        self.reset_call()
        if was == 'active':
            self.log('Звонок завершён', 'call')

    def reset_call(self):
        self.call_state = None
        self.call_peer = None
        self.update_call_ui()

    def update_call_ui(self):
        if self.chat_view is None:
            return
        state = self.call_state
        self.chat_view.show_call_bar(state in ('incoming', 'active', 'outgoing'))
        if state == 'incoming':
            text = f'Входящий звонок от «{self.call_peer}»'
        elif state == 'active':
            text = f'Разговор с «{self.call_peer}»'
        elif state == 'outgoing':
            text = f'Звоним «{self.call_peer}»…'
        else:
            text = ''
        self.chat_view.call_label.text = text

    def on_pause(self):
        return True            # не выгружать приложение при сворачивании

    def on_stop(self):
        self.audio.stop()
        if self.net is not None:
            self.net.close()
    # ---------- диалоги (группы, поиск, меню) ----------
    def _dialog(self, title, widgets, height=0.72):
        """Модальное окно: синяя шапка с заголовком, содержимое, «Закрыть»."""
        box = BoxLayout(orientation='vertical', spacing=dp(6), padding=dp(8))
        _Bg().follow(box, (1, 1, 1, 1))
        content = BoxLayout(orientation='vertical', spacing=dp(6), size_hint_y=None)
        content.bind(minimum_height=content.setter('height'))
        for item in widgets:
            content.add_widget(item)
        scroll = ScrollView()
        scroll.add_widget(content)
        box.add_widget(scroll)
        popup = Popup(title='', content=box, size_hint=(None, None), size=(dp(360), dp(560)),
                      title_size=0, title_color=(0, 0, 0, 0),
                      background='', background_color=(0, 0, 0, 0.3),
                      separator_color=(1, 1, 1, 0))
        header = BoxLayout(size_hint_y=None, height=dp(44))
        _Bg().follow(header, HDR)
        header.add_widget(Label(text=title, font_name=FONT, font_size=dp(16), bold=True,
                                color=(1, 1, 1, 1), halign='left', valign='middle',
                                text_size=(dp(300), dp(44))))
        box.add_widget(header, index=len(box.children))
        close = button('Закрыть', size_hint_y=None, height=dp(44),
                       on_release=lambda *a: popup.dismiss())
        box.add_widget(close)
        popup.open()
        return popup

    @staticmethod
    def _field_row(caption, value='', password=False, numeric=False):
        box = BoxLayout(orientation='vertical', size_hint_y=None, height=dp(64))
        box.add_widget(Label(text=caption, size_hint_y=None, height=dp(24),
                             font_size=dp(13), halign='center', valign='middle',
                             color=MUTED, font_name=FONT, text_size=(dp(320), dp(24))))
        field = TextInput(multiline=False, font_size=dp(16), font_name=FONT,
                          password=password, size_hint_y=None, height=dp(42),
                          foreground_color=TXT,
                          hint_text=caption,
                          hint_text_color=(0.45, 0.47, 0.51, 1),
                          background_normal='', background_active='',
                          background_color=(0.96, 0.97, 0.98, 1),
                          cursor_color=ACCENT, padding=(dp(10), dp(9)))
        if numeric:
            field.input_filter = 'int'
        box.add_widget(field)
        return box, field

    def copy_id(self, text):
        """Копирует ID в буфер обмена (Android — через Clipboard)."""
        done = False
        try:
            from kivy.core.clipboard import Clipboard
            Clipboard.copy(str(text))
            done = True
        except Exception:
            pass
        self.log(f'Ваш ID — {text}' + (' (скопирован)' if done else ''), 'info')

    def create_group_dialog(self):
        def create(popup, field):
            title = field.text.strip()
            popup.dismiss()
            if not title:
                self.log('Укажите название группы', 'err')
                return
            self.net.send('/group ' + title)

        widgets = [Label(text='После создания группа появится в списке чатов. '
                              'Добавлять людей можно по ID через «Участники».',
                         font_name=FONT, font_size=dp(12), color=MUTED,
                         size_hint_y=None, height=dp(44),
                         text_size=(dp(240), None))]
        box, field = self._field_row('Название группы')
        widgets += [box, button('Создать', size_hint_y=None, height=dp(46),
                                on_release=lambda *a: create(popup, field))]
        popup = self._dialog('Новая группа', widgets, 0.5)

    def show_search(self):
        """Поиск людей на сервере по ID или имени."""
        box, field = self._field_row('ID или имя')
        field_box = box

        def go(*a):
            query = field.text.strip()
            if query:
                self.net.send(('/search ' if not query.isdigit()
                               else '/search ') + query)

        field.bind(on_text_validate=go)
        widgets = [field_box,
                   button('Найти на сервере', size_hint_y=None, height=dp(46),
                          on_release=go),
                   Label(text='Например: гость ищет вас по ID из шапки «Чаты — Ваш ID», '
                              'а вы — его. У аккаунта с логином ID постоянный.',
                         font_name=FONT, font_size=dp(12), color=MUTED,
                         size_hint_y=None, height=dp(58),
                         text_size=(dp(240), None))]
        self._search_popup = self._dialog('Найти человека', widgets, 0.62)

    def show_search_results(self, rest):
        query, _, tail = rest.partition(' ')
        count, _, items = tail.partition(' ')
        rows = []
        if items and items != '-':
            for item in items.split(','):
                parts = item.split('|')
                if len(parts) < 3:
                    continue
                user_id, name, online = parts[0], parts[1].replace('_', ' '), parts[2] == '1'
                self.contacts[user_id] = name

                def open_dm(*a, uid=user_id, uname=name):
                    chat = self.ensure_chat(uid, 'dm', uname)
                    chat.online = online
                    self.open_chat(chat)

                row = Row(title=name, sub=f'ID {user_id} · ' + ('в сети' if online else 'не в сети'),
                          avatar=(name or '?')[:1].upper(), size_hint_y=None,
                          height=dp(62))
                row.on_action = open_dm
                rows.append(row)
        if not rows:
            rows = [Label(text=f'Никого не найдено по запросу «{query}»',
                          font_name=FONT, size_hint_y=None, height=dp(50), color=MUTED,
                          text_size=(dp(240), None), halign='center')]
        popup = self._dialog(f'Найдено: {query}', rows, 0.6)
        self._search_popup = popup

    def show_chat_menu(self, chat):
        """Меню чата: участники, добавление по ID, вступление/выход."""
        widgets = [Label(text=(f'Группа · ID {chat.key} · {chat.members} участников'
                               if chat.kind == 'group' else
                               f'ID собеседника: {chat.key}' if chat.kind == 'dm' else
                               f'Общий чат · {len(self.users)} участников'),
                         font_name=FONT, font_size=dp(13), color=MUTED,
                         size_hint_y=None, height=dp(40),
                         text_size=(dp(240), None))]
        if chat.kind == 'group':
            if chat.member:
                widgets.append(button('Участники / добавить по ID', size_hint_y=None,
                                      height=dp(46),
                                      on_release=lambda *a: self.show_group_members(chat)))
                widgets.append(button('Выйти из группы', size_hint_y=None, height=dp(44),
                                      background_color=DANGER,
                                      on_release=lambda *a: self.net.send('/gleave ' + chat.key)))
            else:
                widgets.append(button('Вступить в группу', size_hint_y=None, height=dp(46),
                                      background_color=(0.2, 0.65, 0.3, 1),
                                      on_release=lambda *a: self.net.send('/join ' + chat.key)))
        if chat.kind == 'dm':
            widgets.append(button('Написать (личное сообщение)', size_hint_y=None,
                                  height=dp(44),
                                  on_release=lambda *a: self.open_chat(chat)))
            widgets.append(button('Скопировать ID', size_hint_y=None, height=dp(44),
                                  background_color=HDR,
                                  on_release=lambda *a: self.copy_id(chat.key)))
        if chat.kind == 'general':
            widgets.append(button('Участники', size_hint_y=None, height=dp(44),
                                  on_release=lambda *a: self.show_users()))
            widgets.append(button('Скопировать мой ID', size_hint_y=None, height=dp(44),
                                  background_color=HDR,
                                  on_release=lambda *a: self.copy_id(self.mine_id)))
        self._dialog(chat.title, widgets, 0.66)

    def show_members_popup(self, gid, people):
        """Список участников группы: имя, ID, в сети; можно сразу написать в личку."""
        chat = self.chat(gid)
        rows = [Label(text=(f'Группа «{chat.title}», ID {gid}' if chat else f'Группа {gid}'),
                      font_name=FONT, font_size=dp(13), color=MUTED, size_hint_y=None,
                      height=dp(26), text_size=(dp(250), None))]
        if not people:
            rows.append(Label(text='Участников пока нет', font_name=FONT, size_hint_y=None,
                              height=dp(40), color=MUTED))
        for person in people:
            mark = ' (вы)' if person['id'] == self.mine_id else ''

            def open_dm(*a, p=person):
                dm = self.ensure_chat(p['id'], 'dm', p['name'])
                dm.online = p['online']
                self.open_chat(dm)

            row = Row(title=person['name'] + mark,
                      sub=f"ID {person['id']} · "
                          + ('в сети' if person['online'] else 'не в сети'),
                      avatar=(person['name'] or '?')[:1].upper(),
                      size_hint_y=None, height=dp(62))
            row.on_action = open_dm
            rows.append(row)
        self._dialog('Участники', rows, 0.7)

    def show_users(self):
        rows = []
        for name in self.users or ['—']:
            mark = '  (вы)' if name == self.nickname else ''
            rows.append(Label(text='• ' + name + mark, font_name=FONT, font_size=dp(15),
                              size_hint_y=None, height=dp(30), color=TXT,
                              text_size=(dp(250), None), halign='left'))
        self._dialog('Участники', rows, 0.6)

    def show_group_members(self, chat):
        self.net.send('MEMS ' + chat.key)
        rows = [Label(text=f'Группа «{chat.title}», ID {chat.key}', font_name=FONT,
                      font_size=dp(13), color=MUTED, size_hint_y=None, height=dp(26),
                      text_size=(dp(250), None))]
        box, field = self._field_row('Добавить по ID или имени')

        def add(*a):
            value = field.text.strip()
            if value:
                self.net.send(f'/gadd {chat.key} {value}')
                field.text = ''

        field.bind(on_text_validate=add)
        rows.append(box)
        rows.append(button('Добавить (человек должен быть в сети)', size_hint_y=None,
                           height=dp(44), on_release=add))
        rows.append(Label(text=f'Участников сейчас: {chat.members}. '
                               'ID участников общего чата видно в поиске по имени.',
                          font_name=FONT, font_size=dp(12), color=MUTED,
                          size_hint_y=None, height=dp(50),
                          text_size=(dp(250), None)))
        self._dialog('Группа: ' + chat.title, rows, 0.66)

    def show_menu(self):
        def leave_btn(*a):
            self.leave()

        widgets = [
            Label(text=f'Вы: {self.nickname}\nID: {self.mine_id}\nСервер: {self.server_line}',
                  font_name=FONT, font_size=dp(14), color=TXT, size_hint_y=None,
                  height=dp(70), text_size=(dp(250), None)),
            button('Скопировать мой ID', size_hint_y=None, height=dp(44),
                   background_color=HDR, on_release=lambda *a: self.copy_id(self.mine_id)),
            button('Найти человека по ID', size_hint_y=None, height=dp(44),
                   on_release=lambda *a: self.show_search()),
            button('Новая группа', size_hint_y=None, height=dp(44),
                   on_release=lambda *a: self.create_group_dialog()),
            button('Обновить группы', size_hint_y=None, height=dp(44),
                   background_color=(0.45, 0.48, 0.53, 1),
                   on_release=lambda *a: self.net.send('LIST')),
            button('Выйти из аккаунта', size_hint_y=None, height=dp(44),
                   background_color=DANGER, on_release=leave_btn),
        ]
        self._dialog('Меню', widgets, 0.7)


Builder.load_string(KV)


if __name__ == '__main__':
    ChatApp().run()
