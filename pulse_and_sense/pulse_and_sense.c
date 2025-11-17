// Dieses Skript steuert einen Puls auf einem GPIO-Pin und misst die Reaktion über den ADC.
// Der Puls kann asynchron gestartet werden, während die ADC-Messungen fortgesetzt werden.
// Die Pulsdauer kann über die serielle Schnittstelle eingestellt werden. Die Pulsstärke liegt bei 100%.

#include <stdio.h>
#include <stdlib.h>
#include "pico/stdlib.h"
#include "hardware/gpio.h"
#include "hardware/adc.h"
#include "pico/time.h"

#define PULSE_PIN 15       // GPIO für den Puls
#define DEFAULT_PULSE_MS 100
#define NUM_SAMPLES 1000
#define THRESHOLD 400
#define VperDev 0.806

typedef struct {
    bool active;
    uint32_t start_us;
    uint32_t duration_ms;
} PulseState;

int main() {
    stdio_init_all();

    // --- PWM/Puls Setup ---
    gpio_init(PULSE_PIN);
    gpio_set_dir(PULSE_PIN, GPIO_OUT);
    gpio_put(PULSE_PIN, 0);

    int pulse_ms = DEFAULT_PULSE_MS;
    char input_buffer[32];
    int input_index = 0;

    PulseState pulse = { .active = false, .start_us = 0, .duration_ms = 0 };

    // --- ADC Setup ---
    adc_init();
    adc_gpio_init(26);
    adc_select_input(0);

    printf("Bereit! Gib eine Pulsdauer in ms ein (z.B. 40) und drücke Enter.\n");
    printf("Nur Enter = Wiederhole letzten Puls (%d ms)\n", pulse_ms);

    while (true) {
        // --- Eingabe prüfen ---
        int c = getchar_timeout_us(0);
        if (c != PICO_ERROR_TIMEOUT) {
            if (c == '\r' || c == '\n') {
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

                // Puls asynchron starten
                pulse.active = true;
                pulse.start_us = time_us_32();
                pulse.duration_ms = pulse_ms;
                gpio_put(PULSE_PIN, 1);
                printf("Puls gestartet!\n");
            } else if (c >= '0' && c <= '9' && input_index < (int)sizeof(input_buffer) - 1) {
                input_buffer[input_index++] = (char)c;
            } else if (c == '\b' && input_index > 0) {
                input_index--;
            }
        }

        // --- Puls beenden, falls Zeit abgelaufen ---
        if (pulse.active && time_us_32() - pulse.start_us >= pulse.duration_ms * 1000) {
            gpio_put(PULSE_PIN, 0);
            pulse.active = false;
            printf("Puls beendet!\n");
        }

        // --- ADC-Messung ---
        uint16_t samples[NUM_SAMPLES];
        uint32_t timestamps[NUM_SAMPLES];
        for (int i = 0; i < NUM_SAMPLES; i++) {
            timestamps[i] = time_us_32();
            samples[i] = adc_read();
        }

        // --- Pulsanalyse ---
        /* int pulse_start = -1, pulse_end = -1;
        for (int i = 1; i < NUM_SAMPLES; i++) {
            if (samples[i-1] < THRESHOLD && samples[i] >= THRESHOLD) {
                pulse_start = i;
                break;
            }
        }
        if (pulse_start != -1) {
            for (int i = pulse_start + 1; i < NUM_SAMPLES; i++) {
                if (samples[i-1] >= THRESHOLD && samples[i] < THRESHOLD) {
                    pulse_end = i;
                    break;
                }
            }
        }

        if (pulse_start != -1 && pulse_end != -1) {
            uint32_t sum_an = 0;
            for (int i = pulse_start; i < pulse_end; i++) sum_an += samples[i];
            float avg_an = (float)sum_an / (pulse_end - pulse_start) * VperDev;

            uint32_t sum_aus = 0;
            for (int i = pulse_end; i < NUM_SAMPLES; i++) sum_aus += samples[i];
            float avg_aus = (float)sum_aus / (NUM_SAMPLES - pulse_end) * VperDev;

            uint32_t pulse_time_us = timestamps[pulse_end] - timestamps[pulse_start];

            printf("%d, %d, %d, %.2f, %.2f, %lu\n",
                   pulse_start, pulse_end, pulse_end - pulse_start,
                   avg_an, avg_aus, pulse_time_us);
        } else {
            printf("Kein Puls erkannt, 0,0,0,0,0\n");
        } */

        // --- Pulsanalyse (einfach: Mittelwert, Max, Min) ---
        uint32_t sum = 0;
        uint16_t max_val = 0;
        uint16_t min_val = 4095;  // 12-Bit ADC

        for (int i = 0; i < NUM_SAMPLES; i++) {
            uint16_t val = samples[i];
            sum += val;
            if (val > max_val) max_val = val;
            if (val < min_val) min_val = val;
        }

        float avg = (float)sum / NUM_SAMPLES * VperDev;
        float max_v = (float)max_val * VperDev;
        float min_v = (float)min_val * VperDev;

        printf("%.2f, %.2f, %.2f\n", avg, max_v, min_v);


        sleep_ms(1); // kleine Pause, CPU schonen
    }
}
