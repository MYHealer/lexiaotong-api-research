# 乐校通开阀 API 测试报告

## 测试环境

- 设备类型: 洗澡阀 (typeId=1, deviceVer=01,A4)
- 设备能力: 4G+BLE 双模 (cnflag=4)

## API 端点测试结果

### 1. GET /bath/num/unlock — ✅ 成功
设备解锁接口可用（数据采集功能，非开阀）。

### 2. POST /bath/valve/open — ✅ 成功 (channelWay=4)

4G 纯 HTTP 开阀成功：
- channelWay=4 不需要 machineRandom
- lastPosMoney/lastPosSerial 用 "0000" 占位
- orderId 可为空（预付费设备）
- 服务器返回 offline=1 但设备仍能接收 4G 命令

### 3. POST /bath/valve/prepare — ❌ 未开放
Code=-45 "暂未开放"

### 4. POST /device/open (V4) — ❌ 需要 V4 token
V3 签名不匹配，V4 需要单独登录。

### 5. GET /whitelist/status (V4) — ❌ 需要 V4 token

## 签名算法

### V3 签名
```
sign = MD5(SIGN_KEY + 请求参数)
```

### V4 签名
```
sign = MD5("timestamp=" + timestamp + V4_SIGN_KEY)
```

## 建议

1. **有余额账号**: 直接调用 `/bath/valve/open` (channelWay=4)
2. **设备发现**: 通过 `getLowerAreas` 逐级下钻 + `getMachineByLocation(typeId=1)`
3. **BLE 离线模式**: 需要设备预先授权 + BLE 连接
