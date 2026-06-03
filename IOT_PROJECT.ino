#include <SPI.h>
#include <MFRC522.h>
#include <Keypad.h>
#include <Servo.h>
#include <SoftwareSerial.h>

// ========================================================
//              PAMETNI SEF — KONFIGURACIJA PINOVA
// ========================================================

// --- GSM A9 Modul (RX=D6, TX=D5) ---
SoftwareSerial A9Serial(6, 5);
const String TARGET_PHONE = "+38268803522";

// --- RFID Čitač (SPI: MOSI=11, MISO=12, SCK=13) ---
#define RST_PIN  9
#define SS_PIN   10
MFRC522 rfid(SS_PIN, RST_PIN);

byte UID_Petar[4] = {0x52, 0xC0, 0xB2, 0x1B};
byte UID_Marko[4] = {0x64, 0x5C, 0xE1, 0x22};

// --- Tastatura 3x3 ---
const byte REDOVI = 3;
const byte KOLONE = 3;
char tasteri[REDOVI][KOLONE] = {
  {'9','8','7'},
  {'6','5','4'},
  {'3','2','1'}
};
byte pinoviRedova[REDOVI] = {A1, A2, A3};
byte pinoviKolona[KOLONE] = {A4, A5, A0};
Keypad tastatura = Keypad(makeKeymap(tasteri), pinoviRedova, pinoviKolona, REDOVI, KOLONE);

String tacanPIN = "1234";
String uneseniPIN = "";

// --- Servo Motor (brava) ---
Servo mojServo;
#define SERVO_PIN    8
const int POZ_ZAKLJUCANO  = 45;   // Pozicija kada je sef zaključan
const int POZ_OTKLJUCANO  = 0;    // Pozicija kada je sef otključan
const int SERVO_KORAK_MS  = 45;   // Pauza između svakog stepena (ms)

// --- Buzzer ---
#define BUZZER_PIN 7

// --- Senzori ---
#define TILT_PIN  2
#define TRIG_PIN  4
#define ECHO_PIN  3

// ========================================================
//     STANJA I TAJMERI — SVE RADI BEZ BLOKIRANJA (delay)
// ========================================================

// ── Stanje sefa ──
bool sefOtkljucan = false;

// ── Servo state machine ──
//   IDLE → OTVARA → OTVOREN (15s) → ZATVARA → IDLE
enum ServoStanje { SERVO_IDLE, SERVO_OTVARA, SERVO_OTVOREN, SERVO_ZATVARA };
ServoStanje servoStanje = SERVO_IDLE;
int servoPozicija;
unsigned long servoTimer = 0;
const long VRIJEME_OTVORENO = 15000;  // Sef ostaje otvoren 15 sekundi

// ── Alarm (zujalica) ──
bool alarmAktivan = false;
unsigned long alarmPocetak = 0;
const long TRAJANJE_ALARMA = 4000;  // Zujalica zvuči 4 sekunde

// ── SMS cooldown (spriječava spam) ──
unsigned long vrijemePoslednjegSMS = 0;
const long SMS_COOLDOWN = 60000;  // Min 60 sekundi između SMS poruka

// ── SMS state machine (non-blocking slanje) ──
//   IDLE → CMGF → CMGS → PORUKA → CTRL_Z → (čeka 5s) → IDLE
enum SmsStanje { SMS_IDLE, SMS_CMGF, SMS_CMGS, SMS_PORUKA, SMS_CTRL_Z };
SmsStanje smsStanje = SMS_IDLE;
unsigned long smsTimer = 0;
String smsPoruka = "";
String smsBroj = "";

// ── Tilt debounce ──
unsigned long poslednji_tilt = 0;
const long TILT_COOLDOWN = 1000;  // Max jednom u sekundi

// ── Ultrazvučni tajmer ──
unsigned long prethodnoMjerenje = 0;
const long INTERVAL_MJERENJA = 2000;  // Mjeri svake 2 sekunde

// ========================================================
//                        SETUP
// ========================================================

void setup() {
  Serial.begin(115200);
  while (!Serial) { ; }

  // ── KORAK 1: Čekaj da se A9 GSM modul hardverski podigne ──
  Serial.println("Cekam 15 sekundi da se GSM A9 modul podigne...");
  delay(15000);

  // ── KORAK 2: Pokreni GSM komunikaciju ──
  // A9 defaultno radi na 115200, ali SoftwareSerial je nestabilan na toj brzini.
  // Prvo se spojimo na 115200 da kažemo modulu da pređe na 9600.
  A9Serial.begin(115200);
  delay(500);
  A9Serial.println("AT+IPR=9600");  // Komanda A9 modulu: prebaci na 9600 baud
  delay(1000);
  A9Serial.end();

  // Ponovo se spojimo na 9600 — stabilna brzina za SoftwareSerial
  A9Serial.begin(9600);
  delay(1000);
  A9Serial.println("AT");  // Test komunikacije
  delay(500);
  Serial.println("--- GSM A9 Modul pokrenut (9600 baud) ---");

  // ── KORAK 3: Inicijalizacija RFID čitača ──
  SPI.begin();
  rfid.PCD_Init();
  rfid.PCD_SetAntennaGain(rfid.RxGain_max);

  // ── KORAK 4: Servo motor — postavi na zaključano ──
  mojServo.attach(SERVO_PIN);
  mojServo.write(POZ_ZAKLJUCANO);

  // ── KORAK 5: Konfiguracija ostalih pinova ──
  pinMode(BUZZER_PIN, OUTPUT);
  pinMode(TILT_PIN, INPUT_PULLUP);
  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);

  // Resetuj SMS cooldown da prvi SMS može biti poslat odmah
  vrijemePoslednjegSMS = millis() - SMS_COOLDOWN;

  Serial.println("--- SIGURNOSNI SEF SPREMAN ---");
  Serial.println("Prislonite RFID karticu ili ukucajte PIN:");
}

// ========================================================
//                   GLAVNA PETLJA
//   Nijedna funkcija ovdje ne koristi delay().
//   Sve se provjerava svaki ciklus i reaguje instant.
// ========================================================

void loop() {
  // GSM passthrough (za ručne AT komande preko Serial Monitora)
  if (A9Serial.available()) { Serial.write(A9Serial.read()); }
  if (Serial.available())   { A9Serial.write(Serial.read()); }

  // Pokreni sve non-blocking podsisteme
  upravljajServom();
  upravljajAlarmom();
  upravljajSMS();

  // --- ZAŠTITA 1: Tilt senzor (sa debounce-om) ---
  // Reaguje samo dok je sef zaključan i max jednom u sekundi
  if (!sefOtkljucan && digitalRead(TILT_PIN) == LOW) {
    if (millis() - poslednji_tilt >= TILT_COOLDOWN) {
      poslednji_tilt = millis();
      Serial.println("\n[ALARM] Detektovano pomjeranje sefa!");
      pokreniAlarm("ALARM: Detektovano pomjeranje ili nagib sefa!");
    }
  }

  // --- ZAŠTITA 2: Ultrazvučni senzor (samo dok je sef zaključan) ---
  if (!sefOtkljucan && (millis() - prethodnoMjerenje >= INTERVAL_MJERENJA)) {
    prethodnoMjerenje = millis();
    long distanca = izmjeriDistancu();
    if (distanca > 0 && (distanca > 21 || distanca < 16)) {
      Serial.print("\n[ALARM] Narusavanje distance! Distanca: ");
      Serial.print(distanca);
      Serial.println(" cm");
      pokreniAlarm("ALARM: Detektovan pokret ili nasilno otvaranje sefa!");
    }
  }

  // --- RFID čitač ---
  if (rfid.PICC_IsNewCardPresent() && rfid.PICC_ReadCardSerial()) {
    if (uporediUID(rfid.uid.uidByte, UID_Petar)) {
      Serial.println("Korisnik: Petar Petrovic -> ODOBRENO!");
      zvukUspjeh();
      pokreniOtvaranje();
    }
    else if (uporediUID(rfid.uid.uidByte, UID_Marko)) {
      Serial.println("Korisnik: Marko Markovic -> ODOBRENO!");
      zvukUspjeh();
      pokreniOtvaranje();
    }
    else {
      Serial.println("Korisnik: NEPOZNAT -> ODBIJEN!");
      zvukGreska();
      zakaziSMS("ALARM: Pokusaj otkljucavanja nepoznatom RFID karticom!", TARGET_PHONE);
    }
    rfid.PICC_HaltA();
    rfid.PCD_StopCrypto1();
  }

  // --- Tastatura ---
  char taster = tastatura.getKey();
  if (taster) {
    Serial.print("Pritisnuto: ");
    Serial.println(taster);
    tone(BUZZER_PIN, 1500, 30);
    uneseniPIN += taster;

    if (uneseniPIN.length() == 4) {
      if (uneseniPIN == tacanPIN) {
        Serial.println("PIN tacan! Pristup odobren.");
        zvukUspjeh();
        pokreniOtvaranje();
      } else {
        Serial.println("Pogresan PIN! Pristup odbijen.");
        zvukGreska();
        uneseniPIN = "";
        zakaziSMS("ALARM: Pokusan unos pogresnog PIN koda na sefu!", TARGET_PHONE);
      }
    }
  }
}

// ========================================================
//         SERVO — NON-BLOCKING STATE MACHINE
//   Pomjera servo po 1 stepen svaki SERVO_KORAK_MS
//   bez blokiranja loop() petlje.
// ========================================================

// Pozovi ovo da počne otvaranje sefa
void pokreniOtvaranje() {
  if (servoStanje != SERVO_IDLE) return;  // Već u procesu
  servoStanje = SERVO_OTVARA;
  servoPozicija = POZ_ZAKLJUCANO;
  servoTimer = millis();
  sefOtkljucan = true;
  Serial.println("Otvaram bravu...");
}

// Poziva se svaki loop() — upravljanje servo stanjima
void upravljajServom() {
  if (servoStanje == SERVO_IDLE) return;

  unsigned long sad = millis();

  switch (servoStanje) {

    case SERVO_OTVARA:
      // Pomjeri 1 stepen nadole svaki SERVO_KORAK_MS
      if (sad - servoTimer >= SERVO_KORAK_MS) {
        servoTimer = sad;
        servoPozicija--;
        mojServo.write(servoPozicija);
        if (servoPozicija <= POZ_OTKLJUCANO) {
          servoStanje = SERVO_OTVOREN;
          servoTimer = sad;
          Serial.println("Sef je otvoren. Zatvaranje za 15 sekundi...");
        }
      }
      break;

    case SERVO_OTVOREN:
      // Čekaj 15 sekundi pa počni zatvaranje
      if (sad - servoTimer >= VRIJEME_OTVORENO) {
        servoStanje = SERVO_ZATVARA;
        servoTimer = sad;
        Serial.println("Zakljucavam bravu...");
      }
      break;

    case SERVO_ZATVARA:
      // Pomjeri 1 stepen nagore svaki SERVO_KORAK_MS
      if (sad - servoTimer >= SERVO_KORAK_MS) {
        servoTimer = sad;
        servoPozicija++;
        mojServo.write(servoPozicija);
        if (servoPozicija >= POZ_ZAKLJUCANO) {
          servoStanje = SERVO_IDLE;
          sefOtkljucan = false;
          uneseniPIN = "";
          Serial.println("\nSef je ponovo zakljucan. Cekam unos...");
        }
      }
      break;

    default: break;
  }
}

// ========================================================
//       ALARM (ZUJALICA) — NON-BLOCKING
//  Naizmjenični tonovi 1200Hz/900Hz bez delay-a.
// ========================================================

void pokreniAlarm(String poruka) {
  alarmAktivan = true;
  alarmPocetak = millis();
  zakaziSMS(poruka, TARGET_PHONE);  // SMS sa cooldown provjerom
}

void upravljajAlarmom() {
  if (!alarmAktivan) return;

  unsigned long proteklo = millis() - alarmPocetak;

  if (proteklo >= TRAJANJE_ALARMA) {
    noTone(BUZZER_PIN);
    alarmAktivan = false;
    return;
  }

  // Naizmjenični tonovi: 150ms ton → 50ms pauza → 150ms ton → 50ms pauza
  unsigned long faza = proteklo % 400;
  if      (faza < 150) tone(BUZZER_PIN, 1200);
  else if (faza < 200) noTone(BUZZER_PIN);
  else if (faza < 350) tone(BUZZER_PIN, 900);
  else                 noTone(BUZZER_PIN);
}

// ========================================================
//         SMS — NON-BLOCKING STATE MACHINE
//  Šalje AT komande u koracima koristeći millis() tajmere.
//  Loop() se ne blokira tokom slanja SMS-a.
// ========================================================

// Pozovi ovo da zakazes slanje SMS-a
void zakaziSMS(String poruka, String broj) {
  // Provjeri cooldown — ne šalji ako nije prošlo 60 sekundi
  if (millis() - vrijemePoslednjegSMS < SMS_COOLDOWN) {
    long preostalo = (SMS_COOLDOWN - (millis() - vrijemePoslednjegSMS)) / 1000;
    Serial.print("[SMS Cooldown] Sljedeci SMS za ");
    Serial.print(preostalo);
    Serial.println(" sekundi.");
    return;
  }

  // Ne šalji ako se već šalje drugi SMS
  if (smsStanje != SMS_IDLE) {
    Serial.println("[SMS] Vec se salje SMS, preskacemo.");
    return;
  }

  vrijemePoslednjegSMS = millis();
  smsPoruka = poruka;
  smsBroj = broj;
  smsStanje = SMS_CMGF;
  smsTimer = millis();

  A9Serial.println("AT+CMGF=1");
  Serial.println("[SMS] Pocinjem slanje...");
}

// Poziva se svaki loop() — korak po korak šalje SMS
void upravljajSMS() {
  if (smsStanje == SMS_IDLE) return;

  unsigned long sad = millis();

  switch (smsStanje) {

    case SMS_CMGF:  // Čeka 1s nakon AT+CMGF=1
      if (sad - smsTimer >= 1000) {
        A9Serial.print("AT+CMGS=\"");
        A9Serial.print(smsBroj);
        A9Serial.println("\"");
        smsStanje = SMS_CMGS;
        smsTimer = sad;
      }
      break;

    case SMS_CMGS:  // Čeka 1s nakon AT+CMGS
      if (sad - smsTimer >= 1000) {
        A9Serial.print(smsPoruka);
        smsStanje = SMS_PORUKA;
        smsTimer = sad;
      }
      break;

    case SMS_PORUKA:  // Čeka 500ms nakon teksta poruke
      if (sad - smsTimer >= 500) {
        A9Serial.write(26);  // Ctrl+Z — signal za slanje
        smsStanje = SMS_CTRL_Z;
        smsTimer = sad;
      }
      break;

    case SMS_CTRL_Z:  // Čeka 5s da GSM modul pošalje SMS
      if (sad - smsTimer >= 5000) {
        Serial.println("\n[Sistem]: SMS komanda za slanje izvrsena.");
        smsStanje = SMS_IDLE;
        smsPoruka = "";
        smsBroj = "";
      }
      break;

    default:
      smsStanje = SMS_IDLE;
      break;
  }
}

// ========================================================
//              POMOĆNE FUNKCIJE
// ========================================================

// Ultrazvučni senzor — mjeri distancu u cm
long izmjeriDistancu() {
  digitalWrite(TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);
  long trajanje = pulseIn(ECHO_PIN, HIGH, 10000);
  return trajanje * 0.034 / 2;
}

// Poredi dva 4-bajtna RFID UID-a
bool uporediUID(byte *procitani, byte *korisnicki) {
  for (byte i = 0; i < 4; i++) {
    if (procitani[i] != korisnicki[i]) return false;
  }
  return true;
}

// Kratki zvuk za uspješan pristup (250ms — jedini mali delay)
void zvukUspjeh() {
  tone(BUZZER_PIN, 1000, 250);
  delay(250);
}

// Kratki zvuk za grešku (360ms ukupno)
void zvukGreska() {
  for (int i = 0; i < 3; i++) {
    tone(BUZZER_PIN, 800, 80);
    delay(120);
  }
}