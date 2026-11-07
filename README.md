# BA1SD 手機版訊號

預設策略：**10/15/21 投票 + BA1SD + MA20 50% + dl25%cash**

GitHub 會在雲端定時執行 `generate.py`，抓 Yahoo 數據、計算訊號，產生一個手機網頁。你的電腦不用開著，Android 瀏覽器打開網址就能看，也可以「加到主畫面」當 App 用。

---

## 三種在手機上使用的方式

| 方式 | 電腦要開著？ | 出門能看？ | 難度 | 盤中即時？ |
|---|---|---|---|---|
| **A. GitHub 手機網頁**（本資料夾） | 不用 | ✅ | 中（一次設定約 15 分鐘） | 每天 6 次自動更新 |
| B. Telegram 推播（可和 A 一起用） | 不用 | ✅ | 中 | 只在換倉時通知 |
| C. 同一個 Wi-Fi 開電腦版追蹤器 | 要 | ❌ | 低 | ✅ |

---

## A. 設定 GitHub 手機網頁

### 1. 建立 repository

1. 到 https://github.com 註冊或登入。
2. 右上角 **＋ → New repository**，名稱例如 `ba1sd`。
3. 選 **Public**（免費帳號的 GitHub Pages 需要公開 repository，見下方「隱私」），按 **Create repository**。

### 2. 上傳檔案

在 repository 頁面按 **Add file → Upload files**，把這個資料夾裡的所有東西拖進去：

```
ba1sd_tracker.py
generate.py
requirements.txt
README.md
.github/workflows/update.yml
```

> 網頁上傳時，`.github` 這種以點開頭的資料夾可能不會顯示。如果上傳後看不到 `.github/workflows/update.yml`，就按 **Add file → Create new file**，檔名輸入 `.github/workflows/update.yml`，把內容貼上即可。

### 3. 開啟自動執行的寫入權限

**Settings → Actions → General → Workflow permissions**：選 **Read and write permissions** → Save。

### 4. 第一次手動執行

**Actions** 分頁 → 左邊選 **更新 BA1SD 訊號** → **Run workflow**。約 1–2 分鐘後變成綠色勾勾，repository 裡就會多一個 `docs` 資料夾。

### 5. 開啟 GitHub Pages

**Settings → Pages**：
- Source：**Deploy from a branch**
- Branch：**main**，資料夾選 **/docs** → Save

約 1 分鐘後，頁面上方會顯示網址，例如 `https://你的帳號.github.io/ba1sd/`。

### 6. 在 Android 加到主畫面

用 Chrome 打開網址 → 右上角 ⋮ → **加到主畫面**（或「安裝應用程式」）。

### 自動更新時間（英國時間，週一至週五）

| 時間 | 用途 |
|---|---|
| 約 13:40 | 美股開盤前 |
| 約 19:40／20:40 | 收盤前 20 分鐘的盤中預估（美國夏令／冬令） |
| 約 20:30／21:30 | 收盤後確認訊號（美國夏令／冬令） |
| 隔天約 06:15 | 早上起床看 |

GitHub 的排程可能延遲 5–30 分鐘。想立刻更新，就到 Actions 按 Run workflow。網頁上方會顯示最後更新時間。

---

## B. Telegram 推播（可選）

配置改變，或盤中預估顯示「今天收盤會換倉」時，會傳訊息到你的 Telegram。

1. 在 Telegram 搜尋 **@BotFather** → 傳 `/newbot` → 照指示取名 → 會得到一串 **token**。
2. 打開你的新機器人，隨便傳一則訊息給它。
3. 在瀏覽器打開 `https://api.telegram.org/bot你的token/getUpdates`，找到 `"chat":{"id":123456789` 這串數字，就是 **chat id**。
4. 回到 GitHub repository：**Settings → Secrets and variables → Actions → New repository secret**，新增兩個：
   - `TELEGRAM_TOKEN` = 第 1 步的 token
   - `TELEGRAM_CHAT_ID` = 第 3 步的數字

之後每次自動執行，有變化就會通知你。

---

## C. 同一個 Wi-Fi 下用電腦版追蹤器

電腦上執行：

```bash
streamlit run ba1sd_tracker.py --server.address 0.0.0.0
```

在電腦的命令列輸入 `ipconfig`，找到 IPv4 位址（例如 `192.168.1.23`），手機瀏覽器打開 `http://192.168.1.23:8501`。第一次執行時，Windows 防火牆可能會詢問，請允許「私人網路」。

---

## 隱私

- **方式 A 的網頁是公開的**：知道網址的人都能看到你的訊號（不會顯示你的持股數量或金額）。repository 名稱取得不明顯一點，網址就不容易被猜到。
- 如果不想公開：把 repository 設成 **Private**，不要開 GitHub Pages，只用 **方式 B（Telegram）**。私人 repository 每月有免費的 Actions 執行時間，這個排程大約用掉 200 分鐘，在免費額度內。

---

## 換策略

在 `.github/workflows/update.yml` 的 `python generate.py` 那一步加上環境變數：

```yaml
        env:
          BA1SD_PRESET: "BA1SD + MA20 50% + dl25%cash（預設）"
```

可用的名稱見 `ba1sd_tracker.py` 裡的 `PRESETS`。

---

資料來源：Yahoo Finance（約延遲 15 分鐘）。這只是把回測規則自動化，不是投資建議。
