# -*- coding: utf-8 -*-
import json
import ipaddress
import smtplib
import socket
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import urlparse

import requests

from models import db, SystemConfig, User, UserNotification
from retention_service import cleanup_expired_station_notifications


def _is_enabled(key, default='false'):
    raw = str(SystemConfig.get_config(key, default)).strip().lower()
    return raw in {'true', '1', 'yes', 'on'}


def _get_config(key, default=''):
    return str(SystemConfig.get_config(key, default) or '').strip()


def _user_config_key(user_id, key):
    return f'user_notify_{user_id}_{key}'


def get_user_notification_config(user):
    user_id = getattr(user, 'id', None)
    if not user_id:
        return {}
    return {
        'station_enabled': _is_enabled(_user_config_key(user_id, 'station_enabled'), 'true'),
        'webhook_enabled': _is_enabled(_user_config_key(user_id, 'webhook_enabled')),
        'webhook_url': _get_config(_user_config_key(user_id, 'webhook_url')),
        'telegram_enabled': _is_enabled(_user_config_key(user_id, 'telegram_enabled')),
        'telegram_bot_token': _get_config(_user_config_key(user_id, 'telegram_bot_token')),
        'telegram_chat_id': _get_config(_user_config_key(user_id, 'telegram_chat_id')),
        'pushplus_enabled': _is_enabled(_user_config_key(user_id, 'pushplus_enabled')),
        'pushplus_token': _get_config(_user_config_key(user_id, 'pushplus_token')),
        'pushplus_topic': _get_config(_user_config_key(user_id, 'pushplus_topic')),
        'bark_enabled': _is_enabled(_user_config_key(user_id, 'bark_enabled')),
        'bark_server_url': _get_config(_user_config_key(user_id, 'bark_server_url'), 'https://api.day.app'),
        'bark_device_key': _get_config(_user_config_key(user_id, 'bark_device_key')),
    }


def save_user_notification_config(user, form):
    user_id = getattr(user, 'id', None)
    if not user_id:
        return
    values = {
        'station_enabled': 'true' if 'station_enabled' in form else 'false',
        'webhook_enabled': 'true' if 'webhook_enabled' in form else 'false',
        'webhook_url': (form.get('webhook_url') or '').strip(),
        'telegram_enabled': 'true' if 'telegram_enabled' in form else 'false',
        'telegram_bot_token': (form.get('telegram_bot_token') or '').strip(),
        'telegram_chat_id': (form.get('telegram_chat_id') or '').strip(),
        'pushplus_enabled': 'true' if 'pushplus_enabled' in form else 'false',
        'pushplus_token': (form.get('pushplus_token') or '').strip(),
        'pushplus_topic': (form.get('pushplus_topic') or '').strip(),
        'bark_enabled': 'true' if 'bark_enabled' in form else 'false',
        'bark_server_url': (form.get('bark_server_url') or 'https://api.day.app').strip(),
        'bark_device_key': (form.get('bark_device_key') or '').strip(),
    }
    for key, value in values.items():
        SystemConfig.set_config(_user_config_key(user_id, key), value, f'User notification config {user_id}:{key}')


def _plain_text(value):
    return str(value or '').replace('<br>', '\n').replace('<br/>', '\n')


def _is_public_http_url(raw_url):
    try:
        parsed = urlparse(str(raw_url or '').strip())
        if parsed.scheme not in ('http', 'https') or not parsed.hostname:
            return False
        host = parsed.hostname.strip().lower()
        if host == 'localhost' or host.endswith('.localhost'):
            return False

        try:
            addresses = socket.getaddrinfo(host, None)
        except socket.gaierror:
            return False

        for item in addresses:
            ip = ipaddress.ip_address(item[4][0])
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_reserved
                or ip.is_multicast
                or ip.is_unspecified
            ):
                return False
        return True
    except Exception:
        return False


def has_email_config():
    return all([
        _get_config('smtp_server'),
        _get_config('smtp_username'),
        _get_config('smtp_password'),
    ])


def send_html_email(email, subject, html_body):
    smtp_server = _get_config('smtp_server')
    smtp_port = int(_get_config('smtp_port', '587') or '587')
    smtp_username = _get_config('smtp_username')
    smtp_password = _get_config('smtp_password')

    if not all([smtp_server, smtp_username, smtp_password]):
        raise Exception('邮件服务未配置，请联系管理员')

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = smtp_username
    msg['To'] = email
    msg.attach(MIMEText(html_body, 'html', 'utf-8'))

    server = smtplib.SMTP(smtp_server, smtp_port)
    server.starttls()
    server.login(smtp_username, smtp_password)
    server.send_message(msg)
    server.quit()


def _send_webhook(title, content, event_type='general', user=None, link_url=None, config=None):
    config = config or {}
    if not config.get('webhook_enabled'):
        return False
    webhook_url = (config.get('webhook_url') or '').strip()
    if not webhook_url:
        return False
    if not _is_public_http_url(webhook_url):
        raise ValueError('Webhook URL must be a public http(s) URL')

    text = _plain_text(content)
    payload = {
        'title': title,
        'content': text,
        'event_type': event_type,
        'link_url': link_url or '',
        'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }
    if user:
        payload['user'] = {'id': user.id, 'username': user.username, 'email': user.email}

    lower_url = webhook_url.lower()
    bot_text = f"{title}\n\n{text}"
    if link_url:
        bot_text = f"{bot_text}\n\n{link_url}"
    if 'qyapi.weixin.qq.com' in lower_url:
        payload = {'msgtype': 'text', 'text': {'content': bot_text}}
    elif 'open.feishu.cn' in lower_url:
        payload = {'msg_type': 'text', 'content': {'text': bot_text}}
    elif 'oapi.dingtalk.com' in lower_url:
        payload = {'msgtype': 'text', 'text': {'content': bot_text}}

    requests.post(webhook_url, json=payload, timeout=10).raise_for_status()
    return True


def _send_telegram(title, content, link_url=None, config=None):
    config = config or {}
    if not config.get('telegram_enabled'):
        return False
    token = (config.get('telegram_bot_token') or '').strip()
    chat_id = (config.get('telegram_chat_id') or '').strip()
    if not token or not chat_id:
        return False

    text = f"{title}\n\n{_plain_text(content)}"
    if link_url:
        text = f"{text}\n\n{link_url}"

    api_url = f"https://api.telegram.org/bot{token}/sendMessage"
    requests.post(api_url, json={'chat_id': chat_id, 'text': text}, timeout=10).raise_for_status()
    return True


def _send_pushplus(title, content, config=None):
    config = config or {}
    if not config.get('pushplus_enabled'):
        return False
    token = (config.get('pushplus_token') or '').strip()
    if not token:
        return False

    payload = {
        'token': token,
        'title': title,
        'content': content,
        'template': 'html',
    }
    topic = (config.get('pushplus_topic') or '').strip()
    if topic:
        payload['topic'] = topic

    requests.post('https://www.pushplus.plus/send', json=payload, timeout=10).raise_for_status()
    return True


def _send_bark(title, content, link_url=None, config=None):
    config = config or {}
    if not config.get('bark_enabled'):
        return False
    server_url = (config.get('bark_server_url') or 'https://api.day.app').strip().rstrip('/')
    device_key = (config.get('bark_device_key') or '').strip()
    if not server_url or not device_key:
        return False
    if not _is_public_http_url(server_url):
        raise ValueError('Bark server URL must be a public http(s) URL')

    payload = {'title': title, 'body': _plain_text(content)}
    if link_url:
        payload['url'] = link_url
    requests.post(f'{server_url}/{device_key}', json=payload, timeout=10).raise_for_status()
    return True


def create_station_notification(user, title, content, event_type='general', link_url=None, commit=True, preserve_html=False):
    if not user or not getattr(user, 'id', None):
        return None

    cleanup_expired_station_notifications(user.id, commit=False)

    record = UserNotification(
        user_id=user.id,
        event_type=event_type,
        title=title,
        content=str(content or '') if preserve_html else _plain_text(content),
        link_url=link_url or '',
    )
    db.session.add(record)
    if commit:
        db.session.commit()
    return record


def notify_user(user, title, content, html_content=None, event_type='general', link_url=None, email_subject=None):
    results = {}
    body = html_content or content
    user_config = get_user_notification_config(user)

    if user_config.get('station_enabled', True):
        try:
            station_uses_html = (
                bool(html_content)
                and event_type in {'prediction_generated', 'winning'}
                and 'prediction-summary-notice' in str(html_content)
            )
            create_station_notification(
                user,
                title,
                html_content if station_uses_html else content,
                event_type,
                link_url,
                preserve_html=station_uses_html,
            )
            results['station'] = True
        except Exception as exc:
            db.session.rollback()
            results['station'] = str(exc)

    if user and user.email and _is_enabled('notify_email_enabled', 'true'):
        try:
            send_html_email(user.email, email_subject or title, html_content or content)
            results['email'] = True
        except Exception as exc:
            results['email'] = str(exc)

    for channel, sender in (
        ('webhook', lambda: _send_webhook(title, content, event_type, user, link_url, user_config)),
        ('telegram', lambda: _send_telegram(title, content, link_url, user_config)),
        ('pushplus', lambda: _send_pushplus(title, body, user_config)),
        ('bark', lambda: _send_bark(title, content, link_url, user_config)),
    ):
        try:
            results[channel] = sender()
        except Exception as exc:
            results[channel] = str(exc)

    return results


def notify_admins(title, content, html_content=None, event_type='admin', link_url=None, email_recipients=None):
    admin_users = User.query.filter(User.is_admin.is_(True)).all()
    sent_to = set()
    results = {}

    try:
        station_users = [admin for admin in admin_users if get_user_notification_config(admin).get('station_enabled', True)]
    except Exception:
        station_users = admin_users

    if station_users:
        try:
            for admin in station_users:
                create_station_notification(admin, title, content, event_type, link_url, commit=False)
            db.session.commit()
            results['station'] = True
        except Exception as exc:
            db.session.rollback()
            results['station'] = str(exc)

    if _is_enabled('notify_email_enabled', 'true'):
        recipients = list(email_recipients or [])
        if not recipients:
            recipients = [admin.email for admin in admin_users if admin.email]
        email_results = []
        for email in recipients:
            if not email or email in sent_to:
                continue
            sent_to.add(email)
            try:
                send_html_email(email, title, html_content or content)
                email_results.append({'email': email, 'ok': True})
            except Exception as exc:
                email_results.append({'email': email, 'ok': False, 'error': str(exc)})
        results['email'] = email_results

    external_results = []
    for admin in admin_users:
        admin_config = get_user_notification_config(admin)
        for channel, sender in (
            ('webhook', lambda: _send_webhook(title, content, event_type, admin, link_url, admin_config)),
            ('telegram', lambda: _send_telegram(title, content, link_url, admin_config)),
            ('pushplus', lambda: _send_pushplus(title, html_content or content, admin_config)),
            ('bark', lambda: _send_bark(title, content, link_url, admin_config)),
        ):
            try:
                external_results.append({'user_id': admin.id, 'channel': channel, 'ok': sender()})
            except Exception as exc:
                external_results.append({'user_id': admin.id, 'channel': channel, 'ok': False, 'error': str(exc)})
    results['external'] = external_results

    return results
