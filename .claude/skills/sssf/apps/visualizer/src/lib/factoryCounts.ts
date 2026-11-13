import { shallowRef } from 'vue'

/** The board's red and amber counts, for the header to keep in view. Null while no board is shown. */
export const factoryCounts = shallowRef<{ red: number; amber: number } | null>(null)
