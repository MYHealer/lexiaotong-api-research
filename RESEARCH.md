# 乐校通逆向研究记录

## 开阀 API 研究

### 已确认的 API

| 接口 | 方法 | 说明 |
|------|------|------|
| `/bath/valve/open` | POST | 打开洗澡水阀 |
| `/bath/valve/close` | POST | 关闭洗澡水阀 |
| `/bath/valve/getStatus` | GET | 查询阀门状态 |
| `/bath/valve/prepare` | POST | 预开阀（功能未开放） |
| `/drink/valve/open` | POST | 打开饮水阀 |
| `/hairdryer/valve/open` | POST | 打开吹风机 |
| `/washing/valve/open` | POST | 打开洗衣机 |
| `/device/open` | POST | 通用设备打开 |
| `/mgapp/app/machine/get/custom` | POST | 查询设备信息 |

### 开阀请求体 (PostPrepareBath)

```json
{
  "channelWay": 4,
  "typeId": 1,
  "deviceVer": "01,A4",
  "machineId": "<machineId>",
  "firmwareVer": "",
  "machineInfoJson": {
    "handleType": 0,
    "lastPosMoney": "0000",
    "lastPosSerial": "0000",
    "machineData": "",
    "machinePwdVer": "",
    "machineRandom": null,
    "netFlag": "0000",
    "orderId": "",
    "type": 0
  }
}
```

### channelWay 测试结果

| channelWay | 结果 |
|------------|------|
| 0 | 不支持的通讯方式 |
| **1 (蓝牙)** | 需要 machineRandom |
| 2 | 不支持的通讯方式 |
| 3 | 不支持的通讯方式 |
| **4 (4G)** | **不需要 machineRandom，lastPos="0000" 占位即可** |
| 5 | 不支持的通讯方式 |

### 核心发现

- **纯 HTTP 4G 开阀成功** — typeId=1 洗澡阀，channelWay=4
- **不需要充值单号** — orderId 为空字符串（预付费设备）
- **不需要 BLE** — 4G 设备可纯 HTTP 开阀
- **lastPosMoney/lastPosSerial** 用 "0000" 占位即可通过验证
- 扣费从洗澡钱包扣除

### 开阀验证链路

```
1. 用户充值获得余额
2. POST /bath/valve/open (channelWay=4, machineRandom=null)
3. 服务器验证余额，推送命令到设备 4G 模块
4. 设备执行开阀
5. 轮询 GET /bath/valve/getStatus 等待结果
```

### 洗澡设备发现路径

```
1. GET /baseDict/site/getLowerAreas?areaId=<schoolId>  → L0 区域
2. GET /baseDict/site/getLowerAreas?areaId={L0_id}     → 楼栋
3. GET /baseDict/site/getLowerAreas?areaId={L1_id}     → 楼层
4. GET /baseDict/site/getDormitoryOrPublicRoom?areaId={L2_id} → 房间
5. POST /mgapp/machine/getMachineByLocation {siteId, siteFlag, typeId=1} → 设备
```

### orderId 验证链（仅对 type=0 消费型设备）

```
orderId 为空       → Code=-302 "orderId不能为空"
machineRandom 为空  → Code=-302 "machineRandom不能为空" (channelWay=1)
orderId 不存在      → Code=-45 "充值单不存在"
orderId 非数字      → Code=-66 "For input string..."
```

预付费设备 (typeId=1, type=1) 不需要 orderId。

### V4 平台 (ai.lxt6.cn/api)

- V4 签名: `MD5("timestamp=" + ts + "a3F9Xz7bKp2RtLmN")`
- V4 Headers: timestamp, tokenInfo, sign
- V4 登录: `POST /common/user/customerPersonLogin`
- V4 验证码: `GET /common/user/getLoginCodeImage`
- V4 白名单检查: `GET /whitelist/status?passwordSeed=<studentHex+phone>`

## 水费查询 API

### 预付费水表余额
```
GET /paymentV1/app/wallet/find?walletKey=<设备编号>&schoolId=<学校ID>&investorId=<投资人ID>
```

### 洗澡钱包余额
```
POST /payment/student/getWalletInfoByInvestorid
Body: {"schoolId":"<schoolId>","investorId":"<investorId>","walletType":1}
```

## 结论

- 纯 HTTP 4G 开阀可行，不需要 BLE
- 设备发现通过 `getLowerAreas` 逐级下钻
- V4 白名单路径需要单独的 V4 账号
