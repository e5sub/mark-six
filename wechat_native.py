import base64
import io
import json
import secrets
import time
from datetime import datetime
from urllib.parse import urlparse

import qrcode
import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class WechatPayConfigError(Exception):
    pass


def _normalize_pem_key(raw_key, key_type):
    text = (raw_key or "").strip()
    if not text:
        return ""
    if "BEGIN" in text:
        return text
    body = "".join(text.split())
    line_length = 64
    lines = [body[i:i + line_length] for i in range(0, len(body), line_length)]
    if key_type == "private":
        header = "-----BEGIN PRIVATE KEY-----"
        footer = "-----END PRIVATE KEY-----"
    else:
        header = "-----BEGIN PUBLIC KEY-----"
        footer = "-----END PUBLIC KEY-----"
    return "\n".join([header] + lines + [footer])


def build_qr_data_uri(content):
    qr = qrcode.QRCode(border=2, box_size=8)
    qr.add_data(content)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def build_client(config):
    gateway = (config.get("gateway") or "https://api.mch.weixin.qq.com").strip().rstrip("/")
    mchid = (config.get("mchid") or "").strip()
    appid = (config.get("appid") or "").strip()
    private_key = (config.get("private_key") or "").strip()
    serial_no = (config.get("serial_no") or "").strip()
    api_v3_key = (config.get("api_v3_key") or "").strip()
    platform_public_key = (config.get("platform_public_key") or "").strip()
    if not all([gateway, mchid, appid, private_key, serial_no, api_v3_key, platform_public_key]):
        raise WechatPayConfigError("微信支付配置不完整，请先填写 mchid、appid、商户私钥、商户证书序列号、APIv3 密钥、平台公钥。")
    return {
        "gateway": gateway,
        "mchid": mchid,
        "appid": appid,
        "private_key": private_key,
        "serial_no": serial_no,
        "api_v3_key": api_v3_key,
        "platform_public_key": platform_public_key,
        "platform_public_key_id": (config.get("platform_public_key_id") or "").strip(),
    }


def _sign_message(message, private_key_text):
    private_key = serialization.load_pem_private_key(
        _normalize_pem_key(private_key_text, "private").encode("utf-8"),
        password=None,
    )
    signature = private_key.sign(message.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(signature).decode("utf-8")


def _verify_signature(message, signature, public_key_text):
    public_key = serialization.load_pem_public_key(_normalize_pem_key(public_key_text, "public").encode("utf-8"))
    public_key.verify(
        base64.b64decode(signature),
        message.encode("utf-8"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    return True


def _authorization_header(client, method, canonical_url, body_text=""):
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(16)
    message = f"{method}\n{canonical_url}\n{timestamp}\n{nonce}\n{body_text}\n"
    signature = _sign_message(message, client["private_key"])
    return (
        'WECHATPAY2-SHA256-RSA2048 '
        f'mchid="{client["mchid"]}",'
        f'nonce_str="{nonce}",'
        f'timestamp="{timestamp}",'
        f'serial_no="{client["serial_no"]}",'
        f'signature="{signature}"'
    )


def _request_headers(client, method, canonical_url, body_text=""):
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": _authorization_header(client, method, canonical_url, body_text),
    }


def native_prepay(client, order_no, amount_fen, description, notify_url):
    canonical_url = "/v3/pay/transactions/native"
    body = {
        "appid": client["appid"],
        "mchid": client["mchid"],
        "description": description,
        "out_trade_no": order_no,
        "notify_url": notify_url,
        "amount": {
            "total": int(amount_fen),
            "currency": "CNY",
        },
    }
    body_text = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    response = requests.post(
        f'{client["gateway"]}{canonical_url}',
        data=body_text.encode("utf-8"),
        headers=_request_headers(client, "POST", canonical_url, body_text),
        timeout=20,
    )
    response.raise_for_status()
    return body, response.json()


def query_by_out_trade_no(client, order_no):
    canonical_url = f'/v3/pay/transactions/out-trade-no/{order_no}?mchid={client["mchid"]}'
    response = requests.get(
        f'{client["gateway"]}{canonical_url}',
        headers=_request_headers(client, "GET", canonical_url, ""),
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def verify_callback_headers(client, timestamp, nonce, body_text, signature, serial):
    expected_serial = client.get("platform_public_key_id") or ""
    if expected_serial and serial and expected_serial != serial:
        raise ValueError("微信支付平台公钥ID不匹配")
    message = f"{timestamp}\n{nonce}\n{body_text}\n"
    return _verify_signature(message, signature, client["platform_public_key"])


def decrypt_callback_resource(api_v3_key, resource):
    nonce = (resource or {}).get("nonce") or ""
    associated_data = (resource or {}).get("associated_data") or ""
    ciphertext = (resource or {}).get("ciphertext") or ""
    aesgcm = AESGCM(api_v3_key.encode("utf-8"))
    plaintext = aesgcm.decrypt(
        nonce.encode("utf-8"),
        base64.b64decode(ciphertext),
        associated_data.encode("utf-8"),
    )
    return json.loads(plaintext.decode("utf-8"))


def build_payment_page_payload(code_url):
    return {
        "qr_code": code_url,
        "qr_image_data_uri": build_qr_data_uri(code_url),
        "qr_link": code_url,
    }
