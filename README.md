# 信用卡對帳單自動化處理系統

自動接收 Gmail 信用卡對帳單郵件，解析 PDF 附件中的交易明細，並透過 Firefly III API 匯入個人財務管理系統。

## 功能

- **郵件接收** — 透過 Gmail API + Google Pub/Sub 即時接收對帳單郵件
- **銀行辨識** — 根據寄件者與主旨自動識別銀行（支援 7 家銀行）
- **PDF 解鎖與解析** — 自動輸入密碼解鎖 PDF，擷取交易明細表格
- **交易分類** — 判斷消費/退款/繳費類型，對應支出分類（50+ 類別）
- **Firefly III 匯入** — 批次建立交易，支援防重複與自動重試
- **REST API** — 提供對帳單查詢、交易修改、手動上傳等端點
- **資料暫存** — SQLite 儲存解析結果，支援狀態追蹤

## 支援銀行

| 銀行     | 模組代碼  | 密碼類型       | 解析器狀態 |
|----------|-----------|----------------|------------|
| 永豐銀行 | sinopac   | 身分證字號     | 已實作（綜合對帳單 + 信用卡帳單） |
| 台新銀行 | taishin   | 身分證末2碼+生日月日 | 已實作     |
| 國泰世華 | cathay    | 身分證字號     | 待實作     |
| 富邦銀行 | fubon     | 身分證字號     | 待實作     |
| 玉山銀行 | esun      | 出生日期       | 待實作     |
| 樂天銀行 | rakuten   | 自訂密碼       | 待實作     |
| 中國信託 | ctbc      | 出生日期       | 待實作     |

## 快速開始

### 環境需求

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (套件管理)
- Docker (選用)

### 安裝

```bash
uv sync
```

### 設定

複製範例組態檔並填入個人資訊：

```bash
cp config/config.example.yaml config/config.yaml
```

編輯 `config/config.yaml`：

```yaml
user:
  id_number: "A123456789"    # 身分證字號（用於 PDF 密碼）
  birthday: "19900101"       # 出生日期
  phone: "0912345678"        # 手機號碼

firefly:
  base_url: "https://firefly.example.com"
  api_token: "your-api-token"
```

也可透過環境變數覆寫敏感設定：

```bash
export FIREFLY_BASE_URL="https://firefly.example.com"
export FIREFLY_API_TOKEN="your-api-token"
```

### 啟動

```bash
# 直接執行
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000

# 或使用 Docker
docker compose up
```

### 驗證

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

## API 端點

| 方法   | 路徑                                       | 說明                 |
|--------|-------------------------------------------|----------------------|
| GET    | `/health`                                  | 健康檢查             |
| POST   | `/webhook/gmail`                           | Gmail Pub/Sub 推播   |
| POST   | `/api/upload?bank_code=sinopac`            | 手動上傳 PDF         |
| GET    | `/api/statements`                          | 對帳單列表           |
| GET    | `/api/statements/{id}`                     | 對帳單詳情           |
| GET    | `/api/statements/{id}/transactions`        | 對帳單交易明細       |
| PUT    | `/api/transactions/{id}`                   | 修改交易             |
| POST   | `/api/transactions/{id}/import`            | 手動觸發單筆匯入     |

### 上傳範例

```bash
curl -X POST "http://localhost:8000/api/upload?bank_code=sinopac" \
  -F "file=@statement.pdf"
```

## Gmail 推播設定

1. 建立 Google Cloud 專案，啟用 Gmail API
2. 建立 OAuth 2.0 憑證，下載為 `credentials.json`
3. 建立 Pub/Sub Topic 與 Push Subscription，指向 `/webhook/gmail`
4. 在 `config.yaml` 填入 `gmail.pubsub_topic` 與 `gmail.pubsub_subscription`
5. 首次執行時會開啟瀏覽器完成 OAuth 授權，產生 `token.json`

## 新增銀行解析器

1. 在 `config/config.yaml` 的 `banks` 區段新增銀行設定
2. 建立 `app/parsers/<bank>_parser.py`，繼承 `BaseParser`
3. 實作 `parse()` 方法，回傳交易字典列表
4. 在 `app/parsers/__init__.py` 的 `_register_all()` 中註冊

```python
# app/parsers/cathay_parser.py
from app.parsers.base_parser import BaseParser

class CathayParser(BaseParser):
    def parse(self, pdf_path: str) -> list:
        # 解析 PDF，回傳交易列表
        ...
```

## CLI 工具

提供 `cli.py` 命令列工具，可直接測試 PDF 解析，無需啟動伺服器：

```bash
# 解析 PDF 並顯示表格
uv run python3 cli.py parse statement.pdf --bank taishin --password 710704

# 輸出 JSON 格式
uv run python3 cli.py parse statement.pdf -b sinopac -p S123456789 -f json

# 查看 PDF 原始表格/文字（開發新解析器時使用）
uv run python3 cli.py raw statement.pdf -p 710704 --mode tables
uv run python3 cli.py raw statement.pdf -p 710704 --mode text

# 列出已設定的銀行
uv run python3 cli.py banks
```

## 專案結構

```
bill-pdf-to-firefly/
├── app/
│   ├── main.py                  # FastAPI 應用程式入口
│   ├── config.py                # 組態載入（YAML + Pydantic）
│   ├── models/
│   │   ├── database.py          # SQLModel 資料模型（Statement, Transaction）
│   │   └── statement.py         # API 回應 Schema
│   ├── parsers/
│   │   ├── __init__.py          # ParserFactory 解析器工廠
│   │   ├── base_parser.py       # 解析器基礎類別
│   │   ├── sinopac_parser.py    # 永豐銀行解析器
│   │   └── taishin_parser.py    # 台新銀行解析器
│   ├── routers/
│   │   ├── webhook.py           # Gmail Pub/Sub Webhook
│   │   └── statements.py        # REST API 端點
│   ├── services/
│   │   ├── gmail_service.py     # Gmail API 整合
│   │   ├── mail_classifier.py   # 郵件分類判斷
│   │   ├── pdf_service.py       # PDF 解鎖
│   │   ├── firefly_service.py   # Firefly III API 客戶端
│   │   └── import_service.py    # 匯入流程協調
│   └── utils/
│       ├── pdf_utils.py         # PDF 工具函數
│       └── account_mapper.py    # 帳戶與分類對應
├── config/
│   ├── config.yaml              # 主要組態檔（請勿提交）
│   └── config.example.yaml      # 範例組態檔
├── cli.py                       # CLI 測試工具
├── pyproject.toml               # uv 套件管理設定
├── uv.lock                      # uv 鎖定檔
├── Dockerfile
└── docker-compose.yaml
```

## 技術棧

- **套件管理**: uv
- **Web 框架**: FastAPI
- **PDF 解析**: pdfplumber
- **PDF 解鎖**: pikepdf
- **資料庫**: SQLite + SQLModel
- **HTTP 客戶端**: httpx
- **Gmail 整合**: google-api-python-client
- **組態管理**: PyYAML + Pydantic
- **日誌**: structlog
