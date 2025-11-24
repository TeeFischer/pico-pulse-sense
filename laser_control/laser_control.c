#define timestamping true

#include <stdio.h>
#include "pico/stdlib.h"
#include "hardware/gpio.h"
#include "hardware/adc.h"
#include "hardware/pwm.h" // PWM-Header hinzufügen
#include "pico/time.h" // Zeitfunktionen hinzufügen
#include <string.h>
#include <stdbool.h>
#include <stdint.h>
#include "hardware/flash.h"
#include "hardware/sync.h"

#define NUM_SAMPLES 20
#define THRESHOLD 200 // Schwellwert für Flankenerkennung 4096 enspricht 3.3V
// 600mV entsprechen 744 ADC-Wert bei 12 Bit Auflösung
#define SAMPLES_PER_STEP 1500
#define VperDev 0.806f
#define START_DUTY_CYCLE 0.05f
#define MAX_DUTY_CYCLE 100

#define FLASH_TARGET_OFFSET (PICO_FLASH_SIZE_BYTES - 4096)

#define pwm_min 0.01f // Untere Grenze für PWM
#define pwm_max 0.10f // Obere Grenze für PWM
#define pwm_step 0.01f // Schrittweite für PWM-Anpassung

#define lower_avg_threshold 450.0f // Untere Grenze für PWM-Regelung
#define upper_avg_threshold 500.0f // Obere Grenze für PWM-Regelung

#define PWM_GPIO 15     // Wähle einen freien GPIO, z.B. GPIO15
#define PWM_WRAP 4095   // 12 Bit PWM-Auflösung
//#define PWM_LEVEL 480  // Duty Cycle (30/255)
#define PWM_LEVEL 1606  // Duty Cycle (100/255)

// Funktionsprototypen einfügen
void init_safe_pwm_pin(void);
void run_pwm_sweep(void);
void pwm_sweep(float *result_array);
void save_results_to_flash(float *values, size_t count);
bool load_results_from_flash(float *buffer, size_t count);
void laser_on(void);
void laser_off(void);
void set_pwm_from_float(float pwm);

// Flash table format
#define FLASH_TABLE_MAGIC 0x50574D54u // 'PWMT'
typedef struct __attribute__((packed)) {
    uint32_t magic;
    uint32_t count; // number of float entries
    uint32_t reserved;
    uint32_t checksum;
} flash_table_header_t;

static uint32_t compute_checksum(const uint8_t *data, size_t len) {
    // simple 32-bit additive checksum
    uint32_t sum = 0;
    for (size_t i = 0; i < len; ++i) sum += data[i];
    return sum;
}

// Globals to manage PWM state from multiple functions
static uint pwm_slice = 0;
static uint pwm_channel = 0;
static uint16_t pwm_wrap_g = 0;
static float pwm_clkdiv_g = 0.0f;
static float current_pwm = START_DUTY_CYCLE;
static bool pwm_enabled = false;


int main(void) {
    stdio_init_all();

    // PIN ZUERST ABSICHERN!
    init_safe_pwm_pin();

    // ADC Setup
    adc_init();
    adc_gpio_init(26);
    adc_select_input(0);

    const float sys_clk = 125000000;
    float freq = 1000.0f;
    float clkdiv = 125.0f;
    uint16_t wrap = (uint16_t)((sys_clk / clkdiv) / freq) - 1;
    // store globals for helper functions
    pwm_slice = pwm_gpio_to_slice_num(PWM_GPIO);
    pwm_channel = pwm_gpio_to_channel(PWM_GPIO);
    pwm_wrap_g = wrap;
    pwm_clkdiv_g = clkdiv;
    
    float pwm = START_DUTY_CYCLE;

    sleep_ms(1000); // Warten bis USB-Serial bereit

    uint16_t samples[NUM_SAMPLES];

    static float flash_table[MAX_DUTY_CYCLE + 1];
    bool has_flash_table = false;

    // serial command buffer
    char cmd_buf[64];
    size_t cmd_idx = 0;

    bool startup_done = false;

    while (!startup_done) {
        // Gebe jede Sekunde eine Nachricht aus
        printf("Commands: an, aus, sweep\n");
        sleep_ms(1000);  // Eine Sekunde warten

        // Warten auf Eingabe von Enter (Carriage Return oder Line Feed)
        int c = getchar_timeout_us(0);
        if (c == '\r' || c == '\n') {
            startup_done = true;  // Die Schleife beenden, wenn Enter gedrückt wurde
        }
    }

    // Versuche Tabelle aus Flash zu laden und zu validieren
    if (load_results_from_flash(flash_table, MAX_DUTY_CYCLE + 1)) {
        has_flash_table = true;
        printf("Flash-Tabelle geladen.\n");
    } else {
        printf("Keine gültige Flash-Tabelle gefunden. Starte Sweep...\n");
        run_pwm_sweep();
        // Nach Sweep erneut laden
        if (load_results_from_flash(flash_table, MAX_DUTY_CYCLE + 1)) {
            has_flash_table = true;
            printf("Tabelle nach Sweep geladen.\n");
        } else {
            printf("Warnung: Nach Sweep konnte die Tabelle nicht geladen werden. Verwende Fallback-Schwellen.\n");
            has_flash_table = false;
        }
    }

    while (1) {   // Dauerschleife

        // Messungenen durchführen
        for (int i = 0; i < NUM_SAMPLES; i++) {
            samples[i] = adc_read();
        }

        // Berechnung der durchschnittlichen Amplitude
        float avg_an = 0.0f;
        float sum_an = 0.0f;
        for (int i = 0; i < NUM_SAMPLES; i++) {
            sum_an += samples[i];
        }
        avg_an = (float)sum_an / NUM_SAMPLES * VperDev;

        // String-Variable, die die formatierte Ausgabe speichert
        char print_message[100];  // Ein Puffer, um die Nachricht zu speichern

        // === Regelung: Wenn Tabelle vorhanden, vergleiche mit Erwartungswert ===
        if (has_flash_table) {
            // map current pwm (0..1) to index 0..MAX_DUTY_CYCLE
            int idx = (int)(pwm * (float)MAX_DUTY_CYCLE + 0.5f);
            if (idx < 0) idx = 0; if (idx > MAX_DUTY_CYCLE) idx = MAX_DUTY_CYCLE;
            float expected = flash_table[idx];
            const float response_tolerance = 25.0f; // einstellbar (ADC units*VperDev)

            if (avg_an < expected - response_tolerance) {
                pwm += pwm_step;
                if (pwm > pwm_max) pwm = pwm_max;
                uint16_t new_level = (uint16_t)(wrap * pwm);
                pwm_set_gpio_level(PWM_GPIO, new_level);
                snprintf(print_message, sizeof(print_message), "%.2f, PWM erhöht (gemessen %.2f < erwartet %.2f)\n", pwm, avg_an, expected);
                // printf("%.2f, PWM erhöht (gemessen %.2f < erwartet %.2f)\n", pwm, avg_an, expected);
            } else if (avg_an > expected + response_tolerance) {
                pwm -= pwm_step;
                if (pwm < pwm_min) pwm = pwm_min;
                uint16_t new_level = (uint16_t)(wrap * pwm);
                pwm_set_gpio_level(PWM_GPIO, new_level);
                // Verwende snprintf, um die formatierte Nachricht in den String zu schreiben
                snprintf(print_message, sizeof(print_message), "%.2f, PWM verringert (gemessen %.2f > erwartet %.2f)\n", pwm, avg_an, expected);
                //printf("%.2f, PWM verringert (gemessen %.2f > erwartet %.2f)\n", pwm, avg_an, expected);
            } else {
                snprintf(print_message, sizeof(print_message), "%.2f, PWM bleibt (gemessen %.2f ≈ erwartet %.2f)\n", pwm, avg_an, expected);
                //printf("%.2f, PWM bleibt (gemessen %.2f ≈ erwartet %.2f)\n", pwm, avg_an, expected);
            }
        } else {
            // Fallback: altes Schwellenmodell
            if (avg_an < lower_avg_threshold) {
                pwm += pwm_step;
                if (pwm > pwm_max) pwm = pwm_max;
                uint16_t new_level = (uint16_t)(wrap * pwm);
                pwm_set_gpio_level(PWM_GPIO, new_level);
                //printf("%.2f, PWM erhöht\n", pwm);
                snprintf(print_message, sizeof(print_message), "%.2f, PWM erhöht\n", pwm);
            }
            else if (avg_an > upper_avg_threshold) {
                pwm -= pwm_step;
                if (pwm < pwm_min) pwm = pwm_min;
                uint16_t new_level = (uint16_t)(wrap * pwm);
                pwm_set_gpio_level(PWM_GPIO, new_level);
                //printf("%.2f, PWM verringert\n", pwm);
                snprintf(print_message, sizeof(print_message), "%.2f, PWM verringert\n", pwm);
            } else {
                //printf("%.2f, PWM bleibt\n", pwm);
                snprintf(print_message, sizeof(print_message), "%.2f, PWM bleibt\n", pwm);
            }
        }

        // Ausgabe der Ergebnisse
        //printf("%s", print_message);
        printf("%lu, %.2f, %.2f\n", time_us_32(), avg_an, pwm);

        // --- Serielle Eingabe verarbeiten (nicht-blockierend) ---
        int ch = getchar_timeout_us(0);
        if (ch >= 0) {
            if (ch == '\r' || ch == '\n') {
                if (cmd_idx > 0) {
                    cmd_buf[cmd_idx] = '\0';
                    // process command
                    if (strcmp(cmd_buf, "an") == 0) {
                        laser_on();
                        printf("OK: Laser_an\n");
                    } else if (strcmp(cmd_buf, "aus") == 0) {
                        laser_off();
                        printf("OK: Laser_aus\n");
                    } else if (strcmp(cmd_buf, "sweep") == 0 || strcmp(cmd_buf, "sweep") == 0) {
                        printf("Starte manuellen Sweep...\n");
                        run_pwm_sweep();
                        // reload table
                        if (load_results_from_flash(flash_table, MAX_DUTY_CYCLE + 1)) {
                            has_flash_table = true;
                            printf("Tabelle nach manuellem Sweep geladen.\n");
                        } else {
                            has_flash_table = false;
                            printf("Fehler: Tabelle nach Sweep nicht geladen.\n");
                        }
                    } else {
                        printf("Unbekannter Befehl: %s\n", cmd_buf);
                    }
                    cmd_idx = 0;
                }
            } else {
                if (cmd_idx + 1 < sizeof(cmd_buf)) {
                    cmd_buf[cmd_idx++] = (char)ch;
                } else {
                    // overflow - reset
                    cmd_idx = 0;
                }
            }
        }
    }
}

// --- PWM control helpers ---
void set_pwm_from_float(float pwm) {
    current_pwm = pwm;
    if (pwm_enabled) {
        uint16_t level = (uint16_t)((float)pwm_wrap_g * current_pwm);
        pwm_set_chan_level(pwm_slice, pwm_channel, level);
    }
}

void laser_on(void) {
    // configure pin for PWM and enable slice
    gpio_set_function(PWM_GPIO, GPIO_FUNC_PWM);
    pwm_set_wrap(pwm_slice, pwm_wrap_g);
    pwm_set_clkdiv(pwm_slice, pwm_clkdiv_g);
    uint16_t level = (uint16_t)((float)pwm_wrap_g * current_pwm);
    pwm_set_chan_level(pwm_slice, pwm_channel, level);
    pwm_set_enabled(pwm_slice, true);
    pwm_enabled = true;
}

void laser_off(void) {
    // disable PWM and make pin safe (SIO low)
    pwm_set_enabled(pwm_slice, false);
    pwm_enabled = false;
    gpio_set_function(PWM_GPIO, GPIO_FUNC_SIO);
    gpio_set_dir(PWM_GPIO, GPIO_OUT);
    gpio_put(PWM_GPIO, 0);
}

// -------------------------
//  SICHERER PWM-START
// -------------------------
void init_safe_pwm_pin() {
    gpio_set_function(PWM_GPIO, GPIO_FUNC_SIO);
    gpio_set_dir(PWM_GPIO, GPIO_OUT);
    gpio_put(PWM_GPIO, 0);     // Garantiert aus
}

// -------------------------
//  RUN PWM-SWEEP
// -------------------------
void run_pwm_sweep(){
    printf("Bereit. Drücke Enter, um PWM-Sweep zu starten...\n");
    while (true) {
        int c = getchar_timeout_us(0);
        if (c == '\r' || c == '\n') break;
    }

    printf("Starte Sweep...");

    static float sweep_results[MAX_DUTY_CYCLE + 1];

    pwm_sweep(sweep_results);

    printf("Speichere Daten...\n");
    save_results_to_flash(sweep_results, MAX_DUTY_CYCLE + 1);

    printf("Sweep beendet! Werte gespeichert.\n");

    for (int i = 0; i <= MAX_DUTY_CYCLE; i++)
        printf("%d, %.3f\n", i, sweep_results[i]);
}

// -------------------------
//  PWM-SWEEP AUSGELAGERT
// -------------------------
void pwm_sweep(float *result_array) {

    const float sys_clk = 125000000;
    float freq = 1000.0f;
    float clkdiv = 125.0f;
    uint16_t _wrap = (uint16_t)((sys_clk / clkdiv) / freq) - 1;

    uint slice_num = pwm_gpio_to_slice_num(PWM_GPIO);
    uint channel   = pwm_gpio_to_channel(PWM_GPIO);

    // Erst hier PWM aktivieren!
    gpio_set_function(PWM_GPIO, GPIO_FUNC_PWM);
    pwm_set_wrap(slice_num, _wrap);
    pwm_set_clkdiv(slice_num, clkdiv);
    pwm_set_enabled(slice_num, true);

    for (int duty = 0; duty <= MAX_DUTY_CYCLE; duty++) {

        uint16_t level = (_wrap * duty) / MAX_DUTY_CYCLE;
        pwm_set_chan_level(slice_num, channel, level);

        sleep_ms(20);

        uint64_t sum = 0;
        for (int i = 0; i < SAMPLES_PER_STEP; i++)
            sum += adc_read();

        float avg_adc = (float)sum / SAMPLES_PER_STEP;
        result_array[duty] = avg_adc * VperDev;
    }

    // Nach Sweep PWM wieder komplett abschalten
    pwm_set_enabled(slice_num, false);
    gpio_set_function(PWM_GPIO, GPIO_FUNC_SIO);
    gpio_set_dir(PWM_GPIO, GPIO_OUT);
    gpio_put(PWM_GPIO, 0);  // DEAD-SAFE
}

// -------------------------
// FLASH-Speicher-Funktionen
// -------------------------
void save_results_to_flash(float *values, size_t count) {

    uint32_t ints = save_and_disable_interrupts();

    // prepare header
    flash_table_header_t header;
    header.magic = FLASH_TABLE_MAGIC;
    header.count = (uint32_t)count;
    header.reserved = 0;
    // checksum over float data
    uint32_t checksum = compute_checksum((const uint8_t*)values, count * sizeof(float));
    header.checksum = checksum;

    flash_range_erase(FLASH_TARGET_OFFSET, 4096);

    // write header
    flash_range_program(FLASH_TARGET_OFFSET, (const uint8_t*)&header, sizeof(header));

    // write payload (floats) immediately after header
    flash_range_program(FLASH_TARGET_OFFSET + sizeof(header), (const uint8_t*)values, count * sizeof(float));

    restore_interrupts(ints);
}

bool load_results_from_flash(float *buffer, size_t count) {
    const uint8_t *flash_ptr = (const uint8_t *)(XIP_BASE + FLASH_TARGET_OFFSET);

    // Try new header format first
    if (sizeof(flash_table_header_t) <= 4096) {
        const flash_table_header_t *h = (const flash_table_header_t *)flash_ptr;
        if (h->magic == FLASH_TABLE_MAGIC && h->count == (uint32_t)count) {
            const uint8_t *data_ptr = flash_ptr + sizeof(flash_table_header_t);
            uint32_t cs = compute_checksum(data_ptr, count * sizeof(float));
            if (cs == h->checksum) {
                memcpy(buffer, data_ptr, count * sizeof(float));
                return true;
            }
        }
    }

    // Legacy fallback: try to interpret start of flash region as raw float array
    // Validate values are in plausible range (>=0 and < 5000)
    const float *maybe_floats = (const float *)flash_ptr;
    bool ok = true;
    for (size_t i = 0; i < count; ++i) {
        float v = maybe_floats[i];
        if (!(v >= 0.0f && v < 5000.0f)) { ok = false; break; }
    }
    if (ok) {
        memcpy(buffer, maybe_floats, count * sizeof(float));
        return true;
    }

    return false;
}
