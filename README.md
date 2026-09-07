# LexiaoTong API Research

乐校通校园水电管理系统 API 逆向分析。

## 功能

- **水费查询**: 预付费水表余额、用水量查询
- **设备发现**: 遍历区域层级查找设备
- **开阀控制**: 纯 HTTP 4G 开阀（有余额账号）
- **验证码识别**: CNN 模型自动识别 5 位数字验证码（96.8% 准确率）

## 快速开始

```bash
pip install requests ddddocr torch numpy scipy Pillow

# 查询水费
python lexiaotong.py query

# 查找设备
python lexiaotong.py find

# 开阀（需有余额）
python lexiaotong.py open
```

首次运行会要求输入手机号和密码登录，token 自动缓存。
如果存在 `captcha_cnn_best.pth` 模型文件，会优先使用 CNN 识别验证码（无限重试直到登录成功）。

## 验证码识别

乐校通登录需要 5 位数字验证码，带有蓝色干扰线。

### 训练模型

```bash
# 1. 下载验证码（默认 500 张）
python train_captcha_cnn.py download 500

# 2. 训练 CNN 模型（增量训练，自动标注）
python train_captcha_cnn.py train 5000
```

训练流程：下载 → ddddocr 自动标注 → 蓝色干扰线去除 → 字符分割 → CNN 训练。
模型约 19KB（int8 量化），适合 ESP32-C3 部署。

### 单独登录测试

```bash
python captcha_recognize.py <手机号> <密码>
```

### ESP32 部署

`captcha_cnn_weights.h` 包含量化后的 C 权重和推理函数，可直接在 ESP32 上运行：

```c
#include "captcha_cnn_weights.h"

// 预处理: 去蓝色干扰线 (B>R+50 && B>G+50 → 白色)
// 灰度化 + 二值化 + 字符分割为 12x8
uint8_t char_img[12 * 8];
int digit = captcha_predict(char_img);
```

## API 逆向文档

详见 [RESEARCH.md](RESEARCH.md) 和 [API_TEST_REPORT.md](API_TEST_REPORT.md)。

## 核心发现

- **V3 签名**: `MD5(SIGN_KEY + 请求参数)`
- **X-Ghost**: 基于时间戳的校验码生成
- **4G 开阀**: channelWay=4，不需要 BLE，不需要 machineRandom
- **预付费设备**: typeId=1，orderId 可为空
- **设备发现**: `getLowerAreas` 逐级下钻 + `getMachineByLocation`
- **验证码**: 5 位纯数字，蓝色干扰线，CNN 识别后无限重试登录

## 免责声明

仅供安全研究学习使用。
