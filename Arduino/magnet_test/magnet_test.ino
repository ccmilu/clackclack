/*
  Phase 4：电磁铁极限诊断（IRF520 半开测试）
  ===========================================
  你的 IRF520 在 ESP32 3.3V SIG 驱动下大概率没完全打开。
  这个 sketch 用 4 种极限方式触发，看推杆能否在任意一种下动一下：
    [Test 1] 满 duty PWM 持续 500ms
    [Test 2] 满 duty PWM 持续 2000ms（长时间累积）
    [Test 3] digitalWrite HIGH 持续 1000ms（绕过 LEDC，纯直流）
    [Test 4] 高频通断 100ms × 8 次（看能否瞬间振动）

  每段之间间隔 2 秒，串口同步播报。
  观察结果：
    - 任何一段听到"咔"或感受到推杆移动 → IRF520 是半开状态，换 IRF540N 必然能跑
    - 4 段都完全无反应 → IRF520 在 3.3V 下完全 stuck，更要尽快换
*/

#define MAG_PIN 2

void runFullDutyPWM(int durMs, int testNo) {
  Serial.printf("[Test %d] LEDC full duty for %d ms\n", testNo, durMs);
  ledcAttach(MAG_PIN, 1000, 8);
  ledcWrite(MAG_PIN, 255);
  delay(durMs);
  ledcWrite(MAG_PIN, 0);
  ledcDetach(MAG_PIN);
  pinMode(MAG_PIN, OUTPUT);
  digitalWrite(MAG_PIN, LOW);
}

void runDirectHigh(int durMs, int testNo) {
  Serial.printf("[Test %d] digitalWrite HIGH for %d ms\n", testNo, durMs);
  pinMode(MAG_PIN, OUTPUT);
  digitalWrite(MAG_PIN, HIGH);
  delay(durMs);
  digitalWrite(MAG_PIN, LOW);
}

void runFastToggle(int pulses, int testNo) {
  Serial.printf("[Test %d] fast toggle %d pulses (100ms each)\n", testNo, pulses);
  pinMode(MAG_PIN, OUTPUT);
  for (int i = 0; i < pulses; i++) {
    digitalWrite(MAG_PIN, HIGH);
    delay(100);
    digitalWrite(MAG_PIN, LOW);
    delay(100);
  }
}

void rest(int ms) {
  pinMode(MAG_PIN, OUTPUT);
  digitalWrite(MAG_PIN, LOW);
  delay(ms);
}

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("=== IRF520 limit diagnostic ===");
  Serial.println("⚠️ 推杆即将动作，桌面放稳，远离任何会被推到的东西");
  delay(2000);

  pinMode(MAG_PIN, OUTPUT);
  digitalWrite(MAG_PIN, LOW);   // 保险：上电先确保断电
  delay(500);

  runFullDutyPWM(500, 1);
  rest(2000);

  runFullDutyPWM(2000, 2);
  rest(2000);

  runDirectHigh(1000, 3);
  rest(2000);

  runFastToggle(8, 4);
  rest(500);

  digitalWrite(MAG_PIN, LOW);
  Serial.println("=== Done. Press RESET to replay. ===");
}

void loop() {
  // 空 loop
}
