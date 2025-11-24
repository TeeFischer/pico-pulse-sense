// pwm-pulse.c
// Asynchrone Pulssteuerung + möglichst schnelle ADC-Messung auf Core1.

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "pico/stdlib.h"
#include "pico/multicore.h"
#include "hardware/adc.h"
#include "hardware/pwm.h"

#define PULSE_PIN 15           // GPIO-Pin für den Puls
#define ADC_PIN 26             // GPIO26 -> ADC0
#define DEFAULT_PULSE_MS 100   // Standard-Pulsdauer in ms

// ADC-Referenz und Skalierung (12-bit ADC auf RP2040 -> 0..4095)
#define VREF 3300.0f
#define ADC_MAX 4095.0f
#define VperDev (VREF / ADC_MAX)

// Core1: schnelle ADC-Schleife; sendet (timestamp_us, sample) per FIFO an Core0.
void adc_core1() {
    // ADC initialisieren
    adc_init();
    adc_gpio_init(ADC_PIN); // GPIO26 -> ADC0
    adc_select_input(0);

    while (true) {
        uint32_t sample = adc_read(); // 12-bit (0..4095)
        uint32_t t = time_us_32();

        // Wenn FIFO write-ready ist, sende Timestamp und Sample.
        // Falls FIFO voll ist, verwerfe das Sample (so bleibt Sampling maximal schnell).
        if (multicore_fifo_wready()) {
            multicore_fifo_push_blocking(t);
            multicore_fifo_push_blocking(sample);
        }
        sleep_us(250); 
    }
}

int main() {
    stdio_init_all();

    // --- PWM Setup ---
    gpio_set_function(PULSE_PIN, GPIO_FUNC_PWM);
    uint slice_num = pwm_gpio_to_slice_num(PULSE_PIN);
    uint channel = pwm_gpio_to_channel(PULSE_PIN);

    // PWM-Frequenz einstellen
    // Systemtakt normalerweise 125 MHz
    const float sys_clk = 125000000;
    float freq = 100.0f;                 // Gewünschte Puls-Frequenz
    // Clock divider: z.B. CLKDIV = 125, um Zählerfrequenz 1 MHz zu erhalten
    float clkdiv = 125.0f;

    // Wrap Wert berechnen: Wrap = (PWM_Takt / freq) - 1
    uint16_t wrap = (uint16_t)((sys_clk / clkdiv) / freq) - 1;

    pwm_set_wrap(slice_num, wrap);
    pwm_set_clkdiv(slice_num, 1.0f);
    pwm_set_enabled(slice_num, false);

    // Pulse-Pin konfigurieren
    gpio_init(PULSE_PIN);
    gpio_set_dir(PULSE_PIN, GPIO_OUT);
    gpio_put(PULSE_PIN, 0);

    // Starte ADC-Thread auf Core1
    multicore_launch_core1(adc_core1);

    int pulse_ms = DEFAULT_PULSE_MS;
    char input_buffer[32];
    int input_index = 0;

    // pulse_end_us == 0 -> kein aktiver Puls
    uint32_t pulse_end_us = 0;

    printf("Bereit! Gib eine Pulsdauer in ms ein (z.B. 40) und drücke Enter.\n");
    printf("Nur Enter = Wiederhole letzten Puls (%d ms)\n", pulse_ms);

    while (true) {
        // 1) ADC-Daten aus FIFO lesen und ausgeben (so oft wie Core0 hinterherkommt)
        while (multicore_fifo_rvalid()) {
            uint32_t t = multicore_fifo_pop_blocking();
            uint32_t sample = multicore_fifo_pop_blocking();
            float voltage = (float)sample * VperDev;
            // Ausgabe: Zeitpunkt (us), Spannung (V)
            printf("%llu, %.3f\n", (unsigned long long)t, voltage);
        }

        // 2) Eingabe verarbeiten (nicht-blockierend)
        int c = getchar_timeout_us(0);
        if (c != PICO_ERROR_TIMEOUT) {
            if (c == '\r' || c == '\n') { // Enter
                input_buffer[input_index] = '\0';

                if (input_index > 0) {
                    int new_value = atoi(input_buffer);
                    if (new_value > 0) {
                        pulse_ms = new_value;
                        printf("Neue Pulsdauer: %d ms\n", pulse_ms);
                    } else {
                        printf("Ungültige Eingabe. Verwende letzten Wert: %d ms\n", pulse_ms);
                    }
                    input_index = 0;
                } else {
                    printf("Wiederhole letzten Puls (%d ms)\n", pulse_ms);
                }

                // Asynchronen Puls starten: Pin setzen und Endzeit merken
                printf("Puls (asynchron)!\n");
                gpio_put(PULSE_PIN, 1);
                pulse_end_us = time_us_32() + (uint32_t)pulse_ms * 1000u;

            } else if (c >= '0' && c <= '9' && input_index < (int)sizeof(input_buffer) - 1) {
                input_buffer[input_index++] = (char)c;
            } else if ((c == '\b' || c == 127) && input_index > 0) {
                // Backspace (127 auf manchen Terminals)
                input_index--;
            }
        }

        // 3) Pulse ausschalten, wenn Zeit abgelaufen (asynchron, non-blocking)
        if (pulse_end_us != 0 && (int32_t)(time_us_32() - pulse_end_us) >= 0) {
            gpio_put(PULSE_PIN, 0);
            pulse_end_us = 0;
            printf("Puls fertig.\n");
        }

        // Kurze Pause, um Core0 nicht 100% zu belegen; ADC läuft auf Core1 weiterhin maximal schnell.
        sleep_ms(1);
    }

    return 0;
}
