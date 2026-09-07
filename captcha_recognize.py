"""
乐校通验证码 CNN 识别模块

功能:
- 蓝色干扰线去除
- 5位数字字符分割
- CNN 模型推理 (PyTorch)
- 端到端登录（含自动重试）

ESP32 部署: 导出的 captcha_cnn_weights.h 可直接 #include 使用
"""

import base64
import hashlib
import io
import json
import os
import time

import numpy as np
import requests
from PIL import Image
from scipy import ndimage

CHAR_W, CHAR_H = 8, 12
BASE_URL = "https://v3-prod-beta-app.lxt6.cn"
SIGN_KEY = "1FF75E512"


# ============================================================
#  图像预处理
# ============================================================
def remove_blue_line(img_rgb):
    """去除蓝色干扰线 (B > R+50 && B > G+50 → 白色)"""
    arr = np.array(img_rgb).astype(int)
    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    blue_mask = (b > r + 50) & (b > g + 50)
    arr[blue_mask] = [255, 255, 255]
    return Image.fromarray(arr.astype(np.uint8))


def segment_captcha(img_bytes):
    """
    分割验证码为 5 个字符图像

    流程: 去蓝线 → 灰度 → 二值化 → 列投影 → 合并 → 归一化到 12x8
    返回: 5 个 12x8 float32 数组，或 None（分割失败）
    """
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    img = remove_blue_line(img)
    img = img.convert("L")
    arr = np.array(img)
    binary = (arr < 128).astype(np.uint8)

    # 去小噪点
    labeled, num = ndimage.label(binary)
    for i in range(1, num + 1):
        if np.sum(labeled == i) < 5:
            binary[labeled == i] = 0

    # 列投影找字符边界
    col_sum = np.sum(binary, axis=0)
    in_char = False
    char_ranges = []
    start = 0
    for x in range(len(col_sum)):
        if col_sum[x] >= 2 and not in_char:
            start = x
            in_char = True
        elif col_sum[x] < 2 and in_char:
            if x - start > 2:
                char_ranges.append((start, x))
            in_char = False
    if in_char and len(col_sum) - start > 2:
        char_ranges.append((start, len(col_sum)))

    # 合并太近的区域
    merged = []
    for r in char_ranges:
        if merged and r[0] - merged[-1][1] < 3:
            merged[-1] = (merged[-1][0], r[1])
        else:
            merged.append(r)

    if len(merged) != 5:
        return None

    # 提取并归一化每个字符
    h, w = binary.shape
    chars = []
    for x0, x1 in merged:
        char_slice = binary[:, x0:x1]
        row_sum = np.sum(char_slice, axis=1)
        rows = np.where(row_sum > 0)[0]
        if len(rows) == 0:
            return None
        y0, y1 = max(0, rows[0] - 1), min(h, rows[-1] + 2)
        x0, x1 = max(0, x0 - 1), min(w, x1 + 1)
        char_img = binary[y0:y1, x0:x1]
        pil = Image.fromarray((1 - char_img) * 255)
        pil = pil.resize((CHAR_W, CHAR_H), Image.LANCZOS)
        norm = (np.array(pil) > 128).astype(np.float32)
        chars.append(norm)

    return chars


# ============================================================
#  PyTorch CNN 模型
# ============================================================
def get_model():
    """获取 CNN 模型（延迟导入 torch）"""
    import torch
    import torch.nn as nn

    class CaptchaCNN(nn.Module):
        def __init__(self):
            super().__init__()
            self.features = nn.Sequential(
                nn.Conv2d(1, 16, 3, padding=1),
                nn.BatchNorm2d(16),
                nn.ReLU(),
                nn.MaxPool2d(2),
                nn.Conv2d(16, 32, 3, padding=1),
                nn.BatchNorm2d(32),
                nn.ReLU(),
                nn.MaxPool2d(2),
            )
            self.classifier = nn.Sequential(
                nn.Flatten(),
                nn.Linear(32 * 3 * 2, 64),
                nn.ReLU(),
                nn.Dropout(0.3),
                nn.Linear(64, 32),
                nn.ReLU(),
                nn.Linear(32, 10),
            )

        def forward(self, x):
            return self.classifier(self.features(x))

    return CaptchaCNN()


def predict_captcha(model, img_bytes):
    """
    用 CNN 模型预测验证码

    返回: (预测结果字符串, 平均置信度) 或 (None, 0)
    """
    import torch

    chars = segment_captcha(img_bytes)
    if chars is None:
        return None, 0

    inp = torch.from_numpy(np.array(chars).reshape(5, 1, CHAR_H, CHAR_W))
    with torch.no_grad():
        probs = torch.softmax(model(inp), dim=1)
        preds = probs.argmax(1).numpy()
        confs = probs.max(1).values.numpy()

    result = "".join(str(d) for d in preds)
    return result, float(confs.mean())


# ============================================================
#  协议工具
# ============================================================
def generate_ghost():
    """X-Ghost 生成算法"""
    ts_hex = hex(int(time.time() * 1000))[2:]
    s = ts_hex.zfill(12) if len(ts_hex) < 12 else ts_hex
    total = sum(int(s[i:i + 2], 16) for i in range(0, len(s), 2))
    sc = format(total % 256, "02X")
    parts = [s[i * 2:i * 2 + 2] for i in range(len(s) // 2)]
    xr = int(parts[0], 16)
    for i in range(1, len(parts)):
        xr ^= int(parts[i], 16)
    return (sc + s + format(xr, "02X")).lower()


def base_headers():
    return {
        "X-Sid": "2021011300001",
        "X-Product-Ver": "4.3.9",
        "X-clientType": "1",
        "X-Ghost": generate_ghost(),
        "User-Agent": "okhttp/3.12.1",
    }


def get_captcha(session, phone):
    """获取验证码图片"""
    sig = hashlib.md5((SIGN_KEY + f"account={phone}").encode()).hexdigest()
    r = session.get(
        f"{BASE_URL}/user/authentication/getCode",
        params={"account": phone},
        headers={**base_headers(), "X-Sign": sig},
    )
    data = r.json()
    if data.get("Code") != 0:
        return None
    return base64.b64decode(data["Data"].split(",")[1])


def try_login(session, phone, password, captcha):
    """尝试登录，返回 (成功, token或错误信息)"""
    body = json.dumps(
        {"studentMobile": phone, "loginPassword": password, "code": captcha},
        separators=(",", ":"),
    )
    enc = base64.b64encode(body.encode()).decode()
    payload = json.dumps({"data": enc}, separators=(",", ":"))
    sig = hashlib.md5((SIGN_KEY + payload).encode()).hexdigest()

    resp = session.post(
        f"{BASE_URL}/user/login/userLoginV2WithEncrypt",
        headers={
            **base_headers(),
            "Content-Type": "application/json",
            "X-Sign": sig,
        },
        data=payload,
    )

    # 检查 token
    token = resp.headers.get("TokenInfo") or resp.headers.get("tokenInfo")
    if not token:
        for c in resp.cookies:
            if c.name == "tokenInfo":
                token = c.value
                break
    if token:
        if not token.startswith("tokenInfo="):
            token = "tokenInfo=" + token
        return True, token

    try:
        result = resp.json()
        return False, result.get("Message", "unknown")
    except Exception:
        return False, "unknown"


# ============================================================
#  自动登录（含 CNN 验证码识别 + 无限重试）
# ============================================================
def auto_login(phone, password, model_path="captcha_cnn_best.pth"):
    """
    自动登录乐校通

    流程:
    1. 获取验证码
    2. CNN 模型识别（去蓝线 + 字符分割 + 推理）
    3. 提交登录
    4. 失败则重新获取验证码，无限重试直到成功

    Args:
        phone: 手机号
        password: 密码
        model_path: CNN 模型权重路径

    Returns:
        token 字符串，或 None（被锁定等异常）
    """
    import torch

    model = get_model()
    model.load_state_dict(torch.load(model_path, map_location="cpu"))
    model.eval()

    session = requests.Session()
    session.trust_env = False
    attempt = 0

    while True:
        attempt += 1

        # 获取验证码
        img_bytes = get_captcha(session, phone)
        if img_bytes is None:
            print(f"  [{attempt}] 获取验证码失败，重试...", flush=True)
            time.sleep(1)
            continue

        # CNN 预测
        captcha, conf = predict_captcha(model, img_bytes)
        if captcha is None:
            print(f"  [{attempt}] 分割失败，重试...", flush=True)
            time.sleep(0.5)
            continue

        print(f"  [{attempt}] 预测: {captcha} (置信度: {conf:.3f})", flush=True)

        # 尝试登录
        success, result = try_login(session, phone, password, captcha)
        if success:
            print(f"  登录成功！(第 {attempt} 次)", flush=True)
            return result

        # 被锁定则等待
        if "锁定" in str(result):
            print(f"  账号被锁定，等待 60 秒...", flush=True)
            time.sleep(60)
        else:
            print(f"    {result}", flush=True)
            time.sleep(0.5)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("用法: python captcha_recognize.py <手机号> <密码>")
        print("  首次运行需要 captcha_cnn_best.pth 模型文件")
        print("  训练模型: python train_captcha_cnn.py")
        sys.exit(1)

    phone = sys.argv[1]
    password = sys.argv[2]
    token = auto_login(phone, password)
    if token:
        token_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".lexiaotong_token")
        with open(token_file, "w") as f:
            f.write(token)
        print(f"  Token 已保存到 {token_file}")
