/*
  TFT 屏调校诊断 sketch
  =====================
  画面布局（160x80 横屏，正确显示时）：
    ┌─────────────────────────────────────────┐  ← 顶边白线（看是否被切）
    │ R1                                      │  ← 旋转标记，应该在左上角
    │  ┌──────┐ ┌──────┐ ┌──────┐            │
    │  │ RED  │ │GREEN │ │ BLUE │  ← 三个色块带白字
    │  └──────┘ └──────┘ └──────┘            │
    │                                  TOP-R  │  ← TOP-R 应该在右上
    └─────────────────────────────────────────┘  ← 底边白线

  调校步骤：
    1. 颜色对不对：RED 块该是红色 + 字白；GREEN 该是绿；BLUE 该是蓝。
       全反（红→青 蓝→黄）→ 把下面 INVERT_COLOR 改成 true。
    2. 上下颠倒：把 ROTATION 改 1↔3（或 0↔2）反复烧到正。
    3. 边框被切 / 漏黑边：调 COL_OFFSET、ROW_OFFSET（每次 ±1 试）。
       右边被切 → COL_OFFSET 减 1；右边漏黑边 → COL_OFFSET 加 1。
       下面被切 → ROW_OFFSET 减 1；下面漏黑边 → ROW_OFFSET 加 1。
*/

#include <Adafruit_GFX.h>
#include <Adafruit_ST7735.h>
#include <SPI.h>

// ===================== 调校三参数（反复烧调到满意） =====================
#define ROTATION      3       // 0 / 1 / 2 / 3 试。上下颠倒就改成 1（如果现在是 3）
#define COL_OFFSET    0       // ±1 微调，0 = 用库内置偏移
#define ROW_OFFSET    0       // ±1 微调，0 = 用库内置偏移
#define INVERT_COLOR  false   // 白色变黑 = 反相开错了，保持 false
#define SWAP_RB       false   // R/B 通道修过没用 → 先关掉，让 init 重做
#define USE_PLUGIN_INIT true  // 换 PLUGIN init 序列，专修 0.96 屏颜色错位
// =====================================================================

// 引脚定义（项目硬件接线）
#define TFT_CS    10
#define TFT_RST    8
#define TFT_DC     7
#define TFT_MOSI   5   // SDA
#define TFT_SCLK   6   // SCL

// Adafruit_ST7735 的 setColRowStart / _colstart / _rowstart 都是 protected。
// 这里继承子类把它们暴露出来。
// 注意：INITR_MINI160x80 init 时会设置一组默认 offset（让画布跳过 controller 的隐藏区），
// 不能直接 setColRowStart(0, 0) 覆盖，否则画布范围错位会出现噪点。
// 正确做法：在默认值基础上 ±delta（applyOffsetDelta 实现这个）。
class TFTWithOffset : public Adafruit_ST7735 {
 public:
  using Adafruit_ST7735::Adafruit_ST7735;
  void applyOffsetDelta(int8_t dc, int8_t dr) {
    setColRowStart(_colstart + dc, _rowstart + dr);
  }
  int8_t getColStart() const { return _colstart; }
  int8_t getRowStart() const { return _rowstart; }
};

TFTWithOffset tft(TFT_CS, TFT_DC, TFT_MOSI, TFT_SCLK, TFT_RST);

void setup() {
  Serial.begin(115200);
  Serial.println("TFT diagnostic start");

  // INITR_MINI160x80 vs INITR_MINI160x80_PLUGIN：两套独立的初始化序列
  // 普通版本颜色错就换 PLUGIN。两个版本默认 offset 可能不同，
  // 之后 COL/ROW_OFFSET 微调可能要重调。
  if (USE_PLUGIN_INIT) {
    tft.initR(INITR_MINI160x80_PLUGIN);
  } else {
    tft.initR(INITR_MINI160x80);
  }
  Serial.printf("Library default colstart=%d rowstart=%d\n",
                tft.getColStart(), tft.getRowStart());

  // 在库默认 offset 基础上叠加 ±delta。0/0 = 不动。
  tft.applyOffsetDelta(COL_OFFSET, ROW_OFFSET);
  tft.setRotation(ROTATION);

  // 直接传值覆盖 initR 默认反相状态
  tft.invertDisplay(INVERT_COLOR);

  // SWAP_RB：用一条 MADCTL 命令覆盖库的默认颜色顺序
  // ST7735 MADCTL 位定义：
  //   0x80 MY  | 0x40 MX  | 0x20 MV  | 0x10 ML
  //   0x08 BGR (1=BGR, 0=RGB)  ← 修 R/B 互换就是清这一位
  // 不同 rotation 的方向位组合不一样，这里枚举 4 种
  if (SWAP_RB) {
    uint8_t madctl;
    switch (ROTATION & 3) {
      case 0: madctl = 0x40 | 0x80; break;  // MX | MY, RGB
      case 1: madctl = 0x80 | 0x20; break;  // MY | MV, RGB
      case 2: madctl = 0x00;        break;  // 0, RGB
      case 3: madctl = 0x40 | 0x20; break;  // MX | MV, RGB
    }
    tft.sendCommand(ST77XX_MADCTL, &madctl, 1);
  }

  drawDiagnostic();
}

void loop() {
  // 静态画面，无需 loop
}

void drawDiagnostic() {
  int W = tft.width();
  int H = tft.height();

  Serial.printf("Screen: %dx%d, rotation=%d, colOff=%d, rowOff=%d\n",
                W, H, ROTATION, COL_OFFSET, ROW_OFFSET);

  // 黑底
  tft.fillScreen(ST77XX_BLACK);

  // 四条白边框（最重要：判断边缘对齐 + offset 调校）
  tft.drawRect(0, 0, W, H, ST77XX_WHITE);

  // 旋转标记：左上角写 "R<n>" 表示当前 rotation
  tft.setCursor(2, 2);
  tft.setTextColor(ST77XX_WHITE);
  tft.setTextSize(1);
  tft.printf("R%d", ROTATION);

  // 右上角写 "TOP-R"，验证朝向是否正确（这个字应该出现在屏幕的右上）
  tft.setCursor(W - 35, 2);
  tft.print("TOP-R");

  // 三个色块（红绿蓝），每块上写字
  int blockW = 36, blockH = 24;
  int gap = 6;
  int totalW = blockW * 3 + gap * 2;
  int startX = (W - totalW) / 2;
  int startY = (H - blockH) / 2;

  // RED 块
  tft.fillRect(startX, startY, blockW, blockH, ST77XX_RED);
  tft.setCursor(startX + 6, startY + 8);
  tft.setTextColor(ST77XX_WHITE);
  tft.print("RED");

  // GREEN 块
  int gx = startX + blockW + gap;
  tft.fillRect(gx, startY, blockW, blockH, ST77XX_GREEN);
  tft.setCursor(gx + 2, startY + 8);
  tft.setTextColor(ST77XX_BLACK);
  tft.print("GREEN");

  // BLUE 块
  int bx = gx + blockW + gap;
  tft.fillRect(bx, startY, blockW, blockH, ST77XX_BLUE);
  tft.setCursor(bx + 4, startY + 8);
  tft.setTextColor(ST77XX_WHITE);
  tft.print("BLUE");

  // 底部一行小字标示 offset
  tft.setCursor(2, H - 10);
  tft.setTextColor(ST77XX_YELLOW);
  tft.printf("c=%d r=%d", COL_OFFSET, ROW_OFFSET);
}
