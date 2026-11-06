<script setup lang="ts">
import { SPANS, type Span } from '@shared/spans'

// The span a card's spend tables cover, as a row of buttons.
const model = defineModel<Span>({ required: true })
</script>

<template>
  <div class="span-picker" role="group" aria-label="span">
    <button
      v-for="s in SPANS"
      :key="s"
      type="button"
      :class="{ on: model === s }"
      :aria-pressed="model === s"
      @click="model = s"
    >{{ s }}</button>
  </div>
</template>

<style scoped>
.span-picker {
  display: inline-flex;
  flex-wrap: wrap;
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
}

button {
  font: inherit;
  font-size: 13px;
  padding: 4px 10px;
  color: var(--dim);
  background: transparent;
  border: 0;
  border-left: 1px solid var(--border-soft);
  cursor: pointer;
}

button:first-child { border-left: 0; }
button:hover { color: var(--text); }

button.on {
  color: var(--text);
  background: var(--panel-2);
  font-weight: 600;
}
</style>
