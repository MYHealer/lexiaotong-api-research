#!/usr/bin/env python3
"""
乐校通 API 工具 — 水费查询 / 设备发现 / 开阀控制

用法:
    python lexiaotong.py query          # 查询预付费水表余额
    python lexiaotong.py find           # 遍历区域查找洗澡设备
    python lexiaotong.py open           # 4G 开阀（需有余额）
    python lexiaotong.py find --building 28 --room 405  # 查找指定楼栋房间
"""

import argparse
import base64
import hashlib
import json
import os
import random
import sys
import time

import requests

# ============================================================
#  配置 — 首次使用前填写你的学校信息
# ============================================================
BASE_URL = "https://v3-prod-beta-app.lxt6.cn"
SIGN_KEY = "1FF75E512"
SCHOOL_ID = "<your_school_id>"        # 学校 ID，登录后从 APP 抓包获取
INVESTOR_ID = "<your_investor_id>"    # 投资人 ID

TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".lexiaotong_token")


# ============================================================
#  协议工具
# ============================================================
def md5(s):
    return hashlib.md5(s.encode()).hexdigest()


def sign(params):
    return md5(SIGN_KEY + params)


def _pad(s, length):
    return s if len(s) >= length else "0" * (length - len(s)) + s


def _sum_check(s):
    if len(s) % 2 != 0:
        return "00"
    total = sum(int(s[i:i + 2], 16) for i in range(0, len(s), 2))
    r = format(total % 256, "02X")
    return "0" + r if len(r) == 1 else r


def _xor(s):
    parts = [s[i * 2:i * 2 + 2] for i in range(len(s) // 2)]
    v = int(parts[0], 16)
    for i in range(1, len(parts)):
        v ^= int(parts[i], 16)
    return format(v, "02X")


def gen_ghost():
    """X-Ghost 生成算法 (逆向自 AppUtils.getGhost())"""
    ts = hex(int(time.time() * 1000))[2:]
    ls = _pad(ts, 12)
    return (_sum_check(ls) + ls + _xor(ls)).lower()


def gson_dumps(obj):
    """模拟 Gson serializeNulls 默认格式"""
    compact = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
    result, in_str, esc = [], False, False
    for ch in compact:
        if esc:
            result.append(ch)
            esc = False
            continue
        if ch == "\\":
            result.append(ch)
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            result.append(ch)
            continue
        if not in_str:
            if ch == ":":
                result.append(": ")
            elif ch == ",":
                result.append(", ")
            else:
                result.append(ch)
        else:
            result.append(ch)
    return "".join(result)


def base_headers():
    return {
        "X-Sid": "2021011300001",
        "X-Product-Ver": "4.3.9",
        "X-clientType": "1",
        "X-Ghost": gen_ghost(),
        "User-Agent": "okhttp/3.12.1",
    }


def api_headers(token=None, body_str=None, params_str=None):
    h = base_headers()
    if body_str:
        h["Content-Type"] = "application/json"
        h["X-Sign"] = sign(body_str)
    elif params_str:
        h["Content-Type"] = "application/x-www-form-urlencoded"
        h["X-Sign"] = sign(params_str)
    else:
        h["X-Sign"] = sign("")
    if token:
        h["Cookie"] = token
    return h


# ============================================================
#  登录
# ============================================================
def login(phone, password):
    """自动登录（优先用 CNN 模型，fallback 到 ddddocr）"""
    # 尝试用 CNN 模型
    model_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "captcha_cnn_best.pth")
    if os.path.exists(model_path):
        try:
            from captcha_recognize import auto_login
            return auto_login(phone, password, model_path)
        except Exception as e:
            print(f"  CNN 模型加载失败: {e}, 回退到 ddddocr")

    # fallback: ddddocr
    import ddddocr
    ocr = ddddocr.DdddOcr(show_ad=False)

    for _ in range(15):
        sig = sign(f"account={phone}")
        resp = requests.get(
            f"{BASE_URL}/user/authentication/getCode",
            params={"account": phone},
            headers={**base_headers(), "X-Sign": sig},
        )
        r = resp.json()
        if r.get("Code") != 0:
            time.sleep(1)
            continue

        img = base64.b64decode(r["Data"].split(",")[1])
        captcha = ocr.classification(img)

        body = json.dumps(
            {"studentMobile": phone, "loginPassword": password, "code": captcha},
            separators=(",", ":"),
        )
        enc = base64.b64encode(body.encode()).decode()
        payload = json.dumps({"data": enc}, separators=(",", ":"))

        resp2 = requests.post(
            f"{BASE_URL}/user/login/userLoginV2WithEncrypt",
            headers={**base_headers(), "Content-Type": "application/json",
                     "X-Sign": sign(payload)},
            data=payload,
        )

        token = resp2.headers.get("TokenInfo") or resp2.headers.get("tokenInfo")
        if not token:
            for c in resp2.cookies:
                if c.name == "tokenInfo":
                    token = c.value
                    break

        if token:
            if not token.startswith("tokenInfo="):
                token = "tokenInfo=" + token
            with open(TOKEN_FILE, "w") as f:
                f.write(token)
            print(f"  登录成功 (验证码: {captcha})")
            return token

        time.sleep(1)

    print("  登录失败")
    return None


def get_token(phone, password):
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE) as f:
            t = f.read().strip()
        if t:
            return t
    print("\n  正在登录...")
    return login(phone, password)


# ============================================================
#  查询: 预付费水表余额
# ============================================================
def query_balance(token, wallet_key):
    """查询预付费水表余额"""
    params = f"walletKey={wallet_key}&schoolId={SCHOOL_ID}&investorId={INVESTOR_ID}"
    h = api_headers(token=token, params_str=params)
    resp = requests.get(f"{BASE_URL}/paymentV1/app/wallet/find?{params}", headers=h)
    return resp.json()


# ============================================================
#  设备发现: 遍历区域层级
# ============================================================
def get_lower_areas(token, area_id):
    """获取下级区域列表"""
    params = f"areaId={area_id}"
    h = api_headers(token=token, params_str=params)
    resp = requests.get(f"{BASE_URL}/baseDict/site/getLowerAreas?{params}", headers=h)
    return resp.json()


def get_rooms(token, area_id):
    """获取房间列表（最底层区域）"""
    params = f"areaId={area_id}"
    h = api_headers(token=token, params_str=params)
    resp = requests.get(f"{BASE_URL}/baseDict/site/getDormitoryOrPublicRoom?{params}", headers=h)
    return resp.json()


def get_devices_at_site(token, site_id, site_flag, type_id=1):
    """获取指定站点的设备列表"""
    body = json.dumps({"siteId": site_id, "siteFlag": site_flag, "typeId": type_id},
                      separators=(",", ":"))
    h = api_headers(token=token, body_str=body)
    resp = requests.post(f"{BASE_URL}/mgapp/machine/getMachineByLocation", data=body, headers=h)
    return resp.json()


def find_devices(token, building=None, room=None, type_id=1, verbose=True):
    """
    递归遍历区域层级查找设备

    Args:
        token: 登录 token
        building: 楼栋名称过滤 (如 "28栋")
        room: 房间名称过滤 (如 "405")
        type_id: 设备类型 (1=洗澡阀, 18=预付费水表)
        verbose: 是否打印过程
    """
    # L0: 学校顶层区域
    r0 = get_lower_areas(token, SCHOOL_ID)
    if r0.get("Code") != 0:
        print(f"  获取区域失败: {r0.get('Message')}")
        return []

    found = []
    for l0 in r0.get("Data", []):
        if verbose:
            print(f"  [L0] {l0['name']}")

        # L1: 楼栋
        r1 = get_lower_areas(token, l0["id"])
        for l1 in r1.get("Data", []):
            if building and building not in l1.get("name", ""):
                continue
            if verbose:
                print(f"    [L1] {l1['name']}")

            # L2: 楼层
            r2 = get_lower_areas(token, l1["id"])
            for l2 in r2.get("Data", []):
                if verbose:
                    print(f"      [L2] {l2['name']}")

                # L3: 房间
                r3 = get_rooms(token, l2["id"])
                for l3 in r3.get("Data", []):
                    if room and room not in l3.get("name", ""):
                        continue
                    if verbose:
                        print(f"        [L3] {l3['name']}")

                    # 查设备
                    rd = get_devices_at_site(token, l3["id"], l3.get("siteFlag", 0), type_id)
                    devices = rd.get("Data", []) if rd.get("Data") else []
                    for d in devices:
                        d["_area"] = f"{l0['name']}/{l1['name']}/{l2['name']}/{l3['name']}"
                        found.append(d)
                        if verbose:
                            print(f"          >>> 设备: {d.get('machineId')} ({d.get('deviceName', '')})")

    return found


# ============================================================
#  开阀: 纯 HTTP 4G 开阀
# ============================================================
def open_valve(token, machine_id, type_id=1, device_ver="01,A4"):
    """
    4G 纯 HTTP 开阀 (channelWay=4)

    适用于 typeId=1 洗澡阀，不需要 BLE，不需要 orderId。
    预付费设备 lastPosMoney/lastPosSerial 用 "0000" 占位即可。
    """
    body = {
        "channelWay": 4,
        "typeId": type_id,
        "deviceVer": device_ver,
        "machineId": machine_id,
        "firmwareVer": "",
        "machineInfoJson": {
            "handleType": 0,
            "lastPosMoney": "0000",
            "lastPosSerial": "0000",
            "machineData": "",
            "machinePwdVer": "",
            "machineRandom": None,
            "netFlag": "0000",
            "orderId": "",
            "type": 0,
        },
    }
    body_str = gson_dumps(body)
    h = api_headers(token=token, body_str=body_str)
    resp = requests.post(f"{BASE_URL}/bath/valve/open", data=body_str, headers=h, timeout=15)
    return resp.json()


# ============================================================
#  CLI
# ============================================================
def cmd_query(args, token):
    print("\n  查询预付费水表余额")
    wallet_key = input("  设备编号: ").strip() if not args.device else args.device
    r = query_balance(token, wallet_key)
    if r.get("Code") == 0 and r.get("Data"):
        d = r["Data"]
        print(f"  可用余额: {d['canUseMoney'] / 100:.2f} 元")
        print(f"  累计充值: {d['appendMoney'] / 100:.2f} 元")
        print(f"  累计用水: {d['waterVolume'] / 1000:.0f} 升 ({d['waterVolume'] / 1000000:.1f} m³)")
        print(f"  状态: {'正常' if d.get('status') == 1 else '异常'}")
    else:
        print(f"  查询失败: {r.get('Message', r)}")


def cmd_find(args, token):
    print("\n  查找设备")
    devices = find_devices(token, building=args.building, room=args.room, type_id=args.type)
    if not devices:
        print("  未找到设备")
    else:
        print(f"\n  共找到 {len(devices)} 台设备")
        for d in devices:
            print(f"    {d.get('machineId')} | {d.get('deviceName', '')} | {d.get('_area', '')}")


def cmd_open(args, token):
    print("\n  4G 开阀")
    mid = args.machine_id or input("  machineId: ").strip()
    ver = args.device_ver or "01,A4"
    tid = args.type
    print(f"  设备: {mid}, typeId={tid}, deviceVer={ver}")
    r = open_valve(token, mid, type_id=tid, device_ver=ver)
    print(f"  Code: {r.get('Code')}, Message: {r.get('Message')}")
    if r.get("Data"):
        print(f"  Data: {json.dumps(r['Data'], ensure_ascii=False)}")


def main():
    parser = argparse.ArgumentParser(description="乐校通 API 工具")
    sub = parser.add_subparsers(dest="cmd")

    p_query = sub.add_parser("query", help="查询预付费水表余额")
    p_query.add_argument("--device", "-d", help="设备编号")

    p_find = sub.add_parser("find", help="查找设备")
    p_find.add_argument("--building", "-b", help="楼栋名 (如 28栋)")
    p_find.add_argument("--room", "-r", help="房间号 (如 405)")
    p_find.add_argument("--type", "-t", type=int, default=1, help="设备类型 (1=洗澡阀, 18=水表)")

    p_open = sub.add_parser("open", help="4G 开阀")
    p_open.add_argument("--machine-id", "-m", help="machineId")
    p_open.add_argument("--device-ver", "-v", default="01,A4", help="设备版本")
    p_open.add_argument("--type", "-t", type=int, default=1, help="设备类型")

    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        return

    phone = input("\n手机号: ").strip()
    password = input("密码: ").strip()
    token = get_token(phone, password)
    if not token:
        return

    {"query": cmd_query, "find": cmd_find, "open": cmd_open}[args.cmd](args, token)


if __name__ == "__main__":
    main()
