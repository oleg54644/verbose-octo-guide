# server.py
# Многопользовательский чат-сервер (мессенджер в стиле Telegram).
# Протокол: одно сообщение = одна строка, заканчивающаяся '\n'.
#   Клиент шлёт имя первой строкой ('имя' или 'имя|логин|пароль'),
#   сервер отвечает 'OK <имя> <id>'. Вход по логину: под тем же логином вы
#   снова заходите под своим именем и ID — собеседники находят вас как в Telegram.
#   Сервер шлёт:
#     MSG <время> <отправитель> <текст>     — общее сообщение
#     USERS <a,b,c>                         — список участников
#     JOIN <имя> / LEFT <имя>               — вход/выход участника
#     ACK <n>                               — подтверждение вашего сообщения
#     ERR <текст>                           — ошибка команды
#     INFO <время> <кому> <текст>           — служебное сообщение
#     HIST <время> <отправитель> <текст>    — история общего чата
#   Группы и личные диалоги:
#     LIST <id|название|участников|есть_ли_вы, ...>   — каталог групп
#     GMSG <время> <id> <название> <автор> <текст>    — сообщение группы
#     GHIST <время> <id> <название> <автор> <текст>   — история группы
#     DMSG <время> <id_собеседника> <автор> <имя> <текст>  — личное сообщение
#     DHIST <время> <id_собеседника> <автор> <имя> <текст> — история диалога
#     GROUP+ <id> <название> / GROUP- <id>            — создали группу / вас убрали
#     GMEM <id> <участников>                          — изменился состав группы
#     SEARCH <запрос> <n> <id|имя|онлайн>, ...        — найдено по ID или имени
#   Звонки (сервер только сигналит и ретранслирует аудио-кадры):
#     CALL / CALLRING / CALLA / CALLR / CALLB / VOICE
#   Клиент шлёт:
#     /help                                — команды
#     /list                                — список участников
#     /nick <имя>                          — сменить имя
#     /search <запрос>                     — найти человека по ID или имени
#     PM <id> <текст>  (/msg <имя> <текст>)— личное сообщение
#     G <id> <текст>   (/say <id> <текст>) — сообщение в группу
#     /group <название>                    — создать группу
#     /gadd <id> <имя> / /gleave <id>      — добавить участника / выйти из группы
#     /call <имя> / /answer / /decline / /hangup, VOICE <кадр>
#     /quit                                — выход
#
# Адрес привязки: по умолчанию 127.0.0.1 (только этот компьютер).
# Чтобы подключались с телефона/других машин, запустить с  CHAT_BIND=0.0.0.0
import hashlib
import json
import os
import random
import re
import socket
import sys
import threading
import time
from collections import deque
from datetime import datetime

try:                    # консоль Windows (cp1251/cp866) не умеет печатать часть символов
    sys.stdout.reconfigure(errors='replace')
    sys.stderr.reconfigure(errors='replace')
except (AttributeError, OSError):
    pass

HOST = '127.0.0.1'
PORT = 55
MAX_NAME_LEN = 30
HISTORY_SIZE = 50
MAX_LINE_LEN = 64 * 1024       # предел строки: аудио-кадр base64 помещается с запасом

# --- хранение данных на диске ---
# chat_data/groups.json   — группы (переживают перезапуск сервера)
# chat_data/accounts.json — логины и пароли (только sha256-хеш с солью)
# logs/<ГГГГ-ММ-ДД>_общий.txt            — общий чат
# logs/<ГГГГ-ММ-ДД>_группа_<название>.txt — группа
# logs/<ГГГГ-ММ-ДД>_личные_<A>_<B>.txt   — личные сообщения (имена по алфавиту)
CHAT_DIR = os.environ.get('CHAT_DIR') or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'chat_data')
LOGS_DIR = os.environ.get('CHAT_LOGS_DIR') or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'logs')
GROUPS_FILE = os.path.join(CHAT_DIR, 'groups.json')
ACCOUNTS_FILE = os.path.join(CHAT_DIR, 'accounts.json')
file_lock = threading.Lock()

clients = {}                 # socket -> {'name', 'id', 'addr', 'buf', 'call'}
clients_lock = threading.RLock()
history = deque(maxlen=HISTORY_SIZE)
calls = {}                   # имя звонящего -> {'caller', 'callee', 'state', ...}
calls_lock = threading.RLock()
groups = {}                  # id -> {'id', 'title', 'creator', 'members': [id ...]}
dialogs = {}                 # (id_a, id_b) -> deque([(время, id_автора, имя, текст)])
accounts = {}                # логин -> {'id', 'name', 'salt', 'hash'}
known_names = {}             # id -> последнее известное имя (для офлайн-участников)
meta_lock = threading.RLock()


def now():
    return time.strftime('%H:%M:%S')


# ------------------------------------------------------------- группы / ID
def load_meta():
    """Загружает группы и аккаунты с диска. Битый файл не мешает старту сервера."""
    try:
        with open(GROUPS_FILE, encoding='utf-8') as f:
            groups.update(json.load(f))
    except (OSError, ValueError):
        pass
    try:
        with open(ACCOUNTS_FILE, encoding='utf-8') as f:
            accounts.update(json.load(f))
    except (OSError, ValueError):
        pass


def save_meta():
    with meta_lock:
        snapshot_groups = {gid: dict(g) for gid, g in groups.items()}
        snapshot_accounts = {k: dict(v) for k, v in accounts.items()}
    try:
        os.makedirs(CHAT_DIR, exist_ok=True)
        for path, data in ((GROUPS_FILE, snapshot_groups), (ACCOUNTS_FILE, snapshot_accounts)):
            tmp = path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, path)
    except OSError as e:
        print(f'Не удалось сохранить данные в {CHAT_DIR}: {e}')


def hash_password(password, salt):
    return hashlib.sha256((salt + password).encode('utf-8')).hexdigest()


def make_id(client_id):
    """5-значный ID для аккаунта (логин), 10-значный — для гостя с устройства.
    Гость получает один и тот же ID, пока не очистит данные приложения."""
    client_id = client_id or ''
    if client_id.isdigit() and len(client_id) in (5, 10):
        return client_id
    if client_id.startswith('acct-'):
        with meta_lock:
            used = {acc.get('id') for acc in accounts.values()}
        number = str(random.Random(client_id).randrange(10000, 100000))
        return number if number not in used else str(random.randrange(100000000, 1000000000))
    return str(random.randrange(1000000000, 10000000000))


def clean(text):
    """Слово без экзотики: для составных полей однострочного протокола."""
    return re.sub(r'[\s,|]+', '_', (text or '').strip())[:40] or '_'


def neat(text):
    """Название для показа: одна строка, без '|' и запятых (они разделители в каталоге)."""
    return re.sub(r'[|,\r\n\t]+', ' ', (text or '').strip())[:60]


def member_ids(group_id):
    with meta_lock:
        group = groups.get(group_id)
        return list(group['members']) if group else []


def dialog_key(id_a, id_b):
    return tuple(sorted((str(id_a), str(id_b))))


def add_dialog(id_sender, id_receiver, sender_name, text):
    """Личное сообщение в кэш истории (файл на диске пишется отдельно)."""
    if not id_receiver:
        return
    with meta_lock:
        msgs = dialogs.setdefault(dialog_key(id_sender, id_receiver), deque(maxlen=HISTORY_SIZE))
        msgs.append((now(), str(id_sender), sender_name, text))


# ------------------------------------------------------------- запись в txt
def stamp():
    """Дата и время для записи в файл: 2026-11-23 20:15:02"""
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def describe_duration(entry):
    """Длительность разговора для записи в файл; для несостоявшегося — 'не соединились'."""
    started = entry.get('started')
    if not started:
        return 'не соединились'
    seconds = int(time.time() - started)
    return f'длит. {seconds // 60}:{seconds % 60:02d}'


def log_call(kind, text, *names):
    """Запись о звонке: в общий файл и (если это пара) в их личный файл."""
    write_chat(kind, *names, line=f'{stamp()}  {text}')


def safe_filename(name):
    r"""Имя, пригодное для имени файла (убираем \ / : * ? \" < > | и пробелы)."""
    cleaned = ''.join('_' if c in '\\/:*?"<>|' or c.isspace() else c for c in name)
    return cleaned.strip('.') or 'без_имени'


def log_path(kind, *names):
    day = datetime.now().strftime('%Y-%m-%d')
    if kind == 'общий':
        return os.path.join(LOGS_DIR, f'{day}_общий.txt')
    if kind == 'группа':
        return os.path.join(LOGS_DIR, f'{day}_группа_{safe_filename(names[0])}.txt')
    first, second = sorted(safe_filename(n) for n in names)
    return os.path.join(LOGS_DIR, f'{day}_личные_{first}_и_{second}.txt')


def write_chat(kind, *names, line):
    """Дописывает строку в txt-файл. Ошибка записи не ломает чат — только печатается."""
    path = log_path(kind, *names)
    try:
        with file_lock:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            new_file = not os.path.exists(path) or os.path.getsize(path) == 0
            with open(path, 'a', encoding='utf-8') as f:
                if new_file:
                    if kind == 'общий':
                        title = 'Общий чат'
                    elif kind == 'группа':
                        title = f'Группа «{names[0]}»'
                    else:
                        title = f'Личная переписка: {names[0]} и {names[1]}'
                    f.write(f'{title} — начало {stamp()}\n' + '-' * 60 + '\n')
                f.write(line + '\n')
    except OSError as e:
        print(f'Не удалось записать переписку в {path}: {e}')


# ------------------------------------------------------------- рассылка
def send_line(sock, line):
    try:
        sock.sendall((line + '\n').encode('utf-8'))
        return True
    except OSError:
        return False


def send_to(sock, kind, sender, text):
    return send_line(sock, f'{kind} {now()} {sender} {text}')


def info(sock, text):
    """Служебное сообщение клиенту: 'INFO <время> <кому> <текст>'."""
    send_to(sock, 'INFO', get_name(sock) or '-', text)


def user_list():
    with clients_lock:
        return sorted(info_['name'] for info_ in clients.values())


def broadcast_users():
    names = ','.join(user_list())
    with clients_lock:
        for sock in list(clients):
            send_line(sock, 'USERS ' + names)


# ------------------------------------------------------------- архив сообщений
# Переписка живёт на диске рядом с прочими данными: закрытая программа или
# перезапущенный сервер не теряют ни чатов, ни сообщений.
ARCHIVE_FILE = os.path.join(CHAT_DIR, 'archive.json')
ARCHIVE_LIMIT = 500          # сообщений на один чат (в памяти и на диске)
archive = {'general': []}    # 'general' | 'g:<id>' | 'd:<id>:<id>' -> [{when, sender, name, text}]
archive_lock = threading.Lock()
archive_dirty = False


def dialog_id(id_a, id_b):
    first, second = sorted((str(id_a), str(id_b)))
    return f'd:{first}:{second}'


def group_id_of(group_id):
    return f'g:{group_id}'


def archive_add(key, sender_id, sender_name, text, when=None):
    """Записать сообщение в архив чата key ('general', 'g:<id>', 'd:<id>:<id>')."""
    global archive_dirty
    entry = {'when': when or now(), 'sender': str(sender_id),
             'name': sender_name, 'text': text}
    with archive_lock:
        messages = archive.setdefault(key, [])
        messages.append(entry)
        if len(messages) > ARCHIVE_LIMIT:
            del messages[:len(messages) - ARCHIVE_LIMIT]
        archive_dirty = True
    if key != 'general':        # общий чат пишется в память и сохраняется по таймеру
        save_archive()
    return entry


def archive_read(key, limit=ARCHIVE_LIMIT):
    with archive_lock:
        return list(archive.get(key, []))[-limit:]


def mark_delivered(chat_key, user_id):
    """Отмечает, что непрочитанные личные сообщения из чата уже показаны пользователю."""
    with archive_lock:
        for msg in archive.get(chat_key, []):
            if msg['sender'] != str(user_id):
                msg['delivered'] = True
    save_archive()


def load_archive():
    try:
        with open(ARCHIVE_FILE, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return
    with archive_lock:
        for key, messages in data.items():
            if isinstance(messages, list):
                archive[key] = messages[-ARCHIVE_LIMIT:]


def save_archive(force=False):
    """Сохраняет архив: периодически и при каждом личном/групповом сообщении."""
    global archive_dirty
    with archive_lock:
        if not archive_dirty and not force:
            return
        snapshot = {key: list(messages) for key, messages in archive.items()}
        archive_dirty = False
    try:
        os.makedirs(CHAT_DIR, exist_ok=True)
        tmp = ARCHIVE_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(snapshot, f, ensure_ascii=False)
        os.replace(tmp, ARCHIVE_FILE)
    except OSError as e:
        print(f'Не удалось сохранить архив сообщений: {e}')


def archive_saver():
    """Фоновое сохранение архива раз в 15 секунд."""
    while True:
        time.sleep(15)
        save_archive()


def user_chat_list(user_id):
    """Чаты с историей у пользователя: общий, его группы и личные диалоги."""
    user_id = str(user_id)
    with archive_lock:
        keys = list(archive)
    chats = []
    for key in keys:
        if key == 'general':
            chats.append(('general', 'general', 'Общий чат'))
        elif key.startswith('g:'):
            gid = key[2:]
            if user_id in member_ids(gid):
                with meta_lock:
                    group = groups.get(gid)
                chats.append(('group', gid, group['title'] if group else 'Группа'))
        elif key.startswith('d:'):
            parts = key.split(':')
            if len(parts) == 3 and user_id in parts[1:]:
                peer = parts[2] if parts[1] == user_id else parts[1]
                chats.append(('dm', peer, known_names.get(peer, peer)))
    return chats


def send_chat_catalog(sock):
    """Каталог чатов: 'CHATS general|Общий чат,group:12|Название,dm:34|Имя'."""
    parts = [f'{kind}|{key}|{clean(title)}'
             for kind, key, title in user_chat_list(str(get_id(sock)))]
    send_line(sock, 'CHATS ' + (','.join(parts) or '-'))


def send_archive_chat(sock, kind, chat_key):
    """Отдаёт историю одного чата клиенту (GHIST / DHIST)."""
    user_id = str(get_id(sock))
    for msg in archive_read(chat_key, HISTORY_SIZE * 2):
        if kind == 'group':
            gid = chat_key[2:]
            with meta_lock:
                group = groups.get(gid)
            title = clean(group['title']) if group else 'Группа'
            send_line(sock, f'GHIST {msg["when"]} {gid} {title} '
                            f'{clean(msg["name"])} {msg["text"]}')
        else:
            parts = chat_key.split(':')
            peer = parts[2] if parts[1] == user_id else parts[1]
            send_line(sock, f'DHIST {msg["when"]} {peer} {msg["sender"]} '
                            f'{clean(msg["name"])} {msg["text"]}')


def broadcast(kind, sender, text, exclude=None):
    """Рассылает всем, кроме exclude (один сокет или None). Возвращает число доставленных."""
    delivered = 0
    with clients_lock:
        dead = []
        for sock in list(clients):
            if sock is exclude:
                continue
            if send_to(sock, kind, sender, text):
                delivered += 1
            else:
                dead.append(sock)
    for sock in dead:
        drop_client(sock, 'соединение потеряно')
    return delivered


def drop_client(sock, reason=''):
    with clients_lock:
        info_ = clients.pop(sock, None)
        if info_ is None:
            return None
        name = info_['name']
        try:
            sock.close()
        except OSError:
            pass
    if call_of(name):
        entry = call_of(name)
        if entry['state'] == 'ringing' and entry['callee'] == name:
            decline_call(sock, name, 'отключился')
        else:
            detach_call(entry)
            notify_call_end(entry, name, 'связь потеряна')
            print(f'Звонок прерван: {name}')
            log_call('общий', f'Звонок {entry["caller"]} <-> {entry["callee"]} прерван'
                                f' ({describe_duration(entry)}): {name} отключился')
    print(f'{name} отключился ({reason}). Осталось {len(user_list())} клиентов.')
    write_chat('общий', line=f'{stamp()}  {name} вышел из чата ({reason})')
    broadcast('LEFT', name, 'покинул чат')
    broadcast_users()
    return name


def find_socket(name):
    """Сокет участника по имени (соединение живёт в одном потоке-обработчике)."""
    with clients_lock:
        for s, info_ in clients.items():
            if info_['name'] == name:
                return s
    return None


def sockets_by_name(name):
    with clients_lock:
        return [s for s, info_ in clients.items() if info_['name'] == name]


def socket_by_id(user_id):
    with clients_lock:
        for sock, client in clients.items():
            if client['id'] == str(user_id):
                return sock
    return None


def get_client(sock):
    with clients_lock:
        info_ = clients.get(sock)
        return dict(info_) if info_ else {}


def get_name(sock):
    return get_client(sock).get('name')


def get_id(sock):
    return get_client(sock).get('id')


# ------------------------------------------------------------- группы
def create_group(sock, name, title):
    title = neat(title) or 'Новая группа'
    with meta_lock:
        gid = str(random.randrange(100000, 1000000))
        while gid in groups:
            gid = str(random.randrange(100000, 1000000))
        groups[gid] = {'id': gid, 'title': title, 'creator': get_id(sock),
                       'members': [get_id(sock)]}
    save_meta()
    send_line(sock, f'GROUP+ {gid} {clean(title)}')
    info(sock, f'Группа «{title}» создана — ID {gid}')
    broadcast_groups()
    print(f'Группа создана: «{title}» ({gid}) — {name}')
    return gid


def group_members_send(group_id, text):
    for user_id in member_ids(group_id):
        target = socket_by_id(user_id)
        if target is not None:
            send_to(target, 'GMEM', group_id, text)


def broadcast_groups():
    """Каждый получает каталог с пометкой, в каких группах он состоит."""
    with clients_lock:
        sockets = list(clients)
    for sock in sockets:
        send_line(sock, 'LIST ' + groups_catalog(get_id(sock)))


def groups_catalog(user_id):
    with meta_lock:
        items = sorted(groups.values(), key=lambda g: g['title'].lower())
        parts = [f'{g["id"]}|{clean(g["title"])}|{len(g["members"])}|'
                 f'{"1" if user_id in g["members"] else "0"}' for g in items]
    return ','.join(parts) or '-'


def join_group(sock, group_id):
    group_id = group_id.strip()
    with meta_lock:
        group = groups.get(group_id)
    if not group:
        send_line(sock, f'ERR Группа {group_id} не найдена')
        return
    user_id = get_id(sock)
    if user_id in member_ids(group_id):
        info(sock, f'Вы уже в группе «{group["title"]}»')
        return
    with meta_lock:
        group['members'].append(user_id)
    save_meta()
    send_line(sock, f'GROUP+ {group_id} {clean(group["title"])}')
    info(sock, f'Вы вступили в группу «{group["title"]}»')
    group_members_send(group_id, f'{get_name(sock)} вступил в группу')
    broadcast_groups()


def leave_group(sock, group_id, reason='покинул группу'):
    group_id = group_id.strip()
    user_id = get_id(sock)
    name = get_name(sock)
    with meta_lock:
        group = groups.get(group_id)
        if not group or user_id not in group['members']:
            group = None
        else:
            group['members'] = [m for m in group['members'] if m != user_id]
            emptied = not group['members']
            title = group['title']
            if emptied:
                groups.pop(group_id, None)
    if group is None:
        send_line(sock, f'ERR Вы не в группе {group_id}')
        return
    save_meta()
    send_line(sock, f'GROUP- {group_id}')
    info(sock, f'Вы вышли из группы «{title}»' if not emptied
         else f'Группа «{title}» удалена: не осталось участников')
    if not emptied:
        group_members_send(group_id, f'{name} {reason}')
    broadcast_groups()


def add_to_group(sock, group_id, member):
    member = (member or '').strip().lstrip('@')
    group_id = (group_id or '').strip()
    with meta_lock:
        group = groups.get(group_id)
    if not group:
        send_line(sock, f'ERR Группа {group_id} не найдена')
        return
    target = socket_by_id(member) or find_socket(member)
    if target is None:
        send_line(sock, f'ERR «{member}» не в сети: добавьте по ID (человек должен быть онлайн)')
        return
    target_info = get_client(target)
    if target_info['id'] in member_ids(group_id):
        info(sock, f'{target_info["name"]} уже в группе')
        return
    with meta_lock:
        group['members'].append(target_info['id'])
    save_meta()
    send_line(target, f'GROUP+ {group_id} {clean(group["title"])}')
    info(target, f'Вас добавили в группу «{group["title"]}»')
    info(sock, f'{target_info["name"]} добавлен в группу')
    group_members_send(group_id, f'{target_info["name"]} присоединился')
    broadcast_groups()


def members_info(group_id):
    """Состав группы для клиента: id|имя|в сети, через запятую.
    Имена запоминаем, чтобы офлайн-участник не превращался в безымянного."""
    with clients_lock:
        online = {i['id']: i['name'] for i in clients.values()}
    with meta_lock:
        for uid in member_ids(group_id):
            if uid in online:
                known_names[uid] = online[uid]
    parts = [f'{uid}|{clean(online.get(uid) or known_names.get(uid, "Участник"))}|'
             f'{1 if uid in online else 0}' for uid in member_ids(group_id)]
    return ','.join(parts) or '-'


def send_group(sock, name, group_id, text, kind='GMSG'):
    group_id = (group_id or '').strip()
    with meta_lock:
        group = groups.get(group_id)
    if not group:
        send_line(sock, f'ERR Группа {group_id} не найдена')
        return
    if get_id(sock) not in group['members']:
        send_line(sock, f'ERR Вы не в группе «{group["title"]}»')
        return
    sent = 0
    for user_id in member_ids(group_id):
        target = socket_by_id(user_id)
        if target is not None and send_line(target, f'{kind} {now()} {group_id} '
                                                f'{clean(group["title"])} {name} {text}'):
            sent += 1
    write_chat('группа', group['title'], line=f'{stamp()}  {name}: {text}')
    archive_add(group_id_of(group_id), get_id(sock), name, text)
    print(f'Группа «{group["title"]}»: {name}: {text[:60]}')


# ------------------------------------------------------------- личные
def send_dialog(sock, name, user_id, text, kind='DMSG'):
    user_id = (user_id or '').strip().lstrip('@')
    targets = []
    if user_id.isdigit():
        target = socket_by_id(user_id)
        if target is not None:
            targets = [target]
    else:
        targets = sockets_by_name(user_id)
    key_receiver = user_id if user_id.isdigit() else (get_client(targets[0])['id']
                                                      if targets else user_id)
    if not targets and not user_id.isdigit():
        send_line(sock, 'ACK 0')
        send_line(sock, f'ERR «{user_id}» не в сети')
        return
    sent = 0
    for target in targets:
        if send_line(target, f'{kind} {now()} {get_id(sock)} {name} {text}'):
            sent += 1
    # Сообщение сохраняется, даже если собеседник сейчас не в сети:
    # он прочтёт его, когда зайдёт (история отдаётся через DHIST).
    add_dialog(get_id(sock), key_receiver, name, text)
    archive_add(dialog_id(get_id(sock), key_receiver), get_id(sock), name, text)
    peer_name = (get_client(targets[0])['name'] if targets
                 else known_names.get(str(key_receiver), str(key_receiver)))
    write_chat('личные', name, peer_name, line=f'{stamp()}  {name} -> {peer_name}: {text}')
    send_line(sock, 'ACK 1')
    info(sock, f'Личное сообщение для {peer_name} отправлено')
    if not targets:
        info(sock, f'{peer_name} не в сети — доставим, когда зайдёт')


def send_dialog_history(sock, user_id):
    """История личного диалога — из архива на диске (доступна в любой момент)."""
    user_id = (user_id or '').strip()
    send_archive_chat(sock, 'dm', dialog_id(get_id(sock), user_id))


# ------------------------------------------------------------- поиск
def search_user(sock, query):
    query = (query or '').strip().lstrip('@')
    if not query:
        send_line(sock, 'ERR Использование: /search <ID или имя>')
        return
    with clients_lock:
        found = [dict(i) for i in clients.values()
                 if query.lower() in i['name'].lower() or query in str(i['id'])]
        online = {i['id'] for i in clients.values()}
    if query.isdigit():
        found_accounts = [acc for acc in accounts.values()
                          if query in acc['id']]
    else:
        found_accounts = [acc for acc in accounts.values()
                          if query.lower() in acc['name'].lower()
                          or query.lower() in acc.get('login', '')]
    with meta_lock:
        for acc in found_accounts:
            if acc['id'] not in online:
                found.append({'name': acc['name'], 'id': acc['id']})
    parts = []
    seen = set()
    for item in found[:20]:
        if item['id'] in seen:
            continue
        seen.add(item['id'])
        parts.append(f'{item["id"]}|{clean(item["name"])}|'
                     f'{"1" if item["id"] in online else "0"}')
    send_line(sock, f'SEARCH {clean(query)} {len(parts)} ' + (','.join(parts) or '-'))


# ------------------------------------------------------------- звонки
def set_call(sock, partner):
    with clients_lock:
        info_ = clients.get(sock)
        if info_ is not None:
            info_['call'] = partner


def get_call(sock):
    with clients_lock:
        info_ = clients.get(sock)
        return info_.get('call') if info_ else None


def call_of(key):
    """Разговор, где ключ — имя участника или имя звонящего (ключ таблицы)."""
    with calls_lock:
        entry = calls.get(key)
        if entry is None:
            for e in calls.values():
                if key in (e['caller'], e['callee']):
                    entry = e
                    break
        if entry and key in (entry['caller'], entry['callee']):
            return dict(entry)
    return None


def detach_call(entry):
    """Убирает звонок из таблицы и сбрасывает пометку «занят» у обоих участников."""
    with calls_lock:
        current = calls.get(entry['caller'])
        if current is entry or current == entry:
            calls.pop(entry['caller'], None)
    with clients_lock:
        for who in (entry['caller'], entry['callee']):
            for client in clients.values():
                if client['name'] == who and client.get('call') in (entry['caller'], entry['callee']):
                    client['call'] = None


def notify_call_end(entry, leaver, reason, kind='CALLB'):
    """Сообщает второму участнику (если он ещё в сети) исход звонка: CALLR или CALLB."""
    partner = entry['callee'] if leaver == entry['caller'] else entry['caller']
    partner_sock = find_socket(partner)
    if partner_sock is not None:
        send_to(partner_sock, kind, leaver, reason)
    return partner_sock is not None


def start_call(sock, name, target):
    target = (target or '').strip().lstrip('@')
    if not target:
        send_line(sock, 'ERR Использование: /call <имя или ID>')
        return
    target_sock = socket_by_id(target) if target.isdigit() else find_socket(target)
    target_name = get_name(target_sock) if target_sock is not None else target
    if target_name == name:
        send_line(sock, 'ERR Нельзя позвонить самому себе')
        return
    if target_sock is None:
        send_line(sock, f'ERR Участник «{target}» не найден. /list — список участников')
        return
    if call_of(name):
        send_line(sock, 'ERR Вы уже в звонке. Сначала /hangup')
        return
    if call_of(target_name):
        send_line(sock, f'ERR Участник «{target_name}» сейчас занят')
        return
    entry = {'caller': name, 'callee': target_name, 'state': 'ringing', 'started': None}
    with calls_lock:
        calls[name] = entry
    set_call(sock, target_name)
    set_call(target_sock, name)
    if not send_to(target_sock, 'CALL', name, 'звонит вам: /answer — принять, /decline — отклонить'):
        detach_call(entry)
        send_line(sock, f'ERR Не удалось дозвониться до «{target_name}»')
        return
    send_to(sock, 'CALLRING', target_name, 'ждём ответа...')
    print(f'Звонок: {name} -> {target_name}')


def answer_call(sock, name):
    entry = call_of(name)
    if not entry or entry['callee'] != name or entry['state'] != 'ringing':
        send_line(sock, 'ERR Нет входящего звонка')
        return
    caller_sock = find_socket(entry['caller'])
    if caller_sock is None:
        detach_call(entry)
        send_line(sock, 'ERR Звонящий уже отключился')
        return
    with calls_lock:
        calls[entry['caller']] = dict(entry, state='active', started=time.time())
    send_to(sock, 'INFO', name, 'звонок принят')
    send_to(caller_sock, 'CALLA', name, 'принял звонок')
    print(f'Звонок принят: {entry["caller"]} <-> {name}')
    log_call('общий', f'Звонок {entry["caller"]} <-> {name} начат')


def decline_call(sock, name, reason='отклонил звонок'):
    entry = call_of(name)
    if not entry:
        send_line(sock, 'ERR Нет входящего звонка')
        return
    if entry['callee'] != name or entry['state'] == 'active':
        send_line(sock, 'ERR Это не входящий звонок. /hangup — завершить')
        return
    detach_call(entry)
    notify_call_end(entry, name, reason, kind='CALLR')
    send_to(sock, 'INFO', name, 'звонок отклонён')
    print(f'Звонок отклонён: {entry["caller"]} -/-> {name}')
    log_call('общий', f'Звонок {entry["caller"]} -> {name} отклонён')


def hangup_call(sock, name):
    entry = call_of(name)
    if not entry:
        send_line(sock, 'ERR Активного звонка нет')
        return
    if entry['state'] == 'ringing' and entry['callee'] == name:
        send_line(sock, 'ERR Это входящий звонок. /decline — отклонить, /answer — принять')
        return
    detach_call(entry)
    notify_call_end(entry, name, 'положил трубку')
    send_to(sock, 'INFO', name, 'звонок завершён')
    print(f'Звонок завершён: {name}')
    log_call('общий', f'Звонок {entry["caller"]} <-> {entry["callee"]} завершён'
                        f' ({describe_duration(entry)}), трубку положил {name}')


def relay_voice(sock, name, payload):
    """Пересылает аудио-кадр второму участнику; сервер не разбирает base64."""
    entry = call_of(name)
    if not entry or entry['state'] != 'active' or not payload:
        return  # вне звонка кадры молча отбрасываем (их много)
    partner = entry['callee'] if entry['caller'] == name else entry['caller']
    partner_sock = find_socket(partner)
    if partner_sock is None:
        detach_call(entry)
        send_to(sock, 'CALLB', partner, 'связь потеряна')
        return
    send_line(partner_sock, f'VOICE {name} {payload}')


# ------------------------------------------------------------- вход
def unique_name(wanted):
    with clients_lock:
        taken = {info_['name'] for info_ in clients.values()}
    if wanted not in taken:
        return wanted
    i = 2
    while f'{wanted}_{i}' in taken:
        i += 1
    return f'{wanted}_{i}'


def register_account(name, login, password, sock):
    """Вход по логину. Возвращает (имя, id) или None, если данные неверные."""
    login = login.strip().lower()[:30]
    if not re.fullmatch(r'[\w.@-]+', login):
        send_line(sock, 'ERR Логин: только латиница/цифры/_ .- (до 30 символов)')
        return None
    if len(password) < 4:
        send_line(sock, 'ERR Пароль: минимум 4 символа')
        return None
    with meta_lock:
        acc = accounts.get(login)
        if acc is None:
            acc = {'id': make_id(f'acct-{login}'), 'name': name,
                   'salt': hashlib.sha256(os.urandom(16)).hexdigest()[:16],
                   'hash': '', 'login': login}
            acc['hash'] = hash_password(password, acc['salt'])
            accounts[login] = acc
            print(f'Новый аккаунт: {login} ({clean(name)}, ID {acc["id"]})')
        else:
            if acc['hash'] != hash_password(password, acc['salt']):
                send_line(sock, 'ERR Пароль не подходит к логину')
                return None
            if acc['name'] != name:
                acc['name'] = name
    save_meta()
    return unique_name(name), acc['id']


def read_line(sock, timeout=None):
    """Читает одну строку, буфер хранится в clients[sock]['buf']."""
    with clients_lock:
        info_ = clients.get(sock)
    try:
        sock.settimeout(timeout)
    except OSError:
        return None
    buf = info_['buf'] if info_ else b''
    while b'\n' not in buf:
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            return None
        except OSError:
            return None
        if not chunk:
            return None
        buf += chunk
        if len(buf) > MAX_LINE_LEN:
            return None
    line, rest = buf.split(b'\n', 1)
    if info_:
        info_['buf'] = rest
    return line.decode('utf-8', errors='replace')


# ------------------------------------------------------------- команды
def handle_command(sock, name, text):
    """Обрабатывает команду клиента. Возвращает новое имя или None при выходе."""
    parts = text.split(' ', 2)
    cmd = parts[0].lower()

    if cmd in ('/quit', '/exit', 'exit', 'quit'):
        return 'QUIT'

    if cmd == '/help':
        info(sock, 'Команды: /list — участники, /search <ID> — найти человека, '
                   'PM <ID> <текст> — личное, /group <название> — создать группу, '
                   'G <ID> <текст> — сообщение в группу, /gadd <ID> <имя> — добавить, '
                   '/gleave <ID> — выйти, /nick <имя>, /call <имя>, /quit')
        return name

    if cmd == '/list':
        send_line(sock, 'USERS ' + ','.join(user_list()))
        return name

    if cmd == '/search':
        search_user(sock, ' '.join(parts[1:]))
        return name

    if cmd in ('pm', '/msg'):
        if text.startswith('/'):
            data = text[len(parts[0]):]
        else:
            data = text
        data = data.strip()
        target, _, body = data.partition(' ')
        if not target or not body.strip():
            send_line(sock, 'ERR Использование: PM <ID> <текст> или /msg <имя> <текст>')
            return name
        send_dialog(sock, name, target, body.strip())
        return name

    if cmd in ('g', '/say'):
        data = (text[len(parts[0]):]).strip()
        group_id, _, body = data.partition(' ')
        if not group_id or not body.strip():
            send_line(sock, 'ERR Использование: G <ID группы> <текст>')
            return name
        send_group(sock, name, group_id, body.strip())
        return name

    if cmd == '/group':
        title = ' '.join(parts[1:]).strip()
        if not title:
            send_line(sock, 'ERR Использование: /group <название>')
            return name
        create_group(sock, name, title)
        return name

    if cmd == '/join':
        if len(parts) < 2:
            send_line(sock, 'ERR Использование: /join <ID группы>')
            return name
        join_group(sock, parts[1])
        return name

    if cmd in ('/gadd', '/add'):
        if len(parts) < 3:
            send_line(sock, 'ERR Использование: /gadd <ID группы> <ID или имя>')
            return name
        add_to_group(sock, parts[1], parts[2])
        return name

    if cmd in ('/gleave', '/leave'):
        if len(parts) < 2:
            send_line(sock, 'ERR Использование: /gleave <ID группы>')
            return name
        leave_group(sock, parts[1])
        return name

    if cmd == '/nick':
        if len(parts) < 2 or not parts[1].strip():
            send_line(sock, 'ERR Использование: /nick <новое имя>')
            return name
        new = unique_name(parts[1].strip()[:MAX_NAME_LEN])
        # имя новое — разговор (если есть) переносим, чтобы звонок не отвалился
        with calls_lock:
            if name in calls:
                calls[new] = calls.pop(name)
            for entry in calls.values():
                if entry['caller'] == name:
                    entry['caller'] = new
                if entry['callee'] == name:
                    entry['callee'] = new
        with clients_lock:
            info_ = clients.get(sock)
            if info_:
                info_['name'] = new
        print(f'Смена имени: {name} -> {new}')
        write_chat('общий', line=f'{stamp()}  {name} сменил имя на {new}')
        broadcast('NICK', name, new)
        broadcast_users()
        return new

    if cmd == '/call':
        start_call(sock, name, parts[1].strip() if len(parts) > 1 else '')
        return name

    if cmd == '/answer':
        answer_call(sock, name)
        return name

    if cmd == '/decline':
        decline_call(sock, name)
        return name

    if cmd == '/hangup':
        hangup_call(sock, name)
        return name

    send_line(sock, f'ERR Неизвестная команда: {parts[0]}. /help — список команд')
    return name


def handle_client(sock, addr):
    name = None
    try:
        first = read_line(sock, timeout=10)
        if first is None:
            return
        # 'имя' или 'имя|логин|пароль' или 'имя||id_устройства'
        fields = [f.strip() for f in first.split('|')]
        wanted = (fields[0] or f'Гость_{addr[1]}')[:MAX_NAME_LEN]
        user_id = None
        if len(fields) >= 3 and fields[1]:
            registered = register_account(wanted, fields[1], fields[2], sock)
            if registered is None:
                try:
                    sock.close()
                except OSError:
                    pass
                return
            name, user_id = registered
            with clients_lock:
                taken = {i['name'] for i in clients.values()}
            if name in taken:      # уже в сети с другого устройства
                name = unique_name(name)
        else:
            device = fields[2] if len(fields) >= 3 else ''
            user_id = make_id(device)
            name = unique_name(wanted)
        with clients_lock:
            if sock in clients:
                return
            clients[sock] = {'name': name, 'id': str(user_id), 'addr': addr,
                             'buf': b'', 'call': None}
        send_line(sock, f'OK {name} {user_id}')
        with meta_lock:
            known_names[str(user_id)] = name
            if accounts:
                for acc in accounts.values():
                    if acc['id'] == str(user_id):
                        acc['name'] = name
                        break
        save_meta()
        print(f'Новый клиент: {name} (ID {user_id}) ({addr[0]}:{addr[1]})')
        write_chat('общий', line=f'{stamp()}  {name} вошёл в чат ({addr[0]})')

        # история для новичка
        with clients_lock:
            recent = list(history)
        for t, sender, text in recent:
            send_line(sock, f'HIST {t} {sender} {text}')
        send_line(sock, 'USERS ' + ','.join(user_list()))
        send_line(sock, 'LIST ' + groups_catalog(str(user_id)))
        send_chat_catalog(sock)
        # непрочитанное: личные сообщения, пришедшие пока человека не было
        for kind, key, title in user_chat_list(str(user_id)):
            if kind != 'dm':
                continue
            chat_key = dialog_id(user_id, key)
            for msg in archive_read(chat_key, 50):
                if msg['sender'] != str(user_id) and msg.get('delivered') is not True:
                    send_line(sock, f'DHIST {msg["when"]} {key} {msg["sender"]} '
                                    f'{clean(msg["name"])} {msg["text"]}')
            mark_delivered(chat_key, str(user_id))
        broadcast('JOIN', name, 'вошёл в чат', exclude=sock)
        broadcast_users()

        while True:
            line = read_line(sock)
            if line is None:
                break
            text = line.strip()
            if not text:
                continue
            if text.startswith('VOICE '):
                relay_voice(sock, name, text[6:].strip())
                continue
            if text.startswith('/'):
                new_name = handle_command(sock, name, text)
                if new_name == 'QUIT':
                    break
                if new_name:
                    name = new_name
                continue
            head = text.split(' ', 1)[0].upper()
            if head == 'PM':
                rest = text[2:].strip()
                target, _, body = rest.partition(' ')
                if target and body.strip():
                    send_dialog(sock, name, target, body.strip())
                else:
                    send_line(sock, 'ERR Использование: PM <ID> <текст>')
                continue
            if head == 'G':
                rest = text[1:].strip()
                group_id, _, body = rest.partition(' ')
                if group_id and body.strip():
                    send_group(sock, name, group_id, body.strip())
                else:
                    send_line(sock, 'ERR Использование: G <ID группы> <текст>')
                continue
            if head in ('HISTORY', 'DGHIST', 'GHISTORY', 'CHAT'):
                if head == 'CHAT':
                    arg = text[4:].strip()
                    kind, _, key = arg.partition(':')
                    if kind == 'general':
                        send_line(sock, 'CHAT general')
                        for msg in archive_read('general', HISTORY_SIZE * 2):
                            send_line(sock, f'HIST {msg["when"]} {clean(msg["name"])} '
                                            f'{msg["text"]}')
                    elif kind == 'group':
                        send_archive_chat(sock, 'group', group_id_of(key))
                    elif kind == 'dm':
                        send_archive_chat(sock, 'dm', dialog_id(get_id(sock), key))
                else:
                    target = text.split(' ', 1)[1].strip() if ' ' in text else ''
                    if head == 'GHISTORY':
                        send_archive_chat(sock, 'group', group_id_of(target))
                    else:
                        send_dialog_history(sock, target)
                send_line(sock, 'HISTEND')
                continue
            if head == 'CHATS':
                send_chat_catalog(sock)
                continue
            if head == 'MEMS':
                gid = text[4:].strip()
                send_line(sock, f'MEMBERS {gid} ' + members_info(gid))
                continue
            result = broadcast('MSG', name, text, exclude=sock)
            send_line(sock, f'ACK {result}')   # своя строка уже показана локально
            with clients_lock:
                history.append((now(), name, text))
            archive_add('general', get_id(sock), name, text)
            write_chat('общий', line=f'{stamp()}  {name}: {text}')
    except Exception as e:
        print(f'Ошибка обработки клиента {addr}: {e}')
    finally:
        drop_client(sock, 'выход')


def accept_connections(server_socket):
    while True:
        try:
            sock, addr = server_socket.accept()
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            threading.Thread(target=handle_client, args=(sock, addr), daemon=True).start()
        except OSError:
            break
        except Exception as e:
            print(f'Ошибка приёма подключений: {e}')


def main():
    load_meta()
    load_archive()
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        bind_host = os.environ.get('CHAT_BIND', HOST)
        server_socket.bind((bind_host, PORT))
        server_socket.listen(16)
        shown = 'все интерфейсы' if bind_host in ('0.0.0.0', '') else bind_host
        print(f'Сервер запущен на {shown}:{PORT}', flush=True)
        print(f'Групп загружено: {len(groups)}, аккаунтов: {len(accounts)}', flush=True)
        if bind_host in ('127.0.0.1', 'localhost'):
            print('ℹ Доступен только локально. Для телефона перезапустите с CHAT_BIND=0.0.0.0',
                  flush=True)
        threading.Thread(target=accept_connections, args=(server_socket,), daemon=True).start()
        threading.Thread(target=archive_saver, daemon=True).start()
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print('\nОстановка сервера...')
    except OSError as e:
        print(f'Не удалось запустить сервер: {e}')
    finally:
        with clients_lock:
            for sock in list(clients):
                try:
                    sock.close()
                except OSError:
                    pass
            clients.clear()
        server_socket.close()
        save_archive(force=True)
        print('Сервер завершён.')


if __name__ == '__main__':
    main()
