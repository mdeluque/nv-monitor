#!/usr/bin/env python3
"""Comprobador externo (GitHub Actions cada 5 min): webs, SSL y señal del agente de saas-01.

Variables de entorno: TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, STATE_FILE (por defecto state.json).
Solo librería estándar. El estado entre ejecuciones lo guarda el workflow en la caché de Actions.
"""
import json
import os
import socket
import ssl
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

# (nombre, url, texto que debe aparecer o None)
SITES = [
    ('nv-app', 'https://nv-app.pchinojosa.com/api/health', None),
    ('PulseTicket API', 'https://api.pulseticket.es/up', None),
    ('PulseTicket admin', 'https://admin.pulseticket.es/', 'PulseTicket'),
    ('PulseTicket tienda', 'https://hinojosa.pulseticket.es/tienda/', 'PulseTicket'),
]
CERT_HOSTS = ['nv-app.pchinojosa.com', 'api.pulseticket.es']
AGENT_HEARTBEAT = 'https://nv-app.pchinojosa.com/storage/monitor/agent.json'
AGENT_MAX_AGE = 20 * 60
CERT_MIN_DAYS = 14
REMIND_EVERY = 6 * 3600
STATE_FILE = os.environ.get('STATE_FILE', 'state.json')


def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers={'User-Agent': 'nvmon-external/1.0', 'Cache-Control': 'no-cache'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read(200_000).decode('utf-8', 'replace')


def check_site(url, keyword):
    last = ''
    for attempt in range(2):  # un reintento: evita avisos por un fallo puntual de red
        try:
            t0 = time.time()
            status, body = fetch(url)
            ms = (time.time() - t0) * 1000
            if status != 200:
                last = f'HTTP {status}'
            elif keyword and keyword not in body:
                last = f'no contiene «{keyword}»'
            else:
                return True, f'responde en {ms:.0f} ms'
        except Exception as e:  # noqa: BLE001
            last = getattr(e, 'reason', None) or str(e)
        time.sleep(10)
    return False, str(last)


def cert_days(host):
    ctx = ssl.create_default_context()
    with socket.create_connection((host, 443), timeout=15) as s, ctx.wrap_socket(s, server_hostname=host) as t:
        end = ssl.cert_time_to_seconds(t.getpeercert()['notAfter'])
    return (end - time.time()) / 86400


def checks():
    res = {}
    for name, url, kw in SITES:
        ok, msg = check_site(url, kw)
        res[f'site:{name}'] = (ok, f'{name} ({url}): {msg}')
    for host in CERT_HOSTS:
        try:
            d = cert_days(host)
            res[f'cert:{host}'] = (d > CERT_MIN_DAYS, f'Certificado de {host}: caduca en {d:.0f} días')
        except Exception as e:  # noqa: BLE001
            res[f'cert:{host}'] = (False, f'Certificado de {host}: {e}')
    try:
        _, body = fetch(AGENT_HEARTBEAT)
        age = time.time() - json.loads(body)['ts']
        res['agent'] = (age < AGENT_MAX_AGE, f'Agente de saas-01: última señal hace {age / 60:.0f} min')
    except Exception as e:  # noqa: BLE001
        res['agent'] = (False, f'Agente de saas-01 sin señal: {e}')
    return res


def telegram(text):
    data = urllib.parse.urlencode({'chat_id': os.environ['TELEGRAM_CHAT_ID'], 'text': text}).encode()
    urllib.request.urlopen(f'https://api.telegram.org/bot{os.environ["TELEGRAM_TOKEN"]}/sendMessage', data, timeout=15).read()


def main():
    try:
        with open(STATE_FILE) as f:
            alerts = json.load(f)
    except (FileNotFoundError, ValueError):
        alerts = {}
    now = time.time()
    res = checks()
    msgs = []
    for key, (ok, msg) in res.items():
        a = alerts.get(key)
        if not ok and a is None:
            msgs.append(f'🔴 {msg}')
            alerts[key] = {'since': now, 'notified': now}
        elif not ok and now - a['notified'] > REMIND_EVERY:
            msgs.append(f'🟠 Sigue: {msg} (desde hace {(now - a["since"]) / 3600:.0f} h)')
            a['notified'] = now
        elif ok and a is not None:
            msgs.append(f'✅ Resuelto: {msg}')
            del alerts[key]
    for ok, msg in res.values():
        print(('OK  ' if ok else 'MAL ') + msg)
    if msgs:
        telegram('[externo]\n' + '\n'.join(msgs))
    with open(STATE_FILE, 'w') as f:
        json.dump(alerts, f)
    print(datetime.now(timezone.utc).isoformat(), f'{len(alerts)} alerta(s) activas')


if __name__ == '__main__':
    main()
