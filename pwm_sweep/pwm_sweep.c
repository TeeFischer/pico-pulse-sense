#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "pico/stdlib.h"
#include "hardware/gpio.h"
#include "hardware/adc.h"
#include "hardware/pwm.h"
#include "hardware/flash.h"
#include "hardware/sync.h"
#include "pico/time.h"

#define PULSE_PIN 15
#define SAMPLES_PER_STEP 1500
#define VperDev 0.806f
#define MAX_DUTY_CYCLE 255

#define FLASH_TARGET_OFFSET (PICO_FLASH_SIZE_BYTES - 4096)

// -------------------------
//  SICHERER PWM-START
// -------------------------
void init_safe_pwm_pin() {
    gpio_set_function(PULSE_PIN, GPIO_FUNC_SIO);
    gpio_set_dir(PULSE_PIN, GPIO_OUT);
    gpio_put(PULSE_PIN, 0);     // Garantiert aus
}

// -------------------------
//  PWM-SWEEP AUSGELAGERT
// -------------------------
void run_pwm_sweep(float *result_array) {

    const float sys_clk = 125000000;
    float freq = 1000.0f;
    float clkdiv = 125.0f;
    uint16_t wrap = (uint16_t)((sys_clk / clkdiv) / freq) - 1;

    uint slice_num = pwm_gpio_to_slice_num(PULSE_PIN);
    uint channel   = pwm_gpio_to_channel(PULSE_PIN);

    // Erst hier PWM aktivieren!
    gpio_set_function(PULSE_PIN, GPIO_FUNC_PWM);
    pwm_set_wrap(slice_num, wrap);
    pwm_set_clkdiv(slice_num, clkdiv);
    pwm_set_enabled(slice_num, true);

    for (int duty = 0; duty <= MAX_DUTY_CYCLE; duty++) {

        uint16_t level = (wrap * duty) / MAX_DUTY_CYCLE;
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
    gpio_set_function(PULSE_PIN, GPIO_FUNC_SIO);
    gpio_set_dir(PULSE_PIN, GPIO_OUT);
    gpio_put(PULSE_PIN, 0);  // DEAD-SAFE
}

// -------------------------
// FLASH-Speicher-Funktionen
// -------------------------
void save_results_to_flash(float *values, size_t count) {

    uint32_t ints = save_and_disable_interrupts();

    flash_range_erase(FLASH_TARGET_OFFSET, 4096);

    flash_range_program(
        FLASH_TARGET_OFFSET,
        (const uint8_t*)values,
        count * sizeof(float)
    );

    restore_interrupts(ints);
}

void load_results_from_flash(float *buffer, size_t count) {
    const uint8_t *flash_ptr = (const uint8_t *)(XIP_BASE + FLASH_TARGET_OFFSET);
    memcpy(buffer, flash_ptr, count * sizeof(float));
}


// -------------------------
//            MAIN
// -------------------------
int main() {
    stdio_init_all();

    // PIN ZUERST ABSICHERN!
    init_safe_pwm_pin();

    // ADC Setup
    adc_init();
    adc_gpio_init(26);
    adc_select_input(0);

    static float sweep_results[MAX_DUTY_CYCLE + 1];

    while (true) {

        printf("Bereit. Drücke Enter, um PWM-Sweep zu starten...\n");
        while (true) {
            int c = getchar_timeout_us(0);
            if (c == '\r' || c == '\n') break;
        }

        printf("Starte Sweep...\n");

        run_pwm_sweep(sweep_results);
        save_results_to_flash(sweep_results, MAX_DUTY_CYCLE + 1);

        printf("Sweep beendet! Werte gespeichert.\n");

        for (int i = 0; i <= MAX_DUTY_CYCLE; i++)
            printf("%d, %.3f\n", i, sweep_results[i]);
    }
}
