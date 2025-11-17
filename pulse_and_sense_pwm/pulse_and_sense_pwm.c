#include <stdio.h>
#include <stdlib.h>
#include "pico/stdlib.h"
#include "hardware/gpio.h"
#include "hardware/adc.h"
#include "hardware/pwm.h"
#include "pico/time.h"

#define PULSE_PIN 15
#define PULSE_DURATION_MS 100   // Fixe Pulsdauer
#define PWM_FREQ_HZ 1000        // PWM-Frequenz (1 kHz)
#define NUM_SAMPLES 1000
#define VperDev 0.806

typedef struct {
    bool active;
    uint32_t start_us;
    uint slice_num;
} PulseState;

int main() {
    stdio_init_all();

    // --- PWM Setup ---
    gpio_set_function(PULSE_PIN, GPIO_FUNC_PWM);
    uint slice_num = pwm_gpio_to_slice_num(PULSE_PIN);
    uint channel = pwm_gpio_to_channel(PULSE_PIN);

    // PWM-Frequenz einstellen
    // PWM-Frequenz = clock / (wrap + 1) / divider
    // z. B. 125 MHz / (125000 / 1) = 1 kHz
    uint32_t clock = 125000000; // 125 MHz default clock
    uint32_t wrap = 125000;     // bei divider=1 -> 1 kHz
    pwm_set_wrap(slice_num, wrap);
    pwm_set_clkdiv(slice_num, 1.0f);
    pwm_set_enabled(slice_num, false);

    char input_buffer[32];
    int input_index = 0;
    int duty_percent = 0;

    PulseState pulse = { .active = false, .start_us = 0, .slice_num = slice_num };

    // --- ADC Setup ---
    adc_init();
    adc_gpio_init(26);
    adc_select_input(0);

    printf("Bereit! Gib PWM-Stärke in %% ein (z.B. 40) und drücke Enter.\n");
    printf("Pulsdauer ist fix: %d ms\n", PULSE_DURATION_MS);

    while (true) {
        // --- Eingabe prüfen ---
        int c = getchar_timeout_us(0);
        if (c != PICO_ERROR_TIMEOUT) {
            if (c == '\r' || c == '\n') {
                input_buffer[input_index] = '\0';
                if (input_index > 0) {
                    int new_value = atoi(input_buffer);
                    if (new_value >= 0 && new_value <= 100) {
                        duty_percent = new_value;
                        printf("Neue PWM-Stärke: %d%%\n", duty_percent);
                    } else {
                        printf("Ungültig! Wert zwischen 0 und 100.\n");
                    }
                    input_index = 0;
                }

                // PWM starten
                if (duty_percent > 0) {
                    uint16_t level = (wrap * duty_percent) / 100;
                    pwm_set_chan_level(slice_num, channel, level);
                    pwm_set_enabled(slice_num, true);

                    pulse.active = true;
                    pulse.start_us = time_us_32();

                    printf("PWM %d%% gestartet (%d ms)\n", duty_percent, PULSE_DURATION_MS);
                } else {
                    printf("Duty=0 -> kein Puls.\n");
                }
            } else if (c >= '0' && c <= '9' && input_index < (int)sizeof(input_buffer) - 1) {
                input_buffer[input_index++] = (char)c;
            } else if (c == '\b' && input_index > 0) {
                input_index--;
            }
        }

        // --- Puls beenden ---
        if (pulse.active && (time_us_32() - pulse.start_us) >= PULSE_DURATION_MS * 1000) {
            pwm_set_enabled(slice_num, false);
            pulse.active = false;
            printf("PWM beendet!\n");
        }

        // --- ADC-Messung ---
        uint16_t samples[NUM_SAMPLES];
        uint32_t timestamps[NUM_SAMPLES];
        for (int i = 0; i < NUM_SAMPLES; i++) {
            timestamps[i] = time_us_32();
            samples[i] = adc_read();
        }

        // --- Messwerte auswerten ---
        uint32_t sum = 0;
        uint16_t max_val = 0;
        uint16_t min_val = 4095;

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

        sleep_ms(1);
    }
}
