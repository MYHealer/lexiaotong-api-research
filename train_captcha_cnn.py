"""
乐校通验证码 CNN 训练脚本

流程:
1. 下载验证码图片
2. ddddocr 标注
3. 去蓝线预处理 + 字符分割
4. CNN 模型训练（自训练迭代提升标签质量）
5. 导出 C 权重供 ESP32 使用
"""

import base64
import hashlib
import io
import os
import shutil
import time

import numpy as np
import requests
from PIL import Image, ImageFilter
from scipy import ndimage

import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, TensorDataset

CHAR_W, CHAR_H = 8, 12
CAPTCHA_DIR = "captchas"
BASE_URL = "https://v3-prod-beta-app.lxt6.cn"
SIGN_KEY = "1FF75E512"


# ============================================================
#  图像预处理
# ============================================================
def remove_blue_line(img_rgb):
    arr = np.array(img_rgb).astype(int)
    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    blue_mask = (b > r + 50) & (b > g + 50)
    arr[blue_mask] = [255, 255, 255]
    return Image.fromarray(arr.astype(np.uint8))


def segment_captcha(img_path):
    img = Image.open(img_path).convert("RGB")
    img = remove_blue_line(img)
    img = img.convert("L")
    arr = np.array(img)
    binary = (arr < 128).astype(np.uint8)

    labeled, num = ndimage.label(binary)
    for i in range(1, num + 1):
        if np.sum(labeled == i) < 5:
            binary[labeled == i] = 0

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

    merged = []
    for r in char_ranges:
        if merged and r[0] - merged[-1][1] < 3:
            merged[-1] = (merged[-1][0], r[1])
        else:
            merged.append(r)

    if len(merged) != 5:
        return None

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
#  下载 + 标注
# ============================================================
def generate_ghost():
    ts_hex = hex(int(time.time() * 1000))[2:]
    s = ts_hex.zfill(12) if len(ts_hex) < 12 else ts_hex
    total = sum(int(s[i:i + 2], 16) for i in range(0, len(s), 2))
    sc = format(total % 256, "02X")
    parts = [s[i * 2:i * 2 + 2] for i in range(len(s) // 2)]
    xr = int(parts[0], 16)
    for i in range(1, len(parts)):
        xr ^= int(parts[i], 16)
    return (sc + s + format(xr, "02X")).lower()


def download_captchas(count=500, phone="<your_phone>"):
    os.makedirs(CAPTCHA_DIR, exist_ok=True)
    session = requests.Session()
    session.trust_env = False
    downloaded = len([f for f in os.listdir(CAPTCHA_DIR) if f.endswith(".png")])

    for i in range(count):
        try:
            hdr = {
                "X-Sid": "2021011300001", "X-Product-Ver": "4.3.9",
                "X-clientType": "1", "X-Ghost": generate_ghost(),
                "User-Agent": "okhttp/3.12.1",
            }
            sig = hashlib.md5((SIGN_KEY + f"account={phone}").encode()).hexdigest()
            r = session.get(
                f"{BASE_URL}/user/authentication/getCode",
                params={"account": phone},
                headers={**hdr, "X-Sign": sig},
                timeout=10,
            )
            data = r.json()
            if data.get("Code") == 0:
                img_bytes = base64.b64decode(data["Data"].split(",")[1])
                path = os.path.join(CAPTCHA_DIR, f"cap_{downloaded:05d}.png")
                with open(path, "wb") as f:
                    f.write(img_bytes)
                downloaded += 1
            time.sleep(0.2)
        except Exception:
            pass

    return downloaded


def label_with_ddddocr():
    import ddddocr
    ocr = ddddocr.DdddOcr(show_ad=False)
    labels = {}
    for fname in sorted(os.listdir(CAPTCHA_DIR)):
        if not fname.endswith(".png"):
            continue
        path = os.path.join(CAPTCHA_DIR, fname)
        with open(path, "rb") as f:
            result = ocr.classification(f.read())
        if len(result) == 5 and result.isdigit():
            labels[fname] = result
    return labels


def build_dataset(labels):
    X, y = [], []
    for fname, label in labels.items():
        chars = segment_captcha(os.path.join(CAPTCHA_DIR, fname))
        if chars is None:
            continue
        for i, char_img in enumerate(chars):
            X.append(char_img.reshape(1, CHAR_H, CHAR_W))
            y.append(int(label[i]))
    if X:
        return np.array(X, dtype=np.float32), np.array(y, dtype=np.int64)
    return np.zeros((0, 1, CHAR_H, CHAR_W), np.float32), np.zeros(0, np.int64)


# ============================================================
#  CNN 模型
# ============================================================
class CaptchaCNN(nn.Module):
    def __init__(self):
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv2d(1, 16, 3, padding=1), nn.BatchNorm2d(16), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(16, 32, 3, padding=1), nn.BatchNorm2d(32), nn.ReLU(), nn.MaxPool2d(2),
        )
        self.classifier = nn.Sequential(
            nn.Flatten(), nn.Linear(32 * 3 * 2, 64), nn.ReLU(), nn.Dropout(0.3),
            nn.Linear(64, 32), nn.ReLU(), nn.Linear(32, 10),
        )

    def forward(self, x):
        return self.classifier(self.features(x))


# ============================================================
#  数据增强
# ============================================================
def augment_batch(X, y):
    X_aug, y_aug = [], []
    for i in range(len(X)):
        pil = Image.fromarray((X[i, 0] * 255).astype(np.uint8))
        results = [X[i, 0]]
        for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            shifted = pil.transform(pil.size, Image.AFFINE, (1, 0, dx, 0, 1, dy), fillcolor=0)
            results.append((np.array(shifted) > 128).astype(np.float32))
        for scale in [0.9, 1.1]:
            w, h = pil.size
            nw, nh = int(w * scale), int(h * scale)
            scaled = pil.resize((nw, nh), Image.LANCZOS)
            canvas = Image.new("L", (w, h), 0)
            canvas.paste(scaled, ((w - nw) // 2, (h - nh) // 2))
            results.append((np.array(canvas) > 128).astype(np.float32))
        blurred = pil.filter(ImageFilter.GaussianBlur(radius=0.5))
        results.append((np.array(blurred) > 128).astype(np.float32))
        for img in results:
            X_aug.append(img.reshape(1, CHAR_H, CHAR_W))
            y_aug.append(y[i])
    return np.array(X_aug, dtype=np.float32), np.array(y_aug, dtype=np.int64)


# ============================================================
#  训练
# ============================================================
def train_model(X, y, model=None, epochs=30, lr=0.001):
    X_aug, y_aug = augment_batch(X, y)
    n = len(X_aug)
    perm = np.random.permutation(n)
    split = int(n * 0.9)

    train_ds = TensorDataset(torch.from_numpy(X_aug[perm[:split]]),
                             torch.from_numpy(y_aug[perm[:split]]))
    val_ds = TensorDataset(torch.from_numpy(X_aug[perm[split:]]),
                           torch.from_numpy(y_aug[perm[split:]]))
    train_dl = DataLoader(train_ds, batch_size=128, shuffle=True)
    val_dl = DataLoader(val_ds, batch_size=128)

    if model is None:
        model = CaptchaCNN()
    criterion = nn.CrossEntropyLoss()
    optimizer = optim.Adam(model.parameters(), lr=lr, weight_decay=1e-4)
    scheduler = optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)

    best_val_acc = 0
    best_state = None
    for epoch in range(epochs):
        model.train()
        for xb, yb in train_dl:
            out = model(xb)
            loss = criterion(out, yb)
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
        model.eval()
        vc, vt = 0, 0
        with torch.no_grad():
            for xb, yb in val_dl:
                vc += (model(xb).argmax(1) == yb).sum().item()
                vt += len(yb)
        val_acc = vc / vt if vt > 0 else 0
        if val_acc > best_val_acc:
            best_val_acc = val_acc
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
        scheduler.step()

    if best_state:
        model.load_state_dict(best_state)
    return model, best_val_acc


def clean_captchas():
    if os.path.exists(CAPTCHA_DIR):
        shutil.rmtree(CAPTCHA_DIR)


# ============================================================
#  Main
# ============================================================
if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "download":
        count = int(sys.argv[2]) if len(sys.argv) > 2 else 500
        print(f"下载 {count} 张验证码...", flush=True)
        n = download_captchas(count)
        print(f"完成: {n} 张", flush=True)

    elif len(sys.argv) > 1 and sys.argv[1] == "train":
        total_chars = int(sys.argv[2]) if len(sys.argv) > 2 else 5000
        chunk_size = 500
        all_X = np.zeros((0, 1, CHAR_H, CHAR_W), dtype=np.float32)
        all_y = np.zeros(0, dtype=np.int64)
        model = None
        best_acc = 0

        print(f"增量训练: 目标 {total_chars} 字符", flush=True)

        while len(all_X) < total_chars:
            print(f"\n下载 {chunk_size} 张...", flush=True)
            download_captchas(chunk_size)

            print("标注...", flush=True)
            labels = label_with_ddddocr()
            X_new, y_new = build_dataset(labels)
            print(f"  {len(X_new)} 字符", flush=True)

            clean_captchas()

            if len(X_new) > 0:
                all_X = np.concatenate([all_X, X_new])
                all_y = np.concatenate([all_y, y_new])

            print(f"训练... (总计 {len(all_X)} 字符)", flush=True)
            model, val_acc = train_model(all_X, all_y, model=model, epochs=20, lr=0.0005)
            print(f"  val_acc={val_acc:.4f}", flush=True)

            if val_acc > best_acc:
                best_acc = val_acc
                torch.save(model.state_dict(), "captcha_cnn_best.pth")
                print(f"  >>> 新最佳 {best_acc:.4f}", flush=True)

        print(f"\n完成！最佳: {best_acc:.4f}", flush=True)
        print(f"模型: captcha_cnn_best.pth", flush=True)

    else:
        print("用法:")
        print("  python train_captcha_cnn.py download [count]  # 下载验证码")
        print("  python train_captcha_cnn.py train [chars]     # 训练模型")
        print()
        print("训练完成后用 captcha_recognize.py 登录:")
        print("  python captcha_recognize.py <手机号> <密码>")
