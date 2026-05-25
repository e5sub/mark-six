import base64
import io
import json
import uuid
from datetime import datetime
from decimal import Decimal
from urllib.parse import quote_plus

import qrcode
import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


class AlipayConfigError(Exception):
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


def _sign_content(params):
    items = []
    for key in sorted(params.keys()):
        value = params[key]
        if key == "sign" or value is None or value == "":
            continue
        items.append(f"{key}={value}")
    return "&".join(items)


def sign_rsa2(params, app_private_key):
    content = _sign_content(params)
    pem = _normalize_pem_key(app_private_key, "private")
    private_key = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
    signature = private_key.sign(
        content.encode("utf-8"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("utf-8")


def verify_rsa2(params, sign, alipay_public_key):
    verify_params = {
        key: value
        for key, value in params.items()
        if key not in {"sign", "sign_type"} and value not in (None, "")
    }
    content = _sign_content(verify_params)
    pem = _normalize_pem_key(alipay_public_key, "public")
    public_key = serialization.load_pem_public_key(pem.encode("utf-8"))
    public_key.verify(
        base64.b64decode(sign),
        content.encode("utf-8"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    return True


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
    gateway = (config.get("gateway") or "https://openapi.alipay.com/gateway.do").strip()
    app_id = (config.get("app_id") or "").strip()
    private_key = (config.get("app_private_key") or "").strip()
    public_key = (config.get("alipay_public_key") or "").strip()
    if not all([gateway, app_id, private_key, public_key]):
        raise AlipayConfigError("支付宝当面付配置不完整，请先在后台填写 app_id、应用私钥、支付宝公钥。")
    return {
        "gateway": gateway,
        "app_id": app_id,
        "app_private_key": private_key,
        "alipay_public_key": public_key,
    }


def _build_common_params(client, method, biz_content, notify_url=None):
    params = {
        "app_id": client["app_id"],
        "method": method,
        "format": "JSON",
        "charset": "utf-8",
        "sign_type": "RSA2",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "version": "1.0",
        "biz_content": json.dumps(biz_content, ensure_ascii=False, separators=(",", ":")),
    }
    if notify_url:
        params["notify_url"] = notify_url
    params["sign"] = sign_rsa2(params, client["app_private_key"])
    return params


def precreate_trade(client, order_no, amount, subject, notify_url, timeout_express="15m"):
    amount_decimal = Decimal(str(amount)).quantize(Decimal("0.01"))
    biz_content = {
        "out_trade_no": order_no,
        "total_amount": f"{amount_decimal:.2f}",
        "subject": subject,
        "timeout_express": timeout_express,
    }
    params = _build_common_params(
        client=client,
        method="alipay.trade.precreate",
        biz_content=biz_content,
        notify_url=notify_url,
    )
    response = requests.post(client["gateway"], data=params, timeout=20)
    response.raise_for_status()
    payload = response.json()
    result = payload.get("alipay_trade_precreate_response") or {}
    return payload, result


def query_trade(client, order_no):
    biz_content = {"out_trade_no": order_no}
    params = _build_common_params(
        client=client,
        method="alipay.trade.query",
        biz_content=biz_content,
    )
    response = requests.post(client["gateway"], data=params, timeout=20)
    response.raise_for_status()
    payload = response.json()
    result = payload.get("alipay_trade_query_response") or {}
    return payload, result


def build_payment_page_payload(qr_code):
    return {
        "qr_code": qr_code,
        "qr_image_data_uri": build_qr_data_uri(qr_code),
        "qr_link": f"alipays://platformapi/startapp?saId=10000007&qrcode={quote_plus(qr_code)}",
    }
