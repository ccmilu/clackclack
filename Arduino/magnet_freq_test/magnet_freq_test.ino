/*
  电磁铁频率扫描测试
  ===================
  从 1Hz 到 100Hz 逐档加快，每档持续 1-3 秒，等占空比（ON 一半时间，OFF 一半时间）。
  通过 digitalWrite 直接控制（不走 PWM），最接近"全力推 / 完全断"的对比。

  观察方法：
    - 看推杆能不能跟上节奏（上下来回）
    - 听声音：1-10Hz 应该听到"咔哒咔哒"，
      高频时变成"嗡—"持续嗡鸣（推杆被吸住不再回弹）
    - 串口同步打印当前频率
  关键观察点：
    推杆在第几档变成"持续推出/不回弹" → 那就是这块电磁铁的机械响应频率上限

  注意：测试段总长约 40 秒，每段后有 800ms 散热间隔。
*/

#define MAG_PIN 2

struct Step {
  int hz;
  unsigned long duration_ms;
};

// 慢→快扫描档位
const Step STEPS[] = {
  { 1, 3000},   // 1 秒一次，看 3 次
  { 2, 3000},   // 0.5 秒一次
  { 3, 3000},
  { 4, 2500},
  { 5, 2500},
  { 6, 2500},
  { 8, 2500},
  {10, 2000},
  {12, 2000},
  {15, 2000},
  {20, 2000},
  {25, 1800},
  {30, 1500},
  {40, 1500},
  {50, 1500},
  {70, 1200},
  {100, 1000},
};

void runStep(int idx, int total, const Step& s) {
  int period = 1000 / s.hz;
  int half   = period / 2;
  int cycles = s.duration_ms / period;

  Serial.printf("[%2d/%2d] %3d Hz  period=%dms  ON=%dms OFF=%dms  cycles=%d\n",
                idx + 1, total, s.hz, period, half, half, cycles);

  for (int j = 0; j < cycles; j++) {
    digitalWrite(MAG_PIN, HIGH);
    delay(half);
    digitalWrite(MAG_PIN, LOW);
    delay(half);
  }
  digitalWrite(MAG_PIN, LOW);
}

void setup() {
  Serial.begin(115200);
  delay(500);
  pinMode(MAG_PIN, OUTPUT);
  digitalWrite(MAG_PIN, LOW);

  Serial.println("=== Magnet frequency sweep ===");
  Serial.println("观察推杆何时跟不上 → 变成持续吸住状态");
  Serial.println("3 秒后开始...");
  delay(3000);

  int n = sizeof(STEPS) / sizeof(Step);
  for (int i = 0; i < n; i++) {
    runStep(i, n, STEPS[i]);
    digitalWrite(MAG_PIN, LOW);
    delay(800);   // 段间散热 + 便于分辨
  }

  digitalWrite(MAG_PIN, LOW);
  Serial.println("\n=== Done. 按 RESET 重新跑一次 ===");
}

void loop() {
  // 空 loop
}
