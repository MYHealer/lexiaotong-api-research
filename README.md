# LexiaoTong API Research

乐校通校园水电管理系统 API 逆向分析。

## 功能

- **水费查询**: 预付费水表余额、用水量查询
- **设备发现**: 遍历区域层级查找设备
- **开阀控制**: 纯 HTTP 4G 开阀（有余额账号）

## 快速开始

```bash
pip install requests ddddocr

# 查询水费
python lexiaotong.py query

# 查找设备
python lexiaotong.py find

# 开阀（需有余额）
python lexiaotong.py open
```

首次运行会要求输入手机号和密码登录，token 自动缓存。

## API 逆向文档

详见 [RESEARCH.md](RESEARCH.md) 和 [API_TEST_REPORT.md](API_TEST_REPORT.md)。

## 核心发现

- **V3 签名**: `MD5(SIGN_KEY + 请求参数)`
- **X-Ghost**: 基于时间戳的校验码生成
- **4G 开阀**: channelWay=4，不需要 BLE，不需要 machineRandom
- **预付费设备**: typeId=1，orderId 可为空
- **设备发现**: `getLowerAreas` 逐级下钻 + `getMachineByLocation`

## 免责声明

仅供安全研究学习使用。
