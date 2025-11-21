#include <stdio.h>
#include <stdlib.h>
#include "pico/stdlib.h"
#include "hardware/gpio.h"
#include "hardware/adc.h"
#include "hardware/pwm.h"
#include "pico/time.h"

#define PULSE_PIN 15
#define SAMPLES_PER_STEP 1500    // Anzahl Messungen pro Duty-Cycle
#define VperDev 0.806f           // Umrechnung ADC->Volt (abhängig vom ADC-Setup)

int main() {
    stdio_init_all();

    // --- Startsignal ---
    printf("Bereit. Drücke Enter, um den PWM-Sweep zu starten...\n");
    while (true) {
        int c = getchar_timeout_us(0);
        if (c == '\r' || c == '\n') {
            break;
        }
    }
    printf("Starte Sweep...\n");

    // --- PWM Setup ---
    gpio_set_function(PULSE_PIN, GPIO_FUNC_PWM);
    uint slice_num = pwm_gpio_to_slice_num(PULSE_PIN);
    uint channel = pwm_gpio_to_channel(PULSE_PIN);

    const float sys_clk = 125000000;
    float freq = 1000.0f;                // 1 kHz PWM
    float clkdiv = 125.0f;               // 1 MHz PWM-Takt
    uint16_t wrap = (uint16_t)((sys_clk / clkdiv) / freq) - 1;

    pwm_set_wrap(slice_num, wrap);
    pwm_set_clkdiv(slice_num, clkdiv);
    pwm_set_enabled(slice_num, true);

    // --- ADC Setup ---
    adc_init();
    adc_gpio_init(26);
    adc_select_input(0);

    // --- Tabellenkopf ---
    printf("PWM_Duty(%%), Mittelwert(V)\n");

    // --- PWM Sweep ---
    for (int duty = 0; duty <= 100; duty++) {

        uint16_t level = (wrap * duty) / 100;
        pwm_set_chan_level(slice_num, channel, level);

        // kurze Stabilisationszeit
        sleep_ms(20);

        // --- Mittelwertbildung ---
        uint64_t sum = 0;

        for (int i = 0; i < SAMPLES_PER_STEP; i++) {
            uint16_t sample = adc_read();
            sum += sample;
        }

        float avg_adc = (float)sum / SAMPLES_PER_STEP;
        float avg_voltage = avg_adc * VperDev;

        // --- Tabellenzeile ausgeben ---
        printf("%d, %.3f\n", duty, avg_voltage);
    }

    // PWM ausschalten
    pwm_set_enabled(slice_num, false);
    gpio_set_function(PULSE_PIN, GPIO_FUNC_SIO);
    gpio_set_dir(PULSE_PIN, GPIO_OUT);
    gpio_put(PULSE_PIN, 0);

    printf("\nSweep beendet!\n");

    while (true) {
        tight_loop_contents();
    }
}
